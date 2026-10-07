"""Effectiveness: are the slips issued for agreements paid in time?

A slip is one installment row. It is paid in grace when a positive amount was paid no
later than ``GRACE_DAYS`` (three by default) after its due date. ``TODAY`` is 2026-03-18.

The invented slips, all installment 0 of their own agreement, due in March 2026:

======  =========  =========  ==========================================
agr.    due        amount     state
======  =========  =========  ==========================================
9001    03-10      1000       paid on 03-12, inside grace (written twice)
9002    03-10      500        unpaid, grace over
9003    03-16      600        unpaid, grace still running
9004    03-20      700        unpaid, not due yet
9005    03-05      300        paid on 03-12, after the grace period
9006    03-10      200        BROKEN, unpaid
9007    03-10      999        agent matches an excluded pattern
======  =========  =========  ==========================================
"""

from __future__ import annotations

import dataclasses

import pytest

from collections_api.core.sql import ALL_LINES, Period
from collections_api.domains.effectiveness import queries
from tests import sqlite_adapter as sq
from tests.conftest import TODAY
from tests.sql_inspect import final_select_aliases

MARCH = Period.between(TODAY.replace(day=1), TODAY.replace(day=31))


@pytest.fixture()
def slips(db, sqlite_cfg):
    db.execute("DELETE FROM agreements")
    db.execute("DELETE FROM agreement_debts")
    db.execute("DELETE FROM debt_attributes")
    issued = "2026-03-01 09:00:00"

    def slip(row_id, agreement_id, due, amount, **extra):
        return sq.installment(
            row_id=row_id, agreement_id=agreement_id, due_at=f"2026-03-{due} 00:00:00",
            issued_at=issued, amount=amount, **extra,
        )

    paid_on_time = {"paid_amount": 1000.0, "paid_at": "2026-03-12 00:00:00"}
    sq.insert(
        db,
        "agreements",
        [
            slip(1, 9001, "10", 1000.0, **paid_on_time),
            slip(2, 9001, "10", 1000.0, **paid_on_time),
            slip(3, 9002, "10", 500.0),
            slip(4, 9003, "16", 600.0),
            slip(5, 9004, "20", 700.0),
            slip(6, 9005, "05", 300.0, paid_amount=300.0, paid_at="2026-03-12 00:00:00"),
            slip(7, 9006, "10", 200.0, status_id=sq.BROKEN),
            slip(8, 9007, "10", 999.0, agent_id=3),
            slip(9, 9001, "25", 500.0, installment_no=1),
        ],
    )
    sq.insert(
        db,
        "agreement_debts",
        [{"agreement_id": a, "portfolio_id": 1, "debt_id": a - 9000} for a in range(9001, 9008)],
    )
    sq.insert(
        db,
        "debt_attributes",
        [
            {"debt_id": 1, "portfolio_label": "Portfolio A-01"},
            {"debt_id": 2, "portfolio_label": "Portfolio A-01"},
            {"debt_id": 3, "portfolio_label": "Portfolio A-02"},
            {"debt_id": 4, "portfolio_label": "Portfolio A-02"},
            {"debt_id": 5, "portfolio_label": "Portfolio A-02"},
            {"debt_id": 6, "portfolio_label": "Portfolio A-02"},
        ],
    )
    return db, dataclasses.replace(sqlite_cfg, effectiveness_min_slips=1)


# ---------------------------------------------------------------- shape


@pytest.mark.parametrize("key", sorted(queries.SERIES))
def test_series_columns_match_their_spec(cfg, key):
    for line in ("auto", ALL_LINES):
        assert final_select_aliases(queries.series_query(cfg, line, key).sql) == list(
            queries.SERIES[key].columns
        )


def test_the_other_queries_produce_the_declared_columns(cfg):
    kpi, daily = queries.summary_queries(cfg, ALL_LINES, MARCH, TODAY, "first")
    assert final_select_aliases(kpi.sql) == list(queries.SUMMARY_COLUMNS)
    assert final_select_aliases(daily.sql) == list(queries.DAILY_COLUMNS)
    for kind in queries.DETAIL_KINDS:
        for line in ("auto", ALL_LINES):
            detail = queries.detail_query(cfg, line, MARCH, TODAY, "first", kind)
            assert final_select_aliases(detail.sql) == list(queries.DETAIL_COLUMNS)
    assert final_select_aliases(queries.break_curve_query(cfg, "auto", MARCH, TODAY).sql) == list(
        queries.BREAK_CURVE_COLUMNS
    )
    summary = queries.portfolio_summary_query(cfg, "auto", MARCH, "first", "Portfolio A-01")
    assert final_select_aliases(summary.sql) == list(queries.PORTFOLIO_SUMMARY_COLUMNS)
    ranking = queries.portfolio_ranking_query(cfg, ALL_LINES, MARCH, "later")
    assert final_select_aliases(ranking.sql) == list(queries.PORTFOLIO_RANKING_COLUMNS)
    generation = queries.generation_ranking_query(cfg, ALL_LINES, MARCH)
    assert final_select_aliases(generation.sql) == list(queries.GENERATION_RANKING_COLUMNS)


def test_the_effectiveness_exclusion_list_is_used_except_for_the_generation_ranking(cfg):
    wide = "NOT LIKE '%SYSTEM%'"
    narrow = "NOT LIKE 'SYSTEM%'"
    assert wide in queries.series_query(cfg, "auto", "daily-first").sql
    assert wide in queries.summary_queries(cfg, "auto", MARCH, TODAY, "first")[0].sql
    assert wide in queries.detail_query(cfg, "auto", MARCH, TODAY, "first", "broken").sql
    generation = queries.generation_ranking_query(cfg, "auto", MARCH).sql
    assert narrow in generation and wide not in generation


def test_installment_kind_selects_the_down_payment_or_everything_after_it(cfg):
    assert "installment_no = 0" in queries.series_query(cfg, "auto", "daily-first").sql
    assert "installment_no > 0" in queries.series_query(cfg, "auto", "daily-later").sql


def test_the_window_column_is_the_due_date_for_by_due_series_and_the_issue_date_otherwise(cfg):
    assert "R.due_at >= CAST('20200101' AS DATE)" in queries.series_query(cfg, "auto", "monthly-later-by-due").sql
    assert "R.issued_at >= CAST('20200101' AS DATE)" in queries.series_query(cfg, "auto", "monthly-first").sql


def test_only_the_by_due_agent_series_has_a_minimum_sample(cfg):
    assert "HAVING COUNT(*) >= 8" in queries.series_query(cfg, "auto", "monthly-agent-later-by-due").sql
    assert "HAVING" not in queries.series_query(cfg, "auto", "monthly-agent-first").sql


def test_every_detail_kind_has_its_own_filter(cfg):
    filters = {queries._detail_filter(cfg, kind, TODAY) for kind in queries.DETAIL_KINDS}
    assert len(filters) == len(queries.DETAIL_KINDS)


def test_detail_is_capped_before_the_per_agreement_lookups_run(cfg):
    sql = queries.detail_query(cfg, "auto", MARCH, TODAY, "first", "to-mature").sql
    assert sql.index("SELECT TOP 250") > sql.index("COALESCE((")  # the cap sits in the inner query
    both = queries.detail_query(cfg, ALL_LINES, MARCH, TODAY, "first", "to-mature").sql
    assert both.count("SELECT TOP 250") == 3  # one per line, then the cap on the union
    capped = dataclasses.replace(cfg, effectiveness_detail_limit=10)
    assert "TOP 10" in queries.detail_query(capped, "auto", MARCH, TODAY, "first", "broken").sql


def test_the_portfolio_label_is_bound_once_per_line(cfg):
    one = queries.portfolio_summary_query(cfg, "auto", MARCH, "first", "Portfolio A-01")
    both = queries.portfolio_summary_query(cfg, ALL_LINES, MARCH, "first", "Portfolio A-01")
    assert one.params == ("Portfolio A-01",) and both.params == ("Portfolio A-01",) * 2
    assert both.sql.count("?") == 2 and "Portfolio A-01" not in both.sql


def test_break_bands_follow_the_configured_edges():
    assert queries.break_bands((7, 21)) == [("0-7 days", 0, 7), ("8-21 days", 8, 21), ("22+ days", 22, None)]
    assert queries.break_bands((10,))[-1] == ("11+ days", 11, None)


# ---------------------------------------------------------------- summary


def run_summary(db, cfg, installment_type="first", portfolio_id=None, line="auto"):
    kpi, daily = queries.summary_queries(cfg, line, MARCH, TODAY, installment_type, portfolio_id)
    return sq.run(db, kpi)[0], sq.run(db, daily)


def test_the_buckets_partition_the_slips(slips):
    db, cfg = slips
    kpi, _ = run_summary(db, cfg)
    assert kpi["slips_total"] == 6 and kpi["agreements_total"] == 6
    assert kpi["slips_to_mature"] == 1  # 9004
    assert kpi["slips_in_grace"] == 1  # 9003: due 03-16, grace runs to 03-19
    assert kpi["slips_overdue_unpaid"] == 3  # 9002, 9005 (paid late) and 9006
    assert kpi["slips_paid_in_grace"] == 1  # 9001
    assert kpi["slips_broken"] == 1
    buckets = ("slips_to_mature", "slips_in_grace", "slips_overdue_unpaid", "slips_paid_in_grace")
    assert sum(kpi[b] for b in buckets) == kpi["slips_total"]


def test_conversion_counts_on_time_slips_and_effectiveness_counts_cash(slips):
    db, cfg = slips
    kpi, _ = run_summary(db, cfg)
    assert kpi["conversion_pct"] == pytest.approx(100 / 6, abs=0.01)
    assert kpi["amount_maturing"] == 3300.0
    assert kpi["amount_received"] == 1300.0  # the late payment still counts as cash
    assert kpi["effectiveness_pct"] == pytest.approx(100 * 1300 / 3300, abs=0.01)


def test_the_daily_series_has_one_point_per_due_day(slips):
    db, cfg = slips
    _, daily = run_summary(db, cfg)
    assert [(d["due_date"], d["slips_total"]) for d in daily] == [
        ("2026-03-05", 1), ("2026-03-10", 3), ("2026-03-16", 1), ("2026-03-20", 1),
    ]
    assert sum(d["slips_total"] for d in daily) == 6


def test_the_grace_period_comes_from_the_configuration(slips):
    db, cfg = slips
    longer = dataclasses.replace(cfg, grace_days=10)
    kpi, _ = run_summary(db, longer)
    assert kpi["slips_paid_in_grace"] == 2  # 9005 was paid seven days after its due date


def test_later_installments_are_a_separate_population(slips):
    db, cfg = slips
    kpi, _ = run_summary(db, cfg, installment_type="later")
    assert kpi["slips_total"] == 1 and kpi["slips_to_mature"] == 1


def test_a_portfolio_id_restricts_the_slips(slips):
    db, cfg = slips
    db.execute("UPDATE agreements SET portfolio_id = 2 WHERE agreement_id = 9004")
    kpi, _ = run_summary(db, cfg, portfolio_id=2)
    assert kpi["slips_total"] == 1


def test_the_excluded_agent_and_the_window_are_applied(slips):
    db, cfg = slips
    april = Period.between(TODAY.replace(month=4, day=1), TODAY.replace(month=4, day=30))
    kpi, daily = queries.summary_queries(cfg, "auto", april, TODAY, "first")
    assert sq.run(db, kpi)[0]["slips_total"] == 0 and sq.run(db, daily) == []
    assert 999.0 not in {r["amount_maturing"] for r in run_summary(db, cfg)[1]}


def test_a_window_with_no_slips_has_no_ratio_and_zero_sums(db, sqlite_cfg):
    empty = Period.between(TODAY.replace(month=5, day=1), TODAY.replace(month=5, day=31))
    kpi = sq.run(db, queries.summary_queries(sqlite_cfg, "auto", empty, TODAY, "first")[0])[0]
    assert kpi["slips_total"] == 0 and kpi["conversion_pct"] is None and kpi["effectiveness_pct"] is None
    assert kpi["amount_maturing"] == 0


# ---------------------------------------------------------------- series


def test_the_daily_series_groups_by_issue_day(slips):
    db, cfg = slips
    rows = sq.run(db, queries.series_query(cfg, "auto", "daily-first"))
    assert rows == [{"issue_date": "2026-03-01", "slips_issued": 6, "slips_paid_in_grace": 1, "conversion_pct": 17}]


def test_the_monthly_series_rounds_the_ratio_to_a_whole_percent(slips):
    db, cfg = slips
    db.execute("UPDATE agreements SET paid_amount = 600, paid_at = '2026-03-17 00:00:00' WHERE agreement_id = 9003")
    rows = sq.run(db, queries.series_query(cfg, "auto", "monthly-first"))
    assert rows == [{"year": 2026, "month": 3, "slips_issued": 6, "slips_paid_in_grace": 2, "conversion_pct": 33}]


def test_the_by_due_series_groups_by_due_day(slips):
    db, cfg = slips
    rows = sq.run(db, queries.series_query(cfg, "auto", "daily-later-by-due"))
    assert rows == [{"due_date": "2026-03-25", "slips_issued": 1, "slips_paid_in_grace": 0, "conversion_pct": 0}]


def test_the_agent_series_leaves_out_the_excluded_agent(slips):
    db, cfg = slips
    rows = sq.run(db, queries.series_query(cfg, "auto", "monthly-agent-first"))
    assert [r["agent_login"] for r in rows] == ["agent_01"]
    assert rows[0]["slips_issued"] == 6


def test_the_by_due_agent_series_counts_broken_slips_and_applies_the_minimum(slips):
    db, cfg = slips
    db.execute("UPDATE agreements SET status_id = 22 WHERE row_id = 9")
    rows = sq.run(db, queries.series_query(cfg, "auto", "monthly-agent-later-by-due"))
    assert [(r["agent_login"], r["broken_count"]) for r in rows] == [("agent_01", 1)]
    strict = dataclasses.replace(cfg, effectiveness_min_slips=2)
    assert sq.run(db, queries.series_query(strict, "auto", "monthly-agent-later-by-due")) == []


def test_slips_before_the_floor_date_are_ignored(slips):
    db, cfg = slips
    later = dataclasses.replace(cfg, effectiveness_floor=TODAY)
    assert sq.run(db, queries.series_query(later, "auto", "daily-first")) == []


# ---------------------------------------------------------------- break curve and portfolios


def test_the_break_curve_bands_slips_by_days_since_the_due_date(slips):
    db, cfg = slips
    # Ages today: 9003 is 2 days past due; 9001, 9002, 9006 and 9007 are 8; 9005 is 13; 9004 is
    # not due yet and has no band. Only statuses of the break-curve set count, and the curve
    # reads slips whoever made the agreement, so the excluded agent's 9007 is in.
    rows = sq.run(db, queries.break_curve_query(cfg, "auto", MARCH, TODAY))
    by_band = {r["band"]: r for r in rows}
    assert by_band["0-7 days"] == {"band": "0-7 days", "slips_total": 1, "slips_broken": 0, "break_rate_pct": 0.0}
    assert by_band["8-21 days"]["slips_total"] == 5 and by_band["8-21 days"]["slips_broken"] == 1
    assert by_band["8-21 days"]["break_rate_pct"] == 20.0
    assert [r["band"] for r in rows] == ["0-7 days", "8-21 days"]  # bands without slips are absent


def test_a_slip_that_is_not_due_yet_has_no_age_and_no_band(slips):
    db, cfg = slips
    db.execute("DELETE FROM agreements WHERE agreement_id <> 9004")
    assert sq.run(db, queries.break_curve_query(cfg, "auto", MARCH, TODAY)) == []


def test_the_portfolio_summary_covers_the_slips_of_one_label(slips):
    db, cfg = slips
    summary = queries.portfolio_summary_query(cfg, "auto", MARCH, "first", "Portfolio A-01")
    row = sq.run(db, summary)[0]
    assert row["slips_total"] == 2  # 9001 and 9002 carry the first label
    assert row["slips_paid_in_grace"] == 1
    assert row["amount_maturing"] == 1500.0 and row["amount_received"] == 1000.0


def test_the_portfolio_ranking_is_ordered_by_the_maturing_amount(slips):
    db, cfg = slips
    rows = sq.run(db, queries.portfolio_ranking_query(cfg, "auto", MARCH, "first"))
    assert [r["portfolio_label"] for r in rows] == ["Portfolio A-02", "Portfolio A-01"]
    assert rows[0]["amount_maturing"] == 1800.0  # 9003 + 9004 + 9005 + 9006


def test_the_generation_ranking_counts_agreements_by_issue_date(slips):
    db, cfg = slips
    rows = sq.run(db, queries.generation_ranking_query(cfg, "auto", MARCH))
    by_label = {r["portfolio_label"]: r for r in rows}
    assert by_label["Portfolio A-01"]["agreement_count"] == 2
    assert by_label["Portfolio A-01"]["agreement_value"] == 1500.0
    assert rows[0]["portfolio_label"] == "Portfolio A-02"  # largest value first
    assert sum(r["agreement_count"] for r in rows) == 6  # the excluded agent's agreement is out
