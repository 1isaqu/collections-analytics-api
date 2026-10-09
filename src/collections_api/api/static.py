"""Optional single-page-app hosting next to the API.

Off unless ``FRONTEND_DIST_DIR`` points at a directory with an ``index.html``. The
fallback serves a file from that directory when it exists and ``index.html`` otherwise,
so client-side routes survive a page reload. API prefixes are never served from it: an
unknown API path stays a 404 instead of becoming an HTML page.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from collections_api.api.errors import error_response
from collections_api.api.middleware import ALIAS_FLAG
from collections_api.settings import Settings

SPA_RESERVED_PREFIXES = (
    "dashboard/",
    "effectiveness/",
    "regression/",
    "health/",
    "admin/",
    "api/",
    "docs",
    "redoc",
    "openapi.json",
)


def resolve_inside(root: str | Path, relative: str) -> Path | None:
    """``relative`` resolved under ``root``, or ``None`` if it would leave it.

    The server decodes the URL path but does not normalise it, so ``..`` segments arrive
    intact; an absolute component (``C:/x`` on Windows, ``/etc`` elsewhere) would make
    ``os.path.join`` discard the root. Both are refused, and so is a symlink that
    resolves outside the root.
    """
    if not relative or os.path.isabs(relative) or Path(relative).drive:
        return None
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, relative))
    try:
        if os.path.commonpath([base, target]) != base:
            return None
    except ValueError:  # different drives on Windows
        return None
    return Path(target)


def install_static_routes(app: FastAPI, settings: Settings) -> None:
    """Register ``/`` (service banner or the app) and the SPA fallback."""
    dist = settings.frontend_dist_dir
    index = Path(dist) / "index.html" if dist else None
    has_frontend = bool(index and index.is_file())

    @app.get("/", include_in_schema=False)
    def root():
        if has_frontend:
            return FileResponse(index)
        return {"status": "ok", "docs": "/docs" if settings.docs_enabled else None}

    if not has_frontend:
        return

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(request: Request, full_path: str):
        if request.scope.get(ALIAS_FLAG) or full_path.startswith(SPA_RESERVED_PREFIXES):
            return error_response(404, "Not Found", None)
        candidate = resolve_inside(dist, full_path)
        if candidate is not None and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)
