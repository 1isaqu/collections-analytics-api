"""Serve stored effectiveness series with an honest staleness flag."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from collections_api.core.envelope import build_envelope
from collections_api.core.errors import ApiError
from collections_api.core.sql import QueryConfig
from collections_api.domains.effectiveness.etl import EffectivenessStore


def serve_series(
    store: EffectivenessStore, cfg: QueryConfig, series: str, line: str
) -> dict[str, Any]:
    """Envelope for one series. 503 until the first refresh has produced it.

    ``quality.stale`` is true when the last refresh attempt of this very series failed:
    the rows are then older than ``quality.fetched_at`` suggests they should be.
    """
    state = store.state(series, line)
    if state.rows is None:
        raise ApiError(503, "The effectiveness data is not loaded yet. Try again in a moment.")
    sources = [name for name, _ in cfg.scope(line)]
    fetched = (
        datetime.fromtimestamp(state.fetched_at, tz=timezone.utc).isoformat()
        if state.fetched_at
        else None
    )
    errors = (
        [{"source": f"{series}:{line}", "message": f"The last refresh failed: {state.error}"}]
        if state.error
        else None
    )
    return build_envelope(
        state.rows,
        sources,
        errors=errors,
        filters={"series": series, "line": line, "from": cfg.effectiveness_floor.isoformat()},
        quality={"fetched_at": fetched, "stale": bool(state.error)},
    )
