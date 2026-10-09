from datetime import date

import pytest

from collections_api.core.errors import ApiError
from collections_api.core.sql import (
    ALL_LINES,
    Period,
    Query,
    QueryConfig,
    quote_ident,
    sql_in,
    team_filter,
    team_params,
)
from collections_api.settings import Settings

TODAY = date(2026, 3, 18)


@pytest.fixture()
def cfg() -> QueryConfig:
    return QueryConfig.from_settings(Settings.defaults())


def test_period_defaults_to_today():
    period = Period.from_query(None, None, TODAY)
    assert period.is_today and period.label == "today"
    assert period.start == TODAY and period.end_exclusive == date(2026, 3, 19)


def test_period_range_is_inclusive_for_the_client_and_half_open_in_sql():
    period = Period.from_query(date(2026, 3, 1), date(2026, 3, 31), TODAY)
    assert period.label == "2026-03-01/2026-03-31"
    assert period.end_exclusive == date(2026, 4, 1)
    assert period.contains("R.issued_at") == (
        "R.issued_at >= CAST('20260301' AS DATE) AND R.issued_at < CAST('20260401' AS DATE)"
    )


def test_reversed_range_is_swapped():
    period = Period.from_query(date(2026, 3, 31), date(2026, 3, 1), TODAY)
    assert period.start == date(2026, 3, 1) and period.end_exclusive == date(2026, 4, 1)


@pytest.mark.parametrize("pair", [(date(2026, 1, 1), None), (None, date(2026, 1, 1))])
def test_half_a_period_is_rejected_instead_of_answering_for_today(pair):
    with pytest.raises(ApiError) as caught:
        Period.from_query(*pair, TODAY)
    assert caught.value.status_code == 400


def test_line_resolution(cfg):
    assert cfg.resolve_line("AUTO") == "auto"
    assert cfg.resolve_line(" all ") == ALL_LINES
    with pytest.raises(ApiError) as caught:
        cfg.resolve_line("nope")
    assert caught.value.status_code == 400 and "auto, consumer, all" in caught.value.detail
    with pytest.raises(ApiError):
        cfg.resolve_line("all", allow_all=False)


def test_scope_and_connection_database(cfg):
    assert cfg.scope("consumer") == [("consumer", "collections_consumer")]
    assert [name for name, _ in cfg.scope("all")] == ["auto", "consumer"]
    assert cfg.connection_database("all") == "collections_auto"
    assert cfg.connection_database("consumer") == "collections_consumer"


def test_across_lines_keeps_parameters_in_placeholder_order(cfg):
    query = cfg.across_lines("all", lambda line, db: Query(f"SELECT '{line}' WHERE x = ?", (db,)))
    assert query.sql.count("?") == len(query.params) == 2
    assert query.params == ("collections_auto", "collections_consumer")
    assert query.sql.index("'auto'") < query.sql.index("UNION ALL") < query.sql.index("'consumer'")
    single = cfg.across_lines("auto", lambda line, db: Query("SELECT 1 WHERE x = ?", (db,)))
    assert "UNION" not in single.sql and single.params == ("collections_auto",)


def test_table_reference_uses_three_part_names_and_the_read_hint(cfg):
    assert cfg.table("collections_auto", "agreements", "R") == (
        "collections_auto.dbo.agreements AS R WITH (NOLOCK)"
    )
    no_hint = QueryConfig.from_settings(Settings.from_env({"DB_READ_HINT": ""}))
    assert no_hint.table("d", "agents", "U") == "d.dbo.agents AS U"


def test_dedup_ranks_after_the_business_filter_runs(cfg):
    sql = cfg.dedup("d", "R", extra_filter="AND R.status_id IN (21)")
    assert "PARTITION BY R.agreement_id, R.portfolio_id, R.installment_no" in sql
    assert "ORDER BY R.issued_at DESC, R.row_id DESC" in sql
    assert (
        sql.index("status_id IN (21)") < sql.index("ROW_NUMBER")
        or "WHERE 1 = 1 AND R.status_id" in sql
    )
    # The filter sits inside the derived table, before the ranking is applied to the result.
    assert sql.strip().endswith(")") and sql.index("WHERE 1 = 1") < sql.index("status_id IN (21)")


def test_excluded_agents_apply_every_pattern_to_login_and_name(cfg):
    clause = cfg.excluded_agents("U")
    for pattern in ("SYSTEM%", "TEST%"):
        for column in ("login", "display_name"):
            assert f"UPPER(LTRIM(RTRIM(COALESCE(U.{column}, '')))) NOT LIKE '{pattern}'" in clause
    assert clause.count("NOT LIKE") == 4


def test_effectiveness_exclusion_is_a_separate_list(cfg):
    assert "NOT LIKE '%SYSTEM%'" in cfg.effectiveness_excluded_agents("U")
    assert "'%SYSTEM%'" not in cfg.excluded_agents("U")


def test_paid_in_grace_uses_the_configured_days(cfg):
    assert cfg.paid_in_grace("R") == (
        "CASE WHEN R.paid_amount > 0 AND R.paid_at <= DATEADD(DAY, 3, R.due_at) THEN 1 ELSE 0 END"
    )
    assert (
        "DATEADD(DAY, 9,"
        in QueryConfig.from_settings(Settings.from_env({"GRACE_DAYS": "9"})).paid_in_grace()
    )


def test_portfolio_lookup_matches_on_the_composite_key(cfg):
    sql = cfg.portfolio_label_lookup("d", "R", outer=False)
    assert sql.startswith("CROSS APPLY")
    assert "RD.agreement_id = R.agreement_id" in sql and "RD.portfolio_id = R.portfolio_id" in sql
    assert "DA2.portfolio_label IS NOT NULL" in sql
    assert "LTRIM(RTRIM(DA2.portfolio_label)) <> ''" in sql  # a blank label counts as missing
    assert "ORDER BY DA2.portfolio_label" in sql  # TOP 1 without an order is not repeatable
    assert cfg.portfolio_label_lookup("d", "R", outer=True).startswith("OUTER APPLY")


def test_team_filter_variants():
    assert team_filter(None) == ("all", "", "")
    assert team_filter(" ALL ") == ("all", "", "")
    label, token, clause = team_filter("Region: north ")
    assert (label, token) == ("Region: north", "NORTH")
    assert clause.count("?") == 2 and "U.login" in clause and "U.display_name" in clause
    assert team_params(token, 2) == ("%NORTH%",) * 4
    assert team_params("") == ()


def test_sql_helpers():
    assert sql_in((1, 3, 12)) == "(1, 3, 12)"
    assert quote_ident("a]b") == "[a]]b]"
    joined = Query.union([Query("A", (1,)), Query("B", (2, 3))])
    assert joined.sql == "A\nUNION ALL\nB" and joined.params == (1, 2, 3)
