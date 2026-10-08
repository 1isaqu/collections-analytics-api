"""Sliding-window rate limiter, one window per caller key."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """At most ``limit`` hits per ``window`` seconds for each key.

    The bucket map is pruned once it passes ``max_buckets``: without that, every
    distinct caller key would stay in memory for the life of the process.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        *,
        max_buckets: int = 5000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._max_buckets = max_buckets
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """Record a request. Returns ``None`` if allowed, else seconds to wait."""
        now = self._clock()
        # The read-modify-write on the deque runs on several worker threads; without
        # the lock the check-then-append lets more requests through than the limit.
        with self._lock:
            if len(self._buckets) > self._max_buckets:
                self._prune(now)
            bucket = self._buckets.setdefault(key, deque())
            while bucket and now - bucket[0] > self._window:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return max(1, int(self._window - (now - bucket[0])))
            bucket.append(now)
            return None

    def _prune(self, now: float) -> None:
        cutoff = now - self._window
        for key in [k for k, hits in self._buckets.items() if not hits or hits[-1] < cutoff]:
            del self._buckets[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)
