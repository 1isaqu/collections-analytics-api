# Collections Analytics API

A read-only FastAPI service that turns eight relational tables into collections KPIs: agreements generated, agent productivity, whether payment slips get paid on time, quarterly targets, and a few aggregates for charts and drill-downs. It runs on SQL Server (T-SQL), answers its data routes with one `{ meta, data, errors }` envelope, caches and pools connections, and ships a generated OpenAPI document and endpoint reference so a front end can be built against it before any database exists.

This is an independent portfolio project. It is not affiliated with any company and contains no real data: every table name, status id, portfolio label and default in the repository is invented.

## What it does

The service sits between a database of collection agreements and a dashboard. It has 56 routes in nine groups.

| Group | Routes | What it answers |
|---|---|---|
| health | 4 | Is the process up, can it reach each product line's database. |
| agreements | 4 | Today's approved installments (paged), today's agreements one row each, an agent performance table, portfolio labels. |
| productivity | 5 | Per-agent effort and results, a check that today's data loaded, an agent comparison, internal benchmarks. |
| charts | 11 | The aggregates behind cards and charts: exceptions, rejections, broken agreements and first installments by portfolio or agent. |
| details | 12 | The agreement lists behind each chart, by portfolio, for every portfolio, or for one agent. |
| targets | 2 | Read the latest quarterly targets, or upload a PDF to replace them. |
| effectiveness | 15 | Are issued payment slips paid inside the grace period: nine time series, a summary, a slip list, a break curve and portfolio rankings. |
| regression | 1 | Fit five least-squares models of agent results on effort. |
| admin | 2 | Recommended-index status and creation. Off unless enabled. |

Some details of how it works:

* Each KPI is computed by one SQL builder. The card, the chart and the drill-down list call the same one, so a number cannot come out two ways.
* Aggregates over contact events and agreements are separate CTEs joined on the agent. Installments are de-duplicated with the business filters applied before the ranking. A portfolio label is resolved with `CROSS APPLY ... TOP 1`, so an agreement that covers several debts stays one row.
* A right-party contact (CPC) needs both the `answered` and the `right_party` flag, so attempts, answers, CPC and agreements stay in order. A test pins this.
* The app takes its database through a one-method protocol. Importing it, building it and exporting the OpenAPI document never import `pyodbc`, and the tests run with no database.
* Operational details: a single-flight TTL cache with an entry ceiling, a connection pool that never blocks and replaces stale connections, driver timeouts mapped to 504, constant-time credential checks, a sliding-window rate limit, request body ceilings, optional Sentry and an optional event log.
* `docs/openapi.json` and `docs/api-contract.md` are generated from the app. Tests fail when either differs from a fresh export, when a response model disagrees with the columns of its query, or when a query reads a column the data contract does not list.

## Architecture

```
 HTTP request
      |
  CORS -> body-size ceiling -> /api prefix alias -> security middleware
                                                    (run id, auth, rate limit)
      |
  router handler
      |   resolve product line and date window   core/sql.py   QueryConfig, Period, Query
      |   build a statement                      domains/*     one builder per route family
      |   look in the TTL cache                  core/cache.py single flight per key
      v
  QueryExecutor (protocol) -> ConnectionPool -> pyodbc -> SQL Server
      |
  rows -> envelope { meta, data, errors } -> JSON
```

Nothing opens a connection until the first query runs. [docs/architecture.md](docs/architecture.md) describes each layer, the caching and refresh behaviour, security and observability. [docs/decisions.md](docs/decisions.md) explains why the code is shaped this way.

## Quick start

You need Python 3.11 or newer. The tests ran locally on 3.14, and CI runs 3.11 and 3.13.

```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,pdf]"
pytest
python scripts/export_openapi.py --check
```

That is enough to read, test and regenerate everything. To run the service on its own:

```
cp .env.example .env
python -m collections_api          # http://127.0.0.1:8000, interactive docs at /docs
```

The service starts without a reachable database. `GET /health/live` answers 200 and `/docs` loads. A data route answers 500: first with a message that the `pyodbc` package is not installed, and once you install the `sqlserver` extra, with one that it could not connect. If `DB_USER` or `DB_PASSWORD` is empty, the message names those two variables instead. To serve real numbers, connect your own data as described next.

The command takes `--host`, `--port` and `--env-file`. To run several workers, use `uvicorn --factory collections_api.main:create_app --workers N`, and read the note on process-local state under Limitations first.

## Connect your own data

The service reads eight tables and nothing else: `agreements`, `agreement_debts`, `debts`, `debt_attributes`, `contact_events`, `contact_results`, `agreement_statuses` and `agents`. [docs/data-contract.md](docs/data-contract.md) lists every column the service reads, with types and meaning, and shows how to expose an existing table under a contract name with a view.

1. Create the eight tables or views in one SQL Server database per product line. The names `auto` and `consumer` are defaults; `PRODUCT_LINES` takes any `name=database` pairs.
2. Install the driver pieces: an ODBC driver for SQL Server on the machine, then `pip install -e ".[sqlserver]"`.
3. Create a login that can only read those tables. Put its host, user and password in `.env` (`DB_HOST`, `DB_USER`, `DB_PASSWORD`). The `.env` file is ignored by git.
4. Give your status ids to `STATUS_IDS`. The seven status names are fixed (`ACTIVE`, `BROKEN`, `PAID`, `PENDING_EXCEPTION`, `REJECTED`, `AUTO_BROKEN`, `PAID_MANUAL`); the ids are yours. The sets the KPIs use, such as "generated", are built from them and can be replaced one by one.
5. Start the service and call `GET /health/db`, then `GET /health/ready`. Then try `GET /dashboard/portfolios/auto`.
6. Calibrate the business settings on your own data (see below) before you trust a number.

[docs/data-sources.md](docs/data-sources.md) says where every row and every default in this repository comes from, and what the tests use in place of a database.

One call and its response (the rows are invented):

```
GET /dashboard/portfolios/auto
```

```json
{
  "meta": {
    "generated_at": "2026-03-18T09:30:00+00:00",
    "total_rows": 2,
    "sources": ["auto"],
    "filters": {"line": "auto"},
    "run_id": "srv-298d9f0039b5",
    "quality": {}
  },
  "data": [
    {"portfolio_label": "Portfolio A-01"},
    {"portfolio_label": "Portfolio A-02"}
  ],
  "errors": []
}
```

The same `run_id` comes back in the `X-Run-Id` header. [docs/api-contract.md](docs/api-contract.md) lists every route with its parameters, the fields of each row and the keys that appear in `meta.filters` and `meta.quality`. `docs/openapi.json` is the machine-readable version of the same contract.

## Configuration

Every setting is an environment variable. The table shows the ones you will touch first; [docs/configuration.md](docs/configuration.md) has all of them, and `.env.example` has placeholders for each.

| Variable | Default | Meaning |
|---|---|---|
| `PRODUCT_LINES` | `auto=collections_auto,consumer=collections_consumer` | Line name to database name. `all` combines lines. |
| `DB_HOST`, `DB_PORT` | `localhost`, `1433` | SQL Server address. |
| `DB_USER`, `DB_PASSWORD` | empty | Read-only login. Data routes fail without them. |
| `DB_QUERY_TIMEOUT` | `60` | Statement timeout in seconds. A timeout answers 504. |
| `DB_READ_HINT` | `WITH (NOLOCK)` | Table hint on every read. May be empty. |
| `STATUS_IDS` | `ACTIVE=21,BROKEN=22,...` | Your ids for the seven fixed status names. |
| `GRACE_DAYS` | `3` | A slip counts as paid in grace if paid within this many days after its due date. |
| `EXCLUDED_AGENT_PATTERNS` | `system%,test%` | `LIKE` patterns for agents left out of KPIs, applied in SQL. |
| `CACHE_TTL_SECONDS` | `60` | Row cache lifetime. `0` turns storage off. |
| `REQUIRE_API_AUTH` | `false` | Require `X-API-Key` and a bearer token on every route except `/health/live`. |
| `API_KEY`, `API_TOKEN` | empty | The expected credentials. Required when auth is on. |
| `RATE_LIMIT_REQUESTS` | `120` | Requests per window and client address. |
| `ENABLE_INDEX_ADMIN` | `false` | Allows the only routes that can write to the database. |
| `FRONTEND_DIST_DIR` | empty | Serve a built single-page app next to the API. |
| `SENTRY_DSN` | empty | Turns Sentry on. Read from the environment only. |

The defaults for grace days, band edges, minimum sample sizes, agent patterns and status ids are invented placeholders. The right values depend on the operation, so calibrate them on your own data.

## Running the tests

```
pytest
```

The suite needs no database and no network. It builds the app around a recording fake executor, so each test sees the exact SQL and parameters a route produced. It covers:

* query construction for every route family, including parameter order and the shape of the `UNION ALL` and CTE statements;
* the dedup, exclusion and counting rules, by running a subset of the generated SQL against in-memory SQLite through a syntax-only adapter (`tests/sqlite_adapter.py`) with rows written in the test code;
* the connection pool and executor, through a fake driver: connection string, reuse, expiry, timeout mapping;
* the cache (single flight, bounded waits, entry ceiling), rate limiter, authentication, body ceilings, the error envelope, the static-file fallback, telemetry, the index administration routes and the background refresh;
* the targets PDF extractor, fed synthetic words with checksums, and the regression models;
* the documents: `docs/openapi.json` and `docs/api-contract.md` match a fresh export, every response model matches its query columns, and every column the SQL reads is listed in the data contract.

CI also runs `ruff check`, `ruff format --check` and both export scripts with `--check`. The full run takes a few minutes on a slow laptop. The slowest tests, at 10 to 20 seconds each there, start a fresh interpreter to prove the app imports without a driver or a database.

## Regenerating the contract documents

```
python scripts/export_openapi.py
python scripts/export_api_contract.py
```

Run both after you add or change a route. The steps for adding a route, including the notes a route needs so the documents stay consistent, are in [docs/extending.md](docs/extending.md).

## Project layout

```
src/collections_api/
  main.py              create_app(): routers, middleware, lifespan, OpenAPI security schemes
  __main__.py          the collections-api command
  settings.py          every environment variable, parsed and validated once
  contract_doc.py      renders docs/openapi.json and docs/api-contract.md
  core/                no HTTP in here
    sql.py             QueryConfig, Period, Query: the shared SQL fragments
    database.py        QueryExecutor protocol, connection pool, SQL Server executor
    cache.py           single-flight TTL cache
    envelope.py        the { meta, data, errors } builder
    errors.py          ApiError: a status code and a message that is safe to show
    pagination.py      limit and offset normalisation
    ratelimit.py       sliding-window limiter
    telemetry.py       optional event log and optional Sentry
    indexes.py         recommended-index statements, status and apply
  domains/             query builders and the small services around them
    agreements/  charts/  productivity/  effectiveness/  targets/  regression/
  api/
    routers/           one module per route group
    models.py          response models (documentation only, not run-time validation)
    middleware.py  dependencies.py  errors.py  context.py  static.py  common.py
    field_help.py      shared field descriptions for the generated reference
scripts/               export_openapi.py, export_api_contract.py
docs/                  architecture, decisions, data contract, data sources, configuration,
                       glossary, extending, and the two generated contract files
tests/                 inline fixtures only
```

## Key design decisions

The full list, with reasoning, is in [docs/decisions.md](docs/decisions.md). The ones that most affect the code:

* The service only reads. The one write path, creating indexes, is off by default and runs as a dry run unless asked.
* "Generated" agreements (approved, broken and auto-broken) are the base of every value measure, so an agreement made today counts even if it breaks next week. Breaking is reported separately.
* Excluded agents are removed inside the SQL statement, never after the rows come back, so they cannot leak into sums or top lists.
* Dates are written into the SQL as `CAST('YYYYMMDD' AS DATE)` literals built from Python dates instead of `DECLARE`d variables, so the optimizer sees the values.
* The app receives an explicit `AppContext` instead of using module-level singletons, which is what lets tests and the OpenAPI export run with no database.
* The slow effectiveness series are refreshed on a timer, with freshness and the last error tracked for each series and variant, and served from memory. A series that has never loaded answers 503 and does not return an empty list.
* No response or query carries a name, document number, phone or address of the people being collected from. Effort is counted on an opaque `debt_owner_id`.

## Extending

`create_app(settings, executor=..., extra_routers=[...])` is the supported way to add routes that do not belong in a KPI service, such as a forecast of the day's pace or a conversational layer over the same data. Extra routers get the same authentication, rate limiting, `/api` alias and error envelope. This repository includes neither; [docs/extending.md](docs/extending.md) has a working example and the steps to replace the database executor.

## Limitations

* The SQL has never run against a live SQL Server in this repository. Query construction is unit tested, and part of the SQL runs on SQLite through a syntax adapter. `CROSS APPLY`, `OUTER APPLY`, `OFFSET ... FETCH` and `CONVERT(varchar, ..., 120)` have no SQLite equivalent, so those are covered by shape tests only. The pyodbc path is tested through a fake driver.
* The statements are T-SQL. Another engine needs the builders in `core/sql.py` and `domains/` adapted; `docs/extending.md` lists the constructs.
* The data contract is an abstraction. A real schema usually needs views to match it, and a request for the line `all` across several databases connects to the first one and reaches the others by three-part name, so the login needs read access to all of them.
* Every default is invented. Grace days, break-curve edges, benchmark thresholds, agent patterns and status ids need calibrating on your data.
* The cache, the rate limiter and the background refresh live inside one process. `python -m collections_api` runs one process. With several workers each keeps its own copies, and each starts its own refresh thread.
* The rate limit is keyed on the client address the server sees. Behind a reverse proxy that address is the proxy's, unless uvicorn is configured to trust forwarded headers.
* Authentication is one shared API key plus one bearer token. There are no per-user credentials, roles or audit trail.
* Most routes return the whole result for the window. Only the agreements list is paged, and the slip detail route has a row cap.
* `NOLOCK` reads can see rows that are still being written. That trade is deliberate for analytics and is configurable.
* "Today" is the server's local date. Timestamps are not shifted for time zones.
* The targets extractor reads one PDF layout (a header row, 16 columns, three months) by word position and refuses a document whose row totals do not add up. Other layouts need a change to `Layout` or to the extractor.
* The regression route is a small ordinary-least-squares fit in numpy for charting agent results against effort. It is not a modelling framework.

## License

MIT. See [LICENSE](LICENSE).
