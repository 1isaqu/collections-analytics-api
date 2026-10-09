"""Chart and detail builders: structure, and results on a small invented portfolio.

The ``db`` fixture (see ``conftest.py``) holds seven agreements plus one issued by an
excluded agent. Builders run against SQLite through ``sqlite_cfg``, whose only change
is the portfolio lookup (SQLite has no APPLY).
"""

from __future__ import annotations

import re

import pytest

from collections_api.core.sql import ALL_LINES, Period
from collections_api.domains.charts import details, queries
from tests import sqlite_adapter as sq
from tests.conftest import TODAY
from tests.sql_inspect import final_select_aliases


def rows_by(rows, key):
    return {row[key]: row for row in rows}


# ---------------------------------------------------------------- structure


@pytest.mark.parametrize("kind", sorted(queries.PORTFOLIO_KINDS))
def test_portfolio_chart_columns_match_the_declared_measures(cfg, period, kind):
    spec = queries.PORTFOLIO_KINDS[kind]
    for line in ("auto", ALL_LINES):
        aliases = final_select_aliases(queries.by_portfolio(cfg, line, period, kind).sql)
        assert aliases == ["portfolio_label", spec.count, spec.value]


@pytest.mark.parametrize("kind", sorted(queries.AGENT_KINDS))
def test_agent_chart_columns_match_the_declared_measures(cfg, period, kind):
    spec = queries.AGENT_KINDS[kind]
    for line in ("auto", ALL_LINES):
        query, _ = queries.by_agent(cfg, line, period, kind)
        assert final_select_aliases(query.sql) == ["agent_name", spec.count, spec.value]


def test_other_chart_columns(cfg, period):
    summary, _ = queries.first_installment_summary(cfg, ALL_LINES, period, None)
    assert final_select_aliases(summary.sql) == list(queries.SUMMARY_COLUMNS)
    for line in ("consumer", ALL_LINES):
        assert final_select_aliases(queries.portfolio_rollup(cfg, line, period).sql) == list(
            queries.ROLLUP_COLUMNS
        )
        assert final_select_aliases(queries.received_by_portfolio(cfg, line, period).sql) == list(
            queries.RECEIVED_COLUMNS
        )
        assert final_select_aliases(
            queries.exceptions_without_portfolio(cfg, line, period).sql
        ) == list(queries.WITHOUT_PORTFOLIO_COLUMNS)


@pytest.mark.parametrize("kind", sorted(details.DETAIL_KINDS))
def test_detail_columns(cfg, period, kind):
    for line in ("auto", ALL_LINES):
        assert final_select_aliases(details.by_portfolio(cfg, line, period, kind, "x").sql) == list(
            details.DETAIL_COLUMNS
        )
        assert final_select_aliases(details.by_agent(cfg, line, period, kind, "x").sql) == list(
            details.DETAIL_COLUMNS
        )
        assert final_select_aliases(details.all_portfolios(cfg, line, period, kind).sql) == list(
            details.DETAIL_ALL_COLUMNS
        )


def test_all_lines_unions_one_base_per_line_and_resums(cfg, period):
    query = queries.by_portfolio(cfg, ALL_LINES, period, "exceptions")
    assert query.sql.count("UNION ALL") == 1
    assert "collections_auto.dbo.agreements" in query.sql
    assert "collections_consumer.dbo.agreements" in query.sql
    assert "SUM(exception_count) AS exception_count" in query.sql
    single = queries.by_portfolio(cfg, "auto", period, "exceptions")
    assert "UNION" not in single.sql and "collections_consumer" not in single.sql


def test_each_kind_filters_its_own_status_set_inside_the_ranking(cfg, period):
    expected = {
        "exceptions": "(24)",
        "rejected": "(25)",
        "broken": "(22)",
        "agreements": "(21, 22, 23, 26, 27)",
    }
    for kind, statuses in expected.items():
        sql = queries.by_portfolio(cfg, "auto", period, kind).sql
        derived = sql[sql.index("FROM (\n    SELECT R.*") : sql.index(") AS R")]
        assert f"R.status_id IN {statuses}" in derived
        assert "R.installment_no = 0" in derived
        assert period.contains("R.issued_at") in derived


def test_team_parameters_follow_placeholders_across_lines(cfg, period):
    query, label = queries.by_agent(cfg, ALL_LINES, period, "exceptions", "Region: North")
    assert label == "Region: North"
    assert query.sql.count("?") == len(query.params) == 4
    assert set(query.params) == {"%NORTH%"}
    none, label = queries.by_agent(cfg, "auto", period, "exceptions", None)
    assert none.params == () and label == "all"


def test_detail_portfolio_parameter_is_bound_once_per_line(cfg, period):
    assert details.by_portfolio(cfg, "auto", period, "exceptions", "Portfolio A-01").params == (
        "Portfolio A-01",
    )
    query = details.by_portfolio(cfg, ALL_LINES, period, "exceptions", "Portfolio A-01")
    assert query.params == ("Portfolio A-01",) * 2 and query.sql.count("= ?") == 2


def test_no_query_interpolates_client_text(cfg, period):
    hostile = "x'; DROP TABLE agreements; --"
    for query in (
        details.by_portfolio(cfg, "auto", period, "exceptions", hostile),
        details.by_agent(cfg, "auto", period, "exceptions", hostile),
        queries.by_agent(cfg, "auto", period, "exceptions", hostile)[0],
    ):
        assert "DROP TABLE" not in query.sql


# ---------------------------------------------------------------- results


def run(db, sqlite_cfg, builder, *args):
    result = builder(sqlite_cfg, *args)
    query = result[0] if isinstance(result, tuple) else result
    return sq.run(db, query)


def test_first_installment_summary_counts_generated_agreements_once(db, sqlite_cfg, period):
    (row,) = run(db, sqlite_cfg, queries.first_installment_summary, "auto", period, None)
    # 5001 (written twice, counted once), 5004 broken and 5006: generated. The excluded
    # agent's 999 is out, and so are the exception and rejected agreements.
    assert row == {"first_installment_value": 1000 + 200 + 400, "agreement_count": 3}


def test_summary_for_all_lines_sums_the_lines(db, sqlite_cfg, period):
    (row,) = run(db, sqlite_cfg, queries.first_installment_summary, ALL_LINES, period, None)
    # Both "lines" read the same tables here, so the totals double.
    assert row == {"first_installment_value": 2 * 1600, "agreement_count": 2 * 3}


def test_team_filter_narrows_to_matching_agents(db, sqlite_cfg, period):
    (row,) = run(db, sqlite_cfg, queries.first_installment_summary, "auto", period, "Agent 01")
    assert row["agreement_count"] == 3
    rows = run(db, sqlite_cfg, queries.first_installment_summary, "auto", period, "Agent 02")
    assert rows[0]["agreement_count"] == 0 or rows[0]["first_installment_value"] is None


def test_by_portfolio_agreements(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, queries.by_portfolio, "auto", period, "agreements")
    assert rows == [
        {"portfolio_label": "Portfolio A-02", "agreement_count": 2, "agreement_value": 600.0},
        {"portfolio_label": "Portfolio A-01", "agreement_count": 1, "agreement_value": 1000.0},
    ]


def test_by_portfolio_other_kinds(db, sqlite_cfg, period):
    exceptions = run(db, sqlite_cfg, queries.by_portfolio, "auto", period, "exceptions")
    assert exceptions == [
        {"portfolio_label": "Portfolio A-01", "exception_count": 1, "exception_value": 800.0}
    ]
    rejected = run(db, sqlite_cfg, queries.by_portfolio, "auto", period, "rejected")
    assert rejected == [
        {"portfolio_label": "Portfolio A-02", "rejected_count": 1, "rejected_value": 300.0}
    ]
    broken = run(db, sqlite_cfg, queries.by_portfolio, "auto", period, "broken")
    assert broken == [
        {"portfolio_label": "Portfolio A-02", "broken_count": 1, "broken_value": 200.0}
    ]


def test_first_installment_chart_is_ordered_by_value(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, queries.by_portfolio, "auto", period, "first-installment")
    assert [r["portfolio_label"] for r in rows] == ["Portfolio A-01", "Portfolio A-02"]
    assert rows[0]["first_installment_value"] == 1000.0


def test_all_lines_chart_resums_per_group(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, queries.by_portfolio, ALL_LINES, period, "agreements")
    assert rows_by(rows, "portfolio_label")["Portfolio A-02"] == {
        "portfolio_label": "Portfolio A-02",
        "agreement_count": 4,
        "agreement_value": 1200.0,
    }
    assert len(rows) == 2  # grouped, not one row per line


def test_rollup_slices_by_status(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, queries.portfolio_rollup, "auto", period)
    by_key = {(r["portfolio_label"], r["status_id"]): r for r in rows}
    assert by_key[("Portfolio A-01", sq.ACTIVE)] == {
        "portfolio_label": "Portfolio A-01",
        "status_id": sq.ACTIVE,
        "agreement_count": 1,
        "total_value": 1000.0,
    }
    assert by_key[("Portfolio A-01", sq.PENDING_EXCEPTION)]["total_value"] == 800.0
    assert by_key[("Portfolio A-02", sq.REJECTED)]["agreement_count"] == 1
    assert [(r["portfolio_label"], r["status_id"]) for r in rows] == sorted(
        (r["portfolio_label"], r["status_id"]) for r in rows
    )


def test_by_agent(db, sqlite_cfg, period):
    exceptions, _ = queries.by_agent(sqlite_cfg, "auto", period, "exceptions")
    assert sq.run(db, exceptions) == [
        {"agent_name": "Agent 02", "exception_count": 2, "exception_value": 950.0}
    ]
    first, _ = queries.by_agent(sqlite_cfg, "auto", period, "first-installment")
    assert sq.run(db, first) == [
        {"agent_name": "Agent 01", "first_installment_count": 3, "first_installment_value": 1600.0}
    ]


def test_excluded_agents_never_reach_any_result(db, sqlite_cfg, period):
    first, _ = queries.by_agent(sqlite_cfg, "auto", period, "first-installment")
    assert "System Bot" not in {r["agent_name"] for r in sq.run(db, first)}
    for kind in queries.PORTFOLIO_KINDS:
        rows = run(db, sqlite_cfg, queries.by_portfolio, "auto", period, kind)
        assert 999.0 not in [r[queries.PORTFOLIO_KINDS[kind].value] for r in rows]


def test_exceptions_without_portfolio_lists_only_unlabelled_agreements(db, cfg, period):
    # Pure SQL Server shape (NOT EXISTS runs in SQLite as well, so use the real config).
    rows = sq.run(db, queries.exceptions_without_portfolio(cfg, "auto", period))
    assert rows == [
        {
            "agreement_id": 5007,
            "portfolio_id": 1,
            "first_installment_value": 150.0,
            "total_value": 150.0,
            "agent_name": "Agent 02",
        }
    ]


def test_period_filters_the_window(db, sqlite_cfg):
    yesterday = Period.day(TODAY.replace(day=17))
    assert run(db, sqlite_cfg, queries.by_portfolio, "auto", yesterday, "agreements") == []
    wide = Period.between(TODAY.replace(day=1), TODAY)
    assert len(run(db, sqlite_cfg, queries.by_portfolio, "auto", wide, "agreements")) == 2


def test_received_by_portfolio_uses_the_payment_date(db, sqlite_cfg, period):
    sq.insert(
        db,
        "agreements",
        [
            # Issued earlier, paid today: counts for today's cash.
            sq.installment(
                row_id=21,
                agreement_id=6001,
                installment_no=0,
                amount=500.0,
                paid_amount=500.0,
                issued_at="2026-02-01 10:00:00",
                paid_at="2026-03-18 11:00:00",
                agent_id=1,
            ),
            sq.installment(
                row_id=22,
                agreement_id=6001,
                installment_no=1,
                amount=300.0,
                paid_amount=300.0,
                issued_at="2026-02-01 10:00:00",
                paid_at="2026-03-18 12:00:00",
                agent_id=1,
            ),
        ],
    )
    sq.insert(db, "agreement_debts", [{"agreement_id": 6001, "portfolio_id": 1, "debt_id": 31}])
    sq.insert(db, "debt_attributes", [{"debt_id": 31, "portfolio_label": "Portfolio A-03"}])
    rows = run(db, sqlite_cfg, queries.received_by_portfolio, "auto", period)
    assert rows == [
        {
            "portfolio_label": "Portfolio A-03",
            "agreement_count": 1,
            "received_value": 800.0,
            "first_installment_received": 500.0,
        }
    ]


def test_received_filters_sit_inside_the_ranking(cfg, period):
    sql = queries.received_by_portfolio(cfg, "auto", period).sql
    derived = sql[sql.index("FROM (\n    SELECT R.*") : sql.index(") AS R")]
    assert "R.paid_amount > 0" in derived and "R.paid_at >=" in derived


def test_a_later_rewrite_without_payment_does_not_hide_the_payment(db, sqlite_cfg, period):
    sq.insert(
        db,
        "agreements",
        [
            sq.installment(
                row_id=31,
                agreement_id=6100,
                amount=500.0,
                paid_amount=500.0,
                issued_at="2026-02-01 10:00:00",
                paid_at="2026-03-18 11:00:00",
            ),
            # Newer write of the same installment with no payment recorded.
            sq.installment(
                row_id=32,
                agreement_id=6100,
                amount=500.0,
                paid_amount=0.0,
                issued_at="2026-02-02 10:00:00",
                paid_at=None,
            ),
        ],
    )
    sq.insert(db, "agreement_debts", [{"agreement_id": 6100, "portfolio_id": 1, "debt_id": 41}])
    sq.insert(db, "debt_attributes", [{"debt_id": 41, "portfolio_label": "Portfolio A-04"}])
    rows = run(db, sqlite_cfg, queries.received_by_portfolio, "auto", period)
    assert {r["portfolio_label"]: r["received_value"] for r in rows}["Portfolio A-04"] == 500.0


# ---------------------------------------------------------------- details


def test_detail_by_portfolio_totals_cover_every_installment_once(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, details.by_portfolio, "auto", period, "agreements", "Portfolio A-01")
    assert rows == [
        {
            "agreement_id": 5001,
            "portfolio_id": 1,
            "first_installment_value": 1000.0,
            "total_value": 2000.0,
            "agent_name": "Agent 01",
            "agreement_date": "2026-03-18",
            "due_date": "2026-03-10",
            "installment_count": 3,
        }
    ]


def test_detail_by_portfolio_filters_the_label(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, details.by_portfolio, "auto", period, "agreements", "Portfolio A-02")
    assert [r["agreement_id"] for r in rows] == [5006, 5004]  # largest first installment first
    assert run(db, sqlite_cfg, details.by_portfolio, "auto", period, "agreements", "Nothing") == []


def test_an_agreement_with_two_labels_is_listed_where_the_chart_counts_it(sqlite_cfg, period):
    conn = sq.connect()
    sq.with_agents(conn, (1, "Agent 01", "agent_01"))
    sq.insert(conn, "agreements", [sq.installment(row_id=1, agreement_id=7001, amount=500.0)])
    # The label that sorts last is stored first, so insertion order cannot decide the pick.
    sq.insert(
        conn,
        "agreement_debts",
        [{"agreement_id": 7001, "portfolio_id": 1, "debt_id": d} for d in (71, 72)],
    )
    sq.insert(
        conn,
        "debt_attributes",
        [
            {"debt_id": 71, "portfolio_label": "Portfolio A-02"},
            {"debt_id": 72, "portfolio_label": "Portfolio A-01"},
        ],
    )
    chart = run(conn, sqlite_cfg, queries.by_portfolio, "auto", period, "agreements")
    assert chart == [
        {"portfolio_label": "Portfolio A-01", "agreement_count": 1, "agreement_value": 500.0}
    ]
    listed = {
        label: [
            r["agreement_id"]
            for r in run(
                conn, sqlite_cfg, details.by_portfolio, "auto", period, "agreements", label
            )
        ]
        for label in ("Portfolio A-01", "Portfolio A-02")
    }
    assert listed == {"Portfolio A-01": [7001], "Portfolio A-02": []}
    everything = run(conn, sqlite_cfg, details.all_portfolios, "auto", period, "agreements")
    assert [(r["agreement_id"], r["portfolio_label"]) for r in everything] == [
        (7001, "Portfolio A-01")
    ]


def test_detail_by_agent(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, details.by_agent, "auto", period, "exceptions", "Agent 02")
    assert sorted(r["agreement_id"] for r in rows) == [5002, 5007]
    assert run(db, sqlite_cfg, details.by_agent, "auto", period, "exceptions", "Agent 01") == []


def test_detail_all_adds_the_label_and_keeps_unlabelled_rows(db, sqlite_cfg, period):
    rows = run(db, sqlite_cfg, details.all_portfolios, "auto", period, "exceptions")
    by_id = rows_by(rows, "agreement_id")
    assert by_id[5002]["portfolio_label"] == "Portfolio A-01"
    assert by_id[5007]["portfolio_label"] is None


def test_detail_never_returns_personal_columns(cfg, period):
    for sql in (
        details.by_portfolio(cfg, "auto", period, "agreements", "x").sql,
        details.all_portfolios(cfg, ALL_LINES, period, "agreements").sql,
    ):
        assert not re.search(
            r"\b(tax_id|document|registration|debtor_name|phone|address)\b", sql, re.I
        )
