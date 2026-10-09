"""Checks and coercion for productivity rows before they leave the service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from collections_api.core.errors import ApiError

REQUIRED_FIELDS = (
    "agent_login",
    "agent_name",
    "attempt_count",
    "answer_count",
    "cpc_count",
    "agreement_count",
    "agreement_value",
    "avg_ticket",
    "avg_installments",
    "first_installment_value",
    "exception_count",
    "exception_value",
)

# Counts and amounts: a missing value means zero.
_ZERO_IF_NULL = (
    "attempt_count",
    "answer_count",
    "cpc_count",
    "agreement_count",
    "agreement_count_by_contract",
    "slips_issued",
    "slips_paid",
    "agreement_value",
    "avg_ticket",
    "avg_installments",
    "avg_balance_ratio_pct",
    "first_installment_value",
    "first_installment_received",
    "exception_count",
    "exception_value",
    "exception_first_installment_value",
    "rejected_count",
    "rejected_value",
    "rejected_first_installment_value",
    "avg_debt_age_days",
    "hours_worked",
)
# Of those, the ones that count whole things. They leave the service as integers.
_COUNTS = (
    "attempt_count",
    "answer_count",
    "cpc_count",
    "agreement_count",
    "agreement_count_by_contract",
    "slips_issued",
    "slips_paid",
    "exception_count",
    "rejected_count",
)
# Ratios: a missing value means "undefined" (zero denominator) and stays null.
_RATIOS = ("cpc_rate_pct", "agreements_per_attempt_pct")


def validate_productivity_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Check required fields and coerce numerics.

    Returns ``(rows, metrics)``. A required field missing from a row is a 500: the query
    and the contract have drifted apart. A value that cannot be read as a number becomes
    zero and is counted in ``numeric_cast_failures``; callers surface that count, since a
    silent zero looks exactly like a real one.
    """
    cleaned: list[dict[str, Any]] = []
    cast_failures = 0
    for row in rows:
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ApiError(500, f"Productivity response is missing fields: {', '.join(missing)}")
        out = dict(row)
        for field in _ZERO_IF_NULL:
            if field not in out:
                continue
            value = out[field]
            if value is None:
                out[field] = 0
                continue
            try:
                number = float(value)
                out[field] = int(number) if field in _COUNTS else number
            except (TypeError, ValueError, OverflowError):
                cast_failures += 1
                out[field] = 0 if field in _COUNTS else 0.0
        for field in _RATIOS:
            value = out.get(field)
            if value is None:
                continue
            try:
                out[field] = float(value)
            except (TypeError, ValueError):
                cast_failures += 1
                out[field] = None
        out["agent_login"] = str(out.get("agent_login") or "")
        out["agent_name"] = str(out.get("agent_name") or "")
        cleaned.append(out)
    return cleaned, {"rows": len(cleaned), "numeric_cast_failures": cast_failures}
