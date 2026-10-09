from datetime import date, datetime
from decimal import Decimal

import pytest

from collections_api.core.database import (
    ConnectionPool,
    SqlServerExecutor,
    build_connection_string,
    json_safe,
)
from collections_api.core.errors import ApiError
from collections_api.settings import Settings
from tests.fakes import FakeDriver, FakeDriverError


def make_settings(**overrides):
    env = {"DB_USER": "reader", "DB_PASSWORD": "pw-for-tests", "DB_HOST": "db.example.invalid"}
    env.update(overrides)
    return Settings.from_env(env)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_connection_string_uses_settings_and_quotes_special_values():
    settings = make_settings(DB_PASSWORD="a;b}c", DB_PORT="1444", DB_TRUST_SERVER_CERT="true")
    text = build_connection_string(settings, "collections_auto")
    assert "SERVER=db.example.invalid,1444;" in text
    assert "DATABASE=collections_auto;" in text
    assert "PWD={a;b}}c};" in text
    assert "TrustServerCertificate=yes;" in text
    assert "DRIVER={ODBC Driver 18 for SQL Server};" in text


def test_certificate_trust_is_off_by_default():
    assert "TrustServerCertificate=no;" in build_connection_string(make_settings(), "d")


def test_missing_credentials_are_a_clear_500():
    with pytest.raises(ApiError) as caught:
        build_connection_string(Settings.defaults(), "d")
    assert caught.value.status_code == 500
    assert "DB_USER" in caught.value.detail


def test_pool_reuses_an_idle_connection():
    driver = FakeDriver()
    pool = ConnectionPool(make_settings(), driver=driver)
    with pool.connection("db_a") as first:
        pass
    with pool.connection("db_a") as second:
        pass
    assert first is second
    assert len(driver.connections) == 1
    assert driver.connect_calls[0][1:] == (10, True)  # login timeout, autocommit on


def test_pool_is_per_database():
    driver = FakeDriver()
    pool = ConnectionPool(make_settings(), driver=driver)
    with pool.connection("db_a"):
        pass
    with pool.connection("db_b"):
        pass
    assert len(driver.connections) == 2
    assert "DATABASE=db_a" in driver.connect_calls[0][0]
    assert "DATABASE=db_b" in driver.connect_calls[1][0]


def test_expired_connection_is_closed_and_replaced():
    clock = Clock()
    driver = FakeDriver()
    pool = ConnectionPool(make_settings(DB_POOL_MAX_AGE_SECONDS="100"), driver=driver, clock=clock)
    with pool.connection("db_a") as first:
        pass
    clock.now += 101
    with pool.connection("db_a") as second:
        pass
    assert first is not second and first.closed
    assert len(driver.connections) == 2


def test_dead_connection_is_replaced():
    def script(sql, params):
        if sql.strip().upper() == "SELECT 1":
            return FakeDriverError("08S01", "connection lost")
        return ["v"], [(1,)]

    driver = FakeDriver(script)
    pool = ConnectionPool(make_settings(), driver=driver)
    with pool.connection("db_a"):
        pass
    with pool.connection("db_a") as second:
        pass
    assert len(driver.connections) == 2
    assert driver.connections[0].closed
    assert second is driver.connections[1]


def test_connection_that_raised_is_closed_not_returned():
    driver = FakeDriver()
    pool = ConnectionPool(make_settings(), driver=driver)
    with pytest.raises(RuntimeError):
        with pool.connection("db_a"):
            raise RuntimeError("boom")
    assert driver.connections[0].closed
    assert pool.idle_count("db_a") == 0


def test_returning_to_a_full_pool_closes_the_extra_connection():
    driver = FakeDriver()
    pool = ConnectionPool(make_settings(DB_POOL_SIZE="1"), driver=driver)
    with pool.connection("db_a") as first:
        with pool.connection("db_a") as second:
            assert first is not second
    assert pool.idle_count("db_a") == 1
    assert sum(1 for c in driver.connections if c.closed) == 1


def test_connect_failure_maps_to_500_without_the_driver_message():
    driver = FakeDriver()
    driver.fail_connect = FakeDriverError("secret host name in message")
    pool = ConnectionPool(make_settings(), driver=driver)
    with pytest.raises(ApiError) as caught:
        with pool.connection("db_a"):
            pass
    assert caught.value.status_code == 500
    assert caught.value.detail == "Could not connect to the database."
    assert "secret" not in caught.value.detail


def test_executor_returns_json_safe_dict_rows_and_sets_the_statement_timeout():
    def script(sql, params):
        return ["amount", "day", "stamp", "note"], [
            (Decimal("12.50"), date(2026, 1, 2), datetime(2026, 1, 2, 3, 4, 5), None)
        ]

    driver = FakeDriver(script)
    settings = make_settings(DB_QUERY_TIMEOUT="7")
    executor = SqlServerExecutor(ConnectionPool(settings, driver=driver), settings)
    rows = executor.run("SELECT x", "db_a", ("p",), context="test")
    assert rows == [
        {"amount": 12.5, "day": "2026-01-02", "stamp": "2026-01-02T03:04:05", "note": None}
    ]
    assert driver.connections[0].timeout == 7
    assert driver.connections[0].executed[-1] == ("SELECT x", ("p",))


def test_driver_timeout_becomes_504_and_other_driver_errors_500():
    def timeout_script(sql, params):
        return FakeDriverError("HYT00", "Query timeout expired")

    settings = make_settings()
    executor = SqlServerExecutor(
        ConnectionPool(settings, driver=FakeDriver(timeout_script)), settings
    )
    with pytest.raises(ApiError) as caught:
        executor.run("SELECT 1 AS x", "db_a")
    assert caught.value.status_code == 504

    def broken_script(sql, params):
        return FakeDriverError("42000", "Syntax error near 'x'")

    executor = SqlServerExecutor(
        ConnectionPool(settings, driver=FakeDriver(broken_script)), settings
    )
    with pytest.raises(ApiError) as caught:
        executor.run("SELECT 1 AS x", "db_a")
    assert caught.value.status_code == 500
    assert "Syntax" not in caught.value.detail


def test_statement_without_a_result_set_is_a_controlled_500():
    def no_result(sql, params):
        return [], []

    settings = make_settings()
    executor = SqlServerExecutor(ConnectionPool(settings, driver=FakeDriver(no_result)), settings)
    with pytest.raises(ApiError) as caught:
        executor.run("UPDATE t SET x = 1", "db_a")
    assert caught.value.status_code == 500


def test_a_failed_query_does_not_poison_the_pool():
    calls = {"n": 0}

    def script(sql, params):
        if sql.strip().upper() == "SELECT 1":
            return ["ok"], [(1,)]
        calls["n"] += 1
        return FakeDriverError("42000", "bad") if calls["n"] == 1 else (["v"], [(1,)])

    driver = FakeDriver(script)
    settings = make_settings()
    executor = SqlServerExecutor(ConnectionPool(settings, driver=driver), settings)
    with pytest.raises(ApiError):
        executor.run("SELECT bad", "db_a")
    assert executor.run("SELECT good", "db_a") == [{"v": 1}]
    assert len(driver.connections) == 2 and driver.connections[0].closed


def test_json_safe_conversions():
    assert json_safe(Decimal("3")) == 3 and isinstance(json_safe(Decimal("3")), int)
    assert json_safe(Decimal("3.25")) == 3.25
    assert json_safe(b"\x00\x01") == "AAE="
    assert json_safe(True) is True

    class Opaque:
        def __str__(self) -> str:
            return "opaque"

    assert json_safe(Opaque()) == "opaque"
