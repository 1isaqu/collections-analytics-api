"""Agreement list, agreement table and performance table, run on the invented portfolio.

Fixture recap (see ``conftest.db``): agent 1 owns 5001 (three installments, the first
one written twice), 5004 (broken) and 5006; agent 2 owns the exceptions 5002 and 5007 and
the rejected 5003; agent 3 matches the default exclusion pattern.
"""

from __future__ import annotations

import pytest

from collections_api.core.sql import ALL_LINES, Period
from collections_api.domains.agreements import queries
from tests import sqlite_adapter as sq
from tests.conftest import TODAY
from tests.sql_inspect import final_select_aliases

DAY = Period.day(TODAY)


def label_statuses(db):
    sq.insert(
        db,
        "agreement_statuses",
        [
            {"status_id": sq.ACTIVE, "label": "ACTIVE"},
            {"status_id": sq.BROKEN, "label": "BROKEN"},
            {"status_id": sq.PENDING_EXCEPTION, "label": "PENDING_EXCEPTION"},
        ],
    )


def add_effort(db):
    """Outreach on the same day: agent 1 reaches three debt owners, agent 2 one."""
    sq.insert(
        db,
        "contact_results",
        [
            {"result_id": 1, "label": "Promise", "answered": 1, "right_party": 1},
            {"result_id": 2, "label": "Wrong person", "answered": 1, "right_party": 0},
            {"result_id": 3, "label": "No answer", "answered": 0, "right_party": 0},
        ],
    )
    when = "2026-03-18 10:00:00"
    events = [
        (1, 101, 1, 1),
        (2, 101, 1, 1),  # the same owner called twice: counted once
        (3, 102, 2, 1),
        (4, 103, 3, 1),
        (5, 201, 1, 2),
        (6, 301, 1, 3),  # excluded agent
    ]
    sq.insert(
        db,
        "contact_events",
        [
            {
                "event_id": e,
                "debt_owner_id": owner,
                "result_id": result,
                "agent_id": agent,
                "occurred_at": when,
            }
            for e, owner, result, agent in events
        ],
    )


# ---------------------------------------------------------------- columns


def test_declared_columns_match_the_generated_sql(cfg):
    today_query = queries.agreements_today(cfg, "collections_auto", TODAY, 0, 10)
    assert final_select_aliases(today_query.sql) == [
        "_total_rows",
        *queries.AGREEMENTS_TODAY_COLUMNS,
    ]
    for line in ("auto", ALL_LINES):
        table, _ = queries.agreements_table(cfg, line, TODAY, None, None)
        assert final_select_aliases(table.sql) == list(queries.AGREEMENTS_TABLE_COLUMNS)
        performance = queries.performance_table(cfg, line, DAY, TODAY, None)
        assert final_select_aliases(performance.sql) == list(queries.PERFORMANCE_TABLE_COLUMNS)


def test_no_debtor_identity_is_selected(cfg):
    sql = queries.agreements_today(cfg, "collections_auto", TODAY, 0, 10).sql
    for forbidden in ("cpf", "document", "debtor", "name_razao", "registration"):
        assert forbidden not in sql.lower()


# ---------------------------------------------------------------- agreements today


def test_today_lists_every_installment_of_approved_agreements_once(db, sqlite_cfg):
    label_statuses(db)
    rows = sq.run(db, queries.agreements_today(sqlite_cfg, "collections_auto", TODAY, 0, 50))
    assert [(r["agreement_id"], r["installment_no"]) for r in rows] == [
        (5001, 0),
        (5001, 1),
        (5001, 2),
        (5006, 0),
    ]
    assert {r["_total_rows"] for r in rows} == {4}
    first = rows[0]
    assert first["agent_name"] == "Agent 01"
    assert first["agreement_total_value"] == 2000.0  # 1000 + 500 + 500, the re-write not counted
    assert first["current_debt_value"] == 2000.0
    assert first["discount_granted"] == 0.0
    assert first["status_label"] == "ACTIVE"
    assert first["payment_state"] == "OPEN"
    assert rows[3]["discount_granted"] == 1600.0  # balance 2000, agreement 400


def test_today_excludes_other_statuses_and_excluded_agents(db, sqlite_cfg):
    ids = {
        r["agreement_id"]
        for r in sq.run(db, queries.agreements_today(sqlite_cfg, "x", TODAY, 0, 50))
    }
    assert ids == {5001, 5006}  # no exceptions, rejected, broken, or the excluded agent's 5005


def test_today_pages_with_offset_and_limit(db, sqlite_cfg):
    rows = sq.run(db, queries.agreements_today(sqlite_cfg, "x", TODAY, 1, 2))
    assert [(r["agreement_id"], r["installment_no"]) for r in rows] == [(5001, 1), (5001, 2)]
    assert rows[0]["_total_rows"] == 4  # the grand total ignores the page
    assert sq.run(db, queries.agreements_today(sqlite_cfg, "x", TODAY, 10, 2)) == []


def test_today_marks_paid_installments(db, sqlite_cfg):
    sq.insert(
        db,
        "agreements",
        [
            sq.installment(
                row_id=40, agreement_id=7001, paid_amount=100.0, paid_at="2026-03-18 12:00:00"
            )
        ],
    )
    sq.insert(db, "agreement_debts", [{"agreement_id": 7001, "portfolio_id": 1, "debt_id": 11}])
    rows = sq.run(db, queries.agreements_today(sqlite_cfg, "x", TODAY, 0, 50))
    assert {r["agreement_id"]: r["payment_state"] for r in rows}[7001] == "PAID"


def test_today_other_days_are_empty(db, sqlite_cfg):
    from datetime import date

    assert sq.run(db, queries.agreements_today(sqlite_cfg, "x", date(2026, 3, 17), 0, 50)) == []


# ---------------------------------------------------------------- agreements table


def test_table_has_one_row_per_agreement_with_exceptions_last(db, sqlite_cfg):
    label_statuses(db)
    query, label = queries.agreements_table(sqlite_cfg, "auto", TODAY, None, None)
    rows = sq.run(db, query)
    assert label == "all"
    assert [r["agreement_id"] for r in rows] == [5001, 5004, 5006, 5002, 5007]
    by_id = {r["agreement_id"]: r for r in rows}
    assert by_id[5001] == {
        "agent_name": "Agent 01",
        "agreement_id": 5001,
        "portfolio_id": 1,
        "agreement_type": "ACTIVE",
        "is_exception": 0,
        "first_installment_due": "2026-03-10 00:00:00",
        "first_installment_value": 1000.0,
        "later_installment_value": 500.0,
        "installment_count": 3,
        "agreement_total_value": 2000.0,
        "issued_at": "2026-03-18 09:00:00",
    }
    assert by_id[5002]["is_exception"] == 1 and by_id[5002]["agreement_type"] == "PENDING_EXCEPTION"
    assert by_id[5006]["later_installment_value"] is None


def test_table_leaves_out_rejected_and_excluded_agents(db, sqlite_cfg):
    query, _ = queries.agreements_table(sqlite_cfg, "auto", TODAY, None, None)
    assert {r["agreement_id"] for r in sq.run(db, query)}.isdisjoint({5003, 5005})


def test_table_team_and_agent_filters_bind_in_order(db, sqlite_cfg):
    query, label = queries.agreements_table(sqlite_cfg, "auto", TODAY, "Group: agent 02", None)
    assert label == "Group: agent 02"
    assert query.params == ("%AGENT 02%", "%AGENT 02%")
    assert {r["agreement_id"] for r in sq.run(db, query)} == {5002, 5007}

    query, _ = queries.agreements_table(sqlite_cfg, "auto", TODAY, None, "Agent 01")
    assert query.params == ("Agent 01",)
    assert {r["agreement_id"] for r in sq.run(db, query)} == {5001, 5004, 5006}

    both, _ = queries.agreements_table(sqlite_cfg, ALL_LINES, TODAY, "agent 01", "Agent 01")
    assert both.params == ("%AGENT 01%",) * 4 + ("Agent 01",)  # two per line, then the agent
    assert both.sql.count("?") == len(both.params)


def test_table_all_lines_adds_the_agent_to_the_ordering(cfg):
    single, _ = queries.agreements_table(cfg, "auto", TODAY, None, None)
    both, _ = queries.agreements_table(cfg, ALL_LINES, TODAY, None, None)
    assert single.sql.rstrip().endswith("ORDER BY base.is_exception, base.agreement_id")
    assert both.sql.rstrip().endswith(
        "ORDER BY base.is_exception, base.agent_name, base.agreement_id"
    )


# ---------------------------------------------------------------- performance table


def test_performance_counts_effort_by_distinct_debt_owner(db, sqlite_cfg):
    add_effort(db)
    rows = {
        r["agent_name"]: r
        for r in sq.run(db, queries.performance_table(sqlite_cfg, "auto", DAY, TODAY, None))
    }
    assert set(rows) == {"Agent 01", "Agent 02"}  # the excluded agent never shows
    one = rows["Agent 01"]
    assert (one["attempt_count"], one["answer_count"], one["cpc_count"]) == (3, 2, 1)
    assert (rows["Agent 02"]["attempt_count"], rows["Agent 02"]["cpc_count"]) == (1, 1)


def test_performance_value_columns_come_from_generated_agreements(db, sqlite_cfg):
    add_effort(db)
    rows = {
        r["agent_name"]: r
        for r in sq.run(db, queries.performance_table(sqlite_cfg, "auto", DAY, TODAY, None))
    }
    one, two = rows["Agent 01"], rows["Agent 02"]
    assert one["agreement_count"] == 3  # 5001 once however many installments, 5004, 5006
    assert one["agreement_value"] == 2600.0 and one["first_installment_value"] == 1600.0
    assert one["slips_issued"] == 5 and one["slips_paid"] == 0
    assert (two["agreement_count"], two["agreement_value"]) == (0, 0)
    assert (two["exception_count"], two["exception_value"]) == (2, 950.0)
    assert two["rejected_count"] == 1


def test_performance_ratios(db, sqlite_cfg):
    add_effort(db)
    rows = {
        r["agent_name"]: r
        for r in sq.run(db, queries.performance_table(sqlite_cfg, "auto", DAY, TODAY, None))
    }
    one = rows["Agent 01"]
    assert one["conversion_pct"] == pytest.approx(300.0)  # 3 agreements over 1 right-party contact
    assert one["cpc_rate_pct"] == pytest.approx(50.0)  # 1 of 2 answers
    assert rows["Agent 02"]["conversion_pct"] == 0


def test_performance_a_ratio_with_no_denominator_is_zero_not_an_error(db, sqlite_cfg):
    sq.insert(
        db,
        "contact_results",
        [{"result_id": 3, "label": "No answer", "answered": 0, "right_party": 0}],
    )
    sq.insert(
        db,
        "contact_events",
        [
            {
                "event_id": 1,
                "debt_owner_id": 9,
                "result_id": 3,
                "agent_id": 1,
                "occurred_at": "2026-03-18 10:00:00",
            }
        ],
    )
    row = next(
        r
        for r in sq.run(db, queries.performance_table(sqlite_cfg, "auto", DAY, TODAY, None))
        if r["agent_name"] == "Agent 01"
    )
    assert row["cpc_count"] == 0 and row["conversion_pct"] == 0 and row["cpc_rate_pct"] == 0


def test_performance_is_ordered_by_effort_and_can_filter_one_agent(db, sqlite_cfg):
    add_effort(db)
    rows = sq.run(db, queries.performance_table(sqlite_cfg, "auto", DAY, TODAY, None))
    assert [r["agent_name"] for r in rows] == ["Agent 01", "Agent 02"]
    only = queries.performance_table(sqlite_cfg, "auto", DAY, TODAY, "Agent 02")
    assert only.params == ("Agent 02",)
    assert [r["agent_name"] for r in sq.run(db, only)] == ["Agent 02"]


def test_performance_over_all_lines_sums_counts_and_recomputes_ratios(cfg, db, sqlite_cfg):
    sql = queries.performance_table(cfg, ALL_LINES, DAY, TODAY, None).sql
    assert "SUM(agreement_count) * 100.0 / SUM(cpc_count)" in sql
    assert "GROUP BY agent_name" in sql
    assert (
        "SUM(agreement_count) * 100.0"
        not in queries.performance_table(cfg, "auto", DAY, TODAY, None).sql
    )

    add_effort(db)
    both = {
        r["agent_name"]: r
        for r in sq.run(db, queries.performance_table(sqlite_cfg, ALL_LINES, DAY, TODAY, None))
    }
    # Both lines read the same invented tables, so every count doubles and ratios hold.
    assert both["Agent 01"]["attempt_count"] == 6 and both["Agent 01"]["cpc_count"] == 2
    assert both["Agent 01"]["conversion_pct"] == pytest.approx(300.0)


def test_performance_window_applies_to_effort_and_agreements(db, sqlite_cfg):
    add_effort(db)
    yesterday = Period.day(TODAY.replace(day=17))
    assert sq.run(db, queries.performance_table(sqlite_cfg, "auto", yesterday, TODAY, None)) == []
