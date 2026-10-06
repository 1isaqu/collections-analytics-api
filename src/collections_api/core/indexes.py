"""Recommended indexes and the optional admin route that creates them.

This is the only code path in the service that writes to the database, and it is
gated twice: ``ENABLE_INDEX_ADMIN`` must be on, and ``dry_run`` defaults to true. The
index list is written against the tables of the data contract; adjust it if your
physical schema differs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from collections_api.core.database import ConnectionPool
from collections_api.core.errors import ApiError
from collections_api.core.sql import quote_ident

FILLFACTOR = 90


@dataclass(frozen=True)
class IndexSpec:
    name: str
    table: str
    key_columns: tuple[str, ...]
    include_columns: tuple[str, ...]
    purpose: str


RECOMMENDED_INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec(
        "ix_agreements_issued_at",
        "agreements",
        ("issued_at",),
        (
            "agreement_id",
            "portfolio_id",
            "installment_no",
            "agent_id",
            "status_id",
            "plan_installments",
            "amount",
            "due_at",
            "paid_at",
            "paid_amount",
            "row_id",
        ),
        "Agreements issued inside a window: the base of most routes.",
    ),
    IndexSpec(
        "ix_agreements_agreement",
        "agreements",
        ("agreement_id", "portfolio_id"),
        ("installment_no", "amount", "status_id", "issued_at"),
        "Per-agreement aggregation and the detail routes' join-once subqueries.",
    ),
    IndexSpec(
        "ix_agreements_due_at",
        "agreements",
        ("due_at",),
        (
            "status_id",
            "installment_no",
            "amount",
            "paid_amount",
            "paid_at",
            "agent_id",
            "agreement_id",
            "portfolio_id",
            "issued_at",
            "row_id",
        ),
        "Effectiveness: slips due in a window, paid in grace, break curve.",
    ),
    IndexSpec(
        "ix_agreements_paid_at",
        "agreements",
        ("paid_at",),
        (
            "status_id",
            "installment_no",
            "amount",
            "paid_amount",
            "due_at",
            "agent_id",
            "agreement_id",
            "portfolio_id",
            "issued_at",
            "row_id",
        ),
        "Cash received inside a window.",
    ),
    IndexSpec(
        "ix_agreement_debts_agreement",
        "agreement_debts",
        ("agreement_id", "portfolio_id"),
        ("debt_id",),
        "Portfolio resolution: composite key used by every apply and exists lookup.",
    ),
    IndexSpec(
        "ix_debt_attributes_debt",
        "debt_attributes",
        ("debt_id",),
        ("portfolio_label",),
        "Portfolio label lookup by debt.",
    ),
    IndexSpec(
        "ix_debts_debt",
        "debts",
        ("debt_id",),
        ("balance", "due_at"),
        "Original balance and debt age behind the discount and age averages.",
    ),
    IndexSpec(
        "ix_contact_events_occurred_at",
        "contact_events",
        ("occurred_at",),
        ("agent_id", "result_id", "debtor_id", "event_id"),
        "Outreach attempts in a window (productivity and benchmarks).",
    ),
    IndexSpec(
        "ix_agents_agent",
        "agents",
        ("agent_id",),
        ("display_name", "login"),
        "Agent join plus the exclusion filter on login and display name.",
    ),
)


def build_create_index_sql(schema: str, spec: IndexSpec, *, online: bool) -> str:
    """``CREATE NONCLUSTERED INDEX`` for ``spec``. ``online`` needs an edition that supports it."""
    keys = ", ".join(quote_ident(column) for column in spec.key_columns)
    includes = ", ".join(quote_ident(column) for column in spec.include_columns)
    include_clause = f"\n    INCLUDE ({includes})" if includes else ""
    options = f"FILLFACTOR = {FILLFACTOR}, ONLINE = {'ON' if online else 'OFF'}"
    return (
        f"CREATE NONCLUSTERED INDEX {quote_ident(spec.name)}\n"
        f"    ON {quote_ident(schema)}.{quote_ident(spec.table)} ({keys}){include_clause}\n"
        f"    WITH ({options})"
    )


def build_update_statistics_sql(schema: str, table: str) -> str:
    return f"UPDATE STATISTICS {quote_ident(schema)}.{quote_ident(table)} WITH FULLSCAN"


_EXISTS_SQL = (
    "SELECT 1 FROM sys.indexes i "
    "JOIN sys.objects o ON i.object_id = o.object_id "
    "JOIN sys.schemas s ON o.schema_id = s.schema_id "
    "WHERE s.name = ? AND o.name = ? AND i.name = ?"
)


def index_exists(cursor: Any, schema: str, spec: IndexSpec) -> bool:
    cursor.execute(_EXISTS_SQL, (schema, spec.table, spec.name))
    return cursor.fetchone() is not None


def list_index_status(pool: ConnectionPool, database: str, schema: str) -> list[dict[str, Any]]:
    """For each recommended index, whether it already exists in ``database``."""
    result: list[dict[str, Any]] = []
    try:
        with pool.connection(database) as conn:
            cursor = conn.cursor()
            for spec in RECOMMENDED_INDEXES:
                result.append(
                    {
                        "name": spec.name,
                        "table": f"{schema}.{spec.table}",
                        "key_columns": list(spec.key_columns),
                        "include_columns": list(spec.include_columns),
                        "purpose": spec.purpose,
                        "exists": index_exists(cursor, schema, spec),
                    }
                )
            cursor.close()
    except pool.error_types as exc:
        raise ApiError(500, "Could not inspect the indexes.") from exc
    return result


def apply_indexes(
    pool: ConnectionPool,
    database: str,
    schema: str,
    *,
    online: bool,
    dry_run: bool,
    update_statistics: bool,
) -> dict[str, Any]:
    """Create the recommended indexes that are missing.

    ``dry_run`` returns the statements without executing any. A failed statement is
    reported on its own step and does not stop the others. Statistics are refreshed
    only for tables that gained an index (every table in a dry run).
    """
    started = time.perf_counter()
    steps: list[dict[str, Any]] = []
    stat_steps: list[dict[str, Any]] = []
    touched: set[str] = set()
    try:
        with pool.connection(database) as conn:
            cursor = conn.cursor()
            for spec in RECOMMENDED_INDEXES:
                exists = index_exists(cursor, schema, spec)
                sql = build_create_index_sql(schema, spec, online=online)
                action = (
                    "skipped_existing" if exists else ("would_create" if dry_run else "created")
                )
                step: dict[str, Any] = {
                    "name": spec.name,
                    "table": f"{schema}.{spec.table}",
                    "sql": sql,
                    "action": action,
                    "error": None,
                }
                if not exists and not dry_run:
                    try:
                        cursor.execute(sql)
                        conn.commit()
                        touched.add(spec.table)
                    except pool.error_types as exc:
                        step["action"], step["error"] = "failed", str(exc)
                steps.append(step)

            if update_statistics:
                tables = {s.table for s in RECOMMENDED_INDEXES} if dry_run else touched
                for table in sorted(tables):
                    sql = build_update_statistics_sql(schema, table)
                    stat: dict[str, Any] = {
                        "target": f"{schema}.{table}",
                        "sql": sql,
                        "action": "would_update" if dry_run else "updated",
                        "error": None,
                    }
                    if not dry_run:
                        try:
                            cursor.execute(sql)
                            conn.commit()
                        except pool.error_types as exc:
                            stat["action"], stat["error"] = "failed", str(exc)
                    stat_steps.append(stat)
            cursor.close()
    except pool.error_types as exc:
        raise ApiError(500, "Could not apply the indexes.") from exc

    return {
        "database": database,
        "dry_run": dry_run,
        "online": online,
        "update_statistics": update_statistics,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "indexes": steps,
        "statistics": stat_steps,
    }
