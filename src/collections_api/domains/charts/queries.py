"""Aggregates behind the chart and card routes.

Every builder takes the product line (one line or ``all``) and a ``Period`` and returns
a ``Query``. Each is assembled from a per-database base select; for ``all`` the bases
are combined with ``UNION ALL`` and re-aggregated in an outer select, so a measure is
never summed twice.

Conventions shared by all of them:

* Only first installments (``installment_no = 0``) are counted, so an agreement is
  one row, not one row per installment.
* Rows come from the de-duplicated installment table (``QueryConfig.dedup``) with the
  window and status filters inside it.
* Excluded agents are filtered in SQL, never in application code.
* Portfolio is resolved with ``CROSS APPLY ... TOP 1`` so an agreement with several
  debts is counted once.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from collections_api.core.sql import (
    ALL_LINES,
    FIRST_INSTALLMENT_NO,
    Period,
    Query,
    QueryConfig,
    team_filter,
    team_params,
)


@dataclass(frozen=True)
class KindSpec:
    """One chart flavour: which status set it counts and what it calls its measures."""

    status_set: str
    count: str
    value: str


PORTFOLIO_KINDS: dict[str, KindSpec] = {
    "exceptions": KindSpec("exception", "exception_count", "exception_value"),
    "agreements": KindSpec("generated", "agreement_count", "agreement_value"),
    "rejected": KindSpec("rejected", "rejected_count", "rejected_value"),
    "broken": KindSpec("broken", "broken_count", "broken_value"),
    "first-installment": KindSpec("generated", "agreement_count", "first_installment_value"),
}

AGENT_KINDS: dict[str, KindSpec] = {
    "exceptions": KindSpec("exception", "exception_count", "exception_value"),
    "first-installment": KindSpec(
        "generated", "first_installment_count", "first_installment_value"
    ),
}

SUMMARY_COLUMNS = ("first_installment_value", "agreement_count")
ROLLUP_COLUMNS = ("portfolio_label", "status_id", "agreement_count", "total_value")
RECEIVED_COLUMNS = (
    "portfolio_label",
    "agreement_count",
    "received_value",
    "first_installment_received",
)
WITHOUT_PORTFOLIO_COLUMNS = (
    "agreement_id",
    "portfolio_id",
    "first_installment_value",
    "total_value",
    "agent_name",
)

Base = Callable[[str, str], Query]


def aggregate_lines(
    cfg: QueryConfig,
    line: str,
    base: Base,
    *,
    group_columns: Sequence[str],
    measures: Sequence[str],
    order_by: str = "",
) -> Query:
    """One line: the base select. ``all``: base selects unioned and summed per group."""
    inner = cfg.across_lines(line, base)
    order = f"\nORDER BY {order_by}" if order_by else ""
    if line != ALL_LINES:
        return Query(inner.sql + order, inner.params)
    select = ", ".join([*group_columns, *(f"SUM({m}) AS {m}" for m in measures)])
    group = f"\nGROUP BY {', '.join(group_columns)}" if group_columns else ""
    return Query(f"SELECT {select}\nFROM (\n{inner.sql}\n) AS sub{group}{order}", inner.params)


def list_lines(
    cfg: QueryConfig, line: str, base: Base, *, columns: Sequence[str], order_by: str
) -> Query:
    """Like ``aggregate_lines`` for row lists, which are concatenated rather than summed."""
    inner = cfg.across_lines(line, base)
    if line != ALL_LINES:
        return Query(f"{inner.sql}\nORDER BY {order_by}", inner.params)
    return Query(
        f"SELECT {', '.join(columns)}\nFROM (\n{inner.sql}\n) AS sub\nORDER BY {order_by}",
        inner.params,
    )


def first_installment_filter(period: Period, statuses: str, alias: str = "R") -> str:
    return (
        f"AND {alias}.installment_no = {FIRST_INSTALLMENT_NO}\n"
        f"              AND {period.contains(f'{alias}.issued_at')}\n"
        f"              AND {alias}.status_id IN {statuses}"
    )


# --------------------------------------------------------------------------- cards


def first_installment_summary(
    cfg: QueryConfig, line: str, period: Period, team: str | None
) -> tuple[Query, str]:
    """Top card: first-installment value and agreement count for generated agreements.

    Returns the query and the team label that was applied.
    """
    label, token, clause = team_filter(team)

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, cfg.status.sql("generated"))
        sql = f"""
SELECT SUM(R.amount) AS first_installment_value,
       COUNT(DISTINCT R.agreement_id) AS agreement_count
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
WHERE R.rn = 1
      {cfg.excluded_agents()}
      {clause}"""
        return Query(sql, team_params(token))

    return aggregate_lines(cfg, line, base, group_columns=(), measures=SUMMARY_COLUMNS), label


# --------------------------------------------------------------------------- by portfolio


def by_portfolio(cfg: QueryConfig, line: str, period: Period, kind: str) -> Query:
    spec = PORTFOLIO_KINDS[kind]

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, cfg.status.sql(spec.status_set))
        sql = f"""
SELECT DA.portfolio_label AS portfolio_label,
       COUNT(DISTINCT R.agreement_id) AS {spec.count},
       SUM(R.amount) AS {spec.value}
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{cfg.portfolio_label_lookup(database, "R", outer=False)}
WHERE R.rn = 1
      {cfg.excluded_agents()}
GROUP BY DA.portfolio_label"""
        return Query(sql)

    order = spec.value if kind == "first-installment" else spec.count
    return aggregate_lines(
        cfg,
        line,
        base,
        group_columns=("portfolio_label",),
        measures=(spec.count, spec.value),
        order_by=f"{order} DESC",
    )


def portfolio_rollup(cfg: QueryConfig, line: str, period: Period) -> Query:
    """One scan grouped by portfolio and status.

    A client can rebuild the five by-portfolio charts by slicing on ``status_id``.
    ``agreement_count`` is exact per status. Summing it over several statuses (the
    ``generated`` slice) double counts an agreement that appears under two of them.
    """

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, cfg.status.sql("rollup"))
        sql = f"""
SELECT DA.portfolio_label AS portfolio_label,
       R.status_id AS status_id,
       COUNT(DISTINCT R.agreement_id) AS agreement_count,
       SUM(R.amount) AS total_value
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{cfg.portfolio_label_lookup(database, "R", outer=False)}
WHERE R.rn = 1
      {cfg.excluded_agents()}
GROUP BY DA.portfolio_label, R.status_id"""
        return Query(sql)

    return aggregate_lines(
        cfg,
        line,
        base,
        group_columns=("portfolio_label", "status_id"),
        measures=("agreement_count", "total_value"),
        order_by="portfolio_label, status_id",
    )


def received_by_portfolio(cfg: QueryConfig, line: str, period: Period) -> Query:
    """Cash received per portfolio, filtered on the payment date, not the issue date.

    The window and payment filters sit inside the de-duplicated table, before the
    ranking. A later re-write without a payment would otherwise win the rank and hide
    the payment recorded on the earlier row.
    """

    def base(_line: str, database: str) -> Query:
        extra = (
            f"AND {period.contains('R.paid_at')}\n"
            "              AND R.paid_amount > 0\n"
            f"              AND R.status_id IN {cfg.status.sql('generated')}"
        )
        sql = f"""
SELECT DA.portfolio_label AS portfolio_label,
       COUNT(DISTINCT R.agreement_id) AS agreement_count,
       SUM(R.paid_amount) AS received_value,
       SUM(CASE WHEN R.installment_no = {FIRST_INSTALLMENT_NO} THEN R.paid_amount ELSE 0 END)
           AS first_installment_received
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{cfg.portfolio_label_lookup(database, "R", outer=False)}
WHERE R.rn = 1
      {cfg.excluded_agents()}
GROUP BY DA.portfolio_label"""
        return Query(sql)

    return aggregate_lines(
        cfg,
        line,
        base,
        group_columns=("portfolio_label",),
        measures=("agreement_count", "received_value", "first_installment_received"),
        order_by="received_value DESC",
    )


def exceptions_without_portfolio(cfg: QueryConfig, line: str, period: Period) -> Query:
    """Exception agreements that no by-portfolio chart can show (no portfolio label)."""

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, cfg.status.sql("exception"))
        sql = f"""
SELECT R.agreement_id,
       R.portfolio_id,
       R.amount AS first_installment_value,
       COALESCE((
           SELECT SUM(R2.amount)
           FROM {cfg.dedup(database, "R2")} AS R2
           WHERE R2.rn = 1
             AND R2.agreement_id = R.agreement_id
             AND R2.portfolio_id = R.portfolio_id
       ), R.amount) AS total_value,
       U.display_name AS agent_name
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
WHERE R.rn = 1
      {cfg.excluded_agents()}
      AND NOT EXISTS (
          SELECT 1
          FROM {cfg.table(database, "agreement_debts", "RD")}
          JOIN {cfg.table(database, "debt_attributes", "DA2")} ON RD.debt_id = DA2.debt_id
          WHERE RD.agreement_id = R.agreement_id
            AND RD.portfolio_id = R.portfolio_id
            AND DA2.portfolio_label IS NOT NULL
      )"""
        return Query(sql)

    return list_lines(
        cfg,
        line,
        base,
        columns=WITHOUT_PORTFOLIO_COLUMNS,
        order_by="first_installment_value DESC",
    )


# --------------------------------------------------------------------------- by agent


def by_agent(
    cfg: QueryConfig, line: str, period: Period, kind: str, team: str | None = None
) -> tuple[Query, str]:
    """Per-agent count and value. Returns the query and the team label applied."""
    spec = AGENT_KINDS[kind]
    label, token, clause = team_filter(team)

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, cfg.status.sql(spec.status_set))
        sql = f"""
SELECT U.display_name AS agent_name,
       COUNT(DISTINCT R.agreement_id) AS {spec.count},
       SUM(R.amount) AS {spec.value}
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
WHERE R.rn = 1
      {cfg.excluded_agents()}
      {clause}
GROUP BY U.display_name"""
        return Query(sql, team_params(token))

    order = spec.value if kind == "first-installment" else spec.count
    query = aggregate_lines(
        cfg,
        line,
        base,
        group_columns=("agent_name",),
        measures=(spec.count, spec.value),
        order_by=f"{order} DESC",
    )
    return query, label
