"""Effectiveness routes: series from memory, summary, slips, break curve, portfolio views."""

from __future__ import annotations

import pytest

from collections_api.api.routers.effectiveness import normalize_kpis, pick_days
from collections_api.domains.effectiveness import queries

SERIES_ROW = {
    "issue_date": "2026-03-01",
    "slips_issued": 2,
    "slips_paid_in_grace": 1,
    "paid_in_grace_pct": 50,
}
WINDOW = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def kpis(**overrides):
    row = dict.fromkeys(queries.SUMMARY_COLUMNS, 0)
    row.update(paid_in_grace_pct=25.0, effectiveness_pct=40.0, slips_total=4, slips_paid_in_grace=1)
    row.update(overrides)
    return row


def day(due, total, pct):
    return {"due_date": due, "slips_total": total, "effectiveness_pct": pct}


# ---------------------------------------------------------------- series


@pytest.mark.parametrize("key", sorted(queries.SERIES))
def test_every_series_has_a_route_that_answers_503_before_the_first_refresh(client, key):
    response = client.get(f"/effectiveness/{key}")
    assert response.status_code == 503
    assert "not loaded yet" in response.json()["detail"]


def test_a_series_is_served_from_memory_after_a_refresh(client_for):
    client = client_for(answers={"effectiveness-refresh": [SERIES_ROW]})
    app_context = client.app.state.ctx
    app_context.effectiveness.refresh()
    calls_after_refresh = len(client.executor.calls)

    body = client.get("/effectiveness/daily-first", params={"line": "consumer"}).json()
    assert body["data"] == [SERIES_ROW]
    assert body["meta"]["sources"] == ["consumer"]
    assert body["meta"]["quality"]["stale"] is False
    assert body["meta"]["run_id"]
    assert len(client.executor.calls) == calls_after_refresh  # a request never queries


def test_a_series_defaults_to_every_line_and_refuses_an_unknown_one(client_for):
    client = client_for(answers={"effectiveness-refresh": [SERIES_ROW]})
    client.app.state.ctx.effectiveness.refresh()
    assert client.get("/effectiveness/monthly-first").json()["meta"]["sources"] == [
        "auto",
        "consumer",
    ]
    assert client.get("/effectiveness/monthly-first", params={"line": "nowhere"}).status_code == 400


# ---------------------------------------------------------------- summary


def test_kpis_default_counts_to_zero_and_leave_ratios_undefined():
    clean = normalize_kpis(
        {"slips_total": None, "paid_in_grace_pct": None, "amount_received": None}
    )
    assert clean["slips_total"] == 0 and clean["amount_received"] == 0
    assert clean["paid_in_grace_pct"] is None and clean["effectiveness_pct"] is None
    assert set(clean) == set(queries.SUMMARY_COLUMNS)
    assert normalize_kpis(None)["slips_total"] == 0


def test_the_best_and_worst_day_ignore_days_without_slips_or_without_a_ratio():
    days = [
        day("03-01", 3, 50.0),
        day("03-02", 0, 99.0),
        day("03-03", 2, None),
        day("03-04", 5, 10.0),
        day("03-05", 1, 90.0),
    ]
    best, worst = pick_days(days)
    assert best["due_date"] == "03-05" and worst["due_date"] == "03-04"
    assert pick_days([]) == (None, None)
    assert pick_days([day("03-01", 0, 5.0)]) == (None, None)


def test_summary_returns_kpis_the_daily_series_and_the_extreme_days(client_for):
    daily = [day("2026-03-02", 2, 30.0), day("2026-03-03", 3, 70.0)]
    client = client_for(
        answers={"effectiveness/summary-kpi": [kpis()], "effectiveness/summary-daily": daily}
    )
    body = client.get("/effectiveness/summary", params=WINDOW).json()
    data = body["data"]
    assert data["kpis"]["slips_total"] == 4 and data["daily"] == daily
    assert (
        data["best_day"]["due_date"] == "2026-03-03"
        and data["worst_day"]["due_date"] == "2026-03-02"
    )
    assert body["meta"]["total_rows"] == 1
    assert body["meta"]["filters"] == {
        "line": "all",
        "period": "2026-03-01/2026-03-31",
        "installment_type": "first",
        "portfolio_id": None,
    }


def test_summary_of_an_empty_window_is_zeros_not_an_error(client_for):
    data = client_for().get("/effectiveness/summary", params=WINDOW).json()["data"]
    assert data["kpis"]["slips_total"] == 0 and data["kpis"]["paid_in_grace_pct"] is None
    assert data["daily"] == [] and data["best_day"] is None and data["worst_day"] is None


def test_summary_passes_the_portfolio_id_and_the_installment_type_to_the_query(client_for):
    client = client_for()
    client.get(
        "/effectiveness/summary", params={**WINDOW, "portfolio_id": 3, "installment_type": "later"}
    )
    sql = client.executor.calls[0]["sql"]
    assert "R.portfolio_id = 3" in sql and "installment_no > 0" in sql


def test_summary_is_cached_per_window_and_options(client_for):
    client = client_for()
    for _ in range(2):
        client.get("/effectiveness/summary", params=WINDOW)
    assert len(client.executor.calls) == 2  # the two statements, once
    client.get("/effectiveness/summary", params={**WINDOW, "installment_type": "later"})
    assert len(client.executor.calls) == 4


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-03-01"},
        {**WINDOW, "installment_type": "second"},
        {**WINDOW, "portfolio_id": "x"},
        {"date_from": "not-a-date", "date_to": "2026-03-31"},
    ],
)
def test_summary_rejects_bad_parameters(client, params):
    assert client.get("/effectiveness/summary", params=params).status_code == 422


# ---------------------------------------------------------------- slips


def slip_rows(count):
    return [{"agreement_id": i, "portfolio_id": 1, "slip_amount": 10.0} for i in range(count)]


def test_slips_list_one_bucket(client_for):
    client = client_for(answers={"effectiveness/slips-broken": slip_rows(2)})
    body = client.get("/effectiveness/slips", params={**WINDOW, "kind": "broken"}).json()
    assert len(body["data"]) == 2
    assert body["meta"]["filters"]["kind"] == "broken"
    assert body["meta"]["quality"] == {"truncated": False}
    assert body["meta"]["pagination"] == {"limit": 250, "truncated": False}


def test_slips_say_when_the_cap_cut_the_list(client_for):
    client = client_for(
        {"EFFECTIVENESS_DETAIL_ROW_LIMIT": "3"}, answers={"effectiveness/slips": slip_rows(3)}
    )
    body = client.get("/effectiveness/slips", params={**WINDOW, "kind": "to-mature"}).json()
    assert body["meta"]["quality"]["truncated"] is True
    assert body["meta"]["pagination"] == {"limit": 3, "truncated": True}


def test_slips_need_a_known_kind(client):
    assert client.get("/effectiveness/slips", params=WINDOW).status_code == 422
    assert client.get("/effectiveness/slips", params={**WINDOW, "kind": "lost"}).status_code == 422


def test_each_kind_is_a_separate_cache_entry(client_for):
    client = client_for()
    for kind in ("broken", "paid-in-grace", "broken"):
        client.get("/effectiveness/slips", params={**WINDOW, "kind": kind})
    assert len(client.executor.calls) == 2


# ---------------------------------------------------------------- the rest


def test_break_curve(client_for):
    rows = [{"band": "0-7 days", "slips_total": 4, "slips_broken": 1, "break_rate_pct": 25.0}]
    client = client_for(answers={"effectiveness/break-curve": rows})
    body = client.get("/effectiveness/break-curve", params=WINDOW).json()
    assert body["data"] == rows and body["meta"]["filters"]["period"] == "2026-03-01/2026-03-31"


def test_portfolio_summary_binds_the_label_once_per_line(client_for):
    row = {
        "slips_total": 2,
        "slips_paid_in_grace": 1,
        "amount_maturing": 10.0,
        "amount_received": 5.0,
        "effectiveness_pct": 50.0,
    }
    client = client_for(answers={"effectiveness/portfolio-summary": [row]})
    body = client.get(
        "/effectiveness/portfolio-summary", params={**WINDOW, "portfolio": "Portfolio A-01"}
    ).json()
    assert body["data"] == [row] and body["meta"]["filters"]["portfolio"] == "Portfolio A-01"
    assert client.executor.calls[0]["params"] == ("Portfolio A-01", "Portfolio A-01")
    assert client.get("/effectiveness/portfolio-summary", params=WINDOW).status_code == 422


def test_portfolio_ranking_and_generation_ranking(client_for):
    ranking = [{"portfolio_label": "Portfolio A-01", "slips_total": 1}]
    generation = [
        {"portfolio_label": "Portfolio A-01", "agreement_count": 1, "agreement_value": 10.0}
    ]
    client = client_for(
        answers={
            "effectiveness/portfolio-ranking": ranking,
            "effectiveness/generation-ranking": generation,
        }
    )
    assert client.get("/effectiveness/portfolio-ranking", params=WINDOW).json()["data"] == ranking
    body = client.get("/effectiveness/generation-ranking", params=WINDOW).json()
    assert body["data"] == generation
    assert body["meta"]["filters"] == {"line": "all", "period": "2026-03-01/2026-03-31"}


def test_a_reversed_window_is_swapped_and_an_unknown_line_is_refused(client):
    reversed_window = {"date_from": "2026-03-31", "date_to": "2026-03-01"}
    body = client.get("/effectiveness/break-curve", params=reversed_window).json()
    assert body["meta"]["filters"]["period"] == "2026-03-01/2026-03-31"
    assert (
        client.get("/effectiveness/break-curve", params={**WINDOW, "line": "nowhere"}).status_code
        == 400
    )
