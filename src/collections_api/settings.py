"""Environment-driven configuration.

Everything the service reads from the environment is declared once, in ``_SPEC``:
the field name, the variable name, a parser and the default (as the string a user
would type). ``Settings.from_env`` walks that table, so the defaults, the parsing and
the list of variable names cannot drift apart. A test checks that every variable in
the table is documented in ``.env.example`` and ``docs/configuration.md``.

All defaults are invented placeholders. Values that depend on the operation (grace days,
band edges, minimum sample sizes, agent patterns) are plain settings, and they must be
calibrated on your own data.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_AGENT_PATTERN = re.compile(r"^[A-Za-z0-9_%.\- ]{1,64}$")
_READ_HINT = re.compile(r"^(WITH \((NOLOCK|READUNCOMMITTED)\))?$")
_QUERY_OPTION = re.compile(r"^(OPTION \([A-Za-z0-9_ ,'()]{1,200}\))?$")

STATUS_NAMES = (
    "ACTIVE",
    "BROKEN",
    "PAID",
    "PENDING_EXCEPTION",
    "REJECTED",
    "AUTO_BROKEN",
    "PAID_MANUAL",
)
DEFAULT_STATUS_IDS = (
    "ACTIVE=21,BROKEN=22,PAID=23,PENDING_EXCEPTION=24,REJECTED=25,AUTO_BROKEN=26,PAID_MANUAL=27"
)

# Status set name -> default members. Derived sets reference base sets by composition
# in ``build_status_policy``; overrides replace a set wholesale.
SET_NAMES = (
    "APPROVED",
    "BROKEN",
    "AUTO_BROKEN",
    "EXCEPTION",
    "REJECTED",
    "GENERATED",
    "UNIVERSE",
    "ROLLUP",
    "BREAK_CURVE",
)


class SettingsError(ValueError):
    """A configuration value is missing or malformed."""


# --------------------------------------------------------------------------- parsers


def _bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off", ""):
        return False
    raise SettingsError(f"not a boolean: {raw!r}")


def _optional_bool(raw: str) -> bool | None:
    return None if raw.strip() == "" else _bool(raw)


def _int(minimum: int = 0) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(raw.strip())
        except ValueError as exc:
            raise SettingsError(f"not an integer: {raw!r}") from exc
        if value < minimum:
            raise SettingsError(f"must be >= {minimum}: {raw!r}")
        return value

    return parse


def _float(minimum: float = 0.0) -> Callable[[str], float]:
    def parse(raw: str) -> float:
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise SettingsError(f"not a number: {raw!r}") from exc
        if value < minimum:
            raise SettingsError(f"must be >= {minimum}: {raw!r}")
        return value

    return parse


def _text(raw: str) -> str:
    return raw.strip()


def _csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _identifier(raw: str) -> str:
    value = raw.strip()
    if not _IDENT.match(value):
        raise SettingsError(f"not a plain SQL identifier: {raw!r}")
    return value


def _agent_patterns(raw: str) -> tuple[str, ...]:
    patterns = _csv(raw)
    for pattern in patterns:
        if not _AGENT_PATTERN.match(pattern):
            raise SettingsError(
                f"agent pattern may only use letters, digits, _ % . - and space: {pattern!r}"
            )
    return patterns


def _iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise SettingsError(f"not an ISO date (YYYY-MM-DD): {raw!r}") from exc


def _edges(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(int(part) for part in _csv(raw))
    except ValueError as exc:
        raise SettingsError(f"band edges must be integers: {raw!r}") from exc
    if not values or any(v <= 0 for v in values) or list(values) != sorted(set(values)):
        raise SettingsError(f"band edges must be positive and strictly increasing: {raw!r}")
    return values


def _read_hint(raw: str) -> str:
    value = raw.strip()
    if not _READ_HINT.match(value):
        raise SettingsError(
            "DB_READ_HINT must be empty, 'WITH (NOLOCK)' or 'WITH (READUNCOMMITTED)'"
        )
    return value


def _query_option(raw: str) -> str:
    value = raw.strip()
    if not _QUERY_OPTION.match(value):
        raise SettingsError(
            "PRODUCTIVITY_QUERY_OPTION must be empty or look like 'OPTION (MAXDOP 2)'"
        )
    return value


def parse_product_lines(raw: str) -> tuple[tuple[str, str], ...]:
    """``auto=db_a,consumer=db_b`` -> ``(("auto", "db_a"), ("consumer", "db_b"))``."""
    pairs: list[tuple[str, str]] = []
    for item in _csv(raw):
        name, sep, database = item.partition("=")
        if not sep:
            raise SettingsError(f"PRODUCT_LINES entries look like name=database: {item!r}")
        line = _identifier(name).lower()
        if line == "all":
            raise SettingsError("'all' is reserved for queries that span every line")
        pairs.append((line, _identifier(database)))
    if not pairs:
        raise SettingsError("PRODUCT_LINES needs at least one name=database pair")
    if len({line for line, _ in pairs}) != len(pairs):
        raise SettingsError("PRODUCT_LINES contains a duplicate line name")
    return tuple(pairs)


def parse_status_ids(raw: str) -> dict[str, int]:
    ids: dict[str, int] = {}
    for item in _csv(raw):
        name, sep, number = item.partition("=")
        name = name.strip().upper()
        if not sep or name not in STATUS_NAMES:
            raise SettingsError(
                f"STATUS_IDS entries look like NAME=id with NAME in {STATUS_NAMES}: {item!r}"
            )
        try:
            ids[name] = int(number)
        except ValueError as exc:
            raise SettingsError(f"status id must be an integer: {item!r}") from exc
    missing = [name for name in STATUS_NAMES if name not in ids]
    if missing:
        raise SettingsError(f"STATUS_IDS is missing: {', '.join(missing)}")
    if len(set(ids.values())) != len(ids):
        raise SettingsError("STATUS_IDS maps two names to the same id")
    return ids


# --------------------------------------------------------------------------- status policy


@dataclass(frozen=True)
class StatusPolicy:
    """The status catalogue plus every derived set the queries need.

    Sets are sorted tuples of ids. ``generated`` is the base of every value KPI: an
    agreement generated today counts even if it breaks later.
    """

    ids: Mapping[str, int]
    approved: tuple[int, ...]
    broken: tuple[int, ...]
    auto_broken: tuple[int, ...]
    exception: tuple[int, ...]
    rejected: tuple[int, ...]
    generated: tuple[int, ...]
    universe: tuple[int, ...]
    rollup: tuple[int, ...]
    break_curve: tuple[int, ...]

    def sql(self, name: str) -> str:
        """Render a set as ``(1, 2, 3)`` for an IN clause."""
        members = getattr(self, name)
        return "(" + ", ".join(str(int(v)) for v in members) + ")"


def build_status_policy(
    ids: Mapping[str, int], overrides: Mapping[str, tuple[str, ...]] | None = None
) -> StatusPolicy:
    overrides = {key.upper(): value for key, value in (overrides or {}).items()}
    unknown = set(overrides) - set(SET_NAMES)
    if unknown:
        raise SettingsError(f"unknown status set override: {', '.join(sorted(unknown))}")

    def names(set_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        chosen = overrides.get(set_name, default)
        for status in chosen:
            if status not in ids:
                raise SettingsError(f"STATUS_SET_{set_name} uses an unknown status: {status}")
        return chosen

    approved = names("APPROVED", ("ACTIVE", "PAID", "PAID_MANUAL"))
    broken = names("BROKEN", ("BROKEN",))
    auto_broken = names("AUTO_BROKEN", ("AUTO_BROKEN",))
    exception = names("EXCEPTION", ("PENDING_EXCEPTION",))
    rejected = names("REJECTED", ("REJECTED",))
    generated = names("GENERATED", approved + broken + auto_broken)
    universe = names("UNIVERSE", generated + exception)
    rollup = names("ROLLUP", approved + exception + rejected + broken + auto_broken)
    break_curve = names("BREAK_CURVE", approved + broken)

    def to_ids(members: tuple[str, ...]) -> tuple[int, ...]:
        return tuple(sorted({ids[m] for m in members}))

    return StatusPolicy(
        ids=dict(ids),
        approved=to_ids(approved),
        broken=to_ids(broken),
        auto_broken=to_ids(auto_broken),
        exception=to_ids(exception),
        rejected=to_ids(rejected),
        generated=to_ids(generated),
        universe=to_ids(universe),
        rollup=to_ids(rollup),
        break_curve=to_ids(break_curve),
    )


# --------------------------------------------------------------------------- spec table

# (attribute, environment variable, parser, default as typed in a .env file)
_SPEC: tuple[tuple[str, str, Callable[[str], Any], str], ...] = (
    ("app_env", "APP_ENV", _text, "local"),
    (
        "product_lines_raw",
        "PRODUCT_LINES",
        _text,
        "auto=collections_auto,consumer=collections_consumer",
    ),
    ("db_schema", "DB_SCHEMA", _identifier, "dbo"),
    ("db_read_hint", "DB_READ_HINT", _read_hint, "WITH (NOLOCK)"),
    ("db_driver", "DB_DRIVER", _text, "ODBC Driver 18 for SQL Server"),
    ("db_host", "DB_HOST", _text, "localhost"),
    ("db_port", "DB_PORT", _int(0), "1433"),
    ("db_user", "DB_USER", _text, ""),
    ("db_password", "DB_PASSWORD", _text, ""),
    ("db_trust_server_cert", "DB_TRUST_SERVER_CERT", _bool, "false"),
    ("db_connect_timeout", "DB_CONNECT_TIMEOUT", _int(1), "10"),
    ("db_query_timeout", "DB_QUERY_TIMEOUT", _int(1), "45"),
    ("db_pool_size", "DB_POOL_SIZE", _int(1), "4"),
    ("db_pool_max_age_seconds", "DB_POOL_MAX_AGE_SECONDS", _float(1.0), "900"),
    ("cache_ttl_seconds", "CACHE_TTL_SECONDS", _float(0.0), "30"),
    ("cache_max_entries", "CACHE_MAX_ENTRIES", _int(16), "256"),
    ("benchmarks_cache_ttl_seconds", "BENCHMARKS_CACHE_TTL_SECONDS", _float(0.0), "600"),
    ("agent_effort_cache_ttl_seconds", "AGENT_EFFORT_CACHE_TTL_SECONDS", _float(0.0), "45"),
    ("require_api_auth", "REQUIRE_API_AUTH", _bool, "false"),
    ("api_key", "API_KEY", _text, ""),
    ("api_token", "API_TOKEN", _text, ""),
    ("rate_limit_requests", "RATE_LIMIT_REQUESTS", _int(1), "120"),
    ("rate_limit_window_seconds", "RATE_LIMIT_WINDOW_SECONDS", _int(1), "60"),
    ("rate_limit_max_buckets", "RATE_LIMIT_MAX_BUCKETS", _int(16), "5000"),
    (
        "cors_allow_origins",
        "CORS_ALLOW_ORIGINS",
        _csv,
        "http://localhost:5173,http://127.0.0.1:5173",
    ),
    ("cors_allow_credentials", "CORS_ALLOW_CREDENTIALS", _bool, "false"),
    ("enable_docs_override", "ENABLE_DOCS", _optional_bool, ""),
    ("frontend_dist_dir", "FRONTEND_DIST_DIR", _text, ""),
    ("enable_index_admin", "ENABLE_INDEX_ADMIN", _bool, "false"),
    ("effectiveness_etl_enabled", "EFFECTIVENESS_ETL_ENABLED", _bool, "true"),
    (
        "effectiveness_etl_interval_seconds",
        "EFFECTIVENESS_ETL_INTERVAL_SECONDS",
        _float(1.0),
        "1800",
    ),
    ("enable_telemetry_log", "ENABLE_TELEMETRY_LOG", _bool, "false"),
    ("telemetry_log_path", "TELEMETRY_LOG_PATH", _text, "logs/telemetry.ndjson"),
    ("telemetry_log_max_bytes", "TELEMETRY_LOG_MAX_BYTES", _int(1024), "5242880"),
    ("sentry_dsn", "SENTRY_DSN", _text, ""),
    ("sentry_traces_sample_rate", "SENTRY_TRACES_SAMPLE_RATE", _float(0.0), "0.1"),
    ("sentry_enable_logs", "SENTRY_ENABLE_LOGS", _bool, "true"),
    ("log_level", "LOG_LEVEL", _text, "INFO"),
    ("status_ids_raw", "STATUS_IDS", _text, DEFAULT_STATUS_IDS),
    ("grace_days", "GRACE_DAYS", _int(0), "3"),
    ("excluded_agent_patterns", "EXCLUDED_AGENT_PATTERNS", _agent_patterns, "system%,test%"),
    (
        "effectiveness_excluded_agent_patterns",
        "EFFECTIVENESS_EXCLUDED_AGENT_PATTERNS",
        _agent_patterns,
        "%system%,%test%",
    ),
    ("effectiveness_floor_date", "EFFECTIVENESS_FLOOR_DATE", _iso_date, "2020-01-01"),
    ("effectiveness_detail_row_limit", "EFFECTIVENESS_DETAIL_ROW_LIMIT", _int(1), "250"),
    ("break_curve_edges", "BREAK_CURVE_EDGES", _edges, "7,21,45,90"),
    ("benchmark_min_daily_attempts", "BENCHMARK_MIN_DAILY_ATTEMPTS", _int(1), "4"),
    ("benchmark_min_active_days", "BENCHMARK_MIN_ACTIVE_DAYS", _int(1), "6"),
    (
        "effectiveness_min_slips_per_agent_month",
        "EFFECTIVENESS_MIN_SLIPS_PER_AGENT_MONTH",
        _int(1),
        "8",
    ),
    ("performance_default_days", "PERFORMANCE_DEFAULT_DAYS", _int(1), "30"),
    ("productivity_query_option", "PRODUCTIVITY_QUERY_OPTION", _query_option, ""),
    ("agreements_default_limit", "AGREEMENTS_DEFAULT_LIMIT", _int(1), "200"),
    ("agreements_max_limit", "AGREEMENTS_MAX_LIMIT", _int(1), "2000"),
    ("targets_dir", "TARGETS_DIR", _text, "data/targets"),
    ("targets_upload_max_bytes", "TARGETS_UPLOAD_MAX_BYTES", _int(1024), "10485760"),
    ("target_group_names", "TARGET_GROUP_NAMES", _csv, "Group A,Group B"),
    ("regression_max_points", "REGRESSION_MAX_POINTS", _int(10), "5000"),
)

ENV_VAR_NAMES: tuple[str, ...] = tuple(env for _, env, _, _ in _SPEC) + tuple(
    f"STATUS_SET_{name}" for name in SET_NAMES
)


# --------------------------------------------------------------------------- dotenv


def load_dotenv(path: Path, environ: dict[str, str]) -> None:
    """Fill ``environ`` from a ``KEY=value`` file without overriding existing values."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and not environ.get(key, "").strip():
            environ[key] = value


# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class Settings:
    app_env: str
    product_lines_raw: str
    db_schema: str
    db_read_hint: str
    db_driver: str
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_trust_server_cert: bool
    db_connect_timeout: int
    db_query_timeout: int
    db_pool_size: int
    db_pool_max_age_seconds: float
    cache_ttl_seconds: float
    cache_max_entries: int
    benchmarks_cache_ttl_seconds: float
    agent_effort_cache_ttl_seconds: float
    require_api_auth: bool
    api_key: str
    api_token: str
    rate_limit_requests: int
    rate_limit_window_seconds: int
    rate_limit_max_buckets: int
    cors_allow_origins: tuple[str, ...]
    cors_allow_credentials: bool
    enable_docs_override: bool | None
    frontend_dist_dir: str
    enable_index_admin: bool
    effectiveness_etl_enabled: bool
    effectiveness_etl_interval_seconds: float
    enable_telemetry_log: bool
    telemetry_log_path: str
    telemetry_log_max_bytes: int
    sentry_dsn: str
    sentry_traces_sample_rate: float
    sentry_enable_logs: bool
    log_level: str
    status_ids_raw: str
    grace_days: int
    excluded_agent_patterns: tuple[str, ...]
    effectiveness_excluded_agent_patterns: tuple[str, ...]
    effectiveness_floor_date: date
    effectiveness_detail_row_limit: int
    break_curve_edges: tuple[int, ...]
    benchmark_min_daily_attempts: int
    benchmark_min_active_days: int
    effectiveness_min_slips_per_agent_month: int
    performance_default_days: int
    productivity_query_option: str
    agreements_default_limit: int
    agreements_max_limit: int
    targets_dir: str
    targets_upload_max_bytes: int
    target_group_names: tuple[str, ...]
    regression_max_points: int
    status: StatusPolicy
    lines: tuple[tuple[str, str], ...]

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        dotenv: Path | str | None = None,
    ) -> Settings:
        """Build settings from ``env`` (default: ``os.environ``).

        ``dotenv`` names an optional ``.env`` file. Values already present in the
        environment win over the file, and the file is never written to.
        """
        merged: dict[str, str] = dict(os.environ if env is None else env)
        if dotenv is not None:
            load_dotenv(Path(dotenv), merged)

        values: dict[str, Any] = {}
        for attribute, name, parser, default in _SPEC:
            raw = merged.get(name)
            text = default if raw is None else raw
            try:
                values[attribute] = parser(text)
            except SettingsError as exc:
                raise SettingsError(f"{name}: {exc}") from exc

        overrides = {
            set_name: _csv(merged[f"STATUS_SET_{set_name}"])
            for set_name in SET_NAMES
            if merged.get(f"STATUS_SET_{set_name}")
        }
        overrides = {
            key: tuple(item.upper() for item in members) for key, members in overrides.items()
        }
        values["status"] = build_status_policy(
            parse_status_ids(values["status_ids_raw"]), overrides
        )
        values["lines"] = parse_product_lines(values["product_lines_raw"])

        if values["agreements_default_limit"] > values["agreements_max_limit"]:
            raise SettingsError("AGREEMENTS_DEFAULT_LIMIT must not exceed AGREEMENTS_MAX_LIMIT")
        if values["require_api_auth"] and not (values["api_key"] and values["api_token"]):
            raise SettingsError("REQUIRE_API_AUTH=true needs both API_KEY and API_TOKEN")
        if "*" in values["cors_allow_origins"]:
            values["cors_allow_credentials"] = False
        return cls(**values)

    @classmethod
    def defaults(cls) -> Settings:
        """Settings with every default and no environment. Used to export the OpenAPI document."""
        return cls.from_env({})

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI docs follow the auth switch unless ``ENABLE_DOCS`` says otherwise."""
        if self.enable_docs_override is not None:
            return self.enable_docs_override
        return not self.require_api_auth

    @property
    def line_names(self) -> tuple[str, ...]:
        return tuple(line for line, _ in self.lines)

    @property
    def databases(self) -> tuple[str, ...]:
        return tuple(database for _, database in self.lines)

    def database_for(self, line: str) -> str:
        return dict(self.lines)[line]
