"""Rejected agreements are counted per debt, not per agreement number.

A debt that is rejected, resubmitted and rejected again produces two agreement numbers.
Counting agreements would count the debt twice. The productivity query keeps the latest
rejected agreement per debt, credits that agreement's agent, and counts its value once.
"""

from __future__ import annotations

import pytest

from collections_api.core.sql import Period
from collections_api.domains.productivity import queries
from tests import sqlite_adapter as sq
from tests.conftest import TODAY

DAY = Period.day(TODAY)


@pytest.fixture()
def conn():
    """Two agents who each also made one approved agreement, so both show up in the result."""
    connection = sq.connect()
    sq.with_agents(connection, (1, "Agent 01", "agent_01"), (2, "Agent 02", "agent_02"))
    sq.insert(
        connection,
        "agreements",
        [
            sq.installment(row_id=1, agreement_id=9001, agent_id=1, amount=100.0),
            sq.installment(row_id=2, agreement_id=9002, agent_id=2, amount=100.0),
        ],
    )
    sq.insert(
        connection,
        "agreement_debts",
        [
            {"agreement_id": 9001, "portfolio_id": 1, "debt_id": 1},
            {"agreement_id": 9002, "portfolio_id": 1, "debt_id": 2},
        ],
    )
    sq.insert(
        connection,
        "debts",
        [
            {"debt_id": d, "portfolio_id": 1, "contract_no": f"C{d}", "balance": 1000.0, "due_at": "2025-12-01"}
            for d in (1, 2, 3, 4)
        ],
    )
    return connection


def reject(conn, agreement_id, agent_id, amount, issued_at, debts):
    sq.insert(
        conn,
        "agreements",
        [
            sq.installment(
                row_id=100 + agreement_id,
                agreement_id=agreement_id,
                agent_id=agent_id,
                amount=amount,
                status_id=sq.REJECTED,
                issued_at=issued_at,
            )
        ],
    )
    sq.insert(
        conn,
        "agreement_debts",
        [{"agreement_id": agreement_id, "portfolio_id": 1, "debt_id": d} for d in debts],
    )


def rows(conn, cfg):
    query = queries.productivity_query(cfg, "auto", DAY, TODAY)
    return {r["agent_name"]: r for r in sq.run(conn, query)}


def test_one_rejected_agreement_counts_its_debt_once(conn, sqlite_cfg):
    reject(conn, 8001, 1, 300.0, "2026-03-18 10:00:00", debts=[3])
    one = rows(conn, sqlite_cfg)["Agent 01"]
    assert (one["rejected_count"], one["rejected_value"]) == (1, 300.0)


def test_a_debt_rejected_twice_is_one_rejected_debt_with_the_latest_value(conn, sqlite_cfg):
    reject(conn, 8001, 1, 300.0, "2026-03-18 10:00:00", debts=[3])
    reject(conn, 8002, 1, 350.0, "2026-03-18 11:00:00", debts=[3])
    one = rows(conn, sqlite_cfg)["Agent 01"]
    assert one["rejected_count"] == 1  # per debt, not per agreement number
    assert one["rejected_value"] == 350.0  # the surviving agreement, once
    assert one["rejected_first_installment_value"] == 350.0


def test_the_agent_of_the_latest_rejection_gets_the_credit(conn, sqlite_cfg):
    reject(conn, 8001, 1, 300.0, "2026-03-18 10:00:00", debts=[3])
    reject(conn, 8002, 2, 350.0, "2026-03-18 11:00:00", debts=[3])
    result = rows(conn, sqlite_cfg)
    assert result["Agent 01"]["rejected_count"] == 0
    assert (result["Agent 02"]["rejected_count"], result["Agent 02"]["rejected_value"]) == (1, 350.0)


def test_one_agreement_over_two_debts_counts_both_debts_but_its_value_once(conn, sqlite_cfg):
    reject(conn, 8001, 1, 300.0, "2026-03-18 10:00:00", debts=[3, 4])
    one = rows(conn, sqlite_cfg)["Agent 01"]
    assert one["rejected_count"] == 2
    assert one["rejected_value"] == 300.0


def test_a_later_agreement_that_covers_only_one_of_the_debts_keeps_the_other(conn, sqlite_cfg):
    reject(conn, 8001, 1, 300.0, "2026-03-18 10:00:00", debts=[3, 4])
    reject(conn, 8002, 1, 120.0, "2026-03-18 11:00:00", debts=[3])
    one = rows(conn, sqlite_cfg)["Agent 01"]
    # Debt 3 now belongs to 8002; debt 4 stays with 8001. Two debts, two surviving agreements.
    assert one["rejected_count"] == 2
    assert one["rejected_value"] == 420.0


def test_without_a_rejection_the_counts_are_zero(conn, sqlite_cfg):
    one = rows(conn, sqlite_cfg)["Agent 01"]
    assert (one["rejected_count"], one["rejected_value"]) == (0, 0)
