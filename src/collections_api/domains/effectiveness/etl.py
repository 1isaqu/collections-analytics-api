"""Background refresh of the effectiveness series.

The nine series scan the whole installment history and take seconds each, so they are
computed on a timer and served from memory. State is tracked per series and variant
(``<series>:<line or all>``): each carries the time its rows were fetched and the error
of its last attempt. A shared "last run" stamp would be wrong here, because a series
that keeps failing would keep its old rows and inherit a fresh timestamp, and stale
data would be served as current.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from collections_api.core.database import QueryExecutor, Row
from collections_api.core.sql import ALL_LINES, Query, QueryConfig
from collections_api.core.telemetry import Telemetry
from collections_api.domains.effectiveness.queries import SERIES, series_query

logger = logging.getLogger("collections_api.effectiveness")

Builder = Callable[[QueryConfig, str, str], Query]


@dataclass(frozen=True)
class KeyState:
    rows: list[Row] | None
    fetched_at: float | None
    error: str | None


class EffectivenessStore:
    def __init__(
        self,
        executor: QueryExecutor,
        cfg: QueryConfig,
        *,
        interval_seconds: float = 1800.0,
        telemetry: Telemetry | None = None,
        clock: Callable[[], float] = time.time,
        builder: Builder = series_query,
        series: tuple[str, ...] | None = None,
    ) -> None:
        self._executor = executor
        self._cfg = cfg
        self._interval = interval_seconds
        self._telemetry = telemetry or Telemetry()
        self._clock = clock
        self._builder = builder
        self._series = series or tuple(SERIES)
        self._rows: dict[str, list[Row]] = {}
        self._fetched_at: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._last_run: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def variants(self) -> tuple[str, ...]:
        return (ALL_LINES, *self._cfg.line_names)

    @staticmethod
    def store_key(series: str, variant: str) -> str:
        return f"{series}:{variant}"

    def refresh(self) -> None:
        """One pass over every series and variant. A failing key never stops the others."""
        fresh: dict[str, list[Row]] = {}
        failed: dict[str, str] = {}
        for key in self._series:
            for variant in self.variants:
                store_key = self.store_key(key, variant)
                try:
                    query = self._builder(self._cfg, variant, key)
                    fresh[store_key] = self._executor.run(
                        query.sql,
                        self._cfg.connection_database(variant),
                        query.params or None,
                        context=f"effectiveness-refresh/{store_key}",
                    )
                except Exception as exc:
                    detail = getattr(exc, "detail", None) or str(exc)
                    failed[store_key] = str(detail)
                    self._telemetry.event("effectiveness_refresh_error", {"key": store_key})
                    self._telemetry.log("error", "Effectiveness refresh failed.", key=store_key)
        now = self._clock()
        with self._lock:
            self._rows.update(fresh)
            for store_key in fresh:
                self._fetched_at[store_key] = now
                self._errors.pop(store_key, None)
            self._errors.update(failed)
            # A pass where everything failed is not a refresh.
            if fresh:
                self._last_run = now
        if failed:
            logger.warning(
                "effectiveness refresh: %d of %d keys failed", len(failed), len(failed) + len(fresh)
            )

    def state(self, series: str, variant: str) -> KeyState:
        store_key = self.store_key(series, variant)
        with self._lock:
            return KeyState(
                rows=list(self._rows[store_key]) if store_key in self._rows else None,
                fetched_at=self._fetched_at.get(store_key),
                error=self._errors.get(store_key),
            )

    @property
    def last_run(self) -> float | None:
        with self._lock:
            return self._last_run

    @property
    def interval_seconds(self) -> float:
        return self._interval

    # ------------------------------------------------------------------ thread

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="effectiveness-refresh", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self._interval)

    def snapshot(self) -> dict[str, Any]:
        """Keys and ages, for diagnostics."""
        with self._lock:
            return {key: self._fetched_at.get(key) for key in self._rows}
