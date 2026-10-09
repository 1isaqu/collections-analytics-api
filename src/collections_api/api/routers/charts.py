"""Chart and card aggregates.

Most routes here share one of three shapes (by portfolio, by agent, a single card), so
they are registered from tables with one small factory per shape rather than written
out by hand. Every route has its own ``operation_id`` and response model.
"""

from fastapi import APIRouter, Request

from collections_api.api.common import (
    ERROR_RESPONSES,
    LineParam,
    TeamParam,
    Window,
    apply_team,
    cached_query,
    ok_response,
    respond,
    window_filters,
)
from collections_api.api.dependencies import Ctx
from collections_api.api.models import (
    AgreementsByPortfolioRow,
    BrokenByPortfolioRow,
    ExceptionsByAgentRow,
    ExceptionsByPortfolioRow,
    ExceptionWithoutPortfolioRow,
    FirstInstallmentByAgentRow,
    FirstInstallmentByPortfolioRow,
    FirstInstallmentSummaryRow,
    PortfolioRollupRow,
    ReceivedByPortfolioRow,
    RejectedByPortfolioRow,
)
from collections_api.domains.charts import queries as charts

router = APIRouter(prefix="/dashboard", tags=["charts"], responses=ERROR_RESPONSES)

PORTFOLIO_CHARTS = {
    "exceptions": (
        ExceptionsByPortfolioRow,
        "Exceptions by portfolio",
        "Agreements pending approval, counted by portfolio label, with their first installments.",
    ),
    "agreements": (
        AgreementsByPortfolioRow,
        "Generated agreements by portfolio",
        "Generated agreements (approved, broken and auto-broken), by portfolio label.",
    ),
    "rejected": (
        RejectedByPortfolioRow,
        "Rejected agreements by portfolio",
        "Agreements a supervisor or the creditor rejected, by portfolio label.",
    ),
    "broken": (
        BrokenByPortfolioRow,
        "Broken agreements by portfolio",
        "Agreements broken by hand, by portfolio label.",
    ),
    "first-installment": (
        FirstInstallmentByPortfolioRow,
        "First installments by portfolio",
        "First-installment value of generated agreements, by portfolio label, largest first.",
    ),
}

AGENT_CHARTS = {
    "exceptions": (
        ExceptionsByAgentRow,
        "Exceptions by agent",
        "Agreements pending approval, counted per agent.",
    ),
    "first-installment": (
        FirstInstallmentByAgentRow,
        "First installments by agent",
        "First-installment value of generated agreements per agent, largest first.",
    ),
}

WINDOW_NOTE = (
    "\n\nOnly first installments (`installment_no = 0`) of the latest write of each "
    "installment are counted, so an agreement is one row however many installments it has. "
    "Excluded agents are filtered in SQL. With `all`, per-line results are summed per group."
)


def _register_portfolio_chart(kind: str) -> None:
    model, summary, description = PORTFOLIO_CHARTS[kind]

    def endpoint(ctx: Ctx, request: Request, line: LineParam, window: Window):
        line = ctx.cfg.resolve_line(line)
        query = charts.by_portfolio(ctx.cfg, line, window, kind)
        key = f"chart|{kind}-by-portfolio|{line}|{window.label}"
        rows = cached_query(ctx, request, key, query, line, f"dashboard/{kind}-by-portfolio")
        return respond(ctx, request, rows, line=line, filters=window_filters(line, window))

    router.add_api_route(
        f"/{kind}-by-portfolio/{{line}}",
        endpoint,
        methods=["GET"],
        name=f"{kind.replace('-', '_')}_by_portfolio",
        operation_id=f"{kind.replace('-', '_')}_by_portfolio",
        summary=summary,
        description=description + WINDOW_NOTE,
        response_model=None,
        responses=ok_response(model),
    )


def _register_agent_chart(kind: str) -> None:
    model, summary, description = AGENT_CHARTS[kind]

    def endpoint(
        ctx: Ctx, request: Request, line: LineParam, window: Window, team: TeamParam = None
    ):
        line = ctx.cfg.resolve_line(line)
        query, label = charts.by_agent(ctx.cfg, line, window, kind, team)
        _, token = apply_team(team)
        key = f"chart|{kind}-by-agent|{line}|{window.label}|{token}"
        rows = cached_query(ctx, request, key, query, line, f"dashboard/{kind}-by-agent")
        return respond(
            ctx, request, rows, line=line, filters=window_filters(line, window, team=label)
        )

    router.add_api_route(
        f"/{kind}-by-agent/{{line}}",
        endpoint,
        methods=["GET"],
        name=f"{kind.replace('-', '_')}_by_agent",
        operation_id=f"{kind.replace('-', '_')}_by_agent",
        summary=summary,
        description=description + WINDOW_NOTE,
        response_model=None,
        responses=ok_response(model),
    )


for _kind in PORTFOLIO_CHARTS:
    _register_portfolio_chart(_kind)
for _kind in AGENT_CHARTS:
    _register_agent_chart(_kind)


@router.get(
    "/first-installment-summary/{line}",
    operation_id="first_installment_summary",
    summary="First-installment card",
    response_model=None,
    responses=ok_response(FirstInstallmentSummaryRow),
)
def first_installment_summary(
    ctx: Ctx, request: Request, line: LineParam, window: Window, team: TeamParam = None
):
    """Total first-installment value and agreement count of generated agreements.

    One row. Both fields are `null` when nothing matched.
    """
    line = ctx.cfg.resolve_line(line)
    query, label = charts.first_installment_summary(ctx.cfg, line, window, team)
    _, token = apply_team(team)
    key = f"chart|first-installment-summary|{line}|{window.label}|{token}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/first-installment-summary")
    return respond(ctx, request, rows, line=line, filters=window_filters(line, window, team=label))


@router.get(
    "/portfolio-rollup/{line}",
    operation_id="portfolio_rollup",
    summary="Portfolio by status rollup",
    response_model=None,
    responses=ok_response(PortfolioRollupRow),
)
def portfolio_rollup(ctx: Ctx, request: Request, line: LineParam, window: Window):
    """One scan grouped by portfolio label and status id.

    A client can rebuild the by-portfolio charts by slicing on `status_id`
    (see `docs/configuration.md` for the status sets). `agreement_count` is exact per
    status; adding it across statuses can count one agreement twice.
    """
    line = ctx.cfg.resolve_line(line)
    query = charts.portfolio_rollup(ctx.cfg, line, window)
    key = f"chart|portfolio-rollup|{line}|{window.label}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/portfolio-rollup")
    return respond(ctx, request, rows, line=line, filters=window_filters(line, window))


@router.get(
    "/received-by-portfolio/{line}",
    operation_id="received_by_portfolio",
    summary="Cash received by portfolio",
    response_model=None,
    responses=ok_response(ReceivedByPortfolioRow),
)
def received_by_portfolio(ctx: Ctx, request: Request, line: LineParam, window: Window):
    """Cash paid inside the window, by payment date, per portfolio.

    This is the "actual" side of a target-versus-actual view. The window applies to
    `paid_at`, not to the date the agreement was issued.
    """
    line = ctx.cfg.resolve_line(line)
    query = charts.received_by_portfolio(ctx.cfg, line, window)
    key = f"chart|received-by-portfolio|{line}|{window.label}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/received-by-portfolio")
    return respond(ctx, request, rows, line=line, filters=window_filters(line, window))


@router.get(
    "/exceptions-without-portfolio/{line}",
    operation_id="exceptions_without_portfolio",
    summary="Exceptions with no portfolio label",
    response_model=None,
    responses=ok_response(ExceptionWithoutPortfolioRow),
)
def exceptions_without_portfolio(ctx: Ctx, request: Request, line: LineParam, window: Window):
    """Exception agreements that none of the by-portfolio charts can show.

    Lists the agreements whose debts carry no portfolio label, so the by-portfolio
    totals can be reconciled with the all-agreements total.
    """
    line = ctx.cfg.resolve_line(line)
    query = charts.exceptions_without_portfolio(ctx.cfg, line, window)
    key = f"chart|exceptions-without-portfolio|{line}|{window.label}"
    rows = cached_query(ctx, request, key, query, line, "dashboard/exceptions-without-portfolio")
    return respond(ctx, request, rows, line=line, filters=window_filters(line, window))
