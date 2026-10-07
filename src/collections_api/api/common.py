"""Helpers shared by the routers.

Router modules in this package must not use ``from __future__ import annotations``:
several routes are built inside factory functions, and FastAPI resolves their
parameter annotations from module globals, which cannot see a factory's locals.
"""

from datetime import date
from typing import Annotated, Any

from fastapi import Depends, Path, Query, Request

from collections_api.api.context import AppContext
from collections_api.api.dependencies import Ctx
from collections_api.api.models import ErrorBody, envelope
from collections_api.core.envelope import build_envelope
from collections_api.core.sql import Period, team_filter
from collections_api.core.sql import Query as SqlQuery

LINE_DESCRIPTION = (
    "Product line name as configured in `PRODUCT_LINES`, or `all` for every line. "
    "With `all`, measures are summed across lines."
)
SINGLE_LINE_DESCRIPTION = (
    "Product line name as configured in `PRODUCT_LINES`. `all` is not accepted."
)

LineParam = Annotated[str, Path(description=LINE_DESCRIPTION)]
SingleLineParam = Annotated[str, Path(description=SINGLE_LINE_DESCRIPTION)]

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorBody, "description": "A parameter is not acceptable."},
    401: {"model": ErrorBody, "description": "Authentication is required and failed."},
    422: {"model": ErrorBody, "description": "A parameter is missing or has the wrong type."},
    429: {"model": ErrorBody, "description": "Rate limit reached. See `Retry-After`."},
    500: {"model": ErrorBody, "description": "The query failed or the service is misconfigured."},
    504: {"model": ErrorBody, "description": "The query exceeded the database time limit."},
}


def ok_response(model: type, *, many: bool = True, description: str = "Success.") -> dict:
    """``responses=`` entry that documents a 200 without validating it."""
    return {200: {"model": envelope(model, many=many), "description": description}}


DateFromParam = Annotated[
    date | None,
    Query(
        description=(
            "First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither."
        )
    ),
]
DateToParam = Annotated[
    date | None,
    Query(description="Last day of the window, inclusive (`YYYY-MM-DD`)."),
]
AgentNameParam = Annotated[
    str | None, Query(description="Exact agent display name. Empty means every agent.")
]


def get_period(ctx: Ctx, date_from: DateFromParam = None, date_to: DateToParam = None) -> Period:
    """The requested window. Neither date given means today."""
    return Period.from_query(date_from, date_to, ctx.today())


Window = Annotated[Period, Depends(get_period)]

TeamParam = Annotated[
    str | None,
    Query(
        description=(
            "Keep agents whose login or display name contains this text (case-insensitive). "
            "Only the part after the last colon is used. Empty or `all` means everyone."
        )
    ),
]


def run_id_of(request: Request) -> str | None:
    return getattr(request.state, "run_id", None)


def run_query(ctx: AppContext, request: Request, query: SqlQuery, line: str, context: str) -> list:
    """Execute ``query`` on the database that serves ``line``."""
    return ctx.executor.run(
        query.sql,
        ctx.cfg.connection_database(line),
        query.params or None,
        run_id=run_id_of(request),
        context=context,
    )


def cached_query(
    ctx: AppContext, request: Request, key: str, query: SqlQuery, line: str, context: str
) -> list:
    """Rows for ``query``, shared between identical concurrent and recent requests.

    Callers must not modify the returned rows: they are the cached objects.
    """
    return ctx.cache.get_or_compute(key, lambda: run_query(ctx, request, query, line, context))


def respond(
    ctx: AppContext,
    request: Request,
    rows: Any,
    *,
    line: str | None = None,
    sources: list[str] | None = None,
    filters: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if sources is None:
        sources = [name for name, _ in ctx.cfg.scope(line)] if line else []
    return build_envelope(
        rows,
        sources,
        errors=errors,
        filters=filters,
        run_id=run_id_of(request),
        quality=quality,
        pagination=pagination,
    )


def window_filters(line: str, period: Period, **extra: Any) -> dict[str, Any]:
    return {"line": line, "period": period.label, **extra}


def apply_team(team: str | None) -> tuple[str, str]:
    """``(label, token)`` for filters echoed back and for cache keys."""
    label, token, _ = team_filter(team)
    return label, token
