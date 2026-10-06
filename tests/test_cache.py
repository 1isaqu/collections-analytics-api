import threading
import time

import pytest

from collections_api.core.cache import TtlCache
from collections_api.core.errors import ApiError


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_hit_miss_and_expiry():
    clock = Clock()
    cache = TtlCache(10, clock=clock)
    assert cache.get("k") is None
    cache.set("k", [1])
    assert cache.get("k") == [1]
    clock.now += 11
    assert cache.get("k") is None
    assert len(cache) == 0


def test_zero_ttl_disables_storage_but_still_computes():
    cache = TtlCache(0)
    calls = []
    assert cache.get_or_compute("k", lambda: calls.append(1) or "v") == "v"
    assert cache.get_or_compute("k", lambda: calls.append(1) or "v") == "v"
    assert len(calls) == 2
    assert len(cache) == 0


def test_second_call_is_served_from_the_cache():
    cache = TtlCache(60)
    calls = []
    fetch = lambda: calls.append(1) or {"rows": 3}  # noqa: E731
    assert cache.get_or_compute("k", fetch) == {"rows": 3}
    assert cache.get_or_compute("k", fetch) == {"rows": 3}
    assert len(calls) == 1


def test_concurrent_callers_share_one_computation():
    cache = TtlCache(60)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        started.set()
        release.wait(5)
        return "shared"

    results: list[str] = []
    threads = [
        threading.Thread(target=lambda: results.append(cache.get_or_compute("k", fetch)))
        for _ in range(5)
    ]
    threads[0].start()
    assert started.wait(5)
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(5)
    assert results == ["shared"] * 5
    assert len(calls) == 1


def test_leader_failure_reaches_followers_and_is_not_cached():
    cache = TtlCache(60)
    started, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def failing():
        started.set()
        release.wait(5)
        raise ValueError("boom")

    def run(fetch):
        try:
            cache.get_or_compute("k", fetch)
        except Exception as exc:
            errors.append(exc)

    leader = threading.Thread(target=run, args=(failing,))
    leader.start()
    assert started.wait(5)
    follower = threading.Thread(target=run, args=(lambda: "never",))
    follower.start()
    time.sleep(0.05)
    release.set()
    leader.join(5)
    follower.join(5)
    assert [type(e) for e in errors] == [ValueError, ValueError]
    assert cache.get("k") is None
    assert cache.get_or_compute("k", lambda: "recovered") == "recovered"


def test_follower_wait_is_bounded():
    cache = TtlCache(60, leader_wait_seconds=0.05)
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "late"

    leader = threading.Thread(target=lambda: cache.get_or_compute("k", slow))
    leader.start()
    assert started.wait(5)
    with pytest.raises(ApiError) as caught:
        cache.get_or_compute("k", lambda: "never")
    assert caught.value.status_code == 504
    release.set()
    leader.join(5)


def test_entry_ceiling_drops_expired_entries_first():
    clock = Clock()
    cache = TtlCache(10, max_entries=3, clock=clock)
    cache.set("old", 1)
    clock.now += 5
    cache.set("b", 2)
    cache.set("c", 3)
    clock.now += 6  # "old" expired, b and c still valid
    cache.set("d", 4)
    assert len(cache) == 3
    assert cache.get("old") is None
    assert {cache.get("b"), cache.get("c"), cache.get("d")} == {2, 3, 4}


def test_entry_ceiling_evicts_the_soonest_to_expire_when_all_are_valid():
    clock = Clock()
    cache = TtlCache(100, max_entries=2, clock=clock)
    cache.set("first", 1)
    clock.now += 1
    cache.set("second", 2)
    clock.now += 1
    cache.set("third", 3)
    assert len(cache) == 2
    assert cache.get("first") is None
    assert cache.get("second") == 2 and cache.get("third") == 3


def test_overwriting_a_key_does_not_trigger_eviction():
    cache = TtlCache(100, max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("a", 3)
    assert len(cache) == 2 and cache.get("b") == 2


def test_event_hook_reports_hits_and_misses_by_key_prefix():
    events: list[tuple[str, str]] = []
    cache = TtlCache(60, on_event=lambda name, prefix: events.append((name, prefix)))
    cache.get_or_compute("chart|x|auto", lambda: 1)
    cache.get_or_compute("chart|x|auto", lambda: 1)
    assert events == [("cache.miss", "chart"), ("cache.hit", "chart")]
