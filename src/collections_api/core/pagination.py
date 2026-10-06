"""Limit/offset normalisation for the one paged route."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

TOTAL_COLUMN = "_total_rows"


def normalize_pagination(
    limit: int | None, offset: int | None, *, default_limit: int, max_limit: int
) -> tuple[int, int]:
    """Clamp a requested window into ``[1, max_limit]`` rows starting at ``offset >= 0``."""
    lim = default_limit if limit is None or limit <= 0 else min(int(limit), max_limit)
    off = 0 if offset is None or offset < 0 else int(offset)
    return lim, off


def split_total(rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Remove the ``COUNT(*) OVER()`` helper column and return it as the grand total."""
    if not rows:
        return [], 0
    total = int(rows[0].get(TOTAL_COLUMN) or 0)
    return [{k: v for k, v in row.items() if k != TOTAL_COLUMN} for row in rows], total
