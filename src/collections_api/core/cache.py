"""In-process TTL cache with single-flight computation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from collections_api.core.errors import ApiError

MetricHook = Callable[[str, str], None]


class TtlCache:
    """Entries expire after ``ttl_seconds``; a TTL of zero or less disables the cache.

    ``get_or_compute`` is single-flight: concurrent callers with the same key share
    one execution of the fetcher instead of each sending the same query to the
    database. Followers wait at most ``leader_wait_seconds`` for the leader, so a hung
    query fails one request instead of draining the whole thread pool.

    Keys embed free text from the client (portfolio, agent, dates), so the store has
    a hard entry ceiling. When it is reached, expired entries go first, then the
    entry that expires soonest.
    """

    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = 500,
        leader_wait_seconds: float = 65.0,
        clock: Callable[[], float] = time.time,
        on_event: MetricHook | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._leader_wait = leader_wait_seconds
        self._clock = clock
        self._on_event = on_event
        self._store: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, _Flight] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def get(self, key: str) -> Any | None:
        if self._ttl <= 0:
            return None
        with self._lock:
            return self._get_locked(key)

    def set(self, key: str, value: Any) -> None:
        if self._ttl <= 0:
            return
        with self._lock:
            now = self._clock()
            if key not in self._store and len(self._store) >= self._max_entries:
                self._evict(now)
            self._store[key] = (now + self._ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def get_or_compute(self, key: str, fetcher: Callable[[], Any]) -> Any:
        prefix = key.split("|", 1)[0]
        with self._lock:
            hit = self._get_locked(key) if self._ttl > 0 else None
            if hit is not None:
                self._emit("cache.hit", prefix)
                return hit
            flight = self._inflight.get(key)
            leader = flight is None
            if flight is None:
                flight = self._inflight[key] = _Flight()

        if not leader:
            self._emit("cache.hit", prefix)
            if not flight.done.wait(timeout=self._leader_wait):
                raise ApiError(
                    504, "Timed out waiting for the query already running for this request."
                )
            if flight.ok:
                return flight.value
            # `error` is only filled by the leader's `except Exception`. If the leader
            # died with a BaseException, re-raising None would hide the cause.
            raise flight.error or RuntimeError("The shared query failed.")

        self._emit("cache.miss", prefix)
        try:
            value = fetcher()
            self.set(key, value)
            flight.ok, flight.value = True, value
            return value
        except Exception as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            flight.done.set()

    def _get_locked(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < self._clock():
            del self._store[key]
            return None
        return value

    def _evict(self, now: float) -> None:
        for key in [k for k, (expires_at, _) in self._store.items() if expires_at < now]:
            del self._store[key]
        while len(self._store) >= self._max_entries:
            del self._store[min(self._store, key=lambda k: self._store[k][0])]

    def _emit(self, name: str, prefix: str) -> None:
        if self._on_event is not None:
            self._on_event(name, prefix)


class _Flight:
    __slots__ = ("done", "error", "ok", "value")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.ok = False
        self.value: Any = None
        self.error: BaseException | None = None
