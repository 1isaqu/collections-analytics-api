"""Agreement lists and the per-agent performance table.

No debtor identity is read or returned: the contract has no table for it.
"""

from __future__ import annotations

from datetime import date

from collections_api.core.sql import (
    ALL_LINES,
    FIRST_INSTALLMENT_NO,
    Period,
    Query,
    QueryConfig,
    date_literal,
    team_filter,
    team_params,
)

AGREEMENTS_TODAY_COLUMNS = (
    "agent_name",
    "agreement_id",
    "portfolio_id",
    "current_debt_value",
    "agreement_total_value",
    "discount_granted",
    "installment_count",
    "installment_no",
    "issued_at",
    "due_at",
    "installment_amount",
    "status_label",
    "paid_at",
    "payment_state",
)

AGREEMENTS_TABLE_COLUMNS = (
    "agent_name",
    "agreement_id",
    "portfolio_id",
    "agreement_type",
    "is_exception",
    "first_installment_due",
    "first_installment_value",
    "later_installment_value",
    "installment_count",
    "agreement_total_value",
    "issued_at",
)

PERFORMANCE_TABLE_COLUMNS = (
    "agent_name",
    "attempt_count",
    "agreement_count",
    "slips_issued",
    "slips_paid",
    "conversion_pct",
    "paid_per_cpc_pct",
    "agreement_value",
    "first_installment_value",
    "rejected_count",
    "answer_count",
    "cpc_count",
    "cpc_rate_pct",
    "exception_count",
    "exception_value",
)


def agreements_today(cfg: QueryConfig, database: str, day: date, offset: int, limit: int) -> Query:
    """Installments of the approved agreements issued on ``day`` in one database.

    Paged with ``OFFSET ... FETCH``; ``COUNT(*) OVER()`` carries the grand total on
    every row. Parameters: offset, then limit.
    """
    period = Period.day(day)
    issued_today = (
        f"AND {period.contains('RM.issued_at')} AND RM.status_id IN {cfg.status.sql('approved')}"
    )
    rows_today = cfg.dedup(database, "RM", issued_today)
    todays = f"""SELECT DISTINCT RT.agreement_id, RT.portfolio_id
    FROM {cfg.table(database, "agreements", "RT")}
    WHERE {period.contains("RT.issued_at")}"""
    sql = f"""
WITH todays AS (
    {todays}
),
totals AS (
    -- Sum of every installment of the agreement, after de-duplication.
    SELECT RM.agreement_id, RM.portfolio_id, SUM(RM.amount) AS total_value
    FROM {cfg.dedup(database, "RM")} AS RM
    WHERE RM.rn = 1
      AND EXISTS (SELECT 1 FROM todays T
                  WHERE T.agreement_id = RM.agreement_id AND T.portfolio_id = RM.portfolio_id)
    GROUP BY RM.agreement_id, RM.portfolio_id
),
balances AS (
    -- Current balance of the debts the agreement covers.
    SELECT RD.agreement_id, RD.portfolio_id, SUM(ISNULL(DM.balance, 0)) AS current_balance
    FROM {cfg.table(database, "agreement_debts", "RD")}
    JOIN {cfg.table(database, "debts", "DM")} ON RD.debt_id = DM.debt_id
    WHERE EXISTS (SELECT 1 FROM todays T
                  WHERE T.agreement_id = RD.agreement_id AND T.portfolio_id = RD.portfolio_id)
    GROUP BY RD.agreement_id, RD.portfolio_id
)
SELECT COUNT(*) OVER () AS _total_rows,
       U.display_name AS agent_name,
       RM.agreement_id,
       RM.portfolio_id,
       ISNULL(SD.current_balance, 0) AS current_debt_value,
       ISNULL(TA.total_value, 0) AS agreement_total_value,
       ISNULL(SD.current_balance, 0) - ISNULL(TA.total_value, 0) AS discount_granted,
       RM.plan_installments AS installment_count,
       RM.installment_no,
       RM.issued_at,
       RM.due_at,
       RM.amount AS installment_amount,
       RS.label AS status_label,
       RM.paid_at,
       CASE WHEN RM.paid_at IS NOT NULL THEN 'PAID' ELSE 'OPEN' END AS payment_state
FROM {rows_today} AS RM
JOIN {cfg.table(database, "agents", "U")} ON RM.agent_id = U.agent_id
LEFT JOIN {cfg.table(database, "agreement_statuses", "RS")} ON RM.status_id = RS.status_id
LEFT JOIN totals TA ON RM.agreement_id = TA.agreement_id AND RM.portfolio_id = TA.portfolio_id
LEFT JOIN balances SD ON RM.agreement_id = SD.agreement_id AND RM.portfolio_id = SD.portfolio_id
WHERE RM.rn = 1
      {cfg.excluded_agents()}
ORDER BY RM.issued_at DESC, RM.agreement_id, RM.installment_no
OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"""
    return Query(sql, (offset, limit))


def agreements_table(
    cfg: QueryConfig, line: str, day: date, team: str | None, agent_name: str | None
) -> tuple[Query, str]:
    """One row per agreement issued on ``day``, exceptions last.

    Bind order: for each line in scope the team token twice, then the agent name if an
    agent filter is set. Returns the query and the team label applied.
    """
    label, token, clause = team_filter(team)
    period = Period.day(day)

    def base(line_name: str, database: str) -> Query:
        extra = f"AND {period.contains('RM.issued_at')} AND RM.status_id IN {cfg.status.sql('universe')}"
        sql = f"""
SELECT U.display_name AS agent_name,
       RM.agreement_id,
       RM.portfolio_id,
       RS.label AS agreement_type,
       MAX(CASE WHEN RM.status_id IN {cfg.status.sql("exception")} THEN 1 ELSE 0 END) AS is_exception,
       MAX(CASE WHEN RM.installment_no = {FIRST_INSTALLMENT_NO} THEN RM.due_at END)
           AS first_installment_due,
       MAX(CASE WHEN RM.installment_no = {FIRST_INSTALLMENT_NO} THEN RM.amount END)
           AS first_installment_value,
       MAX(CASE WHEN RM.installment_no = {FIRST_INSTALLMENT_NO + 1} THEN RM.amount END)
           AS later_installment_value,
       MAX(RM.plan_installments) AS installment_count,
       SUM(RM.amount) AS agreement_total_value,
       MAX(RM.issued_at) AS issued_at
FROM {cfg.dedup(database, "RM", extra)} AS RM
JOIN {cfg.table(database, "agents", "U")} ON RM.agent_id = U.agent_id
LEFT JOIN {cfg.table(database, "agreement_statuses", "RS")} ON RM.status_id = RS.status_id
WHERE RM.rn = 1
      {cfg.excluded_agents()}
      {clause}
GROUP BY U.display_name, RM.agreement_id, RM.portfolio_id, RM.status_id, RS.label"""
        return Query(sql, team_params(token))

    inner = cfg.across_lines(line, base)
    where = "WHERE base.agent_name = ?\n" if agent_name else ""
    order = "ORDER BY base.is_exception, base.agent_name, base.agreement_id"
    if line != ALL_LINES:
        order = "ORDER BY base.is_exception, base.agreement_id"
    columns = ",\n       ".join(f"base.{c}" for c in AGREEMENTS_TABLE_COLUMNS)
    sql = f"WITH base AS (\n{inner.sql}\n)\nSELECT {columns}\nFROM base\n{where}{order}"
    params = inner.params + ((agent_name,) if agent_name else ())
    return Query(sql, params), label


def performance_table(
    cfg: QueryConfig, line: str, period: Period, today: date, agent_name: str | None
) -> Query:
    """Per-agent effort and results over a period.

    ``conversion_pct`` is agreements over right-party contacts (CPC). ``paid_per_cpc_pct``
    is slips paid in grace over CPC: a different metric that must not share the name.
    The line-wide variant sums counts and recomputes the ratios, never averaging them.
    """

    def base(_line: str, database: str) -> Query:
        window_a = period.contains("CM.occurred_at")
        window_r = period.contains("RM.issued_at")

        def rows_with(status_set: str) -> str:
            statuses = cfg.status.sql(status_set)
            return cfg.dedup(database, "RM", f"AND {window_r} AND RM.status_id IN {statuses}")

        sql = f"""
SELECT U.display_name AS agent_name,
       ISNULL(A.attempt_count, 0) AS attempt_count,
       ISNULL(A.answer_count, 0) AS answer_count,
       ISNULL(A.cpc_count, 0) AS cpc_count,
       ISNULL(AC.agreement_count, 0) AS agreement_count,
       ISNULL(AC.slips_issued, 0) AS slips_issued,
       ISNULL(AC.slips_paid, 0) AS slips_paid,
       ISNULL(AC.agreement_value, 0) AS agreement_value,
       ISNULL(AC.first_installment_value, 0) AS first_installment_value,
       ISNULL(RJ.rejected_count, 0) AS rejected_count,
       ISNULL(EX.exception_count, 0) AS exception_count,
       ISNULL(EX.exception_value, 0) AS exception_value
FROM {cfg.table(database, "agents", "U")}
LEFT JOIN (
    SELECT CM.agent_id,
           COUNT(DISTINCT CM.debt_owner_id) AS attempt_count,
           COUNT(DISTINCT CASE WHEN CC.answered = 1 THEN CM.debt_owner_id END) AS answer_count,
           COUNT(DISTINCT CASE WHEN CC.answered = 1 AND CC.right_party = 1 THEN CM.debt_owner_id END)
               AS cpc_count
    FROM {cfg.table(database, "contact_events", "CM")}
    LEFT JOIN {cfg.table(database, "contact_results", "CC")} ON CM.result_id = CC.result_id
    WHERE {window_a}
    GROUP BY CM.agent_id
) AS A ON A.agent_id = U.agent_id
LEFT JOIN (
    SELECT RM.agent_id,
           COUNT(DISTINCT RM.agreement_id) AS agreement_count,
           SUM(CASE WHEN RM.due_at < {date_literal(today)} THEN 1 ELSE 0 END) AS slips_issued,
           SUM(CASE WHEN RM.due_at < {date_literal(today)} THEN {cfg.paid_in_grace("RM")} ELSE 0 END)
               AS slips_paid,
           SUM(RM.amount) AS agreement_value,
           SUM(CASE WHEN RM.installment_no = {FIRST_INSTALLMENT_NO} THEN RM.amount ELSE 0 END)
               AS first_installment_value
    FROM {rows_with("generated")} AS RM
    WHERE RM.rn = 1
    GROUP BY RM.agent_id
) AS AC ON AC.agent_id = U.agent_id
LEFT JOIN (
    SELECT RM.agent_id, COUNT(DISTINCT RM.agreement_id) AS rejected_count
    FROM {rows_with("rejected")} AS RM
    WHERE RM.rn = 1
    GROUP BY RM.agent_id
) AS RJ ON RJ.agent_id = U.agent_id
LEFT JOIN (
    SELECT RM.agent_id,
           COUNT(DISTINCT RM.agreement_id) AS exception_count,
           SUM(RM.amount) AS exception_value
    FROM {rows_with("exception")} AS RM
    WHERE RM.rn = 1
    GROUP BY RM.agent_id
) AS EX ON EX.agent_id = U.agent_id
WHERE (ISNULL(A.attempt_count, 0) > 0 OR ISNULL(AC.agreement_count, 0) > 0
       OR ISNULL(RJ.rejected_count, 0) > 0 OR ISNULL(EX.exception_count, 0) > 0)
      {cfg.excluded_agents()}"""
        return Query(sql)

    inner = cfg.across_lines(line, base)
    where = "WHERE agent_name = ?\n" if agent_name else ""
    params = inner.params + ((agent_name,) if agent_name else ())
    # One line: the raw rows already have one row per agent. All lines: sum the counts
    # per agent and derive the ratios from the sums, never average per-line ratios.
    combine = line == ALL_LINES

    def col(name: str) -> str:
        return f"SUM({name})" if combine else name

    def ratio(numerator: str, denominator: str, precision: int) -> str:
        return (
            f"CAST(CASE WHEN {col(denominator)} > 0\n"
            f"                 THEN {col(numerator)} * 100.0 / {col(denominator)} ELSE 0.0 END\n"
            f"            AS DECIMAL({precision}, 1))"
        )

    sql = (
        f"WITH raw AS (\n{inner.sql}\n)\n"
        "SELECT agent_name,\n"
        f"       {col('attempt_count')} AS attempt_count,\n"
        f"       {col('agreement_count')} AS agreement_count,\n"
        f"       {col('slips_issued')} AS slips_issued,\n"
        f"       {col('slips_paid')} AS slips_paid,\n"
        f"       {ratio('agreement_count', 'cpc_count', 6)} AS conversion_pct,\n"
        f"       {ratio('slips_paid', 'cpc_count', 6)} AS paid_per_cpc_pct,\n"
        f"       CAST({col('agreement_value')} AS DECIMAL(18, 2)) AS agreement_value,\n"
        f"       CAST({col('first_installment_value')} AS DECIMAL(18, 2)) AS first_installment_value,\n"
        f"       {col('rejected_count')} AS rejected_count,\n"
        f"       {col('answer_count')} AS answer_count,\n"
        f"       {col('cpc_count')} AS cpc_count,\n"
        f"       {ratio('cpc_count', 'answer_count', 5)} AS cpc_rate_pct,\n"
        f"       {col('exception_count')} AS exception_count,\n"
        f"       CAST({col('exception_value')} AS DECIMAL(18, 2)) AS exception_value\n"
        f"FROM raw\n{where}"
        + ("GROUP BY agent_name\n" if combine else "")
        + f"ORDER BY {col('attempt_count')} DESC"
    )
    return Query(sql, params)
