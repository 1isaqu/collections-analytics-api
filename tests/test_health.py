"""Liveness and readiness: what they report, and what they refuse to leak."""

from __future__ import annotations

from fastapi.testclient import TestClient

from collections_api.core.errors import ApiError


def healthy(client):
    client.executor.default = [{"ok": 1}]


def test_live_never_touches_the_database(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.executor.calls == []


def test_ready_probes_every_line(client):
    healthy(client)
    body = client.get("/health/ready").json()
    assert body["status"] == "ok"
    assert body["product_lines"] == {"auto": "ok", "consumer": "ok"}
    assert body["effectiveness_refresh"] == {
        "enabled": False,
        "last_run_age_seconds": None,
        "interval_seconds": 1800.0,
    }
    databases = [call["database"] for call in client.executor.calls]
    assert databases == ["collections_auto", "collections_consumer"]


def test_ready_is_503_when_one_line_is_down_and_names_the_curated_reason(make_app):
    app, _ = make_app(answers={"health": _fail_for("collections_consumer")})
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["product_lines"] == {"auto": "ok", "consumer": "The query exceeded the database time limit."}


def test_an_unexpected_probe_error_is_reported_without_its_message(make_app):
    app, _ = make_app(answers={"health": RuntimeError("host db.example.invalid refused the login")})
    body = TestClient(app).get("/health/ready").json()
    assert body["product_lines"] == {"auto": "unavailable", "consumer": "unavailable"}
    assert "db.example.invalid" not in str(body)


def test_an_odd_probe_answer_is_not_treated_as_healthy(client):
    client.executor.default = [{"ok": 0}]
    assert client.get("/health/ready").json()["status"] == "degraded"


def test_db_route_covers_every_line(client):
    healthy(client)
    response = client.get("/health/db")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "product_lines": ["auto", "consumer"],
        "connection": "connected",
    }


def test_db_route_is_503_when_any_line_fails(make_app):
    app, _ = make_app(answers={"health": _fail_for("collections_auto")})
    response = TestClient(app).get("/health/db")
    assert response.status_code == 503
    assert response.json()["detail"] == "Product lines unavailable: auto"


def test_db_line_route(client):
    healthy(client)
    assert client.get("/health/db/consumer").json() == {
        "status": "ok",
        "product_line": "consumer",
        "connection": "connected",
    }
    assert client.get("/health/db/nowhere").status_code == 400
    assert client.get("/health/db/all").status_code == 400  # a probe needs one database


def test_db_line_failure_does_not_echo_the_reason(make_app):
    app, _ = make_app(answers={"health": RuntimeError("secret detail")})
    response = TestClient(app).get("/health/db/auto")
    assert response.status_code == 503
    assert "secret detail" not in response.text


def test_ready_reports_the_age_of_the_last_effectiveness_refresh(make_app):
    app, executor = make_app({"EFFECTIVENESS_ETL_ENABLED": "true"})
    executor.default = [{"ok": 1}]
    app.state.ctx.effectiveness.refresh()
    refresh = TestClient(app).get("/health/ready").json()["effectiveness_refresh"]
    assert refresh["enabled"] is True
    assert refresh["last_run_age_seconds"] is not None and refresh["last_run_age_seconds"] < 5


def _fail_for(database: str):
    def answer(sql, db, params):
        if db == database:
            raise ApiError(504, "The query exceeded the database time limit.")
        return [{"ok": 1}]

    return answer
