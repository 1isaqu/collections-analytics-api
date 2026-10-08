"""The extension hook: extra routers get the same protection as the built-in ones."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.testclient import TestClient

from collections_api.api.dependencies import Ctx, prefix_of
from collections_api.core.sql import Period

ROOT = Path(__file__).resolve().parents[1]
AUTH = {"REQUIRE_API_AUTH": "true", "API_KEY": "test-key-1", "API_TOKEN": "test-token-1"}
GOOD = {"X-API-Key": "test-key-1", "Authorization": "Bearer test-token-1"}


def pace_router() -> APIRouter:
    """What a sibling project would mount: a route that reads the shared context."""
    router = APIRouter(prefix="/pace", tags=["pace"])

    @router.get("/{line}")
    def pace(ctx: Ctx, line: str):
        resolved = ctx.cfg.resolve_line(line)
        rows = ctx.executor.run("SELECT 1 AS ok", ctx.cfg.connection_database(resolved))
        return {"line": resolved, "today": Period.day(ctx.today()).label, "rows": rows}

    return router


def test_an_extra_router_is_mounted_and_sees_the_shared_context(make_app):
    app, executor = make_app(extra_routers=[pace_router()])
    executor.default = [{"ok": 1}]
    body = TestClient(app).get("/pace/consumer").json()
    assert body == {"line": "consumer", "today": "today", "rows": [{"ok": 1}]}
    assert executor.calls[0]["database"] == "collections_consumer"


def test_the_extra_prefix_is_authenticated_and_unknown_lines_are_400(make_app):
    app, _ = make_app(AUTH, extra_routers=[pace_router()])
    client = TestClient(app)
    assert client.get("/pace/auto").status_code == 401
    assert client.get("/pace/auto", headers=GOOD).status_code == 200
    assert client.get("/pace/nowhere", headers=GOOD).status_code == 400


def test_the_extra_prefix_is_rate_limited(make_app):
    app, _ = make_app({"RATE_LIMIT_REQUESTS": "1"}, extra_routers=[pace_router()])
    client = TestClient(app)
    assert client.get("/pace/auto").status_code == 200
    assert client.get("/pace/auto").status_code == 429


def test_the_api_alias_reaches_extra_routes(make_app):
    app, _ = make_app(extra_routers=[pace_router()])
    assert TestClient(app).get("/api/pace/auto").status_code == 200


def documented_router() -> APIRouter:
    """Run the first Python block of docs/extending.md, minus its last line.

    The last line builds an app from the environment, which a test must not do.
    """
    text = (ROOT / "docs" / "extending.md").read_text(encoding="utf-8")
    block = text.split("```python\n", 1)[1].split("```", 1)[0]
    lines = block.rstrip("\n").splitlines()
    assert lines[-1].startswith("app = create_app(")
    namespace: dict = {}
    exec(compile("\n".join(lines[:-1]), "docs/extending.md", "exec"), namespace)
    return namespace["router"]


def test_the_example_in_the_extending_guide_runs_as_written(make_app):
    app, executor = make_app(extra_routers=[documented_router()])
    executor.default = [{"ok": 1}]
    client = TestClient(app)
    for path in ("/pace/auto", "/api/pace/auto"):
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == [{"ok": 1}]
        assert body["meta"]["sources"] == ["auto"]
        assert body["meta"]["filters"] == {"line": "auto", "period": "today"}
        assert body["errors"] == []
    assert executor.calls[0]["params"] == (1,)
    assert len(executor.calls) == 1, "the second request is served from the cache"
    assert client.get("/pace/nowhere").status_code == 400


def test_prefix_of_takes_the_first_segment():
    assert prefix_of("/pace/{line}") == "/pace/"
    assert prefix_of("pace") == "/pace/"
    assert prefix_of("/") == "/"
