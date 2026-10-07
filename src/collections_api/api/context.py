"""The objects a request needs, built once and stored on ``app.state``.

Routes never import module-level singletons. They receive an ``AppContext`` through
``Depends(get_context)``, so a test builds an app around a fake executor and the
OpenAPI export builds one with no database at all: nothing connects until the first
query runs.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from collections_api.core.cache import TtlCache
from collections_api.core.database import ConnectionPool, QueryExecutor, SqlServerExecutor
from collections_api.core.ratelimit import RateLimiter
from collections_api.core.sql import QueryConfig
from collections_api.core.telemetry import Telemetry
from collections_api.domains.effectiveness.etl import EffectivenessStore
from collections_api.domains.productivity.service import AgentEffortService
from collections_api.domains.targets.store import TargetsStore
from collections_api.settings import Settings

BENCHMARK_CACHE_ENTRIES = 64
# Followers of a shared query wait a little longer than the statement timeout, so a
# leader that times out reports its own 504 before its followers give up.
LEADER_WAIT_MARGIN_SECONDS = 5


@dataclass
class AppContext:
    settings: Settings
    cfg: QueryConfig
    executor: QueryExecutor
    pool: ConnectionPool | None
    cache: TtlCache
    benchmarks_cache: TtlCache
    telemetry: Telemetry
    rate_limiter: RateLimiter
    effectiveness: EffectivenessStore
    agent_effort: AgentEffortService
    targets: TargetsStore
    today: Callable[[], date]


def build_context(
    settings: Settings,
    *,
    executor: QueryExecutor | None = None,
    telemetry: Telemetry | None = None,
    today: Callable[[], date] = date.today,
    clock: Callable[[], float] = time.time,
) -> AppContext:
    """Wire the service together.

    Without ``executor`` a SQL Server connection pool is created (it opens no
    connection until a query runs). Passing one replaces the database entirely.
    """
    telemetry = telemetry or Telemetry.from_settings(settings)
    cfg = QueryConfig.from_settings(settings)
    pool: ConnectionPool | None = None
    if executor is None:
        pool = ConnectionPool(settings, telemetry=telemetry)
        executor = SqlServerExecutor(pool, settings, telemetry)

    def on_cache_event(name: str, prefix: str) -> None:
        telemetry.metric("count", name, 1, prefix=prefix)

    leader_wait = settings.db_query_timeout + LEADER_WAIT_MARGIN_SECONDS
    return AppContext(
        settings=settings,
        cfg=cfg,
        executor=executor,
        pool=pool,
        cache=TtlCache(
            settings.cache_ttl_seconds,
            max_entries=settings.cache_max_entries,
            leader_wait_seconds=leader_wait,
            clock=clock,
            on_event=on_cache_event,
        ),
        benchmarks_cache=TtlCache(
            settings.benchmarks_cache_ttl_seconds,
            max_entries=BENCHMARK_CACHE_ENTRIES,
            leader_wait_seconds=leader_wait,
            clock=clock,
            on_event=on_cache_event,
        ),
        telemetry=telemetry,
        rate_limiter=RateLimiter(
            settings.rate_limit_requests,
            settings.rate_limit_window_seconds,
            max_buckets=settings.rate_limit_max_buckets,
            clock=clock,
        ),
        effectiveness=EffectivenessStore(
            executor,
            cfg,
            interval_seconds=settings.effectiveness_etl_interval_seconds,
            telemetry=telemetry,
            clock=clock,
        ),
        agent_effort=AgentEffortService(
            executor,
            cfg,
            ttl_seconds=settings.agent_effort_cache_ttl_seconds,
            today=today,
            clock=clock,
        ),
        targets=TargetsStore(settings.targets_dir),
        today=today,
    )
