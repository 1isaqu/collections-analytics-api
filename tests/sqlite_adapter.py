"""Run a subset of the generated T-SQL against in-memory SQLite.

``adapt`` rewrites syntax only: three-part names, table hints, date literals and a
handful of T-SQL functions. It never touches business logic, so a test that executes
the adapted statement exercises the production query text. Constructs with no SQLite
equivalent (``CROSS``/``OUTER APPLY``, ``TOP``, ``OFFSET ... FETCH``) are not
translated: queries that use them are covered by shape tests instead.

Every value inserted by the tests is invented.
"""

from __future__ import annotations

import dataclasses
import math
import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from collections_api.core.sql import QueryConfig

SCHEMA = """
CREATE TABLE agreements (
    row_id INTEGER, agreement_id INTEGER, portfolio_id INTEGER, installment_no INTEGER,
    plan_installments INTEGER, amount REAL, paid_amount REAL, issued_at TEXT, due_at TEXT,
    paid_at TEXT, broken_at TEXT, status_id INTEGER, agent_id INTEGER
);
CREATE TABLE agreement_debts (agreement_id INTEGER, portfolio_id INTEGER, debt_id INTEGER);
CREATE TABLE debts (
    debt_id INTEGER, portfolio_id INTEGER, contract_no TEXT, balance REAL, due_at TEXT,
    days_overdue INTEGER
);
CREATE TABLE debt_attributes (debt_id INTEGER, portfolio_label TEXT);
CREATE TABLE contact_events (
    event_id INTEGER, debt_owner_id INTEGER, result_id INTEGER,
    agent_id INTEGER, occurred_at TEXT
);
CREATE TABLE contact_results (result_id INTEGER, label TEXT, answered INTEGER, right_party INTEGER);
CREATE TABLE agreement_statuses (status_id INTEGER, label TEXT);
CREATE TABLE agents (agent_id INTEGER, display_name TEXT, login TEXT);
"""

ACTIVE, BROKEN, PAID, PENDING_EXCEPTION, REJECTED, AUTO_BROKEN, PAID_MANUAL = (
    21,
    22,
    23,
    24,
    25,
    26,
    27,
)


_OFFSET_FETCH = re.compile(r"OFFSET \? ROWS FETCH NEXT \? ROWS ONLY")


def adapt(sql: str) -> str:
    """T-SQL to SQLite, syntax only."""
    sql = _OFFSET_FETCH.sub("LIMIT ? OFFSET ?", sql)
    sql = re.sub(r"\b\w+\.dbo\.", "", sql)
    sql = sql.replace(" WITH (NOLOCK)", "")
    sql = re.sub(r"\n?OPTION \([^)]*\)\s*$", "", sql.rstrip())
    sql = re.sub(r"CAST\('(\d{4})(\d{2})(\d{2})' AS DATE\)", r"'\1-\2-\3'", sql)
    sql = re.sub(r"CAST\(([\w.]+) AS DATE\)", r"date(\1)", sql)
    sql = re.sub(r"\bISNULL\(", "IFNULL(", sql)
    sql = re.sub(
        r"CONCAT\(([^()]*)\)",
        lambda m: "(" + " || ".join(part.strip() for part in m.group(1).split(",")) + ")",
        sql,
    )
    sql = re.sub(
        r"DATEADD\(DAY,\s*(-?\d+),\s*([\w.]+)\)",
        lambda m: (
            f"date({m.group(2)}, '{'+' if not m.group(1).startswith('-') else ''}{m.group(1)} day')"
        ),
        sql,
    )
    sql = re.sub(
        r"DATEDIFF\(DAY,\s*([\w.]+),\s*('[\d-]+'|[\w.]+)\)",
        r"CAST(julianday(\2) - julianday(\1) AS INTEGER)",
        sql,
    )
    sql = re.sub(
        r"DATEDIFF\(SECOND,\s*([\w.]+),\s*([\w.]+)\)",
        r"CAST((julianday(\2) - julianday(\1)) * 86400 AS INTEGER)",
        sql,
    )
    sql = re.sub(r"CONVERT\(varchar\(10\),\s*([\w.]+),\s*120\)", r"substr(\1, 1, 10)", sql)
    sql = re.sub(r"\bYEAR\(([\w.]+)\)", r"CAST(strftime('%Y', \1) AS INTEGER)", sql)
    sql = re.sub(r"\bMONTH\(([\w.]+)\)", r"CAST(strftime('%m', \1) AS INTEGER)", sql)
    return sql


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.create_function("CEILING", 1, lambda x: None if x is None else math.ceil(x))
    conn.create_function("FLOOR", 1, lambda x: None if x is None else math.floor(x))
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def insert(conn: sqlite3.Connection, table: str, rows: Iterable[Mapping[str, Any]]) -> None:
    for row in rows:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))


def run(conn: sqlite3.Connection, query: Any) -> list[dict[str, Any]]:
    """Execute a ``Query`` (adapted) and return dict rows."""
    params = query.params
    if _OFFSET_FETCH.search(query.sql):
        # T-SQL takes (offset, limit); SQLite's LIMIT comes first.
        params = (*params[:-2], params[-1], params[-2])
    cursor = conn.execute(adapt(query.sql), params)
    return [dict(row) for row in cursor.fetchall()]


def installment(**overrides: Any) -> dict[str, Any]:
    """One agreements row with sensible invented defaults."""
    row: dict[str, Any] = {
        "row_id": 1,
        "agreement_id": 5001,
        "portfolio_id": 1,
        "installment_no": 0,
        "plan_installments": 1,
        "amount": 1000.0,
        "paid_amount": 0.0,
        "issued_at": "2026-03-18 09:00:00",
        "due_at": "2026-03-10 00:00:00",
        "paid_at": None,
        "broken_at": None,
        "status_id": ACTIVE,
        "agent_id": 1,
    }
    row.update(overrides)
    return row


def with_agents(conn: sqlite3.Connection, *agents: tuple[int, str, str]) -> None:
    insert(
        conn,
        "agents",
        [{"agent_id": a, "display_name": name, "login": login} for a, name, login in agents],
    )


def sqlite_config(cfg: QueryConfig) -> QueryConfig:
    """``cfg`` with the portfolio lookup rewritten as a join.

    SQLite has no ``CROSS``/``OUTER APPLY``. The replacement picks one label per
    (agreement, portfolio) with ``MIN``, which is what ``TOP 1`` yields when an
    agreement's debts carry a single label, as they do in the test data.
    """
    values = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg) if f.init}
    return _SqliteQueryConfig(**values)


@dataclasses.dataclass(frozen=True)
class _SqliteQueryConfig(QueryConfig):
    def portfolio_label_lookup(self, database: str, agreement_alias: str, *, outer: bool) -> str:
        join = "LEFT JOIN" if outer else "JOIN"
        return (
            f"{join} (\n"
            "    SELECT RD.agreement_id, RD.portfolio_id, MIN(DA2.portfolio_label) AS portfolio_label\n"
            f"    FROM {self.table(database, 'agreement_debts', 'RD')}\n"
            f"    JOIN {self.table(database, 'debt_attributes', 'DA2')} ON RD.debt_id = DA2.debt_id\n"
            "    WHERE DA2.portfolio_label IS NOT NULL\n"
            "    GROUP BY RD.agreement_id, RD.portfolio_id\n"
            f") AS DA ON DA.agreement_id = {agreement_alias}.agreement_id\n"
            f"       AND DA.portfolio_id = {agreement_alias}.portfolio_id"
        )
