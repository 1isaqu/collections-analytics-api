"""An installment written twice must count once, and the filters must run before the ranking.

The upstream system can write the same installment again (same agreement, portfolio and
installment number). The queries keep the latest write. The business filters (window and
status) sit inside the ranked subquery: a re-write that falls outside the filter must not
win the rank and hide the valid row written before it.

Each test builds a tiny database of its own and runs the generated statements on SQLite.
"""

from __future__ import annotations

import pytest

from collections_api.core.sql import Period
from collections_api.domains.charts import queries as chart_queries
from collections_api.domains.effectiveness import queries as effectiveness_queries
from collections_api.domains.productivity import queries as productivity_queries
from tests import sqlite_adapter as sq
from tests.conftest import TODAY

DAY = Period.day(TODAY)
NEXT_DAY = "2026-03-19 09:00:00"


@pytest.fixture()
def conn():
    connection = sq.connect()
    sq.with_agents(connection, (1, "Agent 01", "agent_01"))
    sq.insert(
        connection, "agreement_debts", [{"agreement_id": 6001, "portfolio_id": 1, "debt_id": 1}]
    )
    sq.insert(connection, "debt_attributes", [{"debt_id": 1, "portfolio_label": "Portfolio A-01"}])
    sq.insert(
        connection,
        "debts",
        [
            {
                "debt_id": 1,
                "portfolio_id": 1,
                "contract_no": "C1",
                "balance": 4000.0,
                "due_at": "2025-12-01",
            }
        ],
    )
    return connection


def agent_row(conn, cfg, period=DAY):
    query = productivity_queries.productivity_query(cfg, "auto", period, TODAY)
    rows = sq.run(conn, query)
    return rows[0] if rows else None


def test_an_installment_written_twice_counts_once(conn, sqlite_cfg):
    sq.insert(
        conn,
        "agreements",
        [
            sq.installment(row_id=1, agreement_id=6001, amount=1000.0),
            sq.installment(row_id=2, agreement_id=6001, amount=1000.0),
        ],
    )
    row = agent_row(conn, sqlite_cfg)
    assert (row["agreement_count"], row["agreement_value"]) == (1, 1000.0)
    assert row["slips_issued"] == 1


def test_the_latest_write_wins_and_the_row_id_breaks_a_tie(conn, sqlite_cfg):
    sq.insert(
        conn,
        "agreements",
        [
            sq.installment(row_id=1, agreement_id=6001, amount=1000.0),
            sq.installment(row_id=2, agreement_id=6001, amount=900.0),  # same timestamp, higher id
            sq.installment(
                row_id=3, agreement_id=6001, amount=800.0, issued_at="2026-03-18 08:00:00"
            ),
        ],
    )
    assert agent_row(conn, sqlite_cfg)["agreement_value"] == 900.0


def test_a_rewrite_outside_the_window_does_not_hide_the_row_inside_it(conn, sqlite_cfg):
    sq.insert(
        conn,
        "agreements",
        [
            sq.installment(row_id=1, agreement_id=6001, amount=1000.0),
            sq.installment(row_id=2, agreement_id=6001, amount=1000.0, issued_at=NEXT_DAY),
        ],
    )
    assert agent_row(conn, sqlite_cfg)["agreement_count"] == 1


def test_a_rewrite_with_a_status_outside_every_set_does_not_hide_the_row(conn, sqlite_cfg):
    sq.insert(
        conn,
        "agreements",
        [
            sq.installment(row_id=1, agreement_id=6001, amount=1000.0),
            sq.installment(
                row_id=2,
                agreement_id=6001,
                amount=1000.0,
                status_id=99,
                issued_at="2026-03-18 15:00:00",
            ),
        ],
    )
    row = agent_row(conn, sqlite_cfg)
    assert row["agreement_count"] == 1 and row["rejected_count"] == 0


def test_chart_totals_and_details_use_the_same_ranking(conn, sqlite_cfg):
    sq.insert(
        conn,
        "agreements",
        [
            sq.installment(row_id=1, agreement_id=6001, amount=1000.0),
            sq.installment(row_id=2, agreement_id=6001, amount=1000.0, issued_at=NEXT_DAY),
        ],
    )
    summary, _ = chart_queries.first_installment_summary(sqlite_cfg, "auto", DAY, None)
    assert sq.run(conn, summary) == [{"first_installment_value": 1000.0, "agreement_count": 1}]


def test_effectiveness_counts_a_duplicated_slip_once(conn, sqlite_cfg):
    rows = [
        sq.installment(row_id=1, agreement_id=6001, due_at="2026-03-16 00:00:00"),
        sq.installment(row_id=2, agreement_id=6001, due_at="2026-03-16 00:00:00"),
    ]
    sq.insert(conn, "agreements", rows)
    kpi, daily = effectiveness_queries.summary_queries(
        sqlite_cfg,
        "auto",
        Period.between(TODAY.replace(day=1), TODAY.replace(day=31)),
        TODAY,
        "first",
    )
    assert sq.run(conn, kpi)[0]["slips_total"] == 1
    assert [d["slips_total"] for d in sq.run(conn, daily)] == [1]
    series = effectiveness_queries.series_query(sqlite_cfg, "auto", "daily-first")
    assert [r["slips_issued"] for r in sq.run(conn, series)] == [1]
