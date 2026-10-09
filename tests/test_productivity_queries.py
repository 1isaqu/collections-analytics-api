"""Productivity, agent-performance, benchmark and effort queries.

Run on the invented portfolio of ``conftest.db``: agent 1 owns 5001 (three installments,
the first written twice), 5004 (broken) and 5006; agent 2 owns the exceptions 5002 and
5007 and the rejected 5003; agent 3 matches the default exclusion pattern. Every
agreement was issued on ``TODAY`` at 09:00 and fell due ten days earlier.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from collections_api.core.sql import ALL_LINES, Period
from collections_api.domains.productivity import queries
from tests import sqlite_adapter as sq
from tests.conftest import TODAY
from tests.sql_inspect import final_select_aliases

DAY = Period.day(TODAY)

# (event_id, debt_owner_id, result_id, agent_id): agent 1 reaches three debt owners (one of
# them twice), two of them answer and one is the right party; agent 2 reaches one.
EFFORT = [(1, 101, 1, 1), (2, 101, 1, 1), (3, 102, 2, 1), (4, 103, 3, 1), (5, 201, 1, 2)]


def productivity(db, cfg, *, period=DAY, portfolio=None):
    query = queries.productivity_query(cfg, "auto", period, TODAY, portfolio)
    return {row["agent_name"]: row for row in sq.run(db, query)}


@pytest.fixture()
def with_effort(db):
    sq.add_contact_results(db)
    sq.add_events(db, EFFORT)
    return db


# ---------------------------------------------------------------- shape


def test_declared_columns_match_the_generated_sql(cfg):
    one = queries.productivity_query(cfg, "auto", DAY, TODAY)
    assert final_select_aliases(one.sql) == list(queries.PRODUCTIVITY_COLUMNS)
    for line in ("consumer", ALL_LINES):
        performance = queries.agent_performance_query(cfg, line, DAY, TODAY)
        assert final_select_aliases(performance.sql) == list(queries.AGENT_PERFORMANCE_COLUMNS)
    benchmark = queries.benchmark_query(cfg, "auto", TODAY, 3)
    assert final_select_aliases(benchmark.sql) == list(queries.BENCHMARK_COLUMNS)
    effort = queries.agent_effort_query(cfg, "collections_auto", TODAY)
    assert final_select_aliases(effort.sql) == list(queries.AGENT_EFFORT_COLUMNS)


def test_two_shapes_read_one_line_and_refuse_all(cfg):
    with pytest.raises(ValueError):
        queries.productivity_query(cfg, ALL_LINES, DAY, TODAY)
    with pytest.raises(ValueError):
        queries.benchmark_query(cfg, ALL_LINES, TODAY, 3)


def test_dates_are_literals_never_declared_variables(cfg):
    for query in (
        queries.productivity_query(cfg, "auto", DAY, TODAY),
        queries.agent_performance_query(cfg, ALL_LINES, DAY, TODAY),
        queries.benchmark_query(cfg, "auto", TODAY, 3),
    ):
        assert "DECLARE" not in query.sql.upper()
        assert "CAST('20260318' AS DATE)" in query.sql or "CAST('20251218' AS DATE)" in query.sql


def test_the_portfolio_label_is_a_bind_parameter_once_per_marker(cfg):
    query = queries.productivity_query(cfg, "auto", DAY, TODAY, "Portfolio A-01")
    assert query.sql.count("?") == len(query.params) > 1
    assert set(query.params) == {"Portfolio A-01"}
    assert "Portfolio A-01" not in query.sql
    assert queries.productivity_query(cfg, "auto", DAY, TODAY).params == ()


def test_aggregates_are_separate_ctes(cfg):
    sql = queries.productivity_query(cfg, "auto", DAY, TODAY).sql
    for name in ("acc", "orig", "fin", "effort", "hours", "slips", "contracts", "rej_acc", "rej"):
        assert f"{name} AS (" in sql


def test_the_query_option_is_appended_when_configured(cfg):
    tuned = dataclasses.replace(cfg, productivity_option="OPTION (MAXDOP 2)")
    assert queries.productivity_query(tuned, "auto", DAY, TODAY).sql.endswith("OPTION (MAXDOP 2)")
    assert "OPTION" not in queries.productivity_query(cfg, "auto", DAY, TODAY).sql


def test_subtract_months_clamps_to_the_end_of_a_short_month():
    assert queries.subtract_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert queries.subtract_months(date(2028, 3, 31), 1) == date(2028, 2, 29)
    assert queries.subtract_months(date(2026, 1, 15), 3) == date(2025, 10, 15)
    assert queries.subtract_months(date(2026, 3, 18), 12) == date(2025, 3, 18)


def test_one_person_has_one_key_across_lines():
    assert queries.normalize_agent_key(" agent_01 ", "Agent 01") == "AGENT_01"
    assert queries.normalize_agent_key(None, " Agent 01 ") == "AGENT 01"
    assert queries.normalize_agent_key("", None) == ""


# ---------------------------------------------------------------- productivity_query results


def test_effort_counts_distinct_debt_owners_over_the_window(with_effort, sqlite_cfg):
    one = productivity(with_effort, sqlite_cfg)["Agent 01"]
    assert (one["attempt_count"], one["answer_count"], one["cpc_count"]) == (3, 2, 1)
    assert one["cpc_rate_pct"] == 50  # 1 right party out of 2 answers, rounded up
    assert one["agreements_per_attempt_pct"] == pytest.approx(100.0)  # 3 agreements, 3 owners


def test_an_agent_with_agreements_and_no_outreach_still_appears(db, sqlite_cfg):
    rows = productivity(db, sqlite_cfg)
    assert set(rows) == {"Agent 01", "Agent 02"}
    one = rows["Agent 01"]
    assert one["attempt_count"] == 0 and one["agreement_count"] == 3
    assert one["cpc_rate_pct"] is None  # no answers, no ratio
    assert one["agreements_per_attempt_pct"] is None


def test_an_excluded_agent_never_appears(with_effort, sqlite_cfg):
    sq.add_events(with_effort, [(9, 301, 1, 3)])
    assert "System Bot" not in productivity(with_effort, sqlite_cfg)


def test_generated_values_include_broken_agreements_and_leave_out_exceptions(db, sqlite_cfg):
    one = productivity(db, sqlite_cfg)["Agent 01"]
    assert one["agreement_count"] == 3  # 5001 once however many installments, 5004, 5006
    assert one["agreement_value"] == 2600.0
    assert one["first_installment_value"] == 1600.0
    assert one["avg_ticket"] == pytest.approx(2600.0 / 3)
    assert one["avg_installments"] == pytest.approx(5 / 3)
    assert one["avg_balance_ratio_pct"] == pytest.approx((100 + 10 + 20) / 3)
    assert one["exception_count"] == 0


def test_exceptions_and_rejections_belong_to_their_agent(db, sqlite_cfg):
    two = productivity(db, sqlite_cfg)["Agent 02"]
    assert (two["agreement_count"], two["agreement_value"]) == (0, 0)
    assert (two["exception_count"], two["exception_value"]) == (2, 950.0)
    assert two["exception_first_installment_value"] == 950.0
    assert (two["rejected_count"], two["rejected_value"]) == (1, 300.0)


def test_agreements_are_counted_per_contract_in_a_separate_column(db, sqlite_cfg):
    sq.insert(db, "agreement_debts", [{"agreement_id": 5001, "portfolio_id": 1, "debt_id": 18}])
    sq.insert(
        db,
        "debts",
        [
            {
                "debt_id": 18,
                "portfolio_id": 1,
                "contract_no": "C18",
                "balance": 500.0,
                "due_at": "2025-12-01",
            }
        ],
    )
    one = productivity(db, sqlite_cfg)["Agent 01"]
    assert one["agreement_count"] == 3  # the agreement grain does not change
    assert one["agreement_count_by_contract"] == 4  # 5001 now covers two debts


def test_slips_count_installments_already_due_and_paid_inside_the_grace_period(db, sqlite_cfg):
    # Default grace is three days and every installment fell due on 2026-03-10.
    db.execute(
        "UPDATE agreements SET paid_amount = 500, paid_at = '2026-03-13 00:00:00' WHERE row_id = 3"
    )
    db.execute(
        "UPDATE agreements SET paid_amount = 500, paid_at = '2026-03-14 00:00:00' WHERE row_id = 4"
    )
    db.execute("UPDATE agreements SET due_at = '2026-04-01 00:00:00' WHERE row_id = 9")
    one = productivity(db, sqlite_cfg)["Agent 01"]
    assert one["slips_issued"] == 4  # the installment due next month is not a slip yet
    assert one["slips_paid"] == 1  # the one paid on the last grace day, not the one a day later


def test_first_installment_received_is_what_was_paid_on_installment_zero(db, sqlite_cfg):
    db.execute("UPDATE agreements SET paid_amount = 400 WHERE row_id = 2")  # the later write wins
    assert productivity(db, sqlite_cfg)["Agent 01"]["first_installment_received"] == 400.0


def test_hours_worked_spans_the_first_to_the_last_agreement_of_each_day(db, sqlite_cfg):
    db.execute("UPDATE agreements SET issued_at = '2026-03-18 11:30:00' WHERE row_id = 9")
    assert productivity(db, sqlite_cfg)["Agent 01"]["hours_worked"] == pytest.approx(2.5)


def test_a_portfolio_filter_restricts_agreements_but_not_effort(with_effort, sqlite_cfg):
    rows = productivity(with_effort, sqlite_cfg, portfolio="Portfolio A-02")
    one = rows["Agent 01"]
    assert one["agreement_count"] == 2  # 5004 and 5006 carry the label, 5001 does not
    assert one["agreement_value"] == 600.0
    assert one["attempt_count"] == 3  # outreach cannot be attributed to a portfolio


def test_the_window_applies_to_effort_and_agreements(with_effort, sqlite_cfg):
    yesterday = Period.day(date(2026, 3, 17))
    assert productivity(with_effort, sqlite_cfg, period=yesterday) == {}


# ---------------------------------------------------------------- agent_performance_query


def performance(db, cfg, line="auto", period=DAY):
    return sq.run(db, queries.agent_performance_query(cfg, line, period, TODAY))


def test_performance_counts_a_debt_owner_once_per_day(with_effort, sqlite_cfg):
    sq.add_events(with_effort, [(20, 101, 1, 1)], when="2026-03-17 10:00:00")
    both_days = Period.between(date(2026, 3, 17), TODAY)
    per_day = {r["agent_name"]: r for r in performance(with_effort, sqlite_cfg, period=both_days)}
    assert per_day["Agent 01"]["attempt_count"] == 4  # owner 101 on two days counts twice
    over_window = productivity(with_effort, sqlite_cfg, period=both_days)["Agent 01"]
    assert over_window["attempt_count"] == 3  # ... but once over the whole window


def test_performance_rows_carry_the_line_and_the_slip_ratio(with_effort, sqlite_cfg):
    with_effort.execute(
        "UPDATE agreements SET paid_amount = 500, paid_at = '2026-03-12 00:00:00' WHERE row_id = 3"
    )
    rows = {r["agent_name"]: r for r in performance(with_effort, sqlite_cfg)}
    one = rows["Agent 01"]
    assert one["product_line"] == "auto"
    assert (one["slips_issued"], one["slips_paid"]) == (5, 1)
    assert one["slip_effectiveness_pct"] == pytest.approx(20.0)
    assert one["cpc_rate_pct"] == 50
    assert rows["Agent 02"]["slip_effectiveness_pct"] is None  # no slips, no ratio


def test_performance_over_all_lines_gives_one_row_per_agent_and_line(with_effort, sqlite_cfg):
    rows = performance(with_effort, sqlite_cfg, line=ALL_LINES)
    assert sorted((r["product_line"], r["agent_name"]) for r in rows) == [
        ("auto", "Agent 01"),
        ("auto", "Agent 02"),
        ("consumer", "Agent 01"),
        ("consumer", "Agent 02"),
    ]


def test_performance_leaves_out_the_excluded_agent(with_effort, sqlite_cfg):
    assert "System Bot" not in {r["agent_name"] for r in performance(with_effort, sqlite_cfg)}


# ---------------------------------------------------------------- benchmark_query


@pytest.fixture()
def history(db, sqlite_cfg):
    """Two earlier days of work, with limits lowered so a small sample qualifies.

    Agent 1: day 10 reaches owners 101 (right party), 102 (wrong person), 103 (no answer);
    day 11 reaches 104 and 105 (right party both). Agent 2: day 10 reaches three owners,
    day 11 only one, which is under the daily minimum, so agent 2 has one active day.
    """
    sq.add_contact_results(db)
    day_10, day_11 = "2026-03-10 10:00:00", "2026-03-11 10:00:00"
    sq.add_events(db, [(1, 101, 1, 1), (2, 102, 2, 1), (3, 103, 3, 1)], when=day_10)
    sq.add_events(db, [(4, 104, 1, 1), (5, 105, 1, 1)], when=day_11)
    sq.add_events(db, [(6, 201, 1, 2), (7, 202, 1, 2), (8, 203, 1, 2)], when=day_10)
    sq.add_events(db, [(9, 204, 1, 2)], when=day_11)
    sq.add_events(db, [(10, 301, 1, 3), (11, 302, 1, 3), (12, 303, 1, 3)], when=day_10)
    sq.insert(
        db,
        "agreements",
        [
            sq.installment(
                row_id=101,
                agreement_id=7001,
                amount=1000.0,
                paid_amount=400.0,
                issued_at="2026-03-10 09:00:00",
                due_at="2026-03-20 00:00:00",
            ),
            sq.installment(
                row_id=102,
                agreement_id=7002,
                amount=500.0,
                status_id=sq.PENDING_EXCEPTION,
                issued_at="2026-03-10 09:30:00",
                due_at="2026-03-20 00:00:00",
            ),
        ],
    )
    limits = {"benchmark_min_daily_attempts": 2, "benchmark_min_active_days": 2}
    return db, dataclasses.replace(sqlite_cfg, **limits)


def test_benchmark_averages_per_agent_over_qualifying_days(history):
    db, cfg = history
    rows = sq.run(db, queries.benchmark_query(cfg, "auto", TODAY, 3))
    assert [r["agent_name"] for r in rows] == ["Agent 01"]  # agent 2 has one qualifying day
    one = rows[0]
    assert one["active_days"] == 2
    assert one["cpc_rate_pct"] == pytest.approx(75.0)  # 3 right parties over 4 answers
    assert one["conversion_pct"] == pytest.approx(100 / 3)  # 1 agreement over 3 right parties
    assert one["cash_effectiveness_pct"] == pytest.approx(40.0)  # 400 received of 1000 due
    assert one["exception_pct"] == pytest.approx(100 * 500 / 1500)


def test_benchmark_leaves_out_the_current_day_and_the_excluded_agent(history):
    db, cfg = history
    sq.add_events(db, [(50, 401, 1, 1), (51, 402, 1, 1)], when="2026-03-18 10:00:00")
    rows = sq.run(db, queries.benchmark_query(cfg, "auto", TODAY, 3))
    assert [r["agent_name"] for r in rows] == ["Agent 01"]
    assert rows[0]["active_days"] == 2  # today is not part of the look-back


def test_benchmark_minimums_come_from_the_configuration(history):
    db, cfg = history
    strict = dataclasses.replace(cfg, benchmark_min_daily_attempts=3)
    assert sq.run(db, queries.benchmark_query(strict, "auto", TODAY, 3)) == []


# ---------------------------------------------------------------- agent_effort_query


def test_agent_effort_counts_distinct_debt_owners_for_the_day(with_effort, sqlite_cfg):
    query = queries.agent_effort_query(sqlite_cfg, "collections_auto", TODAY)
    rows = {r["agent_name"]: r for r in sq.run(with_effort, query)}
    assert (rows["Agent 01"]["attempt_count"], rows["Agent 01"]["answer_count"]) == (3, 2)
    assert rows["Agent 01"]["cpc_count"] == 1
    assert rows["Agent 02"]["attempt_count"] == 1


def test_agent_effort_excludes_configured_agents(with_effort, sqlite_cfg):
    sq.add_events(with_effort, [(9, 301, 1, 3)])
    query = queries.agent_effort_query(sqlite_cfg, "collections_auto", TODAY)
    assert "System Bot" not in {r["agent_name"] for r in sq.run(with_effort, query)}
