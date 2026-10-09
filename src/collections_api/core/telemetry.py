"""Optional observability: an NDJSON event log and Sentry.

Both are off unless configured, and neither can break a request. The Sentry DSN is
read from the environment only, never from code.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

from collections_api.settings import Settings

logger = logging.getLogger("collections_api.telemetry")


class Telemetry:
    """Event log, structured logs and metrics behind one object.

    Everything is a no-op when its sink is not configured, so call sites never need
    to check a flag.
    """

    def __init__(
        self,
        *,
        log_path: Path | None = None,
        log_max_bytes: int = 5 * 1024 * 1024,
        sentry: ModuleType | None = None,
    ) -> None:
        self._log_path = log_path
        self._log_max_bytes = log_max_bytes
        self._sentry = sentry
        self._lock = threading.Lock()

    @property
    def sentry_active(self) -> bool:
        return self._sentry is not None

    @property
    def log_enabled(self) -> bool:
        return self._log_path is not None

    @classmethod
    def from_settings(cls, settings: Settings) -> Telemetry:
        log_path = Path(settings.telemetry_log_path) if settings.enable_telemetry_log else None
        return cls(
            log_path=log_path,
            log_max_bytes=settings.telemetry_log_max_bytes,
            sentry=init_sentry(settings),
        )

    # ------------------------------------------------------------------ event log

    def event(
        self, name: str, data: dict[str, Any] | None = None, run_id: str | None = None
    ) -> None:
        """Append one JSON line to the event log. The log rolls over at its size cap."""
        if self._log_path is None:
            return
        line = {
            "ts_ms": int(time.time() * 1000),
            "event": name,
            "run_id": run_id or "runtime",
            "pid": os.getpid(),
            "data": data or {},
        }
        try:
            with self._lock:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                self._roll_if_needed()
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(line, default=str) + "\n")
        except OSError:
            logger.debug("telemetry event dropped", exc_info=True)

    def _roll_if_needed(self) -> None:
        assert self._log_path is not None
        try:
            if self._log_path.stat().st_size < self._log_max_bytes:
                return
        except FileNotFoundError:
            return
        os.replace(self._log_path, self._log_path.with_suffix(self._log_path.suffix + ".1"))

    # ------------------------------------------------------------------ sentry

    def log(self, level: str, message: str, **attributes: Any) -> None:
        """Structured log record. ``level``: debug, info, warning, error, fatal."""
        if self._sentry is None:
            return
        getattr(self._sentry.logger, level)(message, attributes=attributes)

    def metric(
        self, kind: str, name: str, value: float, unit: str = "none", **attributes: Any
    ) -> None:
        """Metric sample. ``kind``: count, gauge, distribution."""
        if self._sentry is None:
            return
        getattr(self._sentry.metrics, kind)(name, value, unit=unit, attributes=attributes)

    def capture_exception(self, exc: BaseException, run_id: str | None = None) -> None:
        if self._sentry is not None:
            with self._sentry.push_scope() as scope:
                if run_id:
                    scope.set_tag("run_id", run_id)
                self._sentry.capture_exception(exc)
        self.event(
            "unhandled_exception",
            {"type": type(exc).__name__, "message": str(exc)},
            run_id=run_id,
        )


def init_sentry(settings: Settings) -> ModuleType | None:
    """Initialize Sentry if a DSN is configured. Returns the module, or ``None``.

    Local variables and request bodies stay out of events: frames can hold database
    credentials and request bodies can hold uploaded documents. A malformed DSN or a
    missing package is logged and ignored; it must not stop the service from booting.
    """
    if not settings.sentry_dsn:
        return None
    try:
        module = importlib.import_module("sentry_sdk")
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; install the 'sentry' extra"
        )
        return None
    try:
        module.init(
            dsn=settings.sentry_dsn,
            environment=settings.app_env,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            enable_logs=settings.sentry_enable_logs,
            include_local_variables=False,
            max_request_body_size="never",
            send_default_pii=False,
        )
    except Exception:
        logger.exception("Sentry init failed; continuing without it")
        return None
    return module
