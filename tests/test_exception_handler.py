"""Every failure has the same body, a run id, and no internals."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.testclient import TestClient

from collections_api.core.errors import ApiError
from collections_api.core.telemetry import Telemetry
from collections_api.main import create_app
from collections_api.settings import Settings

ENVELOPE_KEYS = {"detail", "meta", "data", "errors"}


def explosive_router() -> APIRouter:
    router = APIRouter(prefix="/boom")

    @router.get("/crash")
    def crash():
        raise RuntimeError("connection string was Server=db.example.invalid;PWD=hunter2")

    @router.get("/refuse")
    def refuse():
        raise ApiError(409, "Already running.")

    @router.get("/typed")
    def typed(count: int):
        return {"count": count}

    return router


def build(make_app):
    app, _ = make_app(extra_routers=[explosive_router()])
    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_error_is_a_generic_envelope_with_the_run_id(make_app):
    client = build(make_app)
    response = client.get("/boom/crash", headers={"X-Run-Id": "run-42"})
    assert response.status_code == 500
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["detail"] == "Internal error while processing the request."
    assert body["data"] == [] and body["errors"] == [{"message": body["detail"]}]
    assert body["meta"]["run_id"] == "run-42"
    assert response.headers["x-run-id"] == "run-42"
    assert "hunter2" not in response.text and "db.example.invalid" not in response.text


def test_api_error_keeps_its_status_and_message(make_app):
    response = build(make_app).get("/boom/refuse")
    assert response.status_code == 409
    assert response.json()["detail"] == "Already running."
    assert set(response.json()) == ENVELOPE_KEYS


def test_validation_error_names_the_parameter(make_app):
    response = build(make_app).get("/boom/typed?count=many")
    assert response.status_code == 422
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["detail"].startswith("query.count:")
    assert body["errors"][0]["message"] == body["detail"]


def test_unknown_path_and_wrong_method_use_the_envelope(make_app):
    client = build(make_app)
    missing = client.get("/no/such/route")
    assert missing.status_code == 404 and set(missing.json()) == ENVELOPE_KEYS
    wrong = client.post("/health/live")
    assert wrong.status_code == 405 and set(wrong.json()) == ENVELOPE_KEYS


def test_a_supplied_run_id_is_reflected_only_when_it_is_well_formed(make_app):
    client = build(make_app)
    assert client.get("/health/live", headers={"X-Run-Id": "abc.DEF_1-2"}).headers["x-run-id"] == "abc.DEF_1-2"
    assert client.get("/health/live", headers={"X-Request-Id": "req-7"}).headers["x-run-id"] == "req-7"
    for bad in ("has space", "x" * 65, "semi;colon", "quote\""):
        reflected = client.get("/health/live", headers={"X-Run-Id": bad}).headers["x-run-id"]
        assert reflected != bad and reflected.startswith("srv-")


def test_every_response_carries_a_run_id_and_nosniff(make_app):
    response = build(make_app).get("/health/live")
    assert response.headers["x-run-id"].startswith("srv-")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_unhandled_errors_reach_the_event_log(tmp_path):
    log = tmp_path / "events.ndjson"
    settings = Settings.from_env({"EFFECTIVENESS_ETL_ENABLED": "false", "TARGETS_DIR": str(tmp_path)})
    app = create_app(
        settings,
        executor=_NoExecutor(),
        extra_routers=[explosive_router()],
        telemetry=Telemetry(log_path=log),
    )
    TestClient(app, raise_server_exceptions=False).get("/boom/crash", headers={"X-Run-Id": "run-9"})
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    crash = next(event for event in events if event["event"] == "unhandled_exception")
    assert crash["run_id"] == "run-9"
    assert crash["data"]["type"] == "RuntimeError"


class _NoExecutor:
    def run(self, *args, **kwargs):
        raise AssertionError("no query expected")
