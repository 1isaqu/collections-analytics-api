"""The twelve agreement-list routes behind the charts: three shapes for four kinds."""

from __future__ import annotations

import pytest

from collections_api.domains.charts.details import DETAIL_KINDS

KINDS = sorted(DETAIL_KINDS)
DETAIL_ROW = {
    "agreement_id": 1,
    "portfolio_id": 1,
    "first_installment_value": 100.0,
    "total_value": 300.0,
    "agent_name": "Agent 01",
    "agreement_date": "2026-03-18",
    "due_date": "2026-03-25",
    "installment_count": 3,
}


@pytest.mark.parametrize("kind", KINDS)
def test_one_portfolio(client_for, kind):
    client = client_for(answers={f"dashboard/{kind}-detail": [DETAIL_ROW]})
    body = client.get(f"/dashboard/{kind}-detail/auto/Portfolio A-01").json()
    assert body["data"] == [DETAIL_ROW]
    assert body["meta"]["filters"] == {
        "line": "auto",
        "period": "today",
        "portfolio": "Portfolio A-01",
    }
    assert client.executor.calls[0]["params"] == ("Portfolio A-01",)


@pytest.mark.parametrize("kind", KINDS)
def test_every_portfolio(client_for, kind):
    row = {**DETAIL_ROW, "portfolio_label": None}
    client = client_for(answers={f"dashboard/{kind}-detail-all": [row]})
    body = client.get(f"/dashboard/{kind}-detail-all/all").json()
    assert body["data"] == [row] and body["meta"]["sources"] == ["auto", "consumer"]
    assert client.executor.calls[0]["params"] == ()


@pytest.mark.parametrize("kind", KINDS)
def test_one_agent(client_for, kind):
    client = client_for(answers={f"dashboard/{kind}-detail-by-agent": [DETAIL_ROW]})
    body = client.get(f"/dashboard/{kind}-detail-by-agent/consumer/Agent 01").json()
    assert body["meta"]["filters"]["agent"] == "Agent 01"
    assert client.executor.calls[0]["params"] == ("Agent 01",)
    assert client.executor.calls[0]["database"] == "collections_consumer"


def test_a_label_with_a_slash_still_routes(client_for):
    client = client_for()
    body = client.get("/dashboard/agreements-detail/auto/Portfolio A/01").json()
    assert body["meta"]["filters"]["portfolio"] == "Portfolio A/01"
    assert client.executor.calls[0]["params"] == ("Portfolio A/01",)
    by_agent = client.get("/dashboard/broken-detail-by-agent/auto/Agent 01/Jr").json()
    assert by_agent["meta"]["filters"]["agent"] == "Agent 01/Jr"


def test_the_label_is_a_bind_parameter_never_part_of_the_statement(client_for):
    client = client_for()
    hostile = "x'; DROP TABLE agreements; --"
    client.get(f"/dashboard/agreements-detail/auto/{hostile}")
    call = client.executor.calls[0]
    assert hostile not in call["sql"] and call["params"] == (hostile,)


def test_the_cache_tells_agents_and_portfolios_apart(client_for):
    client = client_for()
    for agent in ("Agent 01", "Agent 02", "Agent 01"):
        client.get(f"/dashboard/exceptions-detail-by-agent/auto/{agent}")
    assert len(client.executor.calls) == 2
    for portfolio in ("Portfolio A-01", "Portfolio A-02"):
        client.get(f"/dashboard/exceptions-detail/auto/{portfolio}")
    assert len(client.executor.calls) == 4


def test_the_window_applies_to_every_shape(client_for):
    client = client_for()
    params = {"date_from": "2026-03-01", "date_to": "2026-03-10"}
    for path in (
        "/dashboard/rejected-detail/auto/Portfolio A-01",
        "/dashboard/rejected-detail-all/auto",
        "/dashboard/rejected-detail-by-agent/auto/Agent 01",
    ):
        body = client.get(path, params=params).json()
        assert body["meta"]["filters"]["period"] == "2026-03-01/2026-03-10"
        assert client.get(path, params={"date_to": "2026-03-10"}).status_code == 400
    assert "CAST('20260311' AS DATE)" in client.executor.calls[0]["sql"]


def test_unknown_lines_are_refused_and_all_works_everywhere(client):
    assert client.get("/dashboard/broken-detail-all/nowhere").status_code == 400
    assert client.get("/dashboard/broken-detail/nowhere/Portfolio A-01").status_code == 400
    assert client.get("/dashboard/broken-detail/all/Portfolio A-01").status_code == 200


def test_the_first_installment_ordering_is_applied_across_lines(client_for):
    client = client_for()
    client.get("/dashboard/agreements-detail-all/all")
    sql = client.executor.calls[0]["sql"]
    assert "UNION ALL" in sql and sql.rstrip().endswith("ORDER BY first_installment_value DESC")
