"""Quartile summary of the per-agent benchmark rows."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

# Benchmark metric -> column of ``benchmark_query`` it summarizes.
METRICS = ("cpc_rate_pct", "conversion_pct", "cash_effectiveness_pct", "exception_pct")
EMPTY = {"q1": None, "median": None, "q3": None, "top10_mean": None, "mean": None}


def _is_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def quartiles(values: Sequence[Any]) -> dict[str, float | None]:
    """First quartile, median, third quartile, mean, and the mean of the top 10%.

    Missing values (``None``, NaN) are ignored. The top group has at least one member,
    so a population of fewer than ten agents still gets a ``top10_mean``.
    """
    numbers = np.array([float(v) for v in values if _is_number(v)], dtype=np.float64)
    if numbers.size == 0:
        return dict(EMPTY)
    top_size = max(1, numbers.size // 10)
    top = np.sort(numbers)[-top_size:]
    return {
        "q1": round(float(np.percentile(numbers, 25)), 2),
        "median": round(float(np.percentile(numbers, 50)), 2),
        "q3": round(float(np.percentile(numbers, 75)), 2),
        "top10_mean": round(float(np.mean(top)), 2),
        "mean": round(float(np.mean(numbers)), 2),
    }


def summarize_benchmarks(rows: Sequence[Mapping[str, Any]], lookback_months: int) -> dict[str, Any]:
    """The payload of the benchmarks route."""
    missing = [c for c in METRICS if rows and c not in rows[0]]
    if missing:
        raise KeyError(f"benchmark rows lack: {', '.join(missing)}")
    summary: dict[str, Any] = {
        metric: quartiles([r.get(metric) for r in rows]) for metric in METRICS
    }
    summary["agent_count"] = len(rows)
    summary["lookback_months"] = lookback_months
    return summary
