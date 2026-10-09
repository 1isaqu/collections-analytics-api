"""Test doubles shared across the suite. Every value is invented."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from collections_api.core.database import Row


class FakeDriverError(Exception):
    """Stands in for the ODBC driver's error base class."""


class FakeCursor:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        self._connection.executed.append((sql, tuple(params) if params else None))
        outcome = self._connection.script(sql, params)
        if isinstance(outcome, BaseException):
            raise outcome
        columns, rows = outcome
        self.description = [(name,) for name in columns] if columns else None
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, script: Callable[..., Any], serial: int) -> None:
        self.script = script
        self.serial = serial
        self.timeout = 0
        self.closed = False
        self.commits = 0
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def default_script(sql: str, params: Sequence[Any] | None) -> Any:
    if sql.strip().upper() == "SELECT 1":
        return ["ok"], [(1,)]
    return ["value"], [(42,)]


class FakeDriver:
    error_types = (FakeDriverError,)

    def __init__(self, script: Callable[..., Any] = default_script) -> None:
        self.script = script
        self.connections: list[FakeConnection] = []
        self.connect_calls: list[tuple[str, int, bool]] = []
        self.fail_connect: BaseException | None = None

    def connect(self, connection_string: str, *, timeout: int, autocommit: bool) -> FakeConnection:
        self.connect_calls.append((connection_string, timeout, autocommit))
        if self.fail_connect is not None:
            raise self.fail_connect
        conn = FakeConnection(self.script, len(self.connections) + 1)
        self.connections.append(conn)
        return conn


class RecordingExecutor:
    """A ``QueryExecutor`` that records calls and answers from a table.

    ``answers`` maps a substring of the request ``context`` to the rows to return. A
    callable answer receives ``(sql, database, params)``. Unmatched contexts return
    ``default`` (an empty list unless set).
    """

    def __init__(self, answers: dict[str, Any] | None = None, default: list[Row] | None = None):
        self.answers = answers or {}
        self.default = default if default is not None else []
        self.calls: list[dict[str, Any]] = []

    def run(
        self,
        sql: str,
        database: str,
        params: Sequence[Any] | None = None,
        *,
        run_id: str | None = None,
        context: str = "query",
    ) -> list[Row]:
        self.calls.append(
            {
                "sql": sql,
                "database": database,
                "params": tuple(params or ()),
                "context": context,
                "run_id": run_id,
            }
        )
        for needle, answer in self.answers.items():
            if needle in context:
                if isinstance(answer, BaseException):
                    raise answer
                return (
                    answer(sql, database, params) if callable(answer) else [dict(r) for r in answer]
                )
        return [dict(r) for r in self.default]

    def calls_for(self, needle: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if needle in c["context"]]
