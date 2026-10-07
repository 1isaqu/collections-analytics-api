"""Application factory.

``create_app`` builds the whole service from a ``Settings`` object. It opens no
database connection, so importing the package, building the app and exporting its
OpenAPI document all work on a machine with no database and no ODBC driver.

Run it with ``python -m collections_api`` or ``uvicorn --factory collections_api.main:create_app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import APIRouter, FastAPI

from collections_api import __version__
from collections_api.api.context import build_context
from collections_api.api.dependencies import needs_auth, prefix_of
from collections_api.api.errors import install_error_handlers
from collections_api.api.middleware import Protection, body_limits_for, install_middleware
from collections_api.api.routers import (
    admin,
    agreements,
    charts,
    details,
    effectiveness,
    health,
    productivity,
    regression,
    targets,
)
from collections_api.api.static import install_static_routes
from collections_api.core.database import QueryExecutor
from collections_api.core.telemetry import Telemetry
from collections_api.settings import Settings

BASE_ROUTERS: tuple[APIRouter, ...] = (
    health.router,
    agreements.router,
    productivity.router,
    charts.router,
    details.router,
    targets.router,
    effectiveness.router,
    regression.router,
    admin.router,
)

TITLE = "Collections Analytics API"
DESCRIPTION = """\
Read-only KPIs for a collections operation: agreements, agent productivity, payment-slip
effectiveness, quarterly targets and a small regression endpoint.

**Product lines.** Data is split by *product line* (`auto` and `consumer` by default, set
with `PRODUCT_LINES`). Most routes take the line in the path or in a `line` query
parameter; `all` combines every line. Measures are summed and ratios are recomputed from
the sums.

**Envelope.** Data routes answer `{ "meta", "data", "errors" }`. `meta` echoes the filters
that were applied and carries a `run_id`; `errors` lists non-fatal problems. A failed
request answers the same shape with an empty `data`, a non-2xx status and a top-level
`detail` string. The health routes return plain status objects.

**Windows.** `date_from` and `date_to` are inclusive calendar dates. Give both or neither;
neither means today, in the server's local time.

**Authentication.** Off by default. With `REQUIRE_API_AUTH=true` every route except
`/health/live` needs the `X-API-Key` header and an `Authorization: Bearer` token. Requests
are rate limited per client address.

**Prefix.** Every path also answers under `/api`, for example `/api/dashboard/...`.
"""

TAGS = [
    {"name": "health", "description": "Liveness and readiness. Plain status bodies."},
    {"name": "agreements", "description": "Agreement lists, performance table, portfolio labels."},
    {
        "name": "productivity",
        "description": "Per-agent effort and results, load status, benchmarks.",
    },
    {"name": "charts", "description": "Aggregates behind the charts and cards."},
    {"name": "details", "description": "Agreement lists behind the charts."},
    {"name": "targets", "description": "Quarterly targets: read the latest, upload a PDF."},
    {"name": "effectiveness", "description": "Are issued payment slips being paid in time?"},
    {"name": "regression", "description": "Regression of agent results on effort."},
    {"name": "admin", "description": "Index administration. Disabled unless enabled in settings."},
]

API_KEY_SCHEME = {"type": "apiKey", "in": "header", "name": "X-API-Key"}
BEARER_SCHEME = {"type": "http", "scheme": "bearer"}


def _install_openapi(app: FastAPI, protected_prefixes: tuple[str, ...]) -> None:
    """Document the credentials on the routes that ask for them when auth is enabled."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        schema = original()
        components = schema.setdefault("components", {})
        if "securitySchemes" in components:
            return schema
        components["securitySchemes"] = {"ApiKeyAuth": API_KEY_SCHEME, "BearerAuth": BEARER_SCHEME}
        for path, item in schema.get("paths", {}).items():
            if needs_auth(path, protected_prefixes):
                for operation in item.values():
                    operation["security"] = [{"ApiKeyAuth": [], "BearerAuth": []}]
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(
    settings: Settings | None = None,
    *,
    executor: QueryExecutor | None = None,
    extra_routers: Sequence[APIRouter] = (),
    today: Callable[[], date] = date.today,
    telemetry: Telemetry | None = None,
) -> FastAPI:
    """Build the service.

    ``settings`` defaults to the environment plus an optional ``.env`` in the working
    directory. ``executor`` replaces the SQL Server connection (used by the tests and
    by anyone fronting a different store). ``extra_routers`` are mounted after the
    built-in ones and are protected and rate limited like them; see ``docs/extending.md``.
    """
    settings = settings or Settings.from_env(dotenv=".env")
    context = build_context(settings, executor=executor, telemetry=telemetry, today=today)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.effectiveness_etl_enabled:
            context.effectiveness.start()
        try:
            yield
        finally:
            context.effectiveness.stop()

    docs = settings.docs_enabled
    app = FastAPI(
        title=TITLE,
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        lifespan=lifespan,
    )
    app.state.ctx = context
    install_error_handlers(app)
    for router in (*BASE_ROUTERS, *extra_routers):
        app.include_router(router)

    extra_prefixes = tuple(
        dict.fromkeys(prefix_of(route.path) for router in extra_routers for route in router.routes)
    )
    protection = Protection(body_limits=body_limits_for(settings)).with_extra(extra_prefixes)
    install_middleware(app, protection)
    install_static_routes(app, settings)
    _install_openapi(app, protection.protected_prefixes)
    return app
