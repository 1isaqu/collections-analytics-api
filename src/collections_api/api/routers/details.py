"""Agreement lists behind the charts, one row per agreement.

The three shapes (one portfolio, every portfolio, one agent) are registered for each
status kind from a table. Path parameters that hold free text (``portfolio``,
``agent``) use the ``path`` converter so a label that contains a slash still routes.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from collections_api.api.common import (
    ERROR_RESPONSES,
    LineParam,
    Window,
    cached_query,
    ok_response,
    respond,
    window_filters,
)
from collections_api.api.dependencies import Ctx
from collections_api.api.models import AgreementDetailAllRow, AgreementDetailRow
from collections_api.domains.charts import details

router = APIRouter(prefix="/dashboard", tags=["details"], responses=ERROR_RESPONSES)

KIND_TEXT = {
    "agreements": "generated agreements",
    "exceptions": "agreements pending approval",
    "rejected": "rejected agreements",
    "broken": "broken agreements",
}

PortfolioPath = Annotated[str, Path(description="Exact portfolio label. May contain slashes.")]
AgentPath = Annotated[str, Path(description="Exact agent display name. May contain slashes.")]

DETAIL_NOTE = (
    "\n\nOne row per agreement, largest first installment first. `total_value` and "
    "`installment_count` cover every installment of the agreement, each counted once. No "
    "debtor identity is returned."
)


def _register_by_portfolio(kind: str) -> None:
    def endpoint(
        ctx: Ctx, request: Request, line: LineParam, portfolio: PortfolioPath, window: Window
    ):
        line = ctx.cfg.resolve_line(line)
        query = details.by_portfolio(ctx.cfg, line, window, kind, portfolio)
        key = f"detail|{kind}|portfolio|{line}|{window.label}|{portfolio}"
        rows = cached_query(ctx, request, key, query, line, f"dashboard/{kind}-detail")
        return respond(
            ctx, request, rows, line=line, filters=window_filters(line, window, portfolio=portfolio)
        )

    router.add_api_route(
        f"/{kind}-detail/{{line}}/{{portfolio:path}}",
        endpoint,
        methods=["GET"],
        name=f"{kind}_detail",
        operation_id=f"{kind}_detail",
        summary=f"Detail of {KIND_TEXT[kind]} in one portfolio",
        description=f"The {KIND_TEXT[kind]} whose debts carry the given portfolio label."
        + DETAIL_NOTE,
        response_model=None,
        responses=ok_response(AgreementDetailRow),
    )


def _register_all(kind: str) -> None:
    def endpoint(ctx: Ctx, request: Request, line: LineParam, window: Window):
        line = ctx.cfg.resolve_line(line)
        query = details.all_portfolios(ctx.cfg, line, window, kind)
        key = f"detail|{kind}|all|{line}|{window.label}"
        rows = cached_query(ctx, request, key, query, line, f"dashboard/{kind}-detail-all")
        return respond(ctx, request, rows, line=line, filters=window_filters(line, window))

    router.add_api_route(
        f"/{kind}-detail-all/{{line}}",
        endpoint,
        methods=["GET"],
        name=f"{kind}_detail_all",
        operation_id=f"{kind}_detail_all",
        summary=f"Detail of {KIND_TEXT[kind]}, every portfolio",
        description=(
            f"All {KIND_TEXT[kind]} in the window, each with its portfolio label "
            "(`null` when none is known)." + DETAIL_NOTE
        ),
        response_model=None,
        responses=ok_response(AgreementDetailAllRow),
    )


def _register_by_agent(kind: str) -> None:
    def endpoint(ctx: Ctx, request: Request, line: LineParam, agent: AgentPath, window: Window):
        line = ctx.cfg.resolve_line(line)
        query = details.by_agent(ctx.cfg, line, window, kind, agent)
        key = f"detail|{kind}|agent|{line}|{window.label}|{agent}"
        rows = cached_query(ctx, request, key, query, line, f"dashboard/{kind}-detail-by-agent")
        return respond(
            ctx, request, rows, line=line, filters=window_filters(line, window, agent=agent)
        )

    router.add_api_route(
        f"/{kind}-detail-by-agent/{{line}}/{{agent:path}}",
        endpoint,
        methods=["GET"],
        name=f"{kind}_detail_by_agent",
        operation_id=f"{kind}_detail_by_agent",
        summary=f"Detail of {KIND_TEXT[kind]} of one agent",
        description=f"The {KIND_TEXT[kind]} one agent made in the window." + DETAIL_NOTE,
        response_model=None,
        responses=ok_response(AgreementDetailRow),
    )


for _kind in details.DETAIL_KINDS:
    _register_by_portfolio(_kind)
    _register_all(_kind)
    _register_by_agent(_kind)
