"""Per-agent productivity queries.

Two shapes exist on purpose:

* ``productivity_query``: one product line, effort counted as distinct debtors over the
  whole window, with rejected agreements counted per debt. Feeds the "today" route and
  the load-status check.
* ``agent_performance_query``: one line or all of them, effort counted per
  (agent, day, debtor), no rejected counts. Feeds the agent comparison route.

Both build their aggregates as separate CTEs and join them to the agent table, so a
join between two many-sided tables (contact events and agreements) can never multiply
rows. An agent with agreements but no contact events in the window still appears, with
zero effort.

Funnel definitions: an *answer* is a contact result flagged ``answered``; a *CPC*
(right-party contact) is ``answered AND right_party``.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from collections_api.core.sql import (
    ALL_LINES,
    FIRST_INSTALLMENT_NO,
    Period,
    Query,
    QueryConfig,
    date_literal,
)

PRODUCTIVITY_COLUMNS = (
    "agent_login",
    "agent_name",
    "attempt_count",
    "answer_count",
    "cpc_count",
    "cpc_rate_pct",
    "agreement_count",
    "agreement_count_by_contract",
    "slips_issued",
    "slips_paid",
    "agreements_per_attempt_pct",
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

AGENT_PERFORMANCE_COLUMNS = (
    "product_line",
    "agent_name",
    "agent_login",
    "attempt_count",
    "answer_count",
    "cpc_count",
    "agreement_count",
    "slips_issued",
    "slips_paid",
    "agreement_value",
    "avg_ticket",
    "avg_installments",
    "first_installment_value",
    "first_installment_received",
    "slip_effectiveness_pct",
    "cpc_rate_pct",
    "avg_balance_ratio_pct",
    "exception_count",
    "exception_value",
    "exception_first_installment_value",
)

BENCHMARK_COLUMNS = (
    "agent_login",
    "agent_name",
    "cpc_rate_pct",
    "conversion_pct",
    "cash_effectiveness_pct",
    "exception_pct",
    "active_days",
)

AGENT_EFFORT_COLUMNS = ("agent_login", "agent_name", "attempt_count", "answer_count", "cpc_count")

_PORTFOLIO_MARKER = "DA3.portfolio_label = ?"


def subtract_months(day: date, months: int) -> date:
    """``day`` minus ``months`` with the day clamped to the target month's length.

    Matches SQL Server's ``DATEADD(MONTH, -n, d)``: March 31 minus one month is
    February 28 (or 29).
    """
    index = day.month - 1 - months
    year = day.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _first_value(column: str) -> str:
    return f"MAX(CASE WHEN RM.installment_no = {FIRST_INSTALLMENT_NO} THEN RM.{column} ELSE 0 END)"


def _option(cfg: QueryConfig) -> str:
    return f"\n{cfg.productivity_option}" if cfg.productivity_option else ""


# --------------------------------------------------------------------------- one line


def productivity_query(
    cfg: QueryConfig,
    line: str,
    period: Period,
    today: date,
    portfolio: str | None = None,
) -> Query:
    """Per-agent KPIs for one product line.

    With ``portfolio`` set, agreement counts and values are restricted to agreements
    whose debts carry that portfolio label. Outreach effort cannot be attributed to a
    portfolio (an attempt belongs to a debtor, who may owe in several), so effort stays
    unfiltered and any ratio built on it is meaningless: the route nulls those.
    """
    if line == ALL_LINES:
        raise ValueError("productivity_query reads one product line; use agent_performance_query")
    database = cfg.connection_database(line)
    pf = cfg.portfolio_exists(database, "RM", "?") if portfolio else ""
    window_r = period.contains("RM.issued_at")
    generated = cfg.status.sql("generated")
    exception = cfg.status.sql("exception")

    def gen(expr: str, otherwise: str = "") -> str:
        return f"CASE WHEN A.status_id IN {generated} THEN {expr}{otherwise} END"

    def exc(expr: str, otherwise: str = "") -> str:
        return f"CASE WHEN A.status_id IN {exception} THEN {expr}{otherwise} END"

    def rows(statuses: str, extra: str = "") -> str:
        return cfg.dedup(
            database, "RM", f"AND {window_r} AND RM.status_id IN {statuses} {extra} {pf}"
        )

    sql = f"""
WITH acc AS (
    -- One row per agreement and status, totals over its de-duplicated installments.
    SELECT RM.agent_id, RM.agreement_id, RM.portfolio_id, RM.status_id,
           SUM(RM.amount) AS total_value,
           {_first_value("amount")} AS first_value,
           {_first_value("paid_amount")} AS first_paid,
           MAX(RM.plan_installments) AS plan_installments
    FROM {rows(cfg.status.sql("universe"))} AS RM
    WHERE RM.rn = 1
    GROUP BY RM.agent_id, RM.agreement_id, RM.portfolio_id, RM.status_id
),
orig AS (
    -- Balance of the debts each agreement covers, and the earliest debt due date.
    SELECT RD.agreement_id, RD.portfolio_id,
           SUM(ISNULL(DM.balance, 0)) AS original_balance,
           MIN(DM.due_at) AS earliest_debt_due
    FROM {cfg.table(database, "agreement_debts", "RD")}
    JOIN {cfg.table(database, "debts", "DM")} ON RD.debt_id = DM.debt_id
    WHERE EXISTS (
        SELECT 1 FROM {cfg.table(database, "agreements", "RM")}
        WHERE RM.agreement_id = RD.agreement_id AND RM.portfolio_id = RD.portfolio_id
          AND {window_r} {pf}
    )
    GROUP BY RD.agreement_id, RD.portfolio_id
),
fin AS (
    SELECT A.agent_id,
           COUNT({gen("1")}) AS agreement_count,
           SUM({gen("A.total_value", " ELSE 0")}) AS agreement_value,
           AVG({gen("A.total_value")}) AS avg_ticket,
           AVG({gen("CAST(A.plan_installments AS DECIMAL(10, 2))")}) AS avg_installments,
           AVG(CASE WHEN A.status_id IN {generated} AND S.original_balance > 0
                    THEN A.total_value / S.original_balance * 100 END) AS avg_balance_ratio_pct,
           SUM({gen("A.first_value", " ELSE 0")}) AS first_installment_value,
           SUM({gen("A.first_paid", " ELSE 0")}) AS first_installment_received,
           COUNT({exc("1")}) AS exception_count,
           SUM({exc("A.total_value", " ELSE 0")}) AS exception_value,
           SUM({exc("A.first_value", " ELSE 0")}) AS exception_first_installment_value,
           AVG({gen(f"DATEDIFF(DAY, S.earliest_debt_due, {date_literal(period.start)})")}) AS avg_debt_age_days
    FROM acc A
    LEFT JOIN orig S ON A.agreement_id = S.agreement_id AND A.portfolio_id = S.portfolio_id
    GROUP BY A.agent_id
),
effort AS (
    -- Distinct debtors reached, so a debtor called five times counts once.
    SELECT CM.agent_id,
           COUNT(DISTINCT CM.debt_owner_id) AS attempt_count,
           COUNT(DISTINCT CASE WHEN CC.answered = 1 THEN CM.debt_owner_id END) AS answer_count,
           COUNT(DISTINCT CASE WHEN CC.answered = 1 AND CC.right_party = 1
                               THEN CM.debt_owner_id END) AS cpc_count
    FROM {cfg.table(database, "contact_events", "CM")}
    LEFT JOIN {cfg.table(database, "contact_results", "CC")} ON CM.result_id = CC.result_id
    WHERE {period.contains("CM.occurred_at")}
    GROUP BY CM.agent_id
),
hours AS (
    -- Proxy for time worked: first to last generated agreement of each day, summed.
    SELECT agent_id, SUM(DATEDIFF(SECOND, day_min, day_max)) / 3600.0 AS hours_worked
    FROM (
        SELECT RM.agent_id, CAST(RM.issued_at AS DATE) AS work_day,
               MIN(RM.issued_at) AS day_min, MAX(RM.issued_at) AS day_max
        FROM {cfg.table(database, "agreements", "RM")}
        WHERE {window_r} AND RM.status_id IN {generated} {pf}
        GROUP BY RM.agent_id, CAST(RM.issued_at AS DATE)
    ) AS d
    GROUP BY agent_id
),
slips AS (
    -- Slips already due, and how many were paid inside the grace period.
    SELECT RM.agent_id, COUNT(*) AS slips_issued, SUM({cfg.paid_in_grace("RM")}) AS slips_paid
    FROM {rows(generated, f"AND RM.due_at < {date_literal(today)}")} AS RM
    WHERE RM.rn = 1
    GROUP BY RM.agent_id
),
contracts AS (
    -- Agreements counted per contract: one covering N debts counts N.
    SELECT RM.agent_id,
           COUNT(DISTINCT CONCAT(RM.agreement_id, '|', RM.portfolio_id, '|', RD.debt_id))
               AS agreement_count_by_contract
    FROM {rows(generated)} AS RM
    JOIN {cfg.table(database, "agreement_debts", "RD")}
      ON RD.agreement_id = RM.agreement_id AND RD.portfolio_id = RM.portfolio_id
    WHERE RM.rn = 1
    GROUP BY RM.agent_id
),
rej_acc AS (
    SELECT RM.agent_id, RM.agreement_id, RM.portfolio_id,
           SUM(RM.amount) AS total_value,
           {_first_value("amount")} AS first_value,
           MAX(RM.issued_at) AS last_issued
    FROM {rows(cfg.status.sql("rejected"))} AS RM
    WHERE RM.rn = 1
    GROUP BY RM.agent_id, RM.agreement_id, RM.portfolio_id
),
rej_debts AS (
    -- A debt rejected, resubmitted and rejected again produces two agreement numbers.
    -- Keep the latest agreement per debt so the debt is counted once.
    SELECT agent_id, agreement_id, portfolio_id, debt_id
    FROM (
        SELECT A.agent_id, A.agreement_id, A.portfolio_id, RD.debt_id,
               ROW_NUMBER() OVER (
                   PARTITION BY A.portfolio_id, RD.debt_id
                   ORDER BY A.last_issued DESC, A.agreement_id DESC
               ) AS rk
        FROM rej_acc A
        JOIN {cfg.table(database, "agreement_debts", "RD")}
          ON RD.agreement_id = A.agreement_id AND RD.portfolio_id = A.portfolio_id
    ) AS z
    WHERE z.rk = 1
),
rej AS (
    -- Count = distinct rejected debts. Value = once per surviving agreement.
    SELECT q.agent_id, q.rejected_count, v.rejected_value, v.rejected_first_installment_value
    FROM (SELECT agent_id, COUNT(*) AS rejected_count FROM rej_debts GROUP BY agent_id) AS q
    LEFT JOIN (
        SELECT A.agent_id,
               SUM(A.total_value) AS rejected_value,
               SUM(A.first_value) AS rejected_first_installment_value
        FROM rej_acc A
        WHERE EXISTS (SELECT 1 FROM rej_debts D
                      WHERE D.agreement_id = A.agreement_id AND D.portfolio_id = A.portfolio_id)
        GROUP BY A.agent_id
    ) AS v ON v.agent_id = q.agent_id
)
SELECT U.login AS agent_login,
       U.display_name AS agent_name,
       ISNULL(E.attempt_count, 0) AS attempt_count,
       ISNULL(E.answer_count, 0) AS answer_count,
       ISNULL(E.cpc_count, 0) AS cpc_count,
       CAST(CEILING(ISNULL(E.cpc_count, 0) * 100.0 / NULLIF(E.answer_count, 0)) AS INT)
           AS cpc_rate_pct,
       ISNULL(F.agreement_count, 0) AS agreement_count,
       ISNULL(CT.agreement_count_by_contract, 0) AS agreement_count_by_contract,
       ISNULL(SL.slips_issued, 0) AS slips_issued,
       ISNULL(SL.slips_paid, 0) AS slips_paid,
       CAST(ISNULL(F.agreement_count, 0) * 100.0 / NULLIF(E.attempt_count, 0) AS DECIMAL(5, 2))
           AS agreements_per_attempt_pct,
       CAST(ISNULL(F.agreement_value, 0) AS DECIMAL(18, 2)) AS agreement_value,
       CAST(ISNULL(F.avg_ticket, 0) AS DECIMAL(18, 2)) AS avg_ticket,
       CAST(ISNULL(F.avg_installments, 0) AS DECIMAL(10, 2)) AS avg_installments,
       CAST(ISNULL(F.avg_balance_ratio_pct, 0) AS DECIMAL(10, 2)) AS avg_balance_ratio_pct,
       CAST(ISNULL(F.first_installment_value, 0) AS DECIMAL(18, 2)) AS first_installment_value,
       CAST(ISNULL(F.first_installment_received, 0) AS DECIMAL(18, 2))
           AS first_installment_received,
       ISNULL(F.exception_count, 0) AS exception_count,
       CAST(ISNULL(F.exception_value, 0) AS DECIMAL(18, 2)) AS exception_value,
       CAST(ISNULL(F.exception_first_installment_value, 0) AS DECIMAL(18, 2))
           AS exception_first_installment_value,
       ISNULL(RJ.rejected_count, 0) AS rejected_count,
       CAST(ISNULL(RJ.rejected_value, 0) AS DECIMAL(18, 2)) AS rejected_value,
       CAST(ISNULL(RJ.rejected_first_installment_value, 0) AS DECIMAL(18, 2))
           AS rejected_first_installment_value,
       CAST(ISNULL(F.avg_debt_age_days, 0) AS DECIMAL(10, 1)) AS avg_debt_age_days,
       CAST(ISNULL(H.hours_worked, 0) AS DECIMAL(10, 2)) AS hours_worked
FROM {cfg.table(database, "agents", "U")}
LEFT JOIN effort E ON U.agent_id = E.agent_id
LEFT JOIN fin F ON U.agent_id = F.agent_id
LEFT JOIN hours H ON U.agent_id = H.agent_id
LEFT JOIN slips SL ON U.agent_id = SL.agent_id
LEFT JOIN contracts CT ON U.agent_id = CT.agent_id
LEFT JOIN rej RJ ON U.agent_id = RJ.agent_id
WHERE (E.agent_id IS NOT NULL OR F.agent_id IS NOT NULL)
      {cfg.excluded_agents()}
ORDER BY attempt_count DESC{_option(cfg)}"""
    return Query(sql, (portfolio,) * sql.count(_PORTFOLIO_MARKER) if portfolio else ())


# --------------------------------------------------------------------------- one or all lines


def agent_performance_query(cfg: QueryConfig, line: str, period: Period, today: date) -> Query:
    """Per-agent KPIs for one line or all, with a ``product_line`` column.

    The same agent login can exist in several lines and appears once per line; callers
    that want one row per person unify on ``normalize_agent_key``.
    """

    def per_line(build) -> str:
        return cfg.across_lines(line, lambda name, database: Query(build(name, database))).sql

    users = per_line(
        lambda name, db: (
            f"SELECT agent_id, login, display_name, '{name}' AS product_line "
            f"FROM {cfg.table(db, 'agents', 'AG')}"
        )
    )
    contacts = per_line(
        lambda name, db: (
            f"""SELECT CM.agent_id, CM.debt_owner_id, CM.occurred_at,
       CASE WHEN CC.answered = 1 AND CC.right_party = 1 THEN 1 ELSE 0 END AS cpc_flag,
       CASE WHEN CC.answered = 1 THEN 1 ELSE 0 END AS answer_flag,
       '{name}' AS product_line
FROM {cfg.table(db, "contact_events", "CM")}
LEFT JOIN {cfg.table(db, "contact_results", "CC")} ON CM.result_id = CC.result_id
WHERE {period.contains("CM.occurred_at")}"""
        )
    )
    agreements = per_line(
        lambda name, db: (
            f"""SELECT row_id, agent_id, agreement_id, portfolio_id, installment_no, status_id,
       plan_installments, amount, paid_amount, paid_at, due_at, issued_at, '{name}' AS product_line
FROM {cfg.table(db, "agreements", "AG")}
WHERE {period.contains("AG.issued_at")}"""
        )
    )
    links = per_line(
        lambda name, db: (
            f"SELECT agreement_id, portfolio_id, debt_id, '{name}' AS product_line "
            f"FROM {cfg.table(db, 'agreement_debts', 'AG')}"
        )
    )
    debts = per_line(
        lambda name, db: (
            f"SELECT debt_id, balance, '{name}' AS product_line FROM {cfg.table(db, 'debts', 'AG')}"
        )
    )

    generated = cfg.status.sql("generated")
    exception = cfg.status.sql("exception")
    today_sql = date_literal(today)

    def gen(expr: str, otherwise: str = "") -> str:
        return f"CASE WHEN A.status_id IN {generated} THEN {expr}{otherwise} END"

    sql = f"""
WITH users AS ({users}),
effort_days AS (
    -- One row per (agent, line, day, debtor): a debtor called twice a day counts once.
    SELECT CM.agent_id, CM.product_line, CAST(CM.occurred_at AS DATE) AS work_day, CM.debt_owner_id,
           MAX(CM.cpc_flag) AS had_cpc, MAX(CM.answer_flag) AS had_answer
    FROM ({contacts}) AS CM
    GROUP BY CM.agent_id, CM.product_line, CAST(CM.occurred_at AS DATE), CM.debt_owner_id
),
effort AS (
    SELECT agent_id, product_line,
           COUNT(*) AS attempt_count, SUM(had_cpc) AS cpc_count, SUM(had_answer) AS answer_count
    FROM effort_days
    GROUP BY agent_id, product_line
),
unique_agreements AS (
    SELECT R.agent_id, R.product_line, R.agreement_id, R.portfolio_id, R.status_id,
           SUM(R.amount) AS total_value,
           MAX(CASE WHEN R.installment_no = {FIRST_INSTALLMENT_NO} THEN R.amount ELSE 0 END) AS first_value,
           MAX(CASE WHEN R.installment_no = {FIRST_INSTALLMENT_NO} THEN R.paid_amount ELSE 0 END) AS first_paid,
           MAX(R.plan_installments) AS plan_installments
    FROM (
        SELECT R.*,
               ROW_NUMBER() OVER (
                   PARTITION BY R.product_line, R.agreement_id, R.portfolio_id, R.installment_no
                   ORDER BY R.issued_at DESC, R.row_id DESC
               ) AS rn
        FROM ({agreements}) AS R
        WHERE R.status_id IN {cfg.status.sql("universe")}
    ) AS R
    WHERE R.rn = 1
    GROUP BY R.agent_id, R.product_line, R.agreement_id, R.portfolio_id, R.status_id
),
orig AS (
    SELECT RD.agreement_id, RD.portfolio_id, RD.product_line,
           SUM(ISNULL(DM.balance, 0)) AS original_balance
    FROM ({links}) AS RD
    JOIN ({debts}) AS DM ON RD.debt_id = DM.debt_id AND RD.product_line = DM.product_line
    WHERE EXISTS (
        SELECT 1 FROM unique_agreements A
        WHERE A.agreement_id = RD.agreement_id AND A.portfolio_id = RD.portfolio_id
          AND A.product_line = RD.product_line
    )
    GROUP BY RD.agreement_id, RD.portfolio_id, RD.product_line
),
fin AS (
    SELECT A.agent_id, A.product_line,
           COUNT({gen("A.agreement_id")}) AS agreement_count,
           SUM({gen("A.total_value", " ELSE 0")}) AS agreement_value,
           AVG({gen("A.total_value")}) AS avg_ticket,
           SUM({gen("A.first_value", " ELSE 0")}) AS first_installment_value,
           SUM({gen("A.first_paid", " ELSE 0")}) AS first_installment_received,
           AVG({gen("CAST(A.plan_installments AS DECIMAL(10, 2))")}) AS avg_installments,
           AVG(CASE WHEN A.status_id IN {generated} AND S.original_balance > 0
                    THEN A.total_value / S.original_balance * 100 END) AS avg_balance_ratio_pct,
           COUNT(CASE WHEN A.status_id IN {exception} THEN A.agreement_id END) AS exception_count,
           SUM(CASE WHEN A.status_id IN {exception} THEN A.total_value ELSE 0 END) AS exception_value,
           SUM(CASE WHEN A.status_id IN {exception} THEN A.first_value ELSE 0 END)
               AS exception_first_installment_value
    FROM unique_agreements A
    LEFT JOIN orig S ON A.agreement_id = S.agreement_id AND A.portfolio_id = S.portfolio_id
                    AND A.product_line = S.product_line
    GROUP BY A.agent_id, A.product_line
),
slips AS (
    -- Slips already due and how many were paid inside the grace period.
    SELECT R.agent_id, R.product_line, COUNT(*) AS slips_issued,
           SUM({cfg.paid_in_grace("R")}) AS slips_paid
    FROM (
        SELECT R.*,
               ROW_NUMBER() OVER (
                   PARTITION BY R.product_line, R.agreement_id, R.portfolio_id, R.installment_no
                   ORDER BY R.issued_at DESC, R.row_id DESC
               ) AS rn
        FROM ({agreements}) AS R
        WHERE R.status_id IN {generated} AND R.due_at < {today_sql}
    ) AS R
    WHERE R.rn = 1
    GROUP BY R.agent_id, R.product_line
)
SELECT U.product_line,
       U.display_name AS agent_name,
       U.login AS agent_login,
       ISNULL(E.attempt_count, 0) AS attempt_count,
       ISNULL(E.answer_count, 0) AS answer_count,
       ISNULL(E.cpc_count, 0) AS cpc_count,
       ISNULL(F.agreement_count, 0) AS agreement_count,
       ISNULL(SL.slips_issued, 0) AS slips_issued,
       ISNULL(SL.slips_paid, 0) AS slips_paid,
       CAST(ISNULL(F.agreement_value, 0) AS DECIMAL(18, 2)) AS agreement_value,
       CAST(ISNULL(F.avg_ticket, 0) AS DECIMAL(18, 2)) AS avg_ticket,
       CAST(ISNULL(F.avg_installments, 0) AS DECIMAL(10, 2)) AS avg_installments,
       CAST(ISNULL(F.first_installment_value, 0) AS DECIMAL(18, 2)) AS first_installment_value,
       CAST(ISNULL(F.first_installment_received, 0) AS DECIMAL(18, 2))
           AS first_installment_received,
       -- Slips paid over slips issued. This is slip effectiveness, not conversion.
       CAST(100.0 * ISNULL(SL.slips_paid, 0) / NULLIF(SL.slips_issued, 0) AS DECIMAL(18, 2))
           AS slip_effectiveness_pct,
       CAST(CEILING(ISNULL(E.cpc_count, 0) * 100.0 / NULLIF(E.answer_count, 0)) AS INT)
           AS cpc_rate_pct,
       CAST(ISNULL(F.avg_balance_ratio_pct, 0) AS DECIMAL(10, 2)) AS avg_balance_ratio_pct,
       ISNULL(F.exception_count, 0) AS exception_count,
       CAST(ISNULL(F.exception_value, 0) AS DECIMAL(18, 2)) AS exception_value,
       CAST(ISNULL(F.exception_first_installment_value, 0) AS DECIMAL(18, 2))
           AS exception_first_installment_value
FROM users U
LEFT JOIN effort E ON U.agent_id = E.agent_id AND U.product_line = E.product_line
LEFT JOIN fin F ON U.agent_id = F.agent_id AND U.product_line = F.product_line
LEFT JOIN slips SL ON U.agent_id = SL.agent_id AND U.product_line = SL.product_line
WHERE (E.agent_id IS NOT NULL OR F.agent_id IS NOT NULL)
      {cfg.excluded_agents()}
ORDER BY attempt_count DESC{_option(cfg)}"""
    return Query(sql)


# --------------------------------------------------------------------------- benchmarks


def benchmark_query(cfg: QueryConfig, line: str, today: date, lookback_months: int) -> Query:
    """Per-agent averages over a look-back window, input to the benchmark quartiles.

    A single line only: lines are different populations and mixing them would blur the
    quartiles. Days with fewer than ``benchmark_min_daily_attempts`` attempts and agents
    with fewer than ``benchmark_min_active_days`` qualifying days are left out, so a
    handful of tiny samples cannot move a quartile.
    """
    if line == ALL_LINES:
        raise ValueError("Benchmarks are computed per product line")
    database = cfg.connection_database(line)
    window = Period(subtract_months(today, lookback_months), today)
    window_r = window.contains("R.issued_at")
    extra = f"AND {window_r} AND R.status_id IN {cfg.status.sql('universe')}"
    generated = cfg.status.sql("generated")
    sql = f"""
WITH effort_days AS (
    SELECT CM.agent_id, CAST(CM.occurred_at AS DATE) AS work_day, CM.debt_owner_id,
           MAX(CASE WHEN CC.answered = 1 AND CC.right_party = 1 THEN 1 ELSE 0 END) AS had_cpc,
           MAX(CASE WHEN CC.answered = 1 THEN 1 ELSE 0 END) AS had_answer
    FROM {cfg.table(database, "contact_events", "CM")}
    LEFT JOIN {cfg.table(database, "contact_results", "CC")} ON CM.result_id = CC.result_id
    WHERE {window.contains("CM.occurred_at")}
    GROUP BY CM.agent_id, CAST(CM.occurred_at AS DATE), CM.debt_owner_id
),
effort_day AS (
    SELECT agent_id, work_day, COUNT(*) AS attempt_count,
           SUM(had_cpc) AS cpc_count, SUM(had_answer) AS answer_count
    FROM effort_days
    GROUP BY agent_id, work_day
    HAVING COUNT(*) >= {int(cfg.benchmark_min_daily_attempts)}
),
agreements_day AS (
    SELECT R.agent_id, CAST(R.issued_at AS DATE) AS work_day,
           SUM(R.amount) AS total_value,
           COUNT(DISTINCT CASE WHEN R.status_id IN {generated} THEN R.agreement_id END) AS agreement_count,
           SUM(CASE WHEN R.status_id IN {generated} AND R.installment_no = {FIRST_INSTALLMENT_NO}
                    THEN R.amount ELSE 0 END) AS first_value,
           SUM(CASE WHEN R.status_id IN {generated} AND R.installment_no = {FIRST_INSTALLMENT_NO}
                    THEN R.paid_amount ELSE 0 END) AS first_received,
           SUM(CASE WHEN R.status_id IN {cfg.status.sql("exception")} THEN R.amount ELSE 0 END)
               AS exception_value
    FROM {cfg.dedup(database, "R", extra)} AS R
    WHERE R.rn = 1
    GROUP BY R.agent_id, CAST(R.issued_at AS DATE)
)
SELECT U.login AS agent_login,
       U.display_name AS agent_name,
       SUM(E.cpc_count) * 100.0 / NULLIF(SUM(E.answer_count), 0) AS cpc_rate_pct,
       ISNULL(SUM(A.agreement_count), 0) * 100.0 / NULLIF(SUM(E.cpc_count), 0) AS conversion_pct,
       ISNULL(SUM(A.first_received), 0) * 100.0 / NULLIF(SUM(A.first_value), 0)
           AS cash_effectiveness_pct,
       ISNULL(SUM(A.exception_value), 0) * 100.0 / NULLIF(SUM(A.total_value), 0) AS exception_pct,
       COUNT(DISTINCT E.work_day) AS active_days
FROM effort_day E
JOIN {cfg.table(database, "agents", "U")} ON E.agent_id = U.agent_id
LEFT JOIN agreements_day A ON E.agent_id = A.agent_id AND E.work_day = A.work_day
WHERE 1 = 1
      {cfg.excluded_agents()}
GROUP BY U.login, U.display_name
HAVING COUNT(DISTINCT E.work_day) >= {int(cfg.benchmark_min_active_days)}
ORDER BY conversion_pct DESC"""
    return Query(sql)


# --------------------------------------------------------------------------- agent effort


def agent_effort_query(cfg: QueryConfig, database: str, day: date) -> Query:
    """Today's distinct-debtor effort per agent in one database."""
    period = Period(day, day + timedelta(days=1))
    sql = f"""
SELECT U.login AS agent_login,
       U.display_name AS agent_name,
       COUNT(DISTINCT CM.debt_owner_id) AS attempt_count,
       COUNT(DISTINCT CASE WHEN CC.answered = 1 THEN CM.debt_owner_id END) AS answer_count,
       COUNT(DISTINCT CASE WHEN CC.answered = 1 AND CC.right_party = 1
                           THEN CM.debt_owner_id END) AS cpc_count
FROM {cfg.table(database, "contact_events", "CM")}
JOIN {cfg.table(database, "agents", "U")} ON U.agent_id = CM.agent_id
LEFT JOIN {cfg.table(database, "contact_results", "CC")} ON CM.result_id = CC.result_id
WHERE {period.contains("CM.occurred_at")}
      {cfg.excluded_agents()}
GROUP BY U.login, U.display_name"""
    return Query(sql)


def normalize_agent_key(login: str | None, name: str | None) -> str:
    """Key that unifies one person across lines: the login if present, else the name."""
    return (login or "").strip().upper() or (name or "").strip().upper()
