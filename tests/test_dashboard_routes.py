"""Agreement lists, the performance table and the chart routes."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import TODAY


def today_row(n=0):
    return {"_total_rows": 5, "agent_name": "Agent 01", "agreement_id": n, "portfolio_id": 1}


# ---------------------------------------------------------------- agreements-today


def test_agreements_today_pages_one_line(client_for):
    client = client_for(answers={"dashboard/agreements-today": [today_row(1), today_row(2)]})
    body = client.get("/dashboard/agreements-today/auto", params={"limit": 2, "offset": 4}).json()
    assert [r["agreement_id"] for r in body["data"]] == [1, 2]
    assert all(r["product_line"] == "auto" and "_total_rows" not in r for r in body["data"])
    assert body["meta"]["pagination"] == {"limit": 2, "offset": 4, "total": 5, "returned": 2}
    assert client.executor.calls[0]["params"] == (4, 2)  # offset first, as OFFSET/FETCH wants it


def test_agreements_today_for_all_lines_pages_each_line_and_totals_them(client_for):
    client = client_for(answers={"dashboard/agreements-today": [today_row(1)]})
    body = client.get("/dashboard/agreements-today/all").json()
    assert [r["product_line"] for r in body["data"]] == ["auto", "consumer"]
    pagination = body["meta"]["pagination"]
    assert pagination["total"] == 10 and pagination["returned"] == 2
    assert pagination["totals_by_line"] == {"auto": 5, "consumer": 5}


def test_the_page_size_is_capped_and_defaulted_from_the_settings(client_for):
    client = client_for({"AGREEMENTS_DEFAULT_LIMIT": "50", "AGREEMENTS_MAX_LIMIT": "100"})
    assert (
        client.get("/dashboard/agreements-today/auto").json()["meta"]["pagination"]["limit"] == 50
    )
    assert (
        client.get("/dashboard/agreements-today/auto", params={"limit": 5000}).json()["meta"][
            "pagination"
        ]["limit"]
        == 100
    )


@pytest.mark.parametrize("params", [{"limit": 0}, {"offset": -1}, {"limit": "many"}])
def test_paging_parameters_are_validated(client, params):
    assert client.get("/dashboard/agreements-today/auto", params=params).status_code == 422


def test_an_empty_page_is_a_success_with_a_zero_total(client):
    body = client.get("/dashboard/agreements-today/auto").json()
    assert body["data"] == [] and body["meta"]["pagination"]["total"] == 0


# ---------------------------------------------------------------- agreements-table, performance, labels


def test_agreements_table_echoes_the_filters_and_caches_by_team_and_agent(client_for):
    client = client_for(answers={"dashboard/agreements-table": [{"agreement_id": 1}]})
    base = "/dashboard/agreements-table/auto"
    body = client.get(base, params={"team": "Desk: 01", "agent_name": " Agent 01 "}).json()
    assert body["meta"]["filters"] == {
        "line": "auto",
        "period": "today",
        "team": "Desk: 01",
        "agent_name": "Agent 01",
    }
    client.get(base, params={"team": "Desk: 01", "agent_name": "Agent 01"})
    assert len(client.executor.calls) == 1
    client.get(base)
    assert len(client.executor.calls) == 2
    assert client.executor.calls[0]["params"] == ("%01%", "%01%", "Agent 01")


def test_the_performance_window_defaults_to_the_configured_number_of_days(client_for):
    client = client_for({"PERFORMANCE_DEFAULT_DAYS": "7"})
    period = client.get("/dashboard/performance-table/auto").json()["meta"]["filters"]["period"]
    assert period == f"{TODAY - timedelta(days=6)}/{TODAY}"


def test_the_performance_window_can_be_given_and_must_be_whole(client):
    given = client.get(
        "/dashboard/performance-table/all",
        params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
    )
    assert given.json()["meta"]["filters"]["period"] == "2026-01-01/2026-01-31"
    assert (
        client.get("/dashboard/performance-table/all", params={"date_to": "2026-01-31"}).status_code
        == 400
    )


def test_performance_filters_one_agent_with_a_bind_parameter(client_for):
    client = client_for()
    client.get("/dashboard/performance-table/auto", params={"agent_name": "Agent 01"})
    call = client.executor.calls[0]
    assert call["params"] == ("Agent 01",) and "Agent 01" not in call["sql"]


def test_portfolio_labels(client_for):
    rows = [{"portfolio_label": "Portfolio A-01"}, {"portfolio_label": "Portfolio A-02"}]
    client = client_for(answers={"dashboard/portfolios": rows})
    body = client.get("/dashboard/portfolios/all").json()
    assert body["data"] == rows and body["meta"]["filters"] == {"line": "all"}


# ---------------------------------------------------------------- charts


PORTFOLIO_CHARTS = [
    "exceptions-by-portfolio",
    "agreements-by-portfolio",
    "rejected-by-portfolio",
    "broken-by-portfolio",
    "first-installment-by-portfolio",
    "portfolio-rollup",
    "received-by-portfolio",
    "exceptions-without-portfolio",
]
AGENT_CHARTS = ["exceptions-by-agent", "first-installment-by-agent", "first-installment-summary"]


@pytest.mark.parametrize("chart", PORTFOLIO_CHARTS + AGENT_CHARTS)
def test_every_chart_answers_the_envelope_for_today_and_for_a_window(client_for, chart):
    row = {"portfolio_label": "Portfolio A-01", "agent_name": "Agent 01"}
    client = client_for(answers={"dashboard/": [row]})
    today = client.get(f"/dashboard/{chart}/auto").json()
    assert today["data"] == [row] and today["meta"]["filters"]["period"] == "today"
    window = client.get(
        f"/dashboard/{chart}/all", params={"date_from": "2026-03-01", "date_to": "2026-03-15"}
    ).json()
    assert window["meta"]["filters"]["period"] == "2026-03-01/2026-03-15"
    assert window["meta"]["sources"] == ["auto", "consumer"]


@pytest.mark.parametrize("chart", PORTFOLIO_CHARTS + AGENT_CHARTS)
def test_every_chart_refuses_an_unknown_line_and_half_a_window(client, chart):
    assert client.get(f"/dashboard/{chart}/nowhere").status_code == 400
    assert (
        client.get(f"/dashboard/{chart}/auto", params={"date_from": "2026-03-01"}).status_code
        == 400
    )


@pytest.mark.parametrize("chart", AGENT_CHARTS)
def test_the_agent_charts_take_a_team_and_share_the_cache_per_team(client_for, chart):
    client = client_for()
    body = client.get(f"/dashboard/{chart}/auto", params={"team": "Desk: 02"}).json()
    assert body["meta"]["filters"]["team"] == "Desk: 02"
    client.get(f"/dashboard/{chart}/auto", params={"team": "Desk: 02"})
    assert len(client.executor.calls) == 1
    client.get(f"/dashboard/{chart}/auto")
    assert len(client.executor.calls) == 2


def test_the_team_token_is_bound_twice_per_use_of_the_clause(client_for):
    client = client_for()
    client.get("/dashboard/exceptions-by-agent/auto", params={"team": "Desk: 02"})
    assert client.executor.calls[0]["params"] == ("%02%", "%02%")


def test_chart_statements_use_literals_for_the_window_and_the_read_hint(client_for):
    client = client_for()
    client.get(
        "/dashboard/agreements-by-portfolio/auto",
        params={"date_from": "2026-03-01", "date_to": "2026-03-15"},
    )
    sql = client.executor.calls[0]["sql"]
    assert "CAST('20260301' AS DATE)" in sql and "CAST('20260316' AS DATE)" in sql
    assert "WITH (NOLOCK)" in sql and "DECLARE" not in sql.upper()


def test_a_single_day_window_still_labels_itself_as_dates():
    # Guards the label used in cache keys: a one-day explicit window is not "today".
    from collections_api.core.sql import Period

    day = date(2026, 3, 18)
    assert Period.between(day, day).label == "2026-03-18/2026-03-18"
    assert Period.day(day).label == "today"
