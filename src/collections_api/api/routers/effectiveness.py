"""Payment-slip effectiveness: are the slips issued for agreements being paid in time?

The nine series are computed in the background (``EffectivenessStore``) and served from
memory. The summary, the slip list, the break curve and the portfolio views run their
own queries on request.
"""

from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request

from collections_api.api.common import (
    ERROR_RESPONSES,
    LINE_DESCRIPTION,
    cached_query,
    ok_response,
    respond,
    run_query,
)
from collections_api.api.dependencies import Ctx
from collections_api.api.models import (
    BreakCurveRow,
    EffectivenessSummaryData,
    GenerationRankingRow,
    PortfolioEffectivenessRow,
    PortfolioEffectivenessSummaryRow,
    SeriesByAgentMonthBrokenRow,
    SeriesByAgentMonthRow,
    SeriesByDueDayRow,
    SeriesByIssueDayRow,
    SeriesByMonthRow,
    SlipDetailRow,
)
from collections_api.core.sql import Period
from collections_api.domains.effectiveness import queries
from collections_api.domains.effectiveness.service import serve_series

router = APIRouter(prefix="/effectiveness", tags=["effectiveness"], responses=ERROR_RESPONSES)

LineQuery = Annotated[str, Query(description=LINE_DESCRIPTION)]
InstallmentType = Annotated[
    Literal["first", "later"],
    Query(description="`first` is the down payment (installment 0). `later` is every other one."),
]
DetailKind = Literal["to-mature", "in-grace", "overdue-unpaid", "broken", "paid-in-grace"]
DueFrom = Annotated[date, Query(description="First due date to include (`YYYY-MM-DD`).")]
DueTo = Annotated[date, Query(description="Last due date to include (`YYYY-MM-DD`).")]

SERIES_MODELS = {
    "daily-first": SeriesByIssueDayRow,
    "monthly-first": SeriesByMonthRow,
    "daily-later": SeriesByIssueDayRow,
    "monthly-later": SeriesByMonthRow,
    "daily-later-by-due": SeriesByDueDayRow,
    "monthly-later-by-due": SeriesByMonthRow,
    "monthly-agent-first": SeriesByAgentMonthRow,
    "monthly-agent-later": SeriesByAgentMonthRow,
    "monthly-agent-later-by-due": SeriesByAgentMonthBrokenRow,
}

SERIES_NOTE = (
    "\n\nA slip is one installment row. It counts as paid in grace when a positive amount was "
    "paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the "
    "background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start "
    "can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the "
    "rows were read and `meta.quality.stale` is true when the last refresh of this series failed."
)


def _register_series(key: str) -> None:
    spec = queries.SERIES[key]

    def endpoint(ctx: Ctx, request: Request, line: LineQuery = "all"):
        line = ctx.cfg.resolve_line(line)
        envelope = serve_series(ctx.effectiveness, ctx.cfg, key, line)
        envelope["meta"]["run_id"] = getattr(request.state, "run_id", None)
        return envelope

    router.add_api_route(
        f"/{key}",
        endpoint,
        methods=["GET"],
        name=f"effectiveness_{key.replace('-', '_')}",
        operation_id=f"effectiveness_{key.replace('-', '_')}",
        summary=spec.label,
        description=spec.label + "." + SERIES_NOTE,
        response_model=None,
        responses={
            **ok_response(SERIES_MODELS[key]),
            503: ERROR_RESPONSES[500] | {"description": "The first refresh has not finished."},
        },
    )


for _key in queries.SERIES:
    _register_series(_key)


def _num(value: Any) -> Any:
    return 0 if value is None else value


def normalize_kpis(row: dict[str, Any] | None) -> dict[str, Any]:
    """Counts and amounts default to zero; percentages stay ``null`` without a denominator."""
    row = row or {}
    return {
        column: row.get(column) if column.endswith("_pct") else _num(row.get(column))
        for column in queries.SUMMARY_COLUMNS
    }


def pick_days(daily: list[dict[str, Any]]) -> tuple[dict | None, dict | None]:
    """Best and worst day by effectiveness, among days that had slips and a defined ratio."""
    usable = [d for d in daily if d.get("slips_total") and d.get("effectiveness_pct") is not None]
    if not usable:
        return None, None
    key = lambda day: day["effectiveness_pct"]  # noqa: E731
    return max(usable, key=key), min(usable, key=key)


@router.get(
    "/summary",
    operation_id="effectiveness_summary",
    summary="Effectiveness of slips due in a period",
    response_model=None,
    responses=ok_response(EffectivenessSummaryData, many=False),
)
def summary(
    ctx: Ctx,
    request: Request,
    date_from: DueFrom,
    date_to: DueTo,
    line: LineQuery = "all",
    installment_type: InstallmentType = "first",
    portfolio_id: Annotated[
        int | None, Query(description="Only slips of this portfolio id.")
    ] = None,
):
    """KPI block, one point per due day, and the best and worst day.

    The buckets partition the slips: to mature (due today or later, unpaid), in grace
    (overdue, grace still running), overdue and unpaid (grace over) and paid in grace. A
    slip paid after its grace period counts as overdue and unpaid: the figure measures
    on-time payment, not eventual payment. Uses the effectiveness agent exclusion list.
    """
    line = ctx.cfg.resolve_line(line)
    period = Period.between(date_from, date_to)
    today = ctx.today()
    kpi_query, daily_query = queries.summary_queries(
        ctx.cfg, line, period, today, installment_type, portfolio_id
    )
    key = f"effectiveness|summary|{line}|{period.label}|{today}|{installment_type}|{portfolio_id}"

    def compute() -> tuple[list[dict], list[dict]]:
        kpi = run_query(ctx, request, kpi_query, line, "effectiveness/summary-kpi")
        daily = run_query(ctx, request, daily_query, line, "effectiveness/summary-daily")
        return kpi, daily

    kpi_rows, daily = ctx.cache.get_or_compute(key, compute)
    best, worst = pick_days(daily)
    data = {
        "kpis": normalize_kpis(kpi_rows[0] if kpi_rows else None),
        "daily": daily,
        "best_day": best,
        "worst_day": worst,
    }
    return respond(
        ctx,
        request,
        data,
        line=line,
        filters={
            "line": line,
            "period": period.label,
            "installment_type": installment_type,
            "portfolio_id": portfolio_id,
        },
    )


@router.get(
    "/slips",
    operation_id="effectiveness_slips",
    summary="The slips behind one KPI bucket",
    response_model=None,
    responses=ok_response(SlipDetailRow),
)
def slips(
    ctx: Ctx,
    request: Request,
    kind: Annotated[DetailKind, Query(description="Which KPI bucket to list.")],
    date_from: DueFrom,
    date_to: DueTo,
    line: LineQuery = "all",
    installment_type: InstallmentType = "first",
):
    """Slips due in the period that fall in one bucket, largest first.

    The list is capped at `EFFECTIVENESS_DETAIL_ROW_LIMIT` rows. When the list reaches the
    cap, `meta.quality.truncated` and `meta.pagination.truncated` are true and rows may be
    missing. A list with exactly that many rows is flagged too, because the query cannot
    tell it from a cut one, so read the flag as "may be truncated".
    """
    line = ctx.cfg.resolve_line(line)
    period = Period.between(date_from, date_to)
    today = ctx.today()
    query = queries.detail_query(ctx.cfg, line, period, today, installment_type, kind)
    key = f"effectiveness|slips|{line}|{period.label}|{today}|{installment_type}|{kind}"
    rows = cached_query(ctx, request, key, query, line, f"effectiveness/slips-{kind}")
    limit = ctx.cfg.effectiveness_detail_limit
    truncated = len(rows) >= limit
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters={
            "line": line,
            "period": period.label,
            "installment_type": installment_type,
            "kind": kind,
        },
        quality={"truncated": truncated},
        pagination={"limit": limit, "truncated": truncated},
    )


@router.get(
    "/break-curve",
    operation_id="effectiveness_break_curve",
    summary="Break rate by age of the slip",
    response_model=None,
    responses=ok_response(BreakCurveRow),
)
def break_curve(
    ctx: Ctx, request: Request, date_from: DueFrom, date_to: DueTo, line: LineQuery = "all"
):
    """Share of first-installment slips already broken, by days since the due date.

    Bands come from `BREAK_CURVE_EDGES`. Age runs from the due date to today; the date a
    break was recorded is not used, because batch processes can pile recorded breaks onto
    a few days.
    """
    line = ctx.cfg.resolve_line(line)
    period = Period.between(date_from, date_to)
    today = ctx.today()
    query = queries.break_curve_query(ctx.cfg, line, period, today)
    key = f"effectiveness|break-curve|{line}|{period.label}|{today}"
    rows = cached_query(ctx, request, key, query, line, "effectiveness/break-curve")
    return respond(ctx, request, rows, line=line, filters={"line": line, "period": period.label})


@router.get(
    "/portfolio-summary",
    operation_id="effectiveness_portfolio_summary",
    summary="Effectiveness of one portfolio",
    response_model=None,
    responses=ok_response(PortfolioEffectivenessSummaryRow),
)
def portfolio_summary(
    ctx: Ctx,
    request: Request,
    portfolio: Annotated[str, Query(description="Exact portfolio label.")],
    date_from: DueFrom,
    date_to: DueTo,
    line: LineQuery = "all",
    installment_type: InstallmentType = "first",
):
    """Slip counts and cash for the slips due in the period whose debts carry the label."""
    line = ctx.cfg.resolve_line(line)
    period = Period.between(date_from, date_to)
    query = queries.portfolio_summary_query(ctx.cfg, line, period, installment_type, portfolio)
    key = (
        f"effectiveness|portfolio-summary|{line}|{period.label}|{ctx.today()}"
        f"|{installment_type}|{portfolio}"
    )
    rows = cached_query(ctx, request, key, query, line, "effectiveness/portfolio-summary")
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters={
            "line": line,
            "period": period.label,
            "installment_type": installment_type,
            "portfolio": portfolio,
        },
    )


@router.get(
    "/portfolio-ranking",
    operation_id="effectiveness_portfolio_ranking",
    summary="Effectiveness of every portfolio",
    response_model=None,
    responses=ok_response(PortfolioEffectivenessRow),
)
def portfolio_ranking(
    ctx: Ctx,
    request: Request,
    date_from: DueFrom,
    date_to: DueTo,
    line: LineQuery = "all",
    installment_type: InstallmentType = "first",
):
    """One call for every portfolio label, largest maturing amount first."""
    line = ctx.cfg.resolve_line(line)
    period = Period.between(date_from, date_to)
    query = queries.portfolio_ranking_query(ctx.cfg, line, period, installment_type)
    key = f"effectiveness|portfolio-ranking|{line}|{period.label}|{ctx.today()}|{installment_type}"
    rows = cached_query(ctx, request, key, query, line, "effectiveness/portfolio-ranking")
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters={"line": line, "period": period.label, "installment_type": installment_type},
    )


@router.get(
    "/generation-ranking",
    operation_id="effectiveness_generation_ranking",
    summary="Agreements generated per portfolio",
    response_model=None,
    responses=ok_response(GenerationRankingRow),
)
def generation_ranking(
    ctx: Ctx,
    request: Request,
    date_from: Annotated[date, Query(description="First issue date to include (`YYYY-MM-DD`).")],
    date_to: Annotated[date, Query(description="Last issue date to include (`YYYY-MM-DD`).")],
    line: LineQuery = "all",
):
    """Generated agreements per portfolio by issue date, largest value first.

    Unlike the slip views this window is on the issue date: a generated agreement counts
    whatever happens to it afterwards. It uses the standard agent exclusion list.
    """
    line = ctx.cfg.resolve_line(line)
    period = Period.between(date_from, date_to)
    query = queries.generation_ranking_query(ctx.cfg, line, period)
    key = f"effectiveness|generation-ranking|{line}|{period.label}"
    rows = cached_query(ctx, request, key, query, line, "effectiveness/generation-ranking")
    return respond(ctx, request, rows, line=line, filters={"line": line, "period": period.label})
