"""Middleware, outermost first: CORS, body ceilings, ``/api`` alias, security.

CORS sits outside everything else on purpose. A browser preflight carries no
credentials, so it has to be answered before authentication runs, and the 401 and 429
responses need CORS headers or the page cannot read them.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from collections_api.api.dependencies import (
    BASE_PROTECTED_PREFIXES,
    BASE_RATE_LIMITED_PREFIXES,
    check_auth,
    extract_run_id,
    is_rate_limited,
    needs_auth,
    normalize_api_path,
)
from collections_api.api.errors import RUN_ID_HEADER, error_response
from collections_api.core.errors import ApiError
from collections_api.settings import Settings

BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
# Room for multipart boundaries and headers around an upload of the configured size.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
# Rough upper bound for one regression point serialised as JSON.
REGRESSION_BYTES_PER_POINT = 1024

CallNext = Callable[[Request], Awaitable[Response]]


@dataclass(frozen=True)
class Protection:
    """Which paths are authenticated, which are rate limited, how large bodies may be."""

    protected_prefixes: tuple[str, ...] = BASE_PROTECTED_PREFIXES
    rate_limited_prefixes: tuple[str, ...] = BASE_RATE_LIMITED_PREFIXES
    body_limits: tuple[tuple[str, int], ...] = ()

    def with_extra(self, prefixes: tuple[str, ...]) -> Protection:
        return Protection(
            tuple(dict.fromkeys(self.protected_prefixes + prefixes)),
            tuple(dict.fromkeys(self.rate_limited_prefixes + prefixes)),
            self.body_limits,
        )


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Reject request bodies above a per-prefix ceiling before they are buffered.

    A ``Content-Length`` above the ceiling is answered at once. A body that does not
    declare its length (chunked) is counted as it arrives and cut off at the ceiling.
    """

    def __init__(self, app: ASGIApp, limits: tuple[tuple[str, int], ...]) -> None:
        self.app = app
        self.limits = limits

    def _limit_for(self, scope: Scope) -> int | None:
        if scope["type"] != "http" or scope["method"] not in BODY_METHODS:
            return None
        path = normalize_api_path(scope["path"])
        for prefix, limit in self.limits:
            if path.startswith(prefix):
                return limit
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self._limit_for(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send, limit, headers)
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if started:
                raise
            await self._reject(scope, receive, send, limit, headers)

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, limit: int, headers: Headers
    ) -> None:
        response = error_response(
            413,
            f"The request body is larger than the {limit} byte limit for this route.",
            extract_run_id(headers),
        )
        await response(scope, receive, send)


def install_middleware(app: FastAPI, protection: Protection) -> None:
    """Register the middleware. Later registrations wrap earlier ones."""

    async def security_middleware(request: Request, call_next: CallNext) -> Response:
        context = request.app.state.ctx
        path = request.scope["path"]
        run_id = extract_run_id(request.headers)
        request.state.run_id = run_id
        started = time.perf_counter()

        if needs_auth(path, protection.protected_prefixes):
            try:
                check_auth(context.settings, request.headers)
            except ApiError as exc:
                context.telemetry.event("auth_reject", {"path": path}, run_id)
                context.telemetry.log("warning", "Authentication rejected.", path=path)
                return error_response(exc.status_code, exc.detail, run_id)
            if is_rate_limited(path, protection.rate_limited_prefixes):
                client = request.client.host if request.client else "unknown"
                retry_after = context.rate_limiter.hit(client)
                if retry_after is not None:
                    context.telemetry.event("rate_limited", {"path": path}, run_id)
                    return error_response(
                        429,
                        "Too many requests.",
                        run_id,
                        headers={"Retry-After": str(retry_after)},
                    )

        response = await call_next(request)
        response.headers[RUN_ID_HEADER] = run_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        context.telemetry.event(
            "response",
            {
                "path": path,
                "status": response.status_code,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
            run_id,
        )
        return response

    async def api_prefix_middleware(request: Request, call_next: CallNext) -> Response:
        request.scope["path"] = normalize_api_path(request.scope.get("path", ""))
        return await call_next(request)

    app.middleware("http")(security_middleware)
    app.middleware("http")(api_prefix_middleware)
    if protection.body_limits:
        app.add_middleware(BodyLimitMiddleware, limits=protection.body_limits)
    settings = app.state.ctx.settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[RUN_ID_HEADER, "Retry-After"],
    )


def body_limits_for(settings: Settings) -> tuple[tuple[str, int], ...]:
    """Ceilings per prefix, longest prefix first so the upload route wins over its parent."""
    regression = settings.regression_max_points * REGRESSION_BYTES_PER_POINT
    upload = settings.targets_upload_max_bytes + MULTIPART_OVERHEAD_BYTES
    limits = (("/dashboard/targets/upload", upload), ("/regression/", regression))
    return tuple(sorted(limits, key=lambda item: -len(item[0])))
