import pytest

from collections_api.core.database import ConnectionPool
from collections_api.core.errors import ApiError
from collections_api.core.indexes import (
    RECOMMENDED_INDEXES,
    apply_indexes,
    build_create_index_sql,
    build_update_statistics_sql,
    list_index_status,
)
from collections_api.settings import Settings
from tests.fakes import FakeDriver, FakeDriverError

SETTINGS = Settings.from_env({"DB_USER": "reader", "DB_PASSWORD": "pw-for-tests"})


def pool_with(existing: set[str], failing: set[str] | None = None):
    failing = failing or set()

    def script(sql, params):
        if sql.startswith("SELECT 1 FROM sys.indexes"):
            return (["x"], [(1,)]) if params[2] in existing else (["x"], [])
        for name in failing:
            if name in sql:
                return FakeDriverError("42000", "no permission")
        return [], []

    driver = FakeDriver(script)
    return ConnectionPool(SETTINGS, driver=driver), driver


def test_create_sql_quotes_identifiers_and_lists_includes():
    spec = RECOMMENDED_INDEXES[0]
    sql = build_create_index_sql("dbo", spec, online=False)
    assert sql.startswith("CREATE NONCLUSTERED INDEX [ix_agreements_issued_at]")
    assert "ON [dbo].[agreements] ([issued_at])" in sql
    assert "INCLUDE ([agreement_id]" in sql
    assert sql.endswith("WITH (FILLFACTOR = 90, ONLINE = OFF)")
    assert "ONLINE = ON" in build_create_index_sql("dbo", spec, online=True)
    assert (
        build_update_statistics_sql("dbo", "agreements")
        == "UPDATE STATISTICS [dbo].[agreements] WITH FULLSCAN"
    )


def test_every_recommended_index_targets_a_contract_table():
    tables = {s.table for s in RECOMMENDED_INDEXES}
    assert tables <= {
        "agreements",
        "agreement_debts",
        "debts",
        "debt_attributes",
        "contact_events",
        "agents",
    }
    assert len({s.name for s in RECOMMENDED_INDEXES}) == len(RECOMMENDED_INDEXES)


def test_status_lists_existing_and_missing():
    pool, _ = pool_with({"ix_agents_agent"})
    status = list_index_status(pool, "db_a", "dbo")
    assert len(status) == len(RECOMMENDED_INDEXES)
    assert {row["name"] for row in status if row["exists"]} == {"ix_agents_agent"}


def test_dry_run_executes_nothing_and_skips_existing():
    pool, driver = pool_with({"ix_agents_agent"})
    result = apply_indexes(pool, "db_a", "dbo", online=False, dry_run=True, update_statistics=True)
    actions = {step["name"]: step["action"] for step in result["indexes"]}
    assert actions["ix_agents_agent"] == "skipped_existing"
    assert actions["ix_agreements_issued_at"] == "would_create"
    statements = [sql for sql, _ in driver.connections[0].executed]
    assert not any(sql.startswith(("CREATE", "UPDATE")) for sql in statements)
    assert driver.connections[0].commits == 0
    assert all(step["action"] == "would_update" for step in result["statistics"])


def test_apply_creates_missing_indexes_and_refreshes_only_touched_tables():
    existing = {s.name for s in RECOMMENDED_INDEXES if s.table != "agents"}
    pool, driver = pool_with(existing)
    result = apply_indexes(pool, "db_a", "dbo", online=True, dry_run=False, update_statistics=True)
    created = [s["name"] for s in result["indexes"] if s["action"] == "created"]
    assert created == ["ix_agents_agent"]
    assert [s["target"] for s in result["statistics"]] == ["dbo.agents"]
    executed = [sql for sql, _ in driver.connections[0].executed]
    assert any(
        sql.startswith("CREATE NONCLUSTERED INDEX [ix_agents_agent]") and "ONLINE = ON" in sql
        for sql in executed
    )
    assert driver.connections[0].commits == 2


def test_one_failed_statement_does_not_stop_the_rest():
    pool, _ = pool_with(set(), failing={"ix_agreements_issued_at"})
    result = apply_indexes(
        pool, "db_a", "dbo", online=False, dry_run=False, update_statistics=False
    )
    by_name = {s["name"]: s for s in result["indexes"]}
    assert by_name["ix_agreements_issued_at"]["action"] == "failed"
    assert "no permission" in by_name["ix_agreements_issued_at"]["error"]
    assert by_name["ix_agents_agent"]["action"] == "created"
    assert result["statistics"] == []


def test_connection_failure_is_a_controlled_error():
    driver = FakeDriver()
    driver.fail_connect = FakeDriverError("08001", "unreachable")
    pool = ConnectionPool(SETTINGS, driver=driver)
    with pytest.raises(ApiError) as caught:
        list_index_status(pool, "db_a", "dbo")
    assert caught.value.status_code == 500
