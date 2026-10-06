"""SQL Server access: a connection pool and a query executor.

``pyodbc`` is imported the first time a connection is opened, so importing the app,
building it and exporting its OpenAPI document never need the driver or a server.
The pool and the executor talk to a small ``Driver`` protocol, which is how the tests
exercise connection reuse, expiry and timeout mapping with a fake driver.

Nothing here has been run against a live server; see the README.
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import logging
import queue
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Protocol

from collections_api.core.errors import ApiError
from collections_api.core.telemetry import Telemetry
from collections_api.settings import Settings

logger = logging.getLogger("collections_api.database")

Row = dict[str, Any]


class QueryExecutor(Protocol):
    """What the rest of the service needs from a database."""

    def run(
        self,
        sql: str,
        database: str,
        params: Sequence[Any] | None = None,
        *,
        run_id: str | None = None,
        context: str = "query",
    ) -> list[Row]: ...


class Connection(Protocol):
    timeout: int

    def cursor(self) -> Any: ...

    def close(self) -> None: ...

    def commit(self) -> None: ...


class Driver(Protocol):
    """The slice of an ODBC driver the pool uses."""

    error_types: tuple[type[BaseException], ...]

    def connect(self, connection_string: str, *, timeout: int, autocommit: bool) -> Connection: ...


class PyodbcDriver:
    """Adapter over ``pyodbc``, imported lazily."""

    def __init__(self) -> None:
        self._module: Any = None

    def _load(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("pyodbc")
            except ImportError as exc:
                raise ApiError(
                    500, "The 'pyodbc' package is not installed. Install the 'sqlserver' extra."
                ) from exc
        return self._module

    @property
    def error_types(self) -> tuple[type[BaseException], ...]:
        return (self._load().Error,)

    def connect(self, connection_string: str, *, timeout: int, autocommit: bool) -> Connection:
        return self._load().connect(connection_string, timeout=timeout, autocommit=autocommit)


def _odbc_value(value: str) -> str:
    """Quote a connection-string value if it contains characters ODBC treats as syntax."""
    if any(ch in value for ch in ";{}") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def build_connection_string(settings: Settings, database: str) -> str:
    if not settings.db_user or not settings.db_password:
        raise ApiError(500, "Database credentials are not configured: set DB_USER and DB_PASSWORD.")
    server = settings.db_host if not settings.db_port else f"{settings.db_host},{settings.db_port}"
    trust = "yes" if settings.db_trust_server_cert else "no"
    return (
        f"DRIVER={{{settings.db_driver}}};"
        f"SERVER={_odbc_value(server)};"
        f"DATABASE={database};"
        f"UID={_odbc_value(settings.db_user)};"
        f"PWD={_odbc_value(settings.db_password)};"
        f"TrustServerCertificate={trust};"
    )


class ConnectionPool:
    """Idle-connection pool, one queue per database.

    An idle connection is reused if it is younger than ``max_age_seconds`` and answers
    ``SELECT 1``; otherwise it is closed and a new one is opened. The pool never blocks:
    when no idle connection exists it opens another, and a connection returned to a
    full pool is closed. ``size`` therefore caps idle connections, not concurrency.
    A connection that raised is closed instead of being returned.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        driver: Driver | None = None,
        telemetry: Telemetry | None = None,
        clock: Any = time.time,
    ) -> None:
        self._settings = settings
        self._driver: Driver = driver or PyodbcDriver()
        self._telemetry = telemetry or Telemetry()
        self._clock = clock
        self._pools: dict[str, queue.Queue[tuple[Connection, float]]] = {}
        self._lock = threading.Lock()

    @property
    def error_types(self) -> tuple[type[BaseException], ...]:
        return self._driver.error_types

    def _pool_for(self, database: str) -> queue.Queue[tuple[Connection, float]]:
        with self._lock:
            pool = self._pools.get(database)
            if pool is None:
                pool = self._pools[database] = queue.Queue(maxsize=self._settings.db_pool_size)
            return pool

    def idle_count(self, database: str) -> int:
        return self._pool_for(database).qsize()

    def _open(self, database: str) -> Connection:
        conn_str = build_connection_string(self._settings, database)
        try:
            # autocommit: the workload only reads. Without it the server opens an
            # implicit transaction on the first SELECT and keeps it open while the
            # connection idles in the pool.
            return self._driver.connect(
                conn_str, timeout=self._settings.db_connect_timeout, autocommit=True
            )
        except self._driver.error_types as exc:
            logger.error("database connect failed for %s: %s", database, type(exc).__name__)
            self._telemetry.log("error", "Database connection failed.", database=database)
            raise ApiError(500, "Could not connect to the database.") from exc

    @staticmethod
    def _alive(conn: Connection) -> bool:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            return True
        except Exception:
            return False

    @staticmethod
    def _close(conn: Connection) -> None:
        with contextlib.suppress(Exception):
            conn.close()

    @contextlib.contextmanager
    def connection(self, database: str) -> Iterator[Connection]:
        started = time.perf_counter()
        pool = self._pool_for(database)
        conn: Connection | None = None
        opened_at = 0.0
        try:
            conn, opened_at = pool.get_nowait()
        except queue.Empty:
            pass
        if conn is not None:
            expired = (self._clock() - opened_at) > self._settings.db_pool_max_age_seconds
            if expired or not self._alive(conn):
                self._close(conn)
                conn = None
        source = "reused" if conn is not None else "opened"
        if conn is None:
            conn = self._open(database)
            opened_at = self._clock()
        self._telemetry.metric(
            "distribution",
            "db.pool_acquire_ms",
            round((time.perf_counter() - started) * 1000, 2),
            unit="millisecond",
            database=database,
            source=source,
        )
        healthy = False
        try:
            yield conn
            healthy = True
        finally:
            if healthy:
                try:
                    pool.put_nowait((conn, opened_at))
                except queue.Full:
                    self._close(conn)
            else:
                self._close(conn)


def json_safe(value: Any) -> Any:
    """Convert a driver value into something ``json.dumps`` accepts."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return (
            int(value)
            if value == value.to_integral_value() and value.as_tuple().exponent >= 0
            else float(value)
        )
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return str(value)


class SqlServerExecutor:
    """Runs a statement on a pooled connection and returns JSON-safe dict rows."""

    def __init__(
        self, pool: ConnectionPool, settings: Settings, telemetry: Telemetry | None = None
    ):
        self._pool = pool
        self._settings = settings
        self._telemetry = telemetry or Telemetry()

    def run(
        self,
        sql: str,
        database: str,
        params: Sequence[Any] | None = None,
        *,
        run_id: str | None = None,
        context: str = "query",
    ) -> list[Row]:
        started = time.perf_counter()
        self._telemetry.event("query_start", {"database": database, "context": context}, run_id)
        try:
            with self._pool.connection(database) as conn:
                # The login timeout given to connect() covers the login only. Without
                # a statement timeout a stuck query holds its worker thread forever.
                conn.timeout = self._settings.db_query_timeout
                cursor = conn.cursor()
                cursor.execute(sql, tuple(params)) if params else cursor.execute(sql)
                columns = [column[0] for column in cursor.description]
                rows = [
                    {name: json_safe(value) for name, value in zip(columns, record, strict=True)}
                    for record in cursor.fetchall()
                ]
        except ApiError:
            raise
        except self._pool.error_types as exc:
            self._raise_driver_error(exc, database, context, run_id)
        except Exception as exc:
            # Anything that is not a driver error (for example a statement that
            # returned no result set) would otherwise escape as a bare 500.
            self._telemetry.event(
                "query_unexpected_error",
                {"database": database, "context": context, "type": type(exc).__name__},
                run_id,
            )
            logger.exception("unexpected error while running %s", context)
            raise ApiError(500, "The database query failed.") from exc

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        self._telemetry.event(
            "query_end",
            {"database": database, "context": context, "rows": len(rows), "elapsed_ms": elapsed_ms},
            run_id,
        )
        self._telemetry.metric(
            "distribution",
            "db.query_duration_ms",
            elapsed_ms,
            unit="millisecond",
            context=context,
            database=database,
        )
        return rows

    def _raise_driver_error(
        self, exc: BaseException, database: str, context: str, run_id: str | None
    ) -> None:
        # SQLSTATE HYT00 and HYT01 are timeouts. Reporting them as 504 lets a client
        # tell "slow" from "broken"; otherwise every database problem is a bare 500.
        sqlstate = str(exc.args[0]) if exc.args else ""
        timed_out = sqlstate.startswith("HYT")
        self._telemetry.event(
            "query_timeout" if timed_out else "query_error",
            {"database": database, "context": context, "sqlstate": sqlstate},
            run_id,
        )
        logger.error("query failed in %s (sqlstate=%s): %s", context, sqlstate, exc)
        self._telemetry.log(
            "error", "Query failed.", database=database, context=context, sqlstate=sqlstate
        )
        if timed_out:
            raise ApiError(504, "The query exceeded the database time limit.") from exc
        raise ApiError(500, "The database query failed.") from exc
