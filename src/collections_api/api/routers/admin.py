"""Index administration: the only part of the service that can write to a database.

Off unless ``ENABLE_INDEX_ADMIN`` is set, authenticated like every other protected
route, and ``dry_run`` defaults to true. Actually creating indexes additionally needs
``REQUIRE_API_AUTH``: a route that changes the schema must not be open to anyone who can
reach the port.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from collections_api.api.common import ERROR_RESPONSES, SingleLineParam, ok_response, respond
from collections_api.api.context import AppContext
from collections_api.api.dependencies import Ctx
from collections_api.api.models import IndexApplyData, IndexStatusData
from collections_api.core.errors import ApiError
from collections_api.core.indexes import apply_indexes, list_index_status

router = APIRouter(prefix="/admin", tags=["admin"], responses=ERROR_RESPONSES)

DISABLED = "Index administration is disabled. Set ENABLE_INDEX_ADMIN=true to use it."


def _require_enabled(ctx: AppContext) -> None:
    if not ctx.settings.enable_index_admin:
        raise ApiError(403, DISABLED)
    if ctx.pool is None:
        raise ApiError(500, "Index administration needs the SQL Server connection pool.")


@router.get(
    "/indexes/status/{line}",
    operation_id="admin_indexes_status",
    summary="Which recommended indexes exist",
    response_model=None,
    responses=ok_response(IndexStatusData, many=False),
)
def indexes_status(ctx: Ctx, request: Request, line: SingleLineParam):
    """Read-only. For each recommended index, whether it exists in the line's database."""
    _require_enabled(ctx)
    name = ctx.cfg.resolve_line(line, allow_all=False)
    indexes = list_index_status(ctx.pool, ctx.cfg.connection_database(name), ctx.settings.db_schema)
    existing = sum(1 for index in indexes if index["exists"])
    data = {
        "product_line": name,
        "total_recommended": len(indexes),
        "existing": existing,
        "missing": len(indexes) - existing,
        "indexes": indexes,
    }
    return respond(ctx, request, data, line=name, filters={"line": name})


@router.post(
    "/indexes/apply/{line}",
    operation_id="admin_indexes_apply",
    summary="Create the missing recommended indexes",
    response_model=None,
    responses=ok_response(IndexApplyData, many=False),
)
def indexes_apply(
    ctx: Ctx,
    request: Request,
    line: SingleLineParam,
    dry_run: Annotated[
        bool, Query(description="Return the statements without running them. Default true.")
    ] = True,
    online: Annotated[
        bool, Query(description="Use `ONLINE = ON`. Needs a SQL Server edition that supports it.")
    ] = False,
    update_statistics: Annotated[
        bool,
        Query(description="Refresh statistics (full scan) on the tables that gained an index."),
    ] = False,
):
    """Create the recommended indexes that are missing. Existing ones are skipped.

    A failed statement is reported on its own step and does not stop the others. With
    `dry_run=false` the service must run with `REQUIRE_API_AUTH=true`.
    """
    _require_enabled(ctx)
    if not dry_run and not ctx.settings.require_api_auth:
        raise ApiError(
            403, "Creating indexes needs REQUIRE_API_AUTH=true. Use dry_run=true to preview."
        )
    name = ctx.cfg.resolve_line(line, allow_all=False)
    ctx.telemetry.event(
        "admin_indexes_apply",
        {
            "line": name,
            "dry_run": dry_run,
            "online": online,
            "update_statistics": update_statistics,
        },
        getattr(request.state, "run_id", None),
    )
    result = apply_indexes(
        ctx.pool,
        ctx.cfg.connection_database(name),
        ctx.settings.db_schema,
        online=online,
        dry_run=dry_run,
        update_statistics=update_statistics,
    )
    result.pop("database")
    return respond(
        ctx,
        request,
        {"product_line": name, **result},
        line=name,
        filters={
            "line": name,
            "dry_run": dry_run,
            "online": online,
            "update_statistics": update_statistics,
        },
    )
