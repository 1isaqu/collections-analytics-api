"""Request-level helpers: the app context, authentication and request ids."""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Request

from collections_api.api.context import AppContext
from collections_api.core.errors import ApiError
from collections_api.settings import Settings

API_PREFIX = "/api"
# Prefixes that need authentication when it is switched on, and that are rate limited.
# Routers passed to ``create_app(extra_routers=...)`` add their own first path segment.
BASE_PROTECTED_PREFIXES = ("/dashboard/", "/effectiveness/", "/regression/", "/admin/", "/health/")
BASE_RATE_LIMITED_PREFIXES = ("/dashboard/", "/effectiveness/", "/regression/", "/admin/")
# Liveness never touches the database and leaks nothing, so monitors can call it freely.
OPEN_PATHS = frozenset({"/health/live"})

_RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RUN_ID_HEADERS = ("x-run-id", "x-request-id")


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


Ctx = Annotated[AppContext, Depends(get_context)]


def normalize_api_path(path: str) -> str:
    """Drop the optional ``/api`` prefix, so the SPA can be served from the same origin."""
    if path == API_PREFIX:
        return "/"
    if path.startswith(API_PREFIX + "/"):
        return path[len(API_PREFIX) :] or "/"
    return path


def extract_run_id(headers: Mapping[str, str]) -> str:
    """A client-supplied id if it is well formed, else a fresh one.

    The id is echoed in a response header and written to the event log, so anything
    that is not a short run of safe characters is replaced rather than reflected.
    """
    for name in _RUN_ID_HEADERS:
        supplied = (headers.get(name) or "").strip()
        if supplied and _RUN_ID.match(supplied):
            return supplied
    return f"srv-{uuid4().hex[:12]}"


def check_auth(settings: Settings, headers: Mapping[str, str]) -> None:
    """Raise 401 unless both the API key and the bearer token match.

    The comparison is constant-time. Authentication is off unless ``REQUIRE_API_AUTH``
    is set; ``Settings`` refuses to start with it on and a credential missing.
    """
    if not settings.require_api_auth:
        return
    key_ok = _same(headers.get("x-api-key", ""), settings.api_key)
    token_ok = _same(headers.get("authorization", ""), f"Bearer {settings.api_token}")
    if not (key_ok and token_ok):
        raise ApiError(401, "Unauthorized")


def _same(supplied: str, expected: str) -> bool:
    # Bytes, because compare_digest rejects a str with non-ASCII characters.
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def needs_auth(path: str, protected_prefixes: tuple[str, ...]) -> bool:
    return path not in OPEN_PATHS and path.startswith(protected_prefixes)


def is_rate_limited(path: str, limited_prefixes: tuple[str, ...]) -> bool:
    return path not in OPEN_PATHS and path.startswith(limited_prefixes)


def prefix_of(route_path: str) -> str:
    """``/pace/{line}`` -> ``/pace/``: the first path segment, as a prefix."""
    segment = route_path.strip("/").split("/", 1)[0]
    return f"/{segment}/" if segment else "/"
