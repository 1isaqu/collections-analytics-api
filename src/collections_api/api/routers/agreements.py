"""Agreement lists, the agent performance table and the portfolio label list."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request

from collections_api.api.common import (
    ERROR_RESPONSES,
    AgentNameParam,
    DateFromParam,
    DateToParam,
    LineParam,
    TeamParam,
    apply_team,
    cached_query,
    ok_response,
    respond,
)
from collections_api.api.dependencies import Ctx
from collections_api.api.models import (
    AgreementTableRow,
    AgreementTodayRow,
    PerformanceRow,
    PortfolioLabelRow,
)
from collections_api.core.pagination import normalize_pagination, split_total
from collections_api.core.sql import ALL_LINES, Period
from collections_api.domains.agreements import queries
from collections_api.domains.charts.queries import portfolio_labels

router = APIRouter(prefix="/dashboard", tags=["agreements"], responses=ERROR_RESPONSES)


@router.get(
    "/agreements-today/{line}",
    operation_id="agreements_today",
    summary="Installments of today's approved agreements",
    response_model=None,
    responses=ok_response(AgreementTodayRow),
)
def agreements_today(
    ctx: Ctx,
    request: Request,
    line: LineParam,
    limit: Annotated[
        int | None,
        Query(
            ge=1, description="Page size. Values above `AGREEMENTS_MAX_LIMIT` are lowered to it."
        ),
    ] = None,
    offset: Annotated[int | None, Query(ge=0, description="Rows to skip.")] = None,
):
    """One row per installment of every approved agreement issued today.

    With `all`, each line is paged with the same window and the rows are concatenated;
    `meta.pagination.totals_by_line` has the per-line totals.
    """
    line = ctx.cfg.resolve_line(line)
    settings = ctx.settings
    page_size, skip = normalize_pagination(
        limit,
        offset,
        default_limit=settings.agreements_default_limit,
        max_limit=settings.agreements_max_limit,
    )
    day = ctx.today()
    rows: list[dict] = []
    totals: dict[str, int] = {}
    for name, database in ctx.cfg.scope(line):
        query = queries.agreements_today(ctx.cfg, database, day, skip, page_size)
        key = f"agreements-today|{name}|{day}|{skip}|{page_size}"
        raw = cached_query(ctx, request, key, query, name, "dashboard/agreements-today")
        page, totals[name] = split_total(raw)
        rows.extend({"product_line": name, **row} for row in page)
    pagination = {
        "limit": page_size,
        "offset": skip,
        "total": sum(totals.values()),
        "returned": len(rows),
    }
    if line == ALL_LINES:
        pagination["totals_by_line"] = totals
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters={"line": line, "period": "today"},
        pagination=pagination,
    )


@router.get(
    "/agreements-table/{line}",
    operation_id="agreements_table",
    summary="Today's agreements, one row each",
    response_model=None,
    responses=ok_response(AgreementTableRow),
)
def agreements_table(
    ctx: Ctx,
    request: Request,
    line: LineParam,
    agent_name: AgentNameParam = None,
    team: TeamParam = None,
):
    """Agreements issued today with their first-installment and total values.

    Exceptions (agreements pending approval) come last.
    """
    line = ctx.cfg.resolve_line(line)
    agent = (agent_name or "").strip() or None
    day = ctx.today()
    query, team_label = queries.agreements_table(ctx.cfg, line, day, team, agent)
    _, token = apply_team(team)
    key = f"agreements-table|{line}|{day}|{token}|{agent}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/agreements-table")
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters={"line": line, "period": "today", "team": team_label, "agent_name": agent},
    )


@router.get(
    "/performance-table/{line}",
    operation_id="performance_table",
    summary="Agent performance over a period",
    response_model=None,
    responses=ok_response(PerformanceRow),
)
def performance_table(
    ctx: Ctx,
    request: Request,
    line: LineParam,
    date_from: DateFromParam = None,
    date_to: DateToParam = None,
    agent_name: AgentNameParam = None,
):
    """Effort, results and slip payment per agent.

    Without dates the window is the last `PERFORMANCE_DEFAULT_DAYS` days up to and
    including today. With `all`, counts are summed per agent and the ratios are
    recomputed from the sums.
    """
    line = ctx.cfg.resolve_line(line)
    today = ctx.today()
    if date_from is None and date_to is None:
        days = ctx.settings.performance_default_days
        period = Period.between(today - timedelta(days=days - 1), today)
    else:
        period = Period.from_query(date_from, date_to, today)
    agent = (agent_name or "").strip() or None
    query = queries.performance_table(ctx.cfg, line, period, today, agent)
    key = f"performance-table|{line}|{period.label}|{today}|{agent}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/performance-table")
    return respond(
        ctx,
        request,
        rows,
        line=line,
        filters={"line": line, "period": period.label, "agent_name": agent},
    )


@router.get(
    "/portfolios/{line}",
    operation_id="portfolio_labels",
    summary="Portfolio labels",
    response_model=None,
    responses=ok_response(PortfolioLabelRow),
)
def portfolio_label_list(ctx: Ctx, request: Request, line: LineParam):
    """Every distinct, non-blank portfolio label, for filter pickers."""
    line = ctx.cfg.resolve_line(line)
    rows = cached_query(
        ctx,
        request,
        f"portfolios|{line}",
        portfolio_labels(ctx.cfg, line),
        line,
        "dashboard/portfolios",
    )
    return respond(ctx, request, rows, line=line, filters={"line": line})
