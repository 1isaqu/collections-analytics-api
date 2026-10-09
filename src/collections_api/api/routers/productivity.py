"""Per-agent productivity, load status, benchmarks and consolidated effort."""

import time
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from collections_api.api.common import (
    ERROR_RESPONSES,
    LineParam,
    SingleLineParam,
    TeamParam,
    Window,
    apply_team,
    cached_query,
    ok_response,
    respond,
    run_query,
    window_filters,
)
from collections_api.api.dependencies import Ctx
from collections_api.api.models import (
    AgentEffortRow,
    AgentPerformanceRow,
    BenchmarkData,
    LoadStatusRow,
    ProductivityRow,
)
from collections_api.core.errors import ApiError
from collections_api.core.sql import Period
from collections_api.domains.productivity import queries
from collections_api.domains.productivity.benchmarks import summarize_benchmarks
from collections_api.domains.productivity.validation import validate_productivity_rows

router = APIRouter(prefix="/dashboard", tags=["productivity"], responses=ERROR_RESPONSES)

RATIOS_NEEDING_UNFILTERED_EFFORT = ("cpc_rate_pct", "agreements_per_attempt_pct")


def _matches_team(row: dict[str, Any], token: str) -> bool:
    return (
        token in str(row.get("agent_login", "")).upper()
        or token in str(row.get("agent_name", "")).upper()
    )


@router.get(
    "/productivity/{line}",
    operation_id="productivity",
    summary="Per-agent productivity for a period",
    response_model=None,
    responses=ok_response(ProductivityRow),
)
def productivity(
    ctx: Ctx,
    request: Request,
    line: SingleLineParam,
    window: Window,
    team: TeamParam = None,
    portfolio: Annotated[
        str | None,
        Query(
            description=(
                "Restrict agreement counts and values to this portfolio label. Outreach effort "
                "cannot be attributed to a portfolio, so the ratios built on it are `null`."
            )
        ),
    ] = None,
):
    """Effort, agreements, exceptions and rejections per agent, for one product line.

    The window is `date_from`/`date_to`, or today when neither is given. Effort counts
    distinct debt owners over the whole window. `meta.quality` reports how many numeric
    values could not be read and were replaced by zero.
    """
    line = ctx.cfg.resolve_line(line, allow_all=False)
    started = time.perf_counter()
    label, token = apply_team(team)
    portfolio = (portfolio or "").strip() or None
    today = ctx.today()
    query = queries.productivity_query(ctx.cfg, line, window, today, portfolio)
    key = f"productivity|{line}|{window.label}|{today}|{portfolio or 'all'}"

    def compute() -> tuple[list[dict[str, Any]], dict[str, int]]:
        raw = run_query(ctx, request, query, line, "dashboard/productivity")
        return validate_productivity_rows(raw)

    rows, metrics = ctx.cache.get_or_compute(key, compute)
    if token:
        rows = [row for row in rows if _matches_team(row, token)]
    if portfolio:
        rows = [{**row, **dict.fromkeys(RATIOS_NEEDING_UNFILTERED_EFFORT)} for row in rows]
    quality = {
        **metrics,
        "portfolio_partial": bool(portfolio),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters=window_filters(line, window, team=label, portfolio=portfolio),
        quality=quality,
    )


@router.get(
    "/load-status/{line}",
    operation_id="load_status",
    summary="Did today's data load?",
    response_model=None,
    responses=ok_response(LoadStatusRow),
)
def load_status(ctx: Ctx, request: Request, line: LineParam, team: TeamParam = None):
    """Totals of today's productivity rows per line, to confirm the load before reading KPIs.

    `meta.quality.status` is `ok`, or `partial` when a line failed or a value could not
    be read as a number. The route fails (500) only when no line produced anything.
    """
    line = ctx.cfg.resolve_line(line)
    label, token = apply_team(team)
    period = Period.day(ctx.today())
    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    cast_failures = 0
    scope = ctx.cfg.scope(line)
    for name, _ in scope:
        try:
            query = queries.productivity_query(ctx.cfg, name, period, ctx.today())
            raw = run_query(ctx, request, query, name, "dashboard/load-status")
            rows, metrics = validate_productivity_rows(raw)
        except ApiError as exc:
            errors.append({"source": name, "message": exc.detail})
            continue
        cast_failures += metrics["numeric_cast_failures"]
        if token:
            rows = [row for row in rows if _matches_team(row, token)]
        summaries.append(
            {
                "product_line": name,
                "agent_count": len(rows),
                "attempt_count": int(sum(r["attempt_count"] for r in rows)),
                "cpc_count": int(sum(r["cpc_count"] for r in rows)),
                "agreement_count": int(sum(r["agreement_count"] for r in rows)),
                "agreement_value": round(sum(r["agreement_value"] for r in rows), 2),
                "exception_count": int(sum(r["exception_count"] for r in rows)),
                "exception_value": round(sum(r["exception_value"] for r in rows), 2),
            }
        )
    if not summaries:
        raise ApiError(500, errors[0]["message"] if errors else "No product line is available.")
    if len(summaries) > 1:
        total = {
            key: sum(row[key] for row in summaries) for key in summaries[0] if key != "product_line"
        }
        total["agreement_value"] = round(total["agreement_value"], 2)
        total["exception_value"] = round(total["exception_value"], 2)
        summaries.append({"product_line": "all", **total})
    if cast_failures:
        errors.append(
            {
                "source": line,
                "message": f"{cast_failures} numeric value(s) could not be read and became zero.",
            }
        )
    quality = {
        "status": "ok" if not errors else "partial",
        "lines_requested": len(scope),
        "lines_ok": sum(1 for row in summaries if row["product_line"] != "all"),
        "numeric_cast_failures": cast_failures,
    }
    return respond(
        ctx,
        request,
        summaries,
        line=line,
        errors=errors,
        quality=quality,
        filters={"line": line, "period": "today", "team": label},
    )


@router.get(
    "/agent-performance/{line}",
    operation_id="agent_performance",
    summary="Agent comparison",
    response_model=None,
    responses=ok_response(AgentPerformanceRow),
)
def agent_performance(ctx: Ctx, request: Request, line: LineParam, window: Window):
    """One row per agent and product line, with effort, results and slip effectiveness.

    The same login in two lines gives two rows; clients that want one row per person
    group on `agent_login`. Effort here counts distinct (day, debt owner) pairs, so a
    long window counts a debtor once for every day they were contacted.
    """
    line = ctx.cfg.resolve_line(line)
    today = ctx.today()
    query = queries.agent_performance_query(ctx.cfg, line, window, today)
    key = f"agent-performance|{line}|{window.label}|{today}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/agent-performance")
    return respond(ctx, request, rows, line=line, filters=window_filters(line, window))


@router.get(
    "/agent-effort",
    operation_id="agent_effort",
    summary="Today's effort, one row per person",
    response_model=None,
    responses=ok_response(AgentEffortRow),
)
def agent_effort(
    ctx: Ctx,
    request: Request,
    force_refresh: Annotated[
        bool, Query(description="Skip the cached payload and query every line again.")
    ] = False,
):
    """Outreach effort of today summed over every product line, with the per-line split.

    Lines are queried in parallel and the payload is cached for
    `AGENT_EFFORT_CACHE_TTL_SECONDS`. `meta.quality.cache_age_seconds` is 0 for a fresh
    computation.
    """
    agents, age = ctx.agent_effort.fetch(force_refresh=force_refresh)
    return respond(
        ctx,
        request,
        agents,
        sources=list(ctx.cfg.line_names),
        filters={"line": "all", "period": "today", "force_refresh": force_refresh},
        quality={"cache_age_seconds": age},
    )


@router.get(
    "/benchmarks/{line}",
    operation_id="benchmarks",
    summary="Internal benchmarks",
    response_model=None,
    responses=ok_response(BenchmarkData, many=False),
)
def benchmarks(
    ctx: Ctx,
    request: Request,
    line: SingleLineParam,
    lookback_months: Annotated[
        int, Query(ge=1, le=12, description="How many months before today to look at.")
    ] = 3,
):
    """Quartiles of per-agent averages over a look-back window.

    Agent-days with fewer than `BENCHMARK_MIN_DAILY_ATTEMPTS` attempts, and agents with
    fewer than `BENCHMARK_MIN_ACTIVE_DAYS` qualifying days, are left out so that tiny
    samples cannot move a quartile. The scan is wide, so the result is cached for
    `BENCHMARKS_CACHE_TTL_SECONDS`.
    """
    line = ctx.cfg.resolve_line(line, allow_all=False)
    today = ctx.today()
    query = queries.benchmark_query(ctx.cfg, line, today, lookback_months)
    key = f"benchmarks|{line}|{today}|{lookback_months}"
    rows = ctx.benchmarks_cache.get_or_compute(
        key, lambda: run_query(ctx, request, query, line, "dashboard/benchmarks")
    )
    return respond(
        ctx,
        request,
        summarize_benchmarks(rows, lookback_months),
        line=line,
        filters={"line": line, "lookback_months": lookback_months},
    )
