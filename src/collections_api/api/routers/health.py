"""Liveness and readiness. Plain status bodies, not envelopes, so monitors stay simple."""

import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from collections_api.api.common import ERROR_RESPONSES, SingleLineParam
from collections_api.api.context import AppContext
from collections_api.api.dependencies import Ctx
from collections_api.api.errors import error_response
from collections_api.api.models import (
    AllDatabasesStatus,
    DatabaseStatus,
    LiveStatus,
    ReadyStatus,
)
from collections_api.core.errors import ApiError

router = APIRouter(prefix="/health", tags=["health"])

UNAVAILABLE = "unavailable"


def probe(ctx: AppContext, line: str, request: Request) -> str:
    """``ok``, or a short reason. Never raises: a health check must not fail itself."""
    try:
        rows = ctx.executor.run(
            "SELECT 1 AS ok",
            ctx.cfg.connection_database(line),
            run_id=getattr(request.state, "run_id", None),
            context="health/probe",
        )
    except ApiError as exc:
        return exc.detail
    except Exception:
        return UNAVAILABLE
    return "ok" if rows and rows[0].get("ok") == 1 else "unexpected response"


@router.get(
    "/live",
    operation_id="health_live",
    summary="Liveness",
    response_model=None,
    responses={200: {"model": LiveStatus, "description": "The process is up."}},
)
def live() -> dict[str, str]:
    """The process is running. Does not touch the database and never needs authentication."""
    return {"status": "ok"}


@router.get(
    "/ready",
    operation_id="health_ready",
    summary="Readiness",
    response_model=None,
    responses={
        200: {"model": ReadyStatus, "description": "Every product line answers."},
        503: {"model": ReadyStatus, "description": "At least one product line is down."},
    },
)
def ready(ctx: Ctx, request: Request) -> Any:
    """State of every product line, plus how fresh the effectiveness series are."""
    lines = {name: probe(ctx, name, request) for name in ctx.cfg.line_names}
    degraded = any(state != "ok" for state in lines.values())
    last_run = ctx.effectiveness.last_run
    body = {
        "status": "degraded" if degraded else "ok",
        "product_lines": lines,
        "effectiveness_refresh": {
            "enabled": ctx.settings.effectiveness_etl_enabled,
            "last_run_age_seconds": round(time.time() - last_run, 1) if last_run else None,
            "interval_seconds": ctx.effectiveness.interval_seconds,
        },
    }
    if degraded:
        ctx.telemetry.log("warning", "Readiness degraded.", product_lines=",".join(lines))
        return JSONResponse(status_code=503, content=body)
    return body


@router.get(
    "/db",
    operation_id="health_db_all",
    summary="Database check, every line",
    response_model=None,
    responses={
        200: {"model": AllDatabasesStatus, "description": "Every product line answers."},
        503: ERROR_RESPONSES[500] | {"description": "At least one product line is down."},
    },
)
def database_all(ctx: Ctx, request: Request) -> Any:
    """Runs `SELECT 1` against each configured product line. 503 if any fails."""
    states = {name: probe(ctx, name, request) for name in ctx.cfg.line_names}
    down = [name for name, state in states.items() if state != "ok"]
    if down:
        return error_response(
            503,
            f"Product lines unavailable: {', '.join(down)}",
            getattr(request.state, "run_id", None),
        )
    return {"status": "ok", "product_lines": list(states), "connection": "connected"}


@router.get(
    "/db/{line}",
    operation_id="health_db_line",
    summary="Database check, one line",
    response_model=None,
    responses={
        200: {"model": DatabaseStatus, "description": "The line answers."},
        400: ERROR_RESPONSES[400],
        503: ERROR_RESPONSES[500] | {"description": "The line is down."},
    },
)
def database_line(ctx: Ctx, request: Request, line: SingleLineParam) -> Any:
    """Runs `SELECT 1` against one product line. 503 if it fails."""
    name = ctx.cfg.resolve_line(line, allow_all=False)
    state = probe(ctx, name, request)
    if state != "ok":
        return error_response(
            503, "The database check failed.", getattr(request.state, "run_id", None)
        )
    return {"status": "ok", "product_line": name, "connection": "connected"}
