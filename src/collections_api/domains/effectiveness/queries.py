"""Payment-slip effectiveness: are the slips issued for agreements being paid?

A *slip* is one installment row (the payment slip sent for it). It counts as *paid in
grace* when it was paid, for a positive amount, no later than ``grace_days`` after its
due date. Everything is computed over the de-duplicated installment table, and the
slip's own filters (status, installment kind, date floor) sit inside the de-duplication.

Installment kinds: ``first`` is installment 0 (the down payment); ``later`` is every
installment after it.

The agent filter here is ``QueryConfig.effectiveness_excluded_agents``, a wider list
than the one the productivity routes use. They are kept apart because merging them
would change the numbers on this page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from collections_api.core.sql import (
    ALL_LINES,
    FIRST_INSTALLMENT_NO,
    UNION,
    Period,
    Query,
    QueryConfig,
    date_literal,
)

INSTALLMENT_TYPES = ("first", "later")
DETAIL_KINDS = ("to-mature", "in-grace", "overdue-unpaid", "broken", "paid-in-grace")

SUMMARY_COLUMNS = (
    "slips_total",
    "agreements_total",
    "slips_to_mature",
    "slips_in_grace",
    "slips_overdue_unpaid",
    "slips_paid_in_grace",
    "slips_broken",
    "conversion_pct",
    "amount_maturing",
    "amount_received",
    "effectiveness_pct",
)
DAILY_COLUMNS = (
    "due_date",
    "slips_total",
    "slips_paid_in_grace",
    "conversion_pct",
    "amount_maturing",
    "amount_received",
    "effectiveness_pct",
)
DETAIL_COLUMNS = (
    "agreement_id",
    "portfolio_id",
    "slip_amount",
    "total_value",
    "agent_name",
    "agreement_date",
    "due_date",
    "installment_count",
    "paid_installment_count",
    "broken_date",
    "portfolio_label",
    "original_debt_value",
)
BREAK_CURVE_COLUMNS = ("band", "slips_total", "slips_broken", "break_rate_pct")
PORTFOLIO_SUMMARY_COLUMNS = (
    "slips_total",
    "slips_paid_in_grace",
    "amount_maturing",
    "amount_received",
    "effectiveness_pct",
)
PORTFOLIO_RANKING_COLUMNS = ("portfolio_label", *PORTFOLIO_SUMMARY_COLUMNS)
GENERATION_RANKING_COLUMNS = ("portfolio_label", "agreement_count", "agreement_value")


def _kind_condition(installment_type: str) -> str:
    return (
        f"= {FIRST_INSTALLMENT_NO}" if installment_type == "first" else f"> {FIRST_INSTALLMENT_NO}"
    )


# --------------------------------------------------------------------------- series


@dataclass(frozen=True)
class SeriesSpec:
    key: str
    installment_type: str
    date_column: str  # issued_at or due_at
    grain: str  # day, month or agent-month
    label: str

    @property
    def columns(self) -> tuple[str, ...]:
        date_name = "issue_date" if self.date_column == "issued_at" else "due_date"
        base = ("slips_issued", "slips_paid_in_grace", "conversion_pct")
        if self.grain == "day":
            return (date_name, *base)
        if self.grain == "month":
            return ("year", "month", *base)
        extra = ("broken_count",) if self.key == "monthly-agent-later-by-due" else ()
        return ("agent_login", "year", "month", *base, *extra)


SERIES: dict[str, SeriesSpec] = {
    spec.key: spec
    for spec in (
        SeriesSpec("daily-first", "first", "issued_at", "day", "First installments by issue day"),
        SeriesSpec(
            "monthly-first", "first", "issued_at", "month", "First installments by issue month"
        ),
        SeriesSpec("daily-later", "later", "issued_at", "day", "Later installments by issue day"),
        SeriesSpec(
            "monthly-later", "later", "issued_at", "month", "Later installments by issue month"
        ),
        SeriesSpec("daily-later-by-due", "later", "due_at", "day", "Later installments by due day"),
        SeriesSpec(
            "monthly-later-by-due", "later", "due_at", "month", "Later installments by due month"
        ),
        SeriesSpec(
            "monthly-agent-first",
            "first",
            "issued_at",
            "agent-month",
            "First installments by agent and issue month",
        ),
        SeriesSpec(
            "monthly-agent-later",
            "later",
            "issued_at",
            "agent-month",
            "Later installments by agent and issue month",
        ),
        SeriesSpec(
            "monthly-agent-later-by-due",
            "later",
            "due_at",
            "agent-month",
            "Later installments by agent and due month, with broken counts",
        ),
    )
}


def _rounded_pct(cfg: QueryConfig) -> str:
    """Conversion as a whole percent, rounded half up."""
    return f"CAST(FLOOR(100.0 * SUM({cfg.paid_in_grace()}) / NULLIF(COUNT(*), 0) + 0.5) AS INT)"


def series_query(cfg: QueryConfig, line: str, key: str) -> Query:
    """The query behind one effectiveness series (see ``SERIES``)."""
    spec = SERIES[key]
    column = spec.date_column
    paid = f"SUM({cfg.paid_in_grace()}) AS slips_paid_in_grace"
    conversion = f"{_rounded_pct(cfg)} AS conversion_pct"

    def base(_line: str, database: str) -> Query:
        extra = (
            f"AND R.installment_no {_kind_condition(spec.installment_type)}\n"
            f"      AND R.status_id IN {cfg.status.sql('generated')}\n"
            f"      AND R.{column} >= {date_literal(cfg.effectiveness_floor)}"
        )
        sql = f"""
SELECT R.issued_at, R.due_at, R.paid_at, R.paid_amount, R.status_id, U.login AS agent_login
FROM {cfg.dedup(database, "R", extra)} AS R
INNER JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
WHERE R.rn = 1
      {cfg.effectiveness_excluded_agents()}"""
        return Query(sql)

    inner = cfg.across_lines(line, base).sql
    if spec.grain == "day":
        name = "issue_date" if column == "issued_at" else "due_date"
        sql = (
            f"SELECT CAST({column} AS DATE) AS {name}, COUNT(*) AS slips_issued,\n"
            f"       {paid}, {conversion}\n"
            f"FROM (\n{inner}\n) AS T\nGROUP BY CAST({column} AS DATE)\nORDER BY {name}"
        )
    elif spec.grain == "month":
        sql = (
            f"SELECT YEAR({column}) AS year, MONTH({column}) AS month, COUNT(*) AS slips_issued,\n"
            f"       {paid}, {conversion}\n"
            f"FROM (\n{inner}\n) AS T\n"
            f"GROUP BY YEAR({column}), MONTH({column})\nORDER BY year, month"
        )
    else:
        broken = ""
        having = ""
        if key == "monthly-agent-later-by-due":
            broken = (
                f",\n       SUM(CASE WHEN status_id IN {cfg.status.sql('broken')} THEN 1 ELSE 0 END)"
                " AS broken_count"
            )
            having = f"\nHAVING COUNT(*) >= {int(cfg.effectiveness_min_slips)}"
        sql = (
            f"SELECT agent_login, YEAR({column}) AS year, MONTH({column}) AS month,\n"
            f"       COUNT(*) AS slips_issued, {paid}, {conversion}{broken}\n"
            f"FROM (\n{inner}\n) AS T\n"
            f"GROUP BY agent_login, YEAR({column}), MONTH({column}){having}\n"
            "ORDER BY year, month, agent_login"
        )
    return Query(sql)


# --------------------------------------------------------------------------- summary


def _slip_rows(
    cfg: QueryConfig,
    line: str,
    period: Period,
    installment_type: str,
    *,
    portfolio_id: int | None = None,
    columns: str = "R.agreement_id, R.portfolio_id, R.amount, R.paid_amount, R.paid_at, R.due_at, R.status_id",
) -> Query:
    """Slips due inside ``period``, one row each, tagged with their product line."""

    def base(line_name: str, database: str) -> Query:
        portfolio = f"AND R.portfolio_id = {int(portfolio_id)}" if portfolio_id is not None else ""
        extra = (
            f"AND {period.contains('R.due_at')}\n"
            f"      AND R.status_id IN {cfg.status.sql('generated')}\n"
            f"      AND R.installment_no {_kind_condition(installment_type)} {portfolio}"
        )
        sql = f"""
SELECT {columns}, '{line_name}' AS product_line
FROM {cfg.dedup(database, "R", extra)} AS R
INNER JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
WHERE R.rn = 1
      {cfg.effectiveness_excluded_agents()}"""
        return Query(sql)

    return cfg.across_lines(line, base)


def _received(cfg: QueryConfig) -> str:
    return "CASE WHEN paid_at IS NOT NULL AND paid_amount > 0 THEN paid_amount ELSE 0 END"


def summary_queries(
    cfg: QueryConfig,
    line: str,
    period: Period,
    today: date,
    installment_type: str,
    portfolio_id: int | None = None,
) -> tuple[Query, Query]:
    """``(kpi_query, daily_query)`` for slips due in ``period``.

    The status buckets partition the slips: still to mature (due today or later, not
    yet paid), in grace (overdue but still inside the grace period), overdue and
    unpaid (grace expired), and paid in grace. A slip paid after its grace period is
    counted as overdue and unpaid: the figure measures on-time payment, not eventual
    payment.
    """
    inner = _slip_rows(cfg, line, period, installment_type, portfolio_id=portfolio_id)
    now = date_literal(today)
    grace = int(cfg.grace_days)
    paid = cfg.paid_in_grace()
    received = _received(cfg)
    unpaid_overdue = f"due_at < {now} AND ({paid}) = 0"
    kpi = f"""
SELECT COUNT(*) AS slips_total,
       COUNT(DISTINCT CONCAT(product_line, '|', portfolio_id, '|', agreement_id)) AS agreements_total,
       SUM(CASE WHEN due_at >= {now} AND (paid_at IS NULL OR paid_amount = 0) THEN 1 ELSE 0 END)
           AS slips_to_mature,
       SUM(CASE WHEN {unpaid_overdue} AND DATEADD(DAY, {grace}, due_at) >= {now} THEN 1 ELSE 0 END)
           AS slips_in_grace,
       SUM(CASE WHEN {unpaid_overdue} AND DATEADD(DAY, {grace}, due_at) < {now} THEN 1 ELSE 0 END)
           AS slips_overdue_unpaid,
       SUM({paid}) AS slips_paid_in_grace,
       SUM(CASE WHEN status_id IN {cfg.status.sql("broken")} THEN 1 ELSE 0 END) AS slips_broken,
       CAST(100.0 * SUM({paid}) / NULLIF(COUNT(*), 0) AS DECIMAL(8, 2)) AS conversion_pct,
       COALESCE(SUM(amount), 0) AS amount_maturing,
       COALESCE(SUM({received}), 0) AS amount_received,
       CAST(100.0 * SUM({received}) / NULLIF(SUM(amount), 0) AS DECIMAL(8, 2)) AS effectiveness_pct
FROM (
{inner.sql}
) AS T"""
    daily = f"""
SELECT CAST(due_at AS DATE) AS due_date,
       COUNT(*) AS slips_total,
       SUM({paid}) AS slips_paid_in_grace,
       CAST(100.0 * SUM({paid}) / NULLIF(COUNT(*), 0) AS DECIMAL(8, 2)) AS conversion_pct,
       COALESCE(SUM(amount), 0) AS amount_maturing,
       COALESCE(SUM({received}), 0) AS amount_received,
       CAST(100.0 * SUM({received}) / NULLIF(SUM(amount), 0) AS DECIMAL(8, 2)) AS effectiveness_pct
FROM (
{inner.sql}
) AS T
GROUP BY CAST(due_at AS DATE)
ORDER BY due_date"""
    return Query(kpi, inner.params), Query(daily, inner.params)


# --------------------------------------------------------------------------- slip detail


def _detail_filter(cfg: QueryConfig, kind: str, today: date) -> str:
    now = date_literal(today)
    grace = int(cfg.grace_days)
    paid = cfg.paid_in_grace("R")
    return {
        "to-mature": f"AND R.due_at >= {now} AND (R.paid_at IS NULL OR R.paid_amount = 0)",
        "in-grace": (
            f"AND R.due_at < {now} AND ({paid}) = 0 AND DATEADD(DAY, {grace}, R.due_at) >= {now}"
        ),
        "overdue-unpaid": (
            f"AND R.due_at < {now} AND ({paid}) = 0 AND DATEADD(DAY, {grace}, R.due_at) < {now}"
        ),
        "broken": f"AND R.status_id IN {cfg.status.sql('broken')}",
        "paid-in-grace": f"AND ({paid}) = 1",
    }[kind]


def detail_query(
    cfg: QueryConfig,
    line: str,
    period: Period,
    today: date,
    installment_type: str,
    kind: str,
) -> Query:
    """The slips behind one KPI bucket, largest first, capped at ``effectiveness_detail_limit``.

    The cap is applied to the candidate slips *before* the per-agreement lookups run,
    so the expensive correlated subqueries execute for at most the cap, not for every
    slip in the window. With several lines the per-line caps are unioned and capped
    again.
    """
    limit = int(cfg.effectiveness_detail_limit)
    extra = (
        f"AND {period.contains('R.due_at')}\n"
        f"      AND R.status_id IN {cfg.status.sql('generated')}\n"
        f"      AND R.installment_no {_kind_condition(installment_type)}\n"
        f"      {_detail_filter(cfg, kind, today)}"
    )

    def base(_line: str, database: str) -> Query:
        sql = f"""
SELECT T.agreement_id,
       T.portfolio_id,
       T.slip_amount,
       COALESCE((
           SELECT SUM(R2.amount) FROM {cfg.dedup(database, "R2")} AS R2
           WHERE R2.rn = 1 AND R2.agreement_id = T.agreement_id AND R2.portfolio_id = T.portfolio_id
       ), T.slip_amount) AS total_value,
       T.agent_name,
       T.agreement_date,
       T.due_date,
       (SELECT COUNT(1) FROM {cfg.dedup(database, "R3")} AS R3
        WHERE R3.rn = 1 AND R3.agreement_id = T.agreement_id AND R3.portfolio_id = T.portfolio_id
       ) AS installment_count,
       (SELECT COUNT(1) FROM {cfg.dedup(database, "R4")} AS R4
        WHERE R4.rn = 1 AND R4.agreement_id = T.agreement_id AND R4.portfolio_id = T.portfolio_id
          AND R4.paid_amount > 0
       ) AS paid_installment_count,
       T.broken_date,
       (SELECT TOP 1 DA2.portfolio_label
        FROM {cfg.table(database, "agreement_debts", "RD2")}
        JOIN {cfg.table(database, "debt_attributes", "DA2")} ON RD2.debt_id = DA2.debt_id
        WHERE RD2.agreement_id = T.agreement_id AND RD2.portfolio_id = T.portfolio_id
          AND DA2.portfolio_label IS NOT NULL) AS portfolio_label,
       (SELECT SUM(DM.balance)
        FROM {cfg.table(database, "agreement_debts", "RD")}
        JOIN {cfg.table(database, "debts", "DM")} ON RD.debt_id = DM.debt_id
        WHERE RD.agreement_id = T.agreement_id AND RD.portfolio_id = T.portfolio_id
       ) AS original_debt_value
FROM (
    SELECT TOP {limit}
           R.agreement_id, R.portfolio_id, R.amount AS slip_amount,
           U.display_name AS agent_name,
           CONVERT(varchar(10), R.issued_at, 120) AS agreement_date,
           CONVERT(varchar(10), R.due_at, 120) AS due_date,
           CONVERT(varchar(10), R.broken_at, 120) AS broken_date
    FROM {cfg.dedup(database, "R", extra)} AS R
    INNER JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
    WHERE R.rn = 1
          {cfg.effectiveness_excluded_agents()}
    ORDER BY R.amount DESC
) AS T"""
        return Query(sql)

    members = [base(name, database) for name, database in cfg.scope(line)]
    params = tuple(value for member in members for value in member.params)
    if line != ALL_LINES:
        return Query(f"{members[0].sql}\nORDER BY slip_amount DESC", params)
    wrapped = UNION.join(
        f"SELECT * FROM (\n{member.sql}\n) AS member{index}" for index, member in enumerate(members)
    )
    columns = ", ".join(DETAIL_COLUMNS)
    return Query(
        f"SELECT TOP {limit} {columns}\nFROM (\n{wrapped}\n) AS u\nORDER BY slip_amount DESC",
        params,
    )


# --------------------------------------------------------------------------- break curve


def break_bands(edges: tuple[int, ...]) -> list[tuple[str, int, int | None]]:
    """``(label, first_day, last_day)`` bands from the configured upper edges.

    Edges ``(7, 21)`` give ``0-7``, ``8-21`` and an open ``22+`` band.
    """
    bands: list[tuple[str, int, int | None]] = []
    low = 0
    for edge in edges:
        bands.append((f"{low}-{edge} days", low, edge))
        low = edge + 1
    bands.append((f"{low}+ days", low, None))
    return bands


def break_curve_query(cfg: QueryConfig, line: str, period: Period, today: date) -> Query:
    """Share of first-installment slips already broken, by days since the due date.

    Age is measured from the due date to today. The date a break was recorded is not
    used: batch processes can pile recorded breaks onto a few days and distort the curve.
    """

    def base(_line: str, database: str) -> Query:
        extra = (
            f"AND R.installment_no = {FIRST_INSTALLMENT_NO}\n"
            f"      AND {period.contains('R.due_at')}\n"
            f"      AND R.status_id IN {cfg.status.sql('break_curve')}"
        )
        sql = f"""
SELECT R.status_id, DATEDIFF(DAY, R.due_at, {date_literal(today)}) AS days_since_due
FROM {cfg.dedup(database, "R", extra)} AS R
WHERE R.rn = 1"""
        return Query(sql)

    inner = cfg.across_lines(line, base)
    bands = break_bands(cfg.break_curve_edges)
    label_cases, order_cases = [], []
    for index, (label, low, high) in enumerate(bands):
        test = (
            f"days_since_due >= {low}"
            if high is None
            else f"days_since_due BETWEEN {low} AND {high}"
        )
        label_cases.append(f"WHEN {test} THEN '{label}'")
        order_cases.append(f"WHEN {test} THEN {index}")
    broken = f"SUM(CASE WHEN status_id IN {cfg.status.sql('broken')} THEN 1 ELSE 0 END)"
    sql = f"""
SELECT band,
       COUNT(*) AS slips_total,
       {broken} AS slips_broken,
       CAST(100.0 * {broken} / NULLIF(COUNT(*), 0) AS DECIMAL(5, 1)) AS break_rate_pct
FROM (
    SELECT status_id,
           CASE {" ".join(label_cases)} END AS band,
           CASE {" ".join(order_cases)} END AS band_order
    FROM (
{inner.sql}
    ) AS B
) AS T
WHERE band IS NOT NULL
GROUP BY band, band_order
ORDER BY band_order"""
    return Query(sql, inner.params)


# --------------------------------------------------------------------------- portfolio views


def _portfolio_slip_rows(
    cfg: QueryConfig, line: str, period: Period, installment_type: str, *, only_label: bool
) -> Query:
    """Slips due in ``period`` with their portfolio label, resolved per row.

    ``only_label`` binds a label and keeps matching slips; otherwise every slip with a
    resolvable label is kept. Parameters: one label per line in scope when bound.
    """

    def base(line_name: str, database: str) -> Query:
        extra = (
            f"AND {period.contains('R.due_at')}\n"
            f"      AND R.status_id IN {cfg.status.sql('generated')}\n"
            f"      AND R.installment_no {_kind_condition(installment_type)}"
        )
        label_filter = (
            "AND DA.portfolio_label = ?" if only_label else "AND DA.portfolio_label IS NOT NULL"
        )
        sql = f"""
SELECT R.amount, R.paid_amount, R.paid_at, R.due_at, DA.portfolio_label
FROM {cfg.dedup(database, "R", extra)} AS R
INNER JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{cfg.portfolio_label_lookup(database, "R", outer=True)}
WHERE R.rn = 1
      {label_filter}
      {cfg.effectiveness_excluded_agents()}"""
        return Query(sql)

    return cfg.across_lines(line, base)


def _portfolio_measures(cfg: QueryConfig) -> str:
    paid = cfg.paid_in_grace()
    received = _received(cfg)
    return f"""COUNT(*) AS slips_total,
       SUM({paid}) AS slips_paid_in_grace,
       COALESCE(SUM(amount), 0) AS amount_maturing,
       COALESCE(SUM({received}), 0) AS amount_received,
       CAST(100.0 * SUM({received}) / NULLIF(SUM(amount), 0) AS DECIMAL(8, 2)) AS effectiveness_pct"""


def portfolio_summary_query(
    cfg: QueryConfig, line: str, period: Period, installment_type: str, portfolio_label: str
) -> Query:
    """Effectiveness of one portfolio, identified by its label."""
    inner = _portfolio_slip_rows(cfg, line, period, installment_type, only_label=True)
    params = (portfolio_label,) * len(cfg.scope(line))
    sql = f"SELECT {_portfolio_measures(cfg)}\nFROM (\n{inner.sql}\n) AS T"
    return Query(sql, params)


def portfolio_ranking_query(
    cfg: QueryConfig, line: str, period: Period, installment_type: str
) -> Query:
    """Every portfolio in one call, largest maturing amount first."""
    inner = _portfolio_slip_rows(cfg, line, period, installment_type, only_label=False)
    sql = (
        f"SELECT portfolio_label,\n       {_portfolio_measures(cfg)}\n"
        f"FROM (\n{inner.sql}\n) AS T\nGROUP BY portfolio_label\nORDER BY amount_maturing DESC"
    )
    return Query(sql, inner.params)


def generation_ranking_query(cfg: QueryConfig, line: str, period: Period) -> Query:
    """Agreements generated per portfolio, by issue date.

    Unlike the slip views this window is on ``issued_at`` and is open-ended by design:
    a generated agreement counts whatever happens to it afterwards. It uses the
    standard agent exclusion list, the same as every other generation figure.
    """

    def base(line_name: str, database: str) -> Query:
        extra = (
            f"AND {period.contains('R.issued_at')}\n"
            f"      AND R.status_id IN {cfg.status.sql('generated')}\n"
            f"      AND R.installment_no = {FIRST_INSTALLMENT_NO}"
        )
        sql = f"""
SELECT R.agreement_id, R.portfolio_id, R.amount, DA.portfolio_label, '{line_name}' AS product_line
FROM {cfg.dedup(database, "R", extra)} AS R
INNER JOIN {cfg.table(database, "agents", "U")} ON R.agent_id = U.agent_id
{cfg.portfolio_label_lookup(database, "R", outer=True)}
WHERE R.rn = 1
      AND DA.portfolio_label IS NOT NULL
      {cfg.excluded_agents()}"""
        return Query(sql)

    inner = cfg.across_lines(line, base)
    sql = f"""
SELECT portfolio_label,
       COUNT(DISTINCT CONCAT(product_line, '|', portfolio_id, '|', agreement_id)) AS agreement_count,
       COALESCE(SUM(amount), 0) AS agreement_value
FROM (
{inner.sql}
) AS T
GROUP BY portfolio_label
ORDER BY agreement_value DESC"""
    return Query(sql, inner.params)
