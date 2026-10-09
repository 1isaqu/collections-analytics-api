"""Building blocks for the SQL Server queries.

Queries are built as text from validated pieces and carry their bind parameters with
them (``Query``), so the number and order of ``?`` markers can never drift from the
parameter tuple. User text is only ever passed as a bind parameter. The identifiers
that are interpolated (database, schema, agent patterns, status ids) are validated
when ``Settings`` is built, not here.

The dialect is T-SQL. The builders are unit-tested without a database; a subset of
the generated SQL is also run against SQLite in the tests after a syntax-only adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from collections_api.core.errors import ApiError
from collections_api.settings import Settings, StatusPolicy

ALL_LINES = "all"
FIRST_INSTALLMENT_NO = 0
UNION = "\nUNION ALL\n"


def sql_in(values: Sequence[int]) -> str:
    """``(1, 3, 12)`` for an IN clause."""
    return "(" + ", ".join(str(int(v)) for v in values) + ")"


def quote_ident(identifier: str) -> str:
    """Bracket-quote an identifier for DDL."""
    return "[" + identifier.replace("]", "]]") + "]"


@dataclass(frozen=True)
class Query:
    """A statement plus the positional parameters for its ``?`` markers."""

    sql: str
    params: tuple[Any, ...] = ()

    @staticmethod
    def union(parts: Sequence[Query]) -> Query:
        return Query(UNION.join(p.sql for p in parts), tuple(v for p in parts for v in p.params))


# --------------------------------------------------------------------------- periods


@dataclass(frozen=True)
class Period:
    """A half-open date window ``[start, end_exclusive)``.

    Dates are rendered into the SQL as literals, not as ``DECLARE``d variables: a
    local variable hides its value from the optimizer at compile time, which can turn
    an index seek into a scan on the window columns.
    """

    start: date
    end_exclusive: date
    is_today: bool = False

    @classmethod
    def day(cls, day: date) -> Period:
        return cls(day, day + timedelta(days=1), is_today=True)

    @classmethod
    def between(cls, date_from: date, date_to: date) -> Period:
        """Inclusive range as the client states it. A reversed pair is swapped."""
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return cls(date_from, date_to + timedelta(days=1))

    @classmethod
    def from_query(cls, date_from: date | None, date_to: date | None, today: date) -> Period:
        """Period for the optional ``date_from`` / ``date_to`` query parameters.

        Neither given means today. Exactly one given is an error: falling back to
        today would answer a different question than the one asked and still say 200.
        """
        if date_from is None and date_to is None:
            return cls.day(today)
        if date_from is None or date_to is None:
            raise ApiError(400, "Provide date_from and date_to together, or neither.")
        return cls.between(date_from, date_to)

    @property
    def label(self) -> str:
        if self.is_today:
            return "today"
        return f"{self.start.isoformat()}/{(self.end_exclusive - timedelta(days=1)).isoformat()}"

    @property
    def sql_start(self) -> str:
        return date_literal(self.start)

    @property
    def sql_end(self) -> str:
        return date_literal(self.end_exclusive)

    def contains(self, column: str) -> str:
        return f"{column} >= {self.sql_start} AND {column} < {self.sql_end}"


def date_literal(value: date) -> str:
    """Language-neutral date literal. The input is a ``date``, never client text."""
    return f"CAST('{value:%Y%m%d}' AS DATE)"


# --------------------------------------------------------------------------- team filter


def team_filter(team: str | None, alias: str = "U") -> tuple[str, str, str]:
    """Parse the ``team`` query value.

    Returns ``(label, token, clause)``. ``team`` is ``all`` (or empty) for no filter, or
    free text; only the part after the last colon is matched, upper-cased, as a
    substring of the agent login or display name. The token travels as a bind
    parameter twice (login, display name) per use of the clause.
    """
    if not team or team.strip().lower() == "all":
        return "all", "", ""
    label = team.strip()
    token = label.split(":")[-1].strip().upper()
    if not token:
        return label, "", ""
    clause = (
        f" AND (UPPER(LTRIM(RTRIM({alias}.login))) LIKE ?"
        f" OR UPPER(LTRIM(RTRIM({alias}.display_name))) LIKE ?)"
    )
    return label, token, clause


def team_params(token: str, repetitions: int = 1) -> tuple[str, ...]:
    if not token:
        return ()
    like = f"%{token}%"
    return (like, like) * repetitions


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class QueryConfig:
    """Everything a query builder needs to know about the deployment."""

    lines: tuple[tuple[str, str], ...]
    schema: str
    read_hint: str
    status: StatusPolicy
    grace_days: int
    excluded_agent_patterns: tuple[str, ...]
    effectiveness_agent_patterns: tuple[str, ...]
    effectiveness_floor: date
    effectiveness_detail_limit: int
    break_curve_edges: tuple[int, ...]
    benchmark_min_daily_attempts: int
    benchmark_min_active_days: int
    effectiveness_min_slips: int
    productivity_option: str
    _line_map: dict[str, str] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_line_map", dict(self.lines))

    @classmethod
    def from_settings(cls, settings: Settings) -> QueryConfig:
        return cls(
            lines=settings.lines,
            schema=settings.db_schema,
            read_hint=settings.db_read_hint,
            status=settings.status,
            grace_days=settings.grace_days,
            excluded_agent_patterns=settings.excluded_agent_patterns,
            effectiveness_agent_patterns=settings.effectiveness_excluded_agent_patterns,
            effectiveness_floor=settings.effectiveness_floor_date,
            effectiveness_detail_limit=settings.effectiveness_detail_row_limit,
            break_curve_edges=settings.break_curve_edges,
            benchmark_min_daily_attempts=settings.benchmark_min_daily_attempts,
            benchmark_min_active_days=settings.benchmark_min_active_days,
            effectiveness_min_slips=settings.effectiveness_min_slips_per_agent_month,
            productivity_option=settings.productivity_query_option,
        )

    # ---- product lines

    @property
    def line_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.lines)

    def resolve_line(self, value: str, *, allow_all: bool = True) -> str:
        """Canonical line name, or ``all``. Unknown names are a 400."""
        name = (value or "").strip().lower()
        if allow_all and name == ALL_LINES:
            return ALL_LINES
        if name in self._line_map:
            return name
        choices = list(self.line_names) + ([ALL_LINES] if allow_all else [])
        raise ApiError(400, f"Unknown product line {value!r}. Use one of: {', '.join(choices)}.")

    def scope(self, line: str) -> list[tuple[str, str]]:
        """``(line, database)`` pairs a query for ``line`` has to read."""
        if line == ALL_LINES:
            return list(self.lines)
        return [(line, self._line_map[line])]

    def connection_database(self, line: str) -> str:
        """Database to connect to. Multi-line queries use three-part names from the first."""
        return self.lines[0][1] if line == ALL_LINES else self._line_map[line]

    def across_lines(self, line: str, build: Callable[[str, str], Query]) -> Query:
        """``UNION ALL`` of ``build(line_name, database)`` over the lines in scope."""
        return Query.union([build(name, database) for name, database in self.scope(line)])

    # ---- table references

    def table(self, database: str, name: str, alias: str) -> str:
        hint = f" {self.read_hint}" if self.read_hint else ""
        return f"{database}.{self.schema}.{name} AS {alias}{hint}"

    def dedup(self, database: str, alias: str = "R", extra_filter: str = "") -> str:
        """``agreements`` as a derived table with one row per installment.

        The same installment can be written more than once by an upstream error. Only
        the most recent write survives (latest ``issued_at``, ``row_id`` as tie
        breaker), exposed as ``rn = 1``. Business filters (window, status) belong in
        ``extra_filter`` so they run *before* the ranking: filtering after it lets a
        re-write that falls outside the filter win the rank and hide the valid row
        written earlier. ``extra_filter`` may only reference ``alias`` columns.
        """
        return (
            "(\n"
            f"    SELECT {alias}.*,\n"
            "           ROW_NUMBER() OVER (\n"
            f"               PARTITION BY {alias}.agreement_id, {alias}.portfolio_id,"
            f" {alias}.installment_no\n"
            f"               ORDER BY {alias}.issued_at DESC, {alias}.row_id DESC\n"
            "           ) AS rn\n"
            f"    FROM {self.table(database, 'agreements', alias)}\n"
            f"    WHERE 1 = 1 {extra_filter}\n"
            ")"
        )

    def portfolio_label_lookup(self, database: str, agreement_alias: str, *, outer: bool) -> str:
        """The portfolio label of an agreement's debts, resolved per row.

        ``CROSS APPLY ... TOP 1`` keeps the agreement's cardinality (a join would
        multiply rows when an agreement covers several debts). ``outer=True`` uses
        ``OUTER APPLY`` so agreements without a label stay in the result with a null.
        The match uses the pair (agreement_id, portfolio_id): an agreement number can
        exist in more than one portfolio. Null and blank labels are skipped. When the debts
        carry different labels, the one that sorts first wins. The ``ORDER BY`` makes the
        pick repeatable, and the detail lists (``domains/charts/details.py``) apply the
        same rule.
        """
        apply_kind = "OUTER" if outer else "CROSS"
        return (
            f"{apply_kind} APPLY (\n"
            "    SELECT TOP 1 DA2.portfolio_label\n"
            f"    FROM {self.table(database, 'agreement_debts', 'RD')}\n"
            f"    JOIN {self.table(database, 'debt_attributes', 'DA2')}"
            " ON RD.debt_id = DA2.debt_id\n"
            f"    WHERE RD.agreement_id = {agreement_alias}.agreement_id\n"
            f"      AND RD.portfolio_id = {agreement_alias}.portfolio_id\n"
            "      AND DA2.portfolio_label IS NOT NULL\n"
            "      AND LTRIM(RTRIM(DA2.portfolio_label)) <> ''\n"
            "    ORDER BY DA2.portfolio_label\n"
            ") DA"
        )

    def portfolio_exists(self, database: str, agreement_alias: str, marker: str) -> str:
        """``AND EXISTS (...)`` filter on an exact portfolio label (bind marker ``marker``)."""
        return (
            "AND EXISTS (SELECT 1 "
            f"FROM {self.table(database, 'agreement_debts', 'RD2')} "
            f"JOIN {self.table(database, 'debt_attributes', 'DA3')} ON RD2.debt_id = DA3.debt_id "
            f"WHERE RD2.agreement_id = {agreement_alias}.agreement_id "
            f"AND RD2.portfolio_id = {agreement_alias}.portfolio_id "
            f"AND DA3.portfolio_label = {marker})"
        )

    # ---- agent filters

    def excluded_agents(self, alias: str = "U") -> str:
        return _not_like(alias, self.excluded_agent_patterns)

    def effectiveness_excluded_agents(self, alias: str = "U") -> str:
        """Wider exclusion list used by the effectiveness routes (substring patterns).

        Kept separate from ``excluded_agents`` on purpose: merging the two lists would
        change the effectiveness numbers, which is a business decision, not a refactor.
        """
        return _not_like(alias, self.effectiveness_agent_patterns)

    # ---- shared expressions

    def paid_in_grace(self, alias: str = "") -> str:
        """1 when the installment was paid no later than ``grace_days`` after its due date."""
        prefix = f"{alias}." if alias else ""
        return (
            f"CASE WHEN {prefix}paid_amount > 0 "
            f"AND {prefix}paid_at <= DATEADD(DAY, {int(self.grace_days)}, {prefix}due_at) "
            "THEN 1 ELSE 0 END"
        )


def _not_like(alias: str, patterns: Sequence[str]) -> str:
    clauses = []
    for pattern in patterns:
        for column in ("login", "display_name"):
            expr = f"UPPER(LTRIM(RTRIM(COALESCE({alias}.{column}, ''))))"
            clauses.append(f"AND {expr} NOT LIKE '{pattern.upper()}'")
    return "\n      ".join(clauses)
