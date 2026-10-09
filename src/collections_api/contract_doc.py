"""Generate the two documents a front end can be built against.

``docs/openapi.json`` is the OpenAPI document of the default application. It needs no
database: the app is built from ``Settings.defaults()`` and never connects.

``docs/api-contract.md`` is rendered from that document plus ``ROUTE_NOTES`` below. The
notes cover what an OpenAPI response model cannot say: which keys ``meta.filters`` and
``meta.quality`` carry on each route. A test calls every route and compares the keys it
really returns with these notes, so the document cannot drift from the code.

Regenerate both files with ``python scripts/export_openapi.py`` and
``python scripts/export_api_contract.py``. A test fails when either file is stale.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

Schema = dict[str, Any]
Pair = tuple[str, str]

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
ENVELOPE_SUFFIXES = ("ObjectEnvelope", "Envelope")
HIDDEN_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})
SHARED_ERRORS = (400, 401, 422, 429, 500, 504)


# --------------------------------------------------------------------------- export


def export_openapi() -> Schema:
    """The OpenAPI document of the default application."""
    from collections_api.main import create_app
    from collections_api.settings import Settings

    return create_app(Settings.defaults()).openapi()


def render_openapi_json(schema: Schema) -> str:
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- route notes


@dataclass(frozen=True)
class RouteNotes:
    """What a route puts in ``meta`` besides the standard fields."""

    filters: tuple[Pair, ...] = ()
    quality: tuple[Pair, ...] = ()
    pagination: tuple[Pair, ...] = ()
    sources: str = "The product lines that were read."
    errors: str = ""
    plain: bool = False  # a plain status body instead of the envelope


LINE: Pair = ("line", "The product line that was queried: a configured name or `all`.")
PERIOD: Pair = (
    "period",
    "`today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive.",
)
TODAY: Pair = ("period", "Always `today`.")
TEAM: Pair = ("team", "The `team` text as it was sent, or `all`.")
INSTALLMENT: Pair = ("installment_type", "`first` or `later`.")

PAGING: tuple[Pair, ...] = (
    ("limit", "Page size that was applied."),
    ("offset", "Offset that was applied."),
    ("total", "Rows available before paging."),
    ("returned", "Rows in this response."),
    ("totals_by_line", "Rows available per product line. Present only for `all`."),
)
SERIES_NOTES = RouteNotes(
    filters=(
        ("series", "The series key, for example `daily-first`."),
        ("line", "The variant that was served: a configured name or `all`."),
        ("from", "First date the series covers (`EFFECTIVENESS_FLOOR_DATE`)."),
    ),
    quality=(
        ("fetched_at", "When the rows were read from the database, UTC ISO 8601."),
        ("stale", "`true` when the last refresh of this series failed and the rows are older."),
    ),
    errors="When the last refresh failed: one item with `source` `<series>:<line>`.",
)
BY_PORTFOLIO = RouteNotes(filters=(LINE, PERIOD))
BY_AGENT = RouteNotes(filters=(LINE, PERIOD, TEAM))
DETAIL_ONE_PORTFOLIO = RouteNotes(filters=(LINE, PERIOD, ("portfolio", "The label as sent.")))
DETAIL_ALL = RouteNotes(filters=(LINE, PERIOD))
DETAIL_ONE_AGENT = RouteNotes(filters=(LINE, PERIOD, ("agent", "The agent name as sent.")))


def _detail_notes() -> dict[str, RouteNotes]:
    notes: dict[str, RouteNotes] = {}
    for kind in ("agreements", "exceptions", "rejected", "broken"):
        notes[f"{kind}_detail"] = DETAIL_ONE_PORTFOLIO
        notes[f"{kind}_detail_all"] = DETAIL_ALL
        notes[f"{kind}_detail_by_agent"] = DETAIL_ONE_AGENT
    return notes


def _series_notes() -> dict[str, RouteNotes]:
    keys = (
        "daily_first",
        "monthly_first",
        "daily_later",
        "monthly_later",
        "daily_later_by_due",
        "monthly_later_by_due",
        "monthly_agent_first",
        "monthly_agent_later",
        "monthly_agent_later_by_due",
    )
    return {f"effectiveness_{key}": SERIES_NOTES for key in keys}


ROUTE_NOTES: dict[str, RouteNotes] = {
    "health_live": RouteNotes(plain=True),
    "health_ready": RouteNotes(plain=True),
    "health_db_all": RouteNotes(plain=True),
    "health_db_line": RouteNotes(plain=True),
    "agreements_today": RouteNotes(filters=(LINE, TODAY), pagination=PAGING),
    "agreements_table": RouteNotes(
        filters=(LINE, TODAY, TEAM, ("agent_name", "The agent filter after trimming, or `null`."))
    ),
    "performance_table": RouteNotes(
        filters=(LINE, PERIOD, ("agent_name", "The agent filter after trimming, or `null`."))
    ),
    "portfolio_labels": RouteNotes(filters=(LINE,)),
    "productivity": RouteNotes(
        filters=(LINE, PERIOD, TEAM, ("portfolio", "The portfolio label filter, or `null`.")),
        quality=(
            ("rows", "Rows returned by the query, before the `team` filter."),
            ("numeric_cast_failures", "Values that could not be read as numbers and became 0."),
            ("portfolio_partial", "`true` when a portfolio filter nulled the effort ratios."),
            ("elapsed_ms", "Time spent on the request, in milliseconds."),
        ),
    ),
    "load_status": RouteNotes(
        filters=(LINE, TODAY, TEAM),
        quality=(
            ("status", "`ok`, or `partial` when a line failed or a value was unreadable."),
            ("lines_requested", "Product lines the request covered."),
            ("lines_ok", "Product lines that produced a row."),
            ("numeric_cast_failures", "Values that could not be read as numbers and became 0."),
        ),
        errors="One item per failed line (`source` is the line), and one for unreadable values.",
    ),
    "agent_performance": RouteNotes(filters=(LINE, PERIOD)),
    "agent_effort": RouteNotes(
        filters=(
            ("line", "Always `all`."),
            TODAY,
            ("force_refresh", "Whether the cached payload was bypassed."),
        ),
        quality=(("cache_age_seconds", "0 for a fresh computation, else the age of the cache."),),
        sources="Every configured product line.",
    ),
    "benchmarks": RouteNotes(
        filters=(LINE, ("lookback_months", "The look-back that was applied."))
    ),
    "exceptions_by_portfolio": BY_PORTFOLIO,
    "agreements_by_portfolio": BY_PORTFOLIO,
    "rejected_by_portfolio": BY_PORTFOLIO,
    "broken_by_portfolio": BY_PORTFOLIO,
    "first_installment_by_portfolio": BY_PORTFOLIO,
    "exceptions_by_agent": BY_AGENT,
    "first_installment_by_agent": BY_AGENT,
    "first_installment_summary": BY_AGENT,
    "portfolio_rollup": BY_PORTFOLIO,
    "received_by_portfolio": BY_PORTFOLIO,
    "exceptions_without_portfolio": BY_PORTFOLIO,
    **_detail_notes(),
    "targets": RouteNotes(
        filters=(("period", "Quarter label such as `3Q26`. Absent before the first upload."),),
        quality=(
            ("validated", "Whether the document passed both checksums."),
            ("months", "The three months the targets cover, as `YYYYMM`."),
            ("extracted_at", "When the document was extracted, UTC ISO 8601."),
        ),
        sources="The name of the uploaded file. Empty before the first upload.",
        errors="Before the first upload: one item with a `message` and no `source`.",
    ),
    "targets_upload": RouteNotes(sources="The name of the uploaded file."),
    **_series_notes(),
    "effectiveness_summary": RouteNotes(
        filters=(LINE, PERIOD, INSTALLMENT, ("portfolio_id", "The portfolio id filter, or `null`."))
    ),
    "effectiveness_slips": RouteNotes(
        filters=(LINE, PERIOD, INSTALLMENT, ("kind", "The bucket that was listed.")),
        quality=(("truncated", "`true` when the row cap was reached and rows may be missing."),),
        pagination=(
            ("limit", "The row cap (`EFFECTIVENESS_DETAIL_ROW_LIMIT`)."),
            ("truncated", "Same as `quality.truncated`."),
        ),
    ),
    "effectiveness_break_curve": RouteNotes(filters=(LINE, PERIOD)),
    "effectiveness_portfolio_summary": RouteNotes(
        filters=(LINE, PERIOD, INSTALLMENT, ("portfolio", "The label as sent."))
    ),
    "effectiveness_portfolio_ranking": RouteNotes(filters=(LINE, PERIOD, INSTALLMENT)),
    "effectiveness_generation_ranking": RouteNotes(filters=(LINE, PERIOD)),
    "regression_agents": RouteNotes(
        filters=(("point_count", "Points received, before cleaning."),),
        sources='Always `["request"]`: nothing is read from a database.',
    ),
    "admin_indexes_status": RouteNotes(filters=(LINE,)),
    "admin_indexes_apply": RouteNotes(
        filters=(
            LINE,
            ("dry_run", "Whether statements were only listed."),
            ("online", "Whether `ONLINE = ON` was requested."),
            ("update_statistics", "Whether statistics were refreshed."),
        )
    ),
}


# --------------------------------------------------------------------------- fixed text


INTRO = """\
# API contract

This file is generated from the running application by `scripts/export_api_contract.py`.
Do not edit it by hand: change the code or the route notes in
`src/collections_api/contract_doc.py` and run the script again. A test fails when the
file is stale. The machine-readable form of the same contract is `docs/openapi.json`.
"""

CONVENTIONS = """\
## Conventions

### Addressing

Every path also answers under the prefix `/api` (`/api/dashboard/...`), so a single-page
app can be served from the same origin. The tables below show the paths without it.

Most data routes take the **product line** in the path or in a `line` query parameter.
A line name is one of the names configured in `PRODUCT_LINES` (`auto` and `consumer` by
default). `all` combines every line: measures are summed and ratios are recomputed from the
sums. A route that cannot combine lines says so and answers 400 for `all`.

### Date windows

`date_from` and `date_to` are calendar dates (`YYYY-MM-DD`), both inclusive. Give both or
neither; neither means today in the server's local time, and exactly one is a 400. A
reversed pair is swapped. `meta.filters.period` echoes the window that was applied.

### Team filter

`team` keeps agents whose login or display name contains a piece of text, compared
case-insensitively. Only the part after the last colon is used, so `Desk: 02` matches
`02`. Empty or `all` means everyone.

### Envelope

Data routes answer `{ "meta", "data", "errors" }`. `data` is a list of rows, or one object
for routes that return a single result. The health routes answer plain status objects
instead.

### Numbers

Amounts are plain numbers with no currency symbol. Timestamps are ISO 8601 strings and
dates are `YYYY-MM-DD`. Fields ending in `_count` are integers. Fields ending in `_pct` are
percentages from 0 to 100; a ratio whose denominator is zero is `null` (a few routes
document that they return 0 instead). No field identifies a debtor.

### Authentication and limits

Authentication is off unless the server runs with `REQUIRE_API_AUTH=true`. When it is on,
every route except `GET /health/live` needs both the `X-API-Key` header and an
`Authorization: Bearer <token>` header; anything else is a 401. Routes under `/dashboard/`,
`/effectiveness/`, `/regression/` and `/admin/` are rate limited per client address, and a
request counts whether or not its credentials are accepted. The 429 response carries a
`Retry-After` header in seconds. Every response carries an
`X-Run-Id` header, the same value as `meta.run_id`. A client may send its own `X-Run-Id`
(letters, digits, `.`, `_`, `-`, up to 64 characters) and get it back.

### Errors

A failed request answers a non-2xx status and an error body: the envelope with an empty
`data`, one item in `errors`, and a top-level `detail` string with the same message. The
text never contains SQL, driver messages or stack traces.

| Status | Meaning |
|---|---|
| 400 | A parameter is not acceptable: unknown line, half a date window, `all` where one line is needed. |
| 401 | Authentication is on and the credentials are missing or wrong. |
| 403 | The route is switched off, or the request origin is not allowed. |
| 413 | The request body or upload is larger than the limit for the route. |
| 422 | A parameter or body field is missing or has the wrong type; `detail` names it. |
| 429 | Rate limit reached. |
| 500 | A query failed, a row did not match the contract, or the service is misconfigured. |
| 503 | Not ready: effectiveness series before their first refresh, or a database that fails a health check. |
| 504 | A query ran longer than `DB_QUERY_TIMEOUT`. |
"""


# --------------------------------------------------------------------------- rendering helpers


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _is_null(schema: Schema) -> bool:
    return schema.get("type") == "null"


def type_label(prop: Schema) -> str:
    """A short, readable type for a JSON-schema property."""
    if "$ref" in prop:
        name = _ref_name(prop["$ref"])
        return f"[`{name}`](#{name.lower()})"
    if "anyOf" in prop:
        options = [option for option in prop["anyOf"] if not _is_null(option)]
        label = " or ".join(type_label(option) for option in options)
        return f"{label}, nullable" if len(options) != len(prop["anyOf"]) else label
    kind = prop.get("type", "any")
    if kind == "array":
        return f"list of {type_label(prop.get('items', {}))}"
    if kind == "object":
        extra = prop.get("additionalProperties")
        return f"map of text to {type_label(extra)}" if isinstance(extra, dict) else "object"
    if prop.get("format"):
        return f"{kind} ({prop['format']})"
    if "enum" in prop:
        return " or ".join(f"`{value}`" for value in prop["enum"])
    return kind


def field_rows(model: Schema) -> list[tuple[str, ...]]:
    required = set(model.get("required", ()))
    rows = []
    for name, prop in model.get("properties", {}).items():
        presence = "yes" if name in required else "no"
        rows.append((f"`{name}`", type_label(prop), presence, prop.get("description", "")))
    return rows


def fields_table(model: Schema) -> str:
    return _table(("Field", "Type", "Always present", "Description"), field_rows(model))


def operations(schema: Schema) -> Iterator[tuple[str, str, Schema]]:
    for path, item in schema["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                yield method.upper(), path, item[method]


def _response_schema(operation: Schema, status: str) -> Schema | None:
    response = operation.get("responses", {}).get(status)
    if not response:
        return None
    return response.get("content", {}).get("application/json", {}).get("schema")


def payload_of(schema: Schema, operation: Schema) -> tuple[str, bool] | None:
    """``(model name, is_list)`` of a 200 response, or ``None`` if it has no model."""
    body = _response_schema(operation, "200")
    if not body or "$ref" not in body:
        return None
    name = _ref_name(body["$ref"])
    if not name.endswith(ENVELOPE_SUFFIXES):
        return name, False
    data = schema["components"]["schemas"][name]["properties"]["data"]
    if data.get("type") == "array":
        return _ref_name(data["items"]["$ref"]), True
    return _ref_name(data["$ref"]), False


def _referenced(schemas: dict[str, Schema], names: set[str]) -> set[str]:
    """Every schema reachable from ``names``."""
    seen: set[str] = set()
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        pending.extend(_ref_name(ref) for ref in _refs(schemas[name]))
    return seen


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _refs(item)


# --------------------------------------------------------------------------- sections


def _limits(prop: Schema) -> str:
    """Default and bounds of a parameter, as a sentence fragment."""
    notes = []
    if prop.get("default") is not None:
        notes.append(f"Default `{prop['default']}`.")
    if "minimum" in prop:
        notes.append(f"Minimum {prop['minimum']}.")
    if "maximum" in prop:
        notes.append(f"Maximum {prop['maximum']}.")
    return " ".join(notes)


def _parameters(operation: Schema) -> str:
    rows = []
    for parameter in operation.get("parameters", ()):
        prop = parameter.get("schema", {})
        text = parameter.get("description") or prop.get("description", "")
        rows.append(
            (
                f"`{parameter['name']}`",
                parameter["in"],
                type_label(prop),
                "yes" if parameter.get("required") else "no",
                f"{text} {_limits(prop)}".strip(),
            )
        )
    if not rows:
        return "_No parameters._"
    return _table(("Name", "In", "Type", "Required", "Description"), rows)


def _request_body(schema: Schema, operation: Schema) -> str:
    body = operation.get("requestBody")
    if not body:
        return ""
    media, content = next(iter(body["content"].items()))
    name = _ref_name(content["schema"]["$ref"])
    model = schema["components"]["schemas"][name]
    return f"**Request body** (`{media}`, `{name}`)\n\n{fields_table(model)}\n\n"


def _meta_lists(notes: RouteNotes) -> str:
    parts = []
    for title, pairs in (
        ("`meta.filters` keys", notes.filters),
        ("`meta.quality` keys", notes.quality),
        ("`meta.pagination` keys", notes.pagination),
    ):
        if pairs:
            parts.append(
                f"{title}\n\n" + _table(("Key", "Meaning"), [(f"`{k}`", v) for k, v in pairs])
            )
    parts.append(f"`meta.sources`: {notes.sources}")
    if notes.errors:
        parts.append(f"`errors`: {notes.errors}")
    return "\n\n".join(parts)


def _plain_response(schema: Schema, operation: Schema) -> str:
    parts = []
    for status, response in operation["responses"].items():
        body = _response_schema(operation, status)
        name = _ref_name(body["$ref"]) if body and "$ref" in body else None
        if name in HIDDEN_SCHEMAS:
            continue  # the framework's validation schema; Conventions documents the 422
        label = f"**{status}**: {response.get('description', '')}"
        if name and name != "ErrorBody":
            model = schema["components"]["schemas"][name]
            label += f" Body `{name}`:\n\n{fields_table(model)}"
        elif name:
            label += " Body: the error body."
        parts.append(label)
    return "\n\n".join(parts)


def _response(schema: Schema, operation: Schema, notes: RouteNotes) -> str:
    if notes.plain:
        return "**Responses** (plain status body, not an envelope)\n\n" + _plain_response(
            schema, operation
        )
    payload = payload_of(schema, operation)
    if payload is None:
        return ""
    name, is_list = payload
    shape = f"`data` is a list of `{name}`" if is_list else f"`data` is one `{name}` object"
    model = schema["components"]["schemas"][name]
    text = f"**Response 200.** {shape}.\n\n{fields_table(model)}\n\n{_meta_lists(notes)}"
    extras = _other_statuses(operation)
    return text + (f"\n\n{extras}" if extras else "")


def _other_statuses(operation: Schema) -> str:
    lines = []
    for status, response in operation["responses"].items():
        if status == "200" or int(status) in SHARED_ERRORS:
            continue
        lines.append(f"- **{status}**: {response.get('description', '')}")
    return "**Further statuses**\n\n" + "\n".join(lines) if lines else ""


def _operation_section(schema: Schema, method: str, path: str, operation: Schema) -> str:
    notes = ROUTE_NOTES.get(operation["operationId"], RouteNotes())
    secured = "yes" if operation.get("security") else "no"
    head = f"### `{method} {path}`\n\n**{operation['summary']}**\n\n"
    facts = (
        f"Operation `{operation['operationId']}`. "
        f"Needs credentials when authentication is on: {secured}.\n\n"
    )
    description = operation.get("description", "").strip()
    pieces = [
        head + facts + (description + "\n\n" if description else ""),
        "**Parameters**\n\n" + _parameters(operation) + "\n\n",
        _request_body(schema, operation),
        _response(schema, operation, notes) + "\n",
    ]
    return "".join(pieces)


def _index(schema: Schema) -> str:
    rows = []
    for method, path, operation in operations(schema):
        anchor = f"{method} {path}".lower()
        for char in "`/{}":
            anchor = anchor.replace(char, "")
        anchor = anchor.replace(" ", "-")
        rows.append((f"`{method}`", f"[`{path}`](#{anchor})", operation["summary"]))
    return _table(("Method", "Path", "Summary"), rows)


def _tag_sections(schema: Schema) -> str:
    by_tag: dict[str, list[tuple[str, str, Schema]]] = {}
    for method, path, operation in operations(schema):
        by_tag.setdefault(operation["tags"][0], []).append((method, path, operation))
    parts = []
    for tag in schema.get("tags", ()):
        group = by_tag.get(tag["name"], [])
        if not group:
            continue
        parts.append(f"## {tag['name'].capitalize()}\n\n{tag['description']}\n")
        parts.extend(_operation_section(schema, m, p, o) for m, p, o in group)
    return "\n".join(parts)


def _envelope_section(schema: Schema) -> str:
    schemas = schema["components"]["schemas"]
    parts = ["## Envelope types\n"]
    for name in ("Meta", "Pagination", "ErrorItem", "ErrorBody"):
        parts.append(f"### {name}\n\n{fields_table(schemas[name])}\n")
    return "\n".join(parts)


def _nested_section(schema: Schema) -> str:
    schemas = schema["components"]["schemas"]
    tops = {payload[0] for _, _, op in operations(schema) if (payload := payload_of(schema, op))}
    body_models = {
        _ref_name(content["schema"]["$ref"])
        for _, _, op in operations(schema)
        for content in op.get("requestBody", {}).get("content", {}).values()
    }
    wanted = _referenced(schemas, tops | body_models)
    envelope_types = {"Meta", "Pagination", "ErrorItem", "ErrorBody"}
    nested = sorted(
        name
        for name in wanted - tops - envelope_types - HIDDEN_SCHEMAS
        if not name.endswith(ENVELOPE_SUFFIXES)
    )
    parts = ["## Nested types\n", "Types that appear inside the rows above.\n"]
    parts.extend(f"### {name}\n\n{fields_table(schemas[name])}\n" for name in nested)
    return "\n".join(parts)


def render_contract(schema: Schema) -> str:
    """The markdown contract for ``schema`` (the OpenAPI document)."""
    info = schema["info"]
    header = f"{INTRO}\nService: **{info['title']}**, version `{info['version']}`.\n\n"
    overview = f"## Endpoints\n\n{_index(schema)}\n\n"
    sections = [
        header + CONVENTIONS,
        _envelope_section(schema),
        overview + _tag_sections(schema),
        _nested_section(schema),
    ]
    return "\n".join(part.rstrip() + "\n" for part in sections)
