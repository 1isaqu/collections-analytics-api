"""The index admin routes: off by default, authenticated, dry run by default."""

from __future__ import annotations

from fastapi.testclient import TestClient

from collections_api.core.database import ConnectionPool
from collections_api.core.indexes import RECOMMENDED_INDEXES
from tests.fakes import FakeDriver

ENABLE = {"ENABLE_INDEX_ADMIN": "true"}
AUTH = {
    "REQUIRE_API_AUTH": "true",
    "API_KEY": "test-key-1",
    "API_TOKEN": "test-token-1",
}
HEADERS = {"X-API-Key": "test-key-1", "Authorization": "Bearer test-token-1"}


def admin_client(make_app, env, existing=frozenset()):
    app, _ = make_app({"DB_USER": "reader", "DB_PASSWORD": "pw-for-tests", **env})
    driver = FakeDriver(lambda sql, params: _script(sql, params, existing))
    app.state.ctx.pool = ConnectionPool(app.state.ctx.settings, driver=driver)
    return TestClient(app), driver


def _script(sql, params, existing):
    if sql.startswith("SELECT 1 FROM sys.indexes"):
        return (["x"], [(1,)]) if params[2] in existing else (["x"], [])
    return [], []


def test_routes_are_forbidden_until_switched_on(client):
    assert client.get("/admin/indexes/status/auto").status_code == 403
    assert client.post("/admin/indexes/apply/auto").status_code == 403
    assert "ENABLE_INDEX_ADMIN" in client.get("/admin/indexes/status/auto").json()["detail"]


def test_enabled_without_a_pool_is_a_clear_500(make_app):
    app, _ = make_app(ENABLE)  # a fake executor, so there is no connection pool
    response = TestClient(app).get("/admin/indexes/status/auto")
    assert response.status_code == 500
    assert "connection pool" in response.json()["detail"]


def test_status_counts_existing_and_missing(make_app):
    client, _ = admin_client(make_app, ENABLE, existing={"ix_agents_agent"})
    data = client.get("/admin/indexes/status/auto").json()["data"]
    assert data["product_line"] == "auto"
    assert data["total_recommended"] == len(RECOMMENDED_INDEXES)
    assert data["existing"] == 1 and data["missing"] == len(RECOMMENDED_INDEXES) - 1
    assert {i["name"] for i in data["indexes"] if i["exists"]} == {"ix_agents_agent"}


def test_status_needs_one_known_line(make_app):
    client, _ = admin_client(make_app, ENABLE)
    assert client.get("/admin/indexes/status/all").status_code == 400
    assert client.get("/admin/indexes/status/nowhere").status_code == 400


def test_apply_is_a_dry_run_by_default_and_hides_database_names(make_app):
    client, driver = admin_client(make_app, ENABLE, existing={"ix_agents_agent"})
    response = client.post("/admin/indexes/apply/auto")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dry_run"] is True and "database" not in data
    actions = {step["name"]: step["action"] for step in data["indexes"]}
    assert actions["ix_agents_agent"] == "skipped_existing"
    assert actions["ix_agreements_issued_at"] == "would_create"
    executed = [sql for conn in driver.connections for sql, _ in conn.executed]
    assert not any(sql.startswith("CREATE") for sql in executed)
    assert "collections_auto" not in response.text


def test_creating_indexes_needs_authentication_to_be_on(make_app):
    client, driver = admin_client(make_app, ENABLE)
    response = client.post("/admin/indexes/apply/auto?dry_run=false")
    assert response.status_code == 403
    assert "REQUIRE_API_AUTH" in response.json()["detail"]
    assert driver.connections == []  # refused before any connection was opened


def test_apply_for_real_with_authentication(make_app):
    client, driver = admin_client(make_app, {**ENABLE, **AUTH})
    assert client.post("/admin/indexes/apply/auto?dry_run=false").status_code == 401
    response = client.post(
        "/admin/indexes/apply/consumer?dry_run=false&online=true&update_statistics=true",
        headers=HEADERS,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert {step["action"] for step in data["indexes"]} == {"created"}
    assert data["online"] is True and data["update_statistics"] is True
    assert {step["action"] for step in data["statistics"]} == {"updated"}
    created = [
        sql
        for conn in driver.connections
        for sql, _ in conn.executed
        if sql.startswith("CREATE NONCLUSTERED INDEX")
    ]
    assert len(created) == len(RECOMMENDED_INDEXES)
    assert all("ONLINE = ON" in sql for sql in created)
    # The connection used the database that serves the consumer line.
    assert "DATABASE=collections_consumer" in driver.connect_calls[-1][0]


def test_admin_routes_are_rate_limited_and_protected_like_data_routes(make_app):
    client, _ = admin_client(make_app, {**ENABLE, **AUTH})
    assert client.get("/admin/indexes/status/auto").status_code == 401
    assert client.get("/admin/indexes/status/auto", headers=HEADERS).status_code == 200
