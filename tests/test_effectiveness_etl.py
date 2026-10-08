"""The background refresh of the effectiveness series and the way it is served."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from collections_api.core.errors import ApiError
from collections_api.core.sql import Query, QueryConfig
from collections_api.domains.effectiveness.etl import EffectivenessStore
from collections_api.domains.effectiveness.queries import SERIES
from collections_api.domains.effectiveness.service import serve_series
from collections_api.settings import Settings
from tests.fakes import RecordingExecutor

CFG = QueryConfig.from_settings(Settings.defaults())
ROW = {
    "issue_date": "2026-03-01",
    "slips_issued": 2,
    "slips_paid_in_grace": 1,
    "conversion_pct": 50,
}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def builder(cfg, variant, key):
    return Query(f"SELECT '{key}' AS series, '{variant}' AS variant")


def store(executor, clock=None, series=("daily-first", "monthly-first")):
    return EffectivenessStore(
        executor, CFG, interval_seconds=0.01, clock=clock or Clock(), builder=builder, series=series
    )


def failing(key):
    return {
        f"effectiveness-refresh/{key}": ApiError(504, "The query exceeded the database time limit.")
    }


def test_a_refresh_queries_every_series_for_every_variant():
    executor = RecordingExecutor(default=[ROW])
    store(executor).refresh()
    contexts = sorted(call["context"] for call in executor.calls)
    assert contexts == sorted(
        f"effectiveness-refresh/{series}:{variant}"
        for series in ("daily-first", "monthly-first")
        for variant in ("all", "auto", "consumer")
    )
    assert {c["database"] for c in executor.calls} == {"collections_auto", "collections_consumer"}


def test_the_default_store_covers_all_nine_series():
    assert len(SERIES) == 9
    executor = RecordingExecutor(default=[ROW])
    EffectivenessStore(executor, CFG, builder=builder).refresh()
    assert len(executor.calls) == 9 * 3


def test_a_loaded_key_has_rows_a_timestamp_and_no_error():
    clock = Clock(5000.0)
    refreshed = store(RecordingExecutor(default=[ROW]), clock)
    assert refreshed.state("daily-first", "auto").rows is None  # nothing before the first pass
    refreshed.refresh()
    state = refreshed.state("daily-first", "auto")
    assert state.rows == [ROW] and state.fetched_at == 5000.0 and state.error is None
    assert refreshed.last_run == 5000.0


def test_a_failing_key_never_stops_the_others():
    executor = RecordingExecutor(failing("daily-first:auto"), default=[ROW])
    refreshed = store(executor)
    refreshed.refresh()
    assert refreshed.state("daily-first", "auto").rows is None
    assert (
        refreshed.state("daily-first", "auto").error
        == "The query exceeded the database time limit."
    )
    assert refreshed.state("daily-first", "consumer").rows == [ROW]
    assert refreshed.state("monthly-first", "auto").rows == [ROW]


def test_a_key_that_starts_failing_keeps_its_old_rows_and_its_old_timestamp():
    clock = Clock(1000.0)
    executor = RecordingExecutor(default=[ROW])
    refreshed = store(executor, clock)
    refreshed.refresh()
    clock.now = 9000.0
    executor.answers = failing("daily-first:auto")
    refreshed.refresh()
    state = refreshed.state("daily-first", "auto")
    assert state.rows == [ROW]  # still served
    assert state.fetched_at == 1000.0  # and honestly dated
    assert state.error is not None
    assert refreshed.state("daily-first", "consumer").fetched_at == 9000.0  # the others moved on


def test_a_key_that_recovers_clears_its_error():
    executor = RecordingExecutor(failing("daily-first:auto"), default=[ROW])
    refreshed = store(executor)
    refreshed.refresh()
    executor.answers = {}
    refreshed.refresh()
    assert refreshed.state("daily-first", "auto").error is None
    assert refreshed.state("daily-first", "auto").rows == [ROW]


def test_a_pass_where_everything_failed_is_not_a_refresh():
    executor = RecordingExecutor({"effectiveness-refresh": RuntimeError("boom")})
    refreshed = store(executor)
    refreshed.refresh()
    assert refreshed.last_run is None
    assert refreshed.snapshot() == {}
    assert refreshed.state("daily-first", "all").error == "boom"


def test_the_snapshot_lists_keys_and_ages():
    clock = Clock(42.0)
    refreshed = store(RecordingExecutor(default=[ROW]), clock, series=("daily-first",))
    refreshed.refresh()
    assert refreshed.snapshot() == {
        "daily-first:all": 42.0,
        "daily-first:auto": 42.0,
        "daily-first:consumer": 42.0,
    }


def test_the_background_thread_refreshes_and_stops():
    refreshed = store(RecordingExecutor(default=[ROW]), clock=time.time)
    refreshed.start()
    try:
        deadline = time.time() + 5
        while refreshed.last_run is None and time.time() < deadline:
            time.sleep(0.01)
        assert refreshed.last_run is not None
        refreshed.start()  # a second start is a no-op
    finally:
        refreshed.stop()
    assert refreshed._thread is not None and not refreshed._thread.is_alive()


def test_the_interval_is_exposed_for_the_health_check():
    assert store(RecordingExecutor()).interval_seconds == 0.01


# ---------------------------------------------------------------- serving


def test_serving_before_the_first_refresh_is_a_503():
    with pytest.raises(ApiError) as caught:
        serve_series(store(RecordingExecutor()), CFG, "daily-first", "all")
    assert caught.value.status_code == 503


def test_a_served_series_says_when_it_was_read_and_that_it_is_fresh():
    moment = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
    refreshed = store(RecordingExecutor(default=[ROW]), Clock(moment.timestamp()))
    refreshed.refresh()
    envelope = serve_series(refreshed, CFG, "daily-first", "consumer")
    assert envelope["data"] == [ROW]
    assert envelope["meta"]["sources"] == ["consumer"]
    assert envelope["meta"]["quality"] == {"fetched_at": moment.isoformat(), "stale": False}
    assert envelope["meta"]["filters"] == {
        "series": "daily-first",
        "line": "consumer",
        "from": "2020-01-01",
    }
    assert envelope["errors"] == []


def test_a_series_whose_last_refresh_failed_is_marked_stale_and_explains_why():
    executor = RecordingExecutor(default=[ROW])
    refreshed = store(executor)
    refreshed.refresh()
    executor.answers = failing("daily-first:all")
    refreshed.refresh()
    envelope = serve_series(refreshed, CFG, "daily-first", "all")
    assert envelope["data"] == [ROW]
    assert envelope["meta"]["quality"]["stale"] is True
    assert envelope["errors"][0]["source"] == "daily-first:all"
    assert "The last refresh failed" in envelope["errors"][0]["message"]
    other = serve_series(refreshed, CFG, "monthly-first", "all")
    assert other["meta"]["quality"]["stale"] is False  # staleness is per series
