"""Agreement lists behind the charts: one row per agreement.

Three scopes share one base query: a single portfolio, a single agent, or everything.
The expensive parts (total value and installment count per agreement, portfolio label)
are computed once per query as grouped subqueries joined on the agreement key, and
restricted to the agreements that qualify for the period. The earlier shape, a
correlated subquery per output row, scaled with rows times lookups and timed out on
month-long windows across all portfolios.

The portfolio label is resolved with a ``ROW_NUMBER`` subquery and a join here, not
``CROSS APPLY``. The subquery already reduces the label to at most one row per
agreement, so the join cannot multiply rows, and the apply form re-ran the lookup per
row even when restricted to the qualifying set. The pick rule is the one the charts use
(the label that sorts first), so a list for one portfolio holds the agreements the chart
counts under that portfolio. A test with two labels on one agreement pins this.

No debtor identity or staff registration code is returned.
"""

from __future__ import annotations

from collections_api.core.sql import Period, Query, QueryConfig
from collections_api.domains.charts.queries import first_installment_filter, list_lines

DETAIL_KINDS: dict[str, str] = {
    "agreements": "generated",
    "exceptions": "exception",
    "rejected": "rejected",
    "broken": "broken",
}

DETAIL_COLUMNS = (
    "agreement_id",
    "portfolio_id",
    "first_installment_value",
    "total_value",
    "agent_name",
    "agreement_date",
    "due_date",
    "installment_count",
)
DETAIL_ALL_COLUMNS = (*DETAIL_COLUMNS, "portfolio_label")

_ORDER = "first_installment_value DESC"


def _candidates(cfg: QueryConfig, database: str, period: Period, statuses: str) -> str:
    """(agreement_id, portfolio_id) pairs that qualify for the period and status set.

    The pair is the key; an agreement number alone can exist in several portfolios.
    """
    extra = first_installment_filter(period, statuses, "R4")
    return (
        "SELECT DISTINCT R4.agreement_id, R4.portfolio_id\n"
        f"FROM {cfg.dedup(database, 'R4', extra)} AS R4\n"
        "WHERE R4.rn = 1"
    )


def _totals_join(cfg: QueryConfig, database: str, candidates: str) -> str:
    return f"""
LEFT JOIN (
    SELECT DI.agreement_id, DI.portfolio_id,
           SUM(DI.amount) AS total_value,
           -- Distinct installment numbers: an agreement re-written by mistake must not
           -- inflate the installment count.
           COUNT(DISTINCT DI.installment_no) AS installment_count
    FROM (
        SELECT R3.agreement_id, R3.portfolio_id, R3.installment_no, R3.amount,
               ROW_NUMBER() OVER (
                   PARTITION BY R3.agreement_id, R3.portfolio_id, R3.installment_no
                   ORDER BY R3.issued_at DESC, R3.row_id DESC
               ) AS rn
        FROM {cfg.table(database, "agreements", "R3")}
        WHERE EXISTS (
            SELECT 1 FROM ({candidates}) AS CN
            WHERE CN.agreement_id = R3.agreement_id AND CN.portfolio_id = R3.portfolio_id
        )
    ) AS DI
    WHERE DI.rn = 1
    GROUP BY DI.agreement_id, DI.portfolio_id
) AS AGG ON AGG.agreement_id = R.agreement_id AND AGG.portfolio_id = R.portfolio_id"""


def _label_map(cfg: QueryConfig, database: str, candidates: str) -> str:
    """One portfolio label per qualifying agreement.

    The label is picked before any filtering on it, with the rule of
    ``QueryConfig.portfolio_label_lookup``: when an agreement's debts carry several
    labels, the one that sorts first wins. Filtering inside this subquery would match an
    agreement under every label it has, and the portfolio list would disagree with the
    portfolio chart. The caller filters on ``PM.portfolio_label`` instead.
    """
    return f"""(
    SELECT PC.agreement_id, PC.portfolio_id, PC.portfolio_label
    FROM (
        SELECT RD.agreement_id, RD.portfolio_id, DA2.portfolio_label,
               ROW_NUMBER() OVER (
                   PARTITION BY RD.agreement_id, RD.portfolio_id ORDER BY DA2.portfolio_label
               ) AS prn
        FROM {cfg.table(database, "agreement_debts", "RD")}
        JOIN {cfg.table(database, "debt_attributes", "DA2")} ON DA2.debt_id = RD.debt_id
        WHERE DA2.portfolio_label IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM ({candidates}) AS CN
              WHERE CN.agreement_id = RD.agreement_id AND CN.portfolio_id = RD.portfolio_id
          )
    ) AS PC
    WHERE PC.prn = 1
)"""


def _select_columns(with_label: bool) -> str:
    label = ",\n       PM.portfolio_label AS portfolio_label" if with_label else ""
    return (
        "R.agreement_id,\n"
        "       R.portfolio_id,\n"
        "       R.amount AS first_installment_value,\n"
        "       COALESCE(AGG.total_value, R.amount) AS total_value,\n"
        "       U.display_name AS agent_name,\n"
        "       CONVERT(varchar(10), R.issued_at, 120) AS agreement_date,\n"
        "       CONVERT(varchar(10), R.due_at, 120) AS due_date,\n"
        f"       COALESCE(AGG.installment_count, 1) AS installment_count{label}"
    )


def by_portfolio(cfg: QueryConfig, line: str, period: Period, kind: str, portfolio: str) -> Query:
    statuses = cfg.status.sql(DETAIL_KINDS[kind])

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, statuses)
        candidates = _candidates(cfg, database, period, statuses)
        sql = f"""
SELECT {_select_columns(False)}
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
JOIN {_label_map(cfg, database, candidates)} AS PM
    ON PM.agreement_id = R.agreement_id AND PM.portfolio_id = R.portfolio_id
{_totals_join(cfg, database, candidates)}
WHERE R.rn = 1
      {cfg.excluded_agents()}
      AND PM.portfolio_label = ?"""
        return Query(sql, (portfolio,))

    return list_lines(cfg, line, base, columns=DETAIL_COLUMNS, order_by=_ORDER)


def by_agent(cfg: QueryConfig, line: str, period: Period, kind: str, agent_name: str) -> Query:
    statuses = cfg.status.sql(DETAIL_KINDS[kind])

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, statuses)
        candidates = _candidates(cfg, database, period, statuses)
        sql = f"""
SELECT {_select_columns(False)}
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{_totals_join(cfg, database, candidates)}
WHERE R.rn = 1
      {cfg.excluded_agents()}
      AND U.display_name = ?"""
        return Query(sql, (agent_name,))

    return list_lines(cfg, line, base, columns=DETAIL_COLUMNS, order_by=_ORDER)


def all_portfolios(cfg: QueryConfig, line: str, period: Period, kind: str) -> Query:
    statuses = cfg.status.sql(DETAIL_KINDS[kind])

    def base(_line: str, database: str) -> Query:
        extra = first_installment_filter(period, statuses)
        candidates = _candidates(cfg, database, period, statuses)
        sql = f"""
SELECT {_select_columns(True)}
FROM {cfg.dedup(database, "R", extra)} AS R
JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{_totals_join(cfg, database, candidates)}
LEFT JOIN {_label_map(cfg, database, candidates)} AS PM
    ON PM.agreement_id = R.agreement_id AND PM.portfolio_id = R.portfolio_id
WHERE R.rn = 1
      {cfg.excluded_agents()}"""
        return Query(sql)

    return list_lines(cfg, line, base, columns=DETAIL_ALL_COLUMNS, order_by=_ORDER)
