"""Turn every failure into the same body.

An error response is the usual envelope with an empty ``data`` list, plus a top-level
``detail`` string. ``detail`` is what simple clients read; ``errors`` carries the same
message in the envelope shape. The text never contains driver messages, SQL or stack
traces: those go to the server log and, when configured, to Sentry.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from collections_api.core.envelope import build_envelope
from collections_api.core.errors import ApiError

logger = logging.getLogger("collections_api.api")

INTERNAL_ERROR_MESSAGE = "Internal error while processing the request."
RUN_ID_HEADER = "X-Run-Id"


def error_body(
    detail: str, run_id: str | None, *, errors: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    envelope = build_envelope([], [], errors=errors or [{"message": detail}], run_id=run_id)
    return {"detail": detail, **envelope}


def error_response(
    status_code: int,
    detail: str,
    run_id: str | None,
    *,
    errors: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged = dict(headers or {})
    if run_id:
        merged[RUN_ID_HEADER] = run_id
    return JSONResponse(
        status_code=status_code,
        content=error_body(detail, run_id, errors=errors),
        headers=merged or None,
    )


def _run_id(request: Request) -> str | None:
    return getattr(request.state, "run_id", None)


async def handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.detail, _run_id(request))


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return error_response(
        exc.status_code, detail, _run_id(request), headers=dict(exc.headers or {})
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    messages = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        messages.append(
            f"{location}: {item.get('msg', 'invalid value')}" if location else item["msg"]
        )
    detail = "; ".join(messages) or "Invalid request."
    return error_response(
        422, detail, _run_id(request), errors=[{"message": message} for message in messages]
    )


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    run_id = _run_id(request)
    logger.error(
        "unhandled error on %s (run_id=%s)",
        request.url.path,
        run_id,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    context = getattr(request.app.state, "ctx", None)
    if context is not None:
        context.telemetry.capture_exception(exc, run_id=run_id)
    return error_response(500, INTERNAL_ERROR_MESSAGE, run_id)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected)
