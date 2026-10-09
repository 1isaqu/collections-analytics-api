"""The response envelope every data route returns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


def build_envelope(
    rows: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    sources: Sequence[str],
    *,
    errors: Sequence[Mapping[str, str]] | None = None,
    filters: Mapping[str, Any] | None = None,
    run_id: str | None = None,
    quality: Mapping[str, Any] | None = None,
    pagination: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap payload rows in ``{"meta", "data", "errors"}``.

    ``rows`` is normally a list. Routes that return one object (a benchmark set, a
    summary) pass a mapping, which counts as a single row.
    """
    total = 1 if isinstance(rows, Mapping) else len(rows)
    meta: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_rows": total,
        "sources": list(sources),
        "filters": dict(filters or {}),
        "run_id": run_id,
        "quality": dict(quality or {}),
    }
    if pagination is not None:
        meta["pagination"] = dict(pagination)
    return {
        "meta": meta,
        "data": rows if isinstance(rows, Mapping) else list(rows),
        "errors": [dict(error) for error in (errors or [])],
    }
