"""Consolidated agent effort across product lines."""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from collections_api.core.database import QueryExecutor
from collections_api.core.sql import QueryConfig
from collections_api.domains.productivity.queries import agent_effort_query, normalize_agent_key


class AgentEffortService:
    """Today's outreach effort per person, summed over every product line.

    The same login in two lines is one person. Lines are queried in parallel, one
    thread per line, and the consolidated payload is cached for ``ttl_seconds``;
    ``force_refresh`` bypasses the cache.
    """

    def __init__(
        self,
        executor: QueryExecutor,
        cfg: QueryConfig,
        *,
        ttl_seconds: float = 60.0,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._executor = executor
        self._cfg = cfg
        self._ttl = ttl_seconds
        self._today = today
        self._clock = clock
        self._cached: tuple[float, list[dict[str, Any]]] | None = None

    def fetch(self, *, force_refresh: bool = False) -> tuple[list[dict[str, Any]], int]:
        """``(agents, cache_age_seconds)``. Age is 0 for a fresh computation."""
        now = self._clock()
        if not force_refresh and self._cached is not None:
            stored_at, agents = self._cached
            if now - stored_at < self._ttl:
                return agents, max(1, int(now - stored_at))
        agents = self._compute()
        self._cached = (self._clock(), agents)
        return agents, 0

    def _fetch_line(self, database: str, day: date) -> list[dict[str, Any]]:
        query = agent_effort_query(self._cfg, database, day)
        return self._executor.run(
            query.sql, database, query.params or None, context="dashboard/agent-effort"
        )

    def _compute(self) -> list[dict[str, Any]]:
        lines = self._cfg.scope("all")
        day = self._today()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(lines)) as pool:
            futures = {name: pool.submit(self._fetch_line, db, day) for name, db in lines}
            rows_by_line = {name: future.result() for name, future in futures.items()}

        people: dict[str, dict[str, Any]] = {}
        for name, _ in lines:
            for row in rows_by_line[name]:
                login = str(row.get("agent_login") or "").strip()
                display = str(row.get("agent_name") or "").strip()
                key = normalize_agent_key(login, display)
                if not key:
                    continue
                person = people.setdefault(
                    key,
                    {
                        "agent_key": key.lower(),
                        "agent_login": login,
                        "agent_name": display,
                        "by_line": {},
                        "total": {"attempt_count": 0, "answer_count": 0, "cpc_count": 0},
                    },
                )
                counts = {
                    field: int(row.get(field) or 0)
                    for field in ("attempt_count", "answer_count", "cpc_count")
                }
                person["by_line"][name] = counts
                for field, value in counts.items():
                    person["total"][field] += value
        return sorted(people.values(), key=lambda p: p["total"]["attempt_count"], reverse=True)
