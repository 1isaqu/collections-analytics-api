"""Productivity routes, row validation, benchmarks and the consolidated agent effort."""

from __future__ import annotations

import pytest

from collections_api.core.errors import ApiError
from collections_api.core.sql import QueryConfig
from collections_api.domains.productivity.benchmarks import quartiles, summarize_benchmarks
from collections_api.domains.productivity.service import AgentEffortService
from collections_api.domains.productivity.validation import validate_productivity_rows
from collections_api.settings import Settings
from tests.conftest import TODAY
from tests.fakes import RecordingExecutor


def productivity_row(login="agent_01", name="Agent 01", **overrides):
    row = {
        "agent_login": login,
        "agent_name": name,
        "attempt_count": 10,
        "answer_count": 6,
        "cpc_count": 3,
        "cpc_rate_pct": 50,
        "agreement_count": 2,
        "agreements_per_attempt_pct": 20.0,
        "agreement_value": 1000.0,
        "avg_ticket": 500.0,
        "avg_installments": 2,
        "first_installment_value": 300.0,
        "exception_count": 1,
        "exception_value": 100.0,
    }
    row.update(overrides)
    return row


TWO_AGENTS = [productivity_row(), productivity_row("agent_02", "Agent 02", attempt_count=4)]


# ---------------------------------------------------------------- validation


def test_validation_zero_fills_counts_and_keeps_ratios_undefined():
    rows, metrics = validate_productivity_rows(
        [productivity_row(avg_ticket=None, cpc_rate_pct=None, agreements_per_attempt_pct=None)]
    )
    assert rows[0]["avg_ticket"] == 0
    assert rows[0]["cpc_rate_pct"] is None and rows[0]["agreements_per_attempt_pct"] is None
    assert metrics == {"rows": 1, "numeric_cast_failures": 0}


def test_validation_counts_values_it_could_not_read():
    rows, metrics = validate_productivity_rows(
        [productivity_row(agreement_value="n/a", cpc_rate_pct="high")]
    )
    assert rows[0]["agreement_value"] == 0.0 and rows[0]["cpc_rate_pct"] is None
    assert metrics["numeric_cast_failures"] == 2


def test_validation_turns_numeric_strings_into_numbers_and_names_into_text():
    rows, _ = validate_productivity_rows([productivity_row(agreement_value="12.5", agent_name=None)])
    assert rows[0]["agreement_value"] == 12.5 and rows[0]["agent_name"] == ""


def test_a_row_missing_a_required_field_is_a_server_error():
    row = productivity_row()
    del row["exception_value"]
    with pytest.raises(ApiError) as caught:
        validate_productivity_rows([row])
    assert caught.value.status_code == 500 and "exception_value" in caught.value.detail


# ---------------------------------------------------------------- /productivity


def test_productivity_returns_the_envelope_with_quality_facts(client_for):
    client = client_for(answers={"dashboard/productivity": TWO_AGENTS})
    body = client.get("/dashboard/productivity/auto").json()
    assert [r["agent_name"] for r in body["data"]] == ["Agent 01", "Agent 02"]
    assert body["meta"]["sources"] == ["auto"]
    assert body["meta"]["filters"] == {"line": "auto", "period": "today", "team": "all", "portfolio": None}
    quality = body["meta"]["quality"]
    assert quality["rows"] == 2 and quality["numeric_cast_failures"] == 0
    assert quality["portfolio_partial"] is False and quality["elapsed_ms"] >= 0
    assert body["errors"] == []


def test_productivity_reads_one_line_only(client_for):
    client = client_for()
    assert client.get("/dashboard/productivity/all").status_code == 400
    assert client.get("/dashboard/productivity/nowhere").status_code == 400


def test_the_team_filter_narrows_rows_after_the_cached_query(client_for):
    client = client_for(answers={"dashboard/productivity": TWO_AGENTS})
    everyone = client.get("/dashboard/productivity/auto").json()["data"]
    team = client.get("/dashboard/productivity/auto", params={"team": "Desk: 02"}).json()
    assert len(everyone) == 2
    assert [r["agent_login"] for r in team["data"]] == ["agent_02"]
    assert team["meta"]["filters"]["team"] == "Desk: 02"
    assert len(client.executor.calls) == 1  # both requests shared one cached query


def test_a_portfolio_filter_binds_the_label_and_nulls_the_ratios_that_need_all_effort(client_for):
    client = client_for(answers={"dashboard/productivity": TWO_AGENTS})
    body = client.get("/dashboard/productivity/auto", params={"portfolio": "Portfolio A-01"}).json()
    assert all(r["cpc_rate_pct"] is None and r["agreements_per_attempt_pct"] is None for r in body["data"])
    assert body["data"][0]["agreement_count"] == 2  # counts and values stay
    assert body["meta"]["quality"]["portfolio_partial"] is True
    params = client.executor.calls[0]["params"]
    assert params and set(params) == {"Portfolio A-01"}


def test_unreadable_numbers_are_reported_not_hidden(client_for):
    rows = [productivity_row(agreement_value="n/a")]
    body = client_for(answers={"dashboard/productivity": rows}).get("/dashboard/productivity/auto").json()
    assert body["data"][0]["agreement_value"] == 0.0
    assert body["meta"]["quality"]["numeric_cast_failures"] == 1


def test_a_row_the_contract_does_not_allow_is_a_controlled_500(client_for):
    broken = productivity_row()
    del broken["agent_login"]
    response = client_for(answers={"dashboard/productivity": [broken]}).get("/dashboard/productivity/auto")
    assert response.status_code == 500
    assert "agent_login" in response.json()["detail"]


def test_the_window_is_echoed_and_half_a_window_is_refused(client_for):
    client = client_for()
    ok = client.get("/dashboard/productivity/auto", params={"date_from": "2026-03-01", "date_to": "2026-03-10"})
    assert ok.json()["meta"]["filters"]["period"] == "2026-03-01/2026-03-10"
    half = client.get("/dashboard/productivity/auto", params={"date_from": "2026-03-01"})
    assert half.status_code == 400
    assert client.get("/dashboard/productivity/auto", params={"date_from": "soon", "date_to": "later"}).status_code == 422


# ---------------------------------------------------------------- /load-status


def failing_for(database):
    def answer(sql, db, params):
        if db == database:
            raise ApiError(504, "The query exceeded the database time limit.")
        return TWO_AGENTS

    return answer


def test_load_status_totals_each_line_and_adds_an_all_row(client_for):
    client = client_for(answers={"dashboard/load-status": TWO_AGENTS})
    body = client.get("/dashboard/load-status/all").json()
    by_line = {row["product_line"]: row for row in body["data"]}
    assert set(by_line) == {"auto", "consumer", "all"}
    assert by_line["auto"]["agent_count"] == 2 and by_line["auto"]["attempt_count"] == 14
    assert by_line["all"]["attempt_count"] == 28 and by_line["all"]["agreement_value"] == 4000.0
    assert body["meta"]["quality"] == {
        "status": "ok", "lines_requested": 2, "lines_ok": 2, "numeric_cast_failures": 0,
    }


def test_load_status_for_one_line_has_no_all_row(client_for):
    body = client_for(answers={"dashboard/load-status": TWO_AGENTS}).get("/dashboard/load-status/auto").json()
    assert [row["product_line"] for row in body["data"]] == ["auto"]


def test_a_failing_line_makes_the_status_partial_and_names_it(client_for):
    client = client_for(answers={"dashboard/load-status": failing_for("collections_consumer")})
    body = client.get("/dashboard/load-status/all").json()
    assert [row["product_line"] for row in body["data"]] == ["auto"]
    assert body["errors"] == [{"source": "consumer", "message": "The query exceeded the database time limit."}]
    assert body["meta"]["quality"]["status"] == "partial"
    assert body["meta"]["quality"]["lines_ok"] == 1


def test_every_line_failing_is_an_error_not_an_empty_success(client_for):
    answer = ApiError(504, "The query exceeded the database time limit.")
    response = client_for(answers={"dashboard/load-status": answer}).get("/dashboard/load-status/all")
    assert response.status_code == 500


def test_load_status_reports_numbers_it_had_to_zero(client_for):
    rows = [productivity_row(attempt_count="many")]
    body = client_for(answers={"dashboard/load-status": rows}).get("/dashboard/load-status/auto").json()
    assert body["meta"]["quality"]["status"] == "partial"
    assert body["meta"]["quality"]["numeric_cast_failures"] == 1
    assert "could not be read" in body["errors"][0]["message"]


# ---------------------------------------------------------------- /agent-performance


def test_agent_performance_passes_rows_through_and_caches_them(client_for):
    rows = [{"product_line": "auto", "agent_name": "Agent 01", "agent_login": "agent_01", "attempt_count": 3}]
    client = client_for(answers={"dashboard/agent-performance": rows})
    first = client.get("/dashboard/agent-performance/all").json()
    client.get("/dashboard/agent-performance/all")
    assert first["data"] == rows
    assert first["meta"]["sources"] == ["auto", "consumer"]
    assert len(client.executor.calls) == 1
    client.get("/dashboard/agent-performance/auto")
    assert len(client.executor.calls) == 2  # another line is another cache entry


# ---------------------------------------------------------------- /benchmarks


def benchmark_rows():
    return [
        {
            "agent_login": f"agent_{i}", "agent_name": f"Agent {i}", "cpc_rate_pct": 10.0 * i,
            "conversion_pct": 5.0 * i, "cash_effectiveness_pct": 20.0 * i, "exception_pct": None,
            "active_days": 8,
        }
        for i in range(1, 6)
    ]


def test_benchmarks_summarise_the_agents_as_quartiles(client_for):
    client = client_for(answers={"dashboard/benchmarks": benchmark_rows()})
    body = client.get("/dashboard/benchmarks/auto").json()
    data = body["data"]
    assert data["agent_count"] == 5 and data["lookback_months"] == 3
    assert data["cpc_rate_pct"] == {"q1": 20.0, "median": 30.0, "q3": 40.0, "top10_mean": 50.0, "mean": 30.0}
    assert data["exception_pct"]["median"] is None  # nobody had a value
    assert body["meta"]["total_rows"] == 1


def test_benchmarks_are_cached_per_line_and_lookback(client_for):
    client = client_for(answers={"dashboard/benchmarks": benchmark_rows()})
    client.get("/dashboard/benchmarks/auto")
    client.get("/dashboard/benchmarks/auto")
    assert len(client.executor.calls) == 1
    client.get("/dashboard/benchmarks/auto", params={"lookback_months": 6})
    assert len(client.executor.calls) == 2


@pytest.mark.parametrize("months", [0, 13, "x"])
def test_benchmark_lookback_has_bounds(client_for, months):
    response = client_for().get("/dashboard/benchmarks/auto", params={"lookback_months": months})
    assert response.status_code == 422


def test_benchmarks_refuse_all_lines(client_for):
    assert client_for().get("/dashboard/benchmarks/all").status_code == 400


def test_quartiles_ignore_missing_values_and_booleans():
    summary = quartiles([None, float("nan"), True, "x", 10, 20, 30, 40])
    assert summary == {"q1": 17.5, "median": 25.0, "q3": 32.5, "top10_mean": 40.0, "mean": 25.0}


def test_quartiles_of_nothing_are_all_null():
    assert quartiles([]) == dict.fromkeys(("q1", "median", "q3", "top10_mean", "mean"))


def test_the_top_group_is_ten_percent_with_a_floor_of_one():
    assert quartiles(list(range(1, 21)))["top10_mean"] == 19.5  # top two of twenty
    assert quartiles([5, 7])["top10_mean"] == 7.0


def test_benchmark_rows_that_lack_a_metric_column_are_a_programming_error():
    with pytest.raises(KeyError):
        summarize_benchmarks([{"cpc_rate_pct": 1}], 3)
    assert summarize_benchmarks([], 3)["agent_count"] == 0


# ---------------------------------------------------------------- agent effort


def effort_row(login, name, attempts, answers, cpcs):
    return {
        "agent_login": login, "agent_name": name,
        "attempt_count": attempts, "answer_count": answers, "cpc_count": cpcs,
    }


LINE_ROWS = {
    "collections_auto": [effort_row("agent_01", "Agent 01", 10, 6, 3), effort_row("agent_02", "Agent 02", 5, 2, 1)],
    "collections_consumer": [effort_row("AGENT_01", "Agent 01", 7, 4, 2), effort_row("", "", 9, 9, 9)],
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def effort_service(clock=None, ttl=60.0):
    executor = RecordingExecutor({"dashboard/agent-effort": lambda sql, db, params: LINE_ROWS[db]})
    cfg = QueryConfig.from_settings(Settings.defaults())
    service = AgentEffortService(executor, cfg, ttl_seconds=ttl, today=lambda: TODAY, clock=clock or Clock())
    return service, executor


def test_the_same_login_in_two_lines_is_one_person():
    service, _ = effort_service()
    agents, age = service.fetch()
    assert age == 0
    assert [a["agent_key"] for a in agents] == ["agent_01", "agent_02"]  # most attempts first
    one = agents[0]
    assert one["total"] == {"attempt_count": 17, "answer_count": 10, "cpc_count": 5}
    assert one["by_line"]["auto"]["attempt_count"] == 10
    assert one["by_line"]["consumer"]["attempt_count"] == 7


def test_a_row_with_neither_login_nor_name_is_dropped():
    service, _ = effort_service()
    assert all(a["agent_key"] for a in service.fetch()[0])


def test_every_line_is_queried_once_per_computation():
    service, executor = effort_service()
    service.fetch()
    assert sorted(call["database"] for call in executor.calls) == ["collections_auto", "collections_consumer"]


def test_the_payload_is_cached_until_it_expires_or_is_forced():
    clock = Clock()
    service, executor = effort_service(clock, ttl=60.0)
    service.fetch()
    clock.now += 30
    _, age = service.fetch()
    assert age == 30 and len(executor.calls) == 2  # still the first computation
    clock.now += 31
    assert service.fetch()[1] == 0 and len(executor.calls) == 4  # expired, queried again
    assert service.fetch(force_refresh=True)[1] == 0 and len(executor.calls) == 6


def test_agent_effort_route(client_for):
    answers = {"dashboard/agent-effort": lambda sql, db, params: LINE_ROWS[db]}
    client = client_for(answers=answers)
    first = client.get("/dashboard/agent-effort").json()
    assert first["meta"]["quality"] == {"cache_age_seconds": 0}
    assert first["meta"]["sources"] == ["auto", "consumer"]
    assert first["data"][0]["total"]["attempt_count"] == 17
    assert client.get("/dashboard/agent-effort").json()["meta"]["quality"]["cache_age_seconds"] >= 1
    forced = client.get("/dashboard/agent-effort", params={"force_refresh": "true"}).json()
    assert forced["meta"]["quality"]["cache_age_seconds"] == 0
    assert forced["meta"]["filters"]["force_refresh"] is True
