# Extending the service

Four extension points exist. Each one is exercised by a test.

## Mount your own routes

`create_app` accepts `extra_routers`. They are mounted after the built-in routers and share
what the built-in ones use:

* the application context (settings, query configuration, executor, caches),
* authentication and rate limiting, applied by the first path segment of the router,
* the `/api` prefix alias, the error envelope and the `X-Run-Id` header.

Body size ceilings are set only for the built-in upload and regression prefixes. A route
that accepts a body has to bound it itself.

This is the place for features that do not belong to a KPI service, for example a forecast
of the day's pace or a conversational layer over the same data. This repository ships
neither.

```python
from fastapi import APIRouter, Request

from collections_api.api.common import cached_query, respond
from collections_api.api.dependencies import Ctx
from collections_api.core.sql import Period, Query
from collections_api.main import create_app
from collections_api.settings import Settings

router = APIRouter(prefix="/pace", tags=["pace"])


@router.get("/{line}")
def pace(ctx: Ctx, request: Request, line: str):
    line = ctx.cfg.resolve_line(line)  # 400 for an unknown line
    period = Period.day(ctx.today())
    query = Query("SELECT 1 AS ok WHERE 1 = ?", (1,))  # build your own statement
    key = f"pace|{line}|{period.label}"
    rows = cached_query(ctx, request, key, query, line, "pace/forecast")
    return respond(ctx, request, rows, line=line, filters={"line": line, "period": period.label})


app = create_app(Settings.from_env(dotenv=".env"), extra_routers=[router])
```

`/pace/auto` and `/api/pace/auto` now answer with the standard envelope. With
`REQUIRE_API_AUTH=true` they need the same credentials as every other route, and they count
against the same rate limit.

Routes added this way do not appear in `docs/api-contract.md`, which describes the built-in
app. To document them, generate your own OpenAPI document from your app object.

## Replace the database

The rest of the service talks to a database through one method:

```python
class QueryExecutor(Protocol):
    def run(self, sql, database, params=None, *, run_id=None, context="query") -> list[dict]: ...
```

Pass your own implementation as `create_app(settings, executor=...)`. The tests do exactly
that with a recording fake. An executor for another driver can reuse the rest of the
service as it is, as long as it understands the dialect of the statements.

The statements are T-SQL. If your engine differs, these are the constructs to translate, all
in `core/sql.py` and `domains/`:

| Construct | Where |
|---|---|
| `SELECT TOP n`, `CROSS APPLY` and `OUTER APPLY` | `QueryConfig.portfolio_label_lookup`, the detail and chart builders |
| `OFFSET ... ROWS FETCH NEXT ... ROWS ONLY` | the paged agreements list |
| `DATEADD`, `DATEDIFF`, `CAST(... AS DATE)`, `YEAR()`, `MONTH()` | `Period`, `QueryConfig.paid_in_grace`, the effectiveness and productivity builders |
| `CONVERT(varchar(10), ..., 120)` | date output in the effectiveness and detail queries |
| `ISNULL`, `NULLIF`, `CONCAT`, `CEILING` | the productivity and agreements builders |
| table hints such as `WITH (NOLOCK)` | `DB_READ_HINT`, which can be set to empty |
| `database.schema.table` names | `QueryConfig.table` |

The test adapter in `tests/sqlite_adapter.py` shows how far a mechanical rewrite goes: it
translates most of these with regular expressions for a SQLite run, and it leaves `APPLY`
and `OFFSET ... FETCH` alone.

## Add a route to the built-in app

A new route has to leave the documents consistent, and the tests enforce that. The steps:

1. Write the query builder in the matching `domains/` package. Export its column list as a
   constant, as the existing builders do.
2. Add a row model to `api/models.py`, deriving from `ContractModel`, with the same fields
   in the same order as the column list.
3. Describe each field: with `Field(description=...)` on the model, or with an entry in
   `api/field_help.py` when the field means the same on several routes.
4. Add the route to a router under `api/routers/` with a unique `operation_id` and a
   summary, using `responses=ok_response(YourRow)`.
5. Add an entry for the `operation_id` to `ROUTE_NOTES` in `contract_doc.py`: the keys the
   route puts in `meta.filters`, `meta.quality` and `meta.pagination`.
6. If the statement reads a column that is not in `docs/data-contract.md`, add it there.
7. Run `python scripts/export_openapi.py` and `python scripts/export_api_contract.py`.

`tests/test_contract_docs.py` and `tests/test_contract_coverage.py` fail at the step you
skipped.

## Add a setting

Add a line to `_SPEC` in `settings.py` and a field to `Settings`. Then document the
variable in `.env.example` and `docs/configuration.md`; `tests/test_settings.py` fails if
either is missing.
