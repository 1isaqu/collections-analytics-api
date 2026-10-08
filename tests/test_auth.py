"""Authentication, rate limiting, CORS order and the docs switch."""

from __future__ import annotations

from fastapi.testclient import TestClient

AUTH = {"REQUIRE_API_AUTH": "true", "API_KEY": "test-key-1", "API_TOKEN": "test-token-1"}
GOOD = {"X-API-Key": "test-key-1", "Authorization": "Bearer test-token-1"}
DATA_URL = "/dashboard/portfolios/auto"


def with_auth(make_app, **extra):
    app, executor = make_app({**AUTH, **extra})
    return TestClient(app), executor


def test_auth_is_off_by_default(client):
    assert client.get(DATA_URL).status_code == 200


def test_missing_or_wrong_credentials_are_401(make_app):
    client, executor = with_auth(make_app)
    for headers in (
        {},
        {"X-API-Key": "test-key-1"},
        {"Authorization": "Bearer test-token-1"},
        {"X-API-Key": "wrong", "Authorization": "Bearer test-token-1"},
        {"X-API-Key": "test-key-1", "Authorization": "Bearer wrong"},
        {"X-API-Key": "test-key-1", "Authorization": "test-token-1"},
    ):
        response = client.get(DATA_URL, headers=headers)
        assert response.status_code == 401, headers
        assert response.json()["detail"] == "Unauthorized"
    assert executor.calls == []  # a rejected request never reaches the database


def test_both_credentials_open_the_route(make_app):
    client, _ = with_auth(make_app)
    assert client.get(DATA_URL, headers=GOOD).status_code == 200


def test_a_non_ascii_credential_is_401_not_a_crash(make_app):
    client, _ = with_auth(make_app)
    response = client.get(
        DATA_URL,
        headers={"X-API-Key": "caf\xe9".encode("latin-1"), "Authorization": GOOD["Authorization"]},
    )
    assert response.status_code == 401


def test_every_protected_prefix_needs_the_credentials(make_app):
    client, _ = with_auth(make_app)
    for url in (
        "/dashboard/targets",
        "/effectiveness/summary?date_from=2026-03-01&date_to=2026-03-02",
        "/health/ready",
        "/health/db",
        "/admin/indexes/status/auto",
    ):
        assert client.get(url).status_code == 401, url
    assert client.post("/regression/agents", json={"points": []}).status_code == 401
    assert client.get("/api/dashboard/targets").status_code == 401  # the alias is no way around it


def test_liveness_and_the_service_banner_stay_open(make_app):
    client, _ = with_auth(make_app)
    assert client.get("/health/live").status_code == 200
    assert client.get("/").status_code == 200


def test_docs_are_off_when_auth_is_required_unless_switched_on(make_app):
    client, _ = with_auth(make_app)
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code == 404
    on, _ = with_auth(make_app, ENABLE_DOCS="true")
    assert on.get("/docs").status_code == 200
    off_app, _ = make_app({"ENABLE_DOCS": "false"})
    assert TestClient(off_app).get("/docs").status_code == 404


def test_docs_are_on_when_auth_is_off(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_rate_limit_answers_429_with_retry_after(make_app):
    client, _ = with_auth(make_app, RATE_LIMIT_REQUESTS="2", RATE_LIMIT_WINDOW_SECONDS="60")
    assert client.get(DATA_URL, headers=GOOD).status_code == 200
    assert client.get(DATA_URL, headers=GOOD).status_code == 200
    limited = client.get(DATA_URL, headers=GOOD)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.json()["detail"] == "Too many requests."
    assert client.get("/health/live").status_code == 200  # monitoring is never limited


def test_rate_limit_does_not_apply_to_health_probes_or_rejected_requests(make_app):
    client, _ = with_auth(make_app, RATE_LIMIT_REQUESTS="1")
    for _ in range(5):
        assert client.get("/health/live").status_code == 200
    for _ in range(5):
        assert client.get(DATA_URL).status_code == 401  # rejected before it is counted


def test_rate_limit_also_works_with_auth_off(make_app):
    app, _ = make_app({"RATE_LIMIT_REQUESTS": "1"})
    client = TestClient(app)
    assert client.get(DATA_URL).status_code == 200
    assert client.get(DATA_URL).status_code == 429


def test_cors_preflight_is_answered_before_authentication(make_app):
    client, _ = with_auth(make_app)
    response = client.options(
        DATA_URL,
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_error_responses_carry_cors_headers_and_expose_the_run_id(make_app):
    client, _ = with_auth(make_app)
    response = client.get(DATA_URL, headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "x-run-id" in response.headers["access-control-expose-headers"].lower()


def test_an_unlisted_origin_gets_no_cors_headers(client):
    response = client.get(DATA_URL, headers={"Origin": "http://elsewhere.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
