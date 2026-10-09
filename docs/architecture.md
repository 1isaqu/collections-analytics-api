# Architecture

The service is a FastAPI application that turns a relational schema (see
[data-contract.md](data-contract.md)) into JSON for dashboards. It only reads. All business
logic that changes a number lives in SQL builders; the HTTP layer resolves parameters, runs
a builder, and wraps the rows.

```
 HTTP request
      |
  CORS  ->  body-size ceiling  ->  /api prefix alias  ->  security middleware
                                                          (run id, rate limit, auth)
      |
  router handler
      |   resolve line and window         core/sql.py   QueryConfig, Period, Query
      |   build a Query                   domains/*     one builder per route family
      |   look in the TTL cache           core/cache.py single flight per key
      v
  QueryExecutor (protocol)  ->  ConnectionPool  ->  pyodbc  ->  SQL Server
      |
  rows -> envelope { meta, data, errors } -> JSON
```

Nothing connects to a database until the first query runs. Building the app, importing the
package and generating the OpenAPI document need neither a driver nor a server.

## Layers

| Layer | Path | Responsibility |
|---|---|---|
| Settings | `settings.py` | One table declares every environment variable with its parser and default. Builds the status catalogue and the derived status sets. Invalid values stop start-up with a message that names the variable. |
| Core | `core/` | Pieces with no HTTP in them: SQL building blocks (`sql.py`), connection pool and executor (`database.py`), TTL cache (`cache.py`), envelope builder, pagination, rate limiter, telemetry, recommended-index DDL. |
| Domains | `domains/` | Query builders and the small services around them, one package per area: agreements, charts, productivity, effectiveness, targets, regression. They take a `QueryConfig` and return a `Query`; they never touch HTTP. |
| API | `api/` | Application context, dependencies (auth, run id), middleware, error handlers, response models, routers, optional single-page-app hosting. |
| Entry | `main.py`, `__main__.py` | `create_app(settings, executor=..., extra_routers=...)` and the `collections-api` command. |
| Documents | `contract_doc.py`, `scripts/` | Generate `docs/openapi.json` and `docs/api-contract.md` from the app. |

## Application context

Routes do not import module-level objects. `build_context` creates one `AppContext`
(settings, query configuration, executor, caches, rate limiter, telemetry, the effectiveness
store, the agent-effort service, the targets store) and stores it on `app.state`. A route
receives it through `Depends`. A test passes a fake executor to `create_app` and gets a
fully wired app with no database; the OpenAPI export does the same.

## Building queries

Statements are text built from validated pieces. A `Query` carries its SQL and its bind
parameters together, so the `?` markers and the parameter tuple cannot drift apart. Client
text only ever travels as a bind parameter. The values that are interpolated (database and
schema names, agent patterns, status ids, date literals) are validated when `Settings` is
built or come from typed Python values.

`QueryConfig` holds what a builder needs to know about the deployment and offers the shared
fragments:

* `table(database, name, alias)` renders `<database>.<schema>.<table> AS <alias> <hint>`.
* `dedup(...)` renders `agreements` as a derived table that keeps one row per installment
  (latest write wins) with the business filters applied before the ranking.
* `portfolio_label_lookup(...)` resolves an agreement's portfolio label with
  `CROSS APPLY (SELECT TOP 1 ... ORDER BY label)` so an agreement that covers several debts
  stays one row and always gets the same label.
* `excluded_agents()` renders the agent exclusion as `NOT LIKE` clauses.
* `paid_in_grace()` renders the grace-period expression.
* `across_lines(line, build)` builds one statement per product line in scope and joins them
  with `UNION ALL`.

`Period` is a half-open date window. Its dates are written into the SQL as
`CAST('YYYYMMDD' AS DATE)` literals rather than `DECLARE`d variables, which keeps the
values visible to the optimizer.

Aggregates that mix two many-sided tables (contact events and agreements) are built as
separate CTEs and joined on the agent, so a join can never multiply rows. For the line
`all`, a per-line base select is unioned and re-aggregated in an outer select, so a measure
is summed once.

## Running queries

`QueryExecutor` is a one-method protocol. The production implementation,
`SqlServerExecutor`, takes a connection from `ConnectionPool`, sets the statement timeout,
runs the query and converts values to JSON-safe types.

* `pyodbc` is imported on the first connection. A missing package is a 500 that says to
  install the `sqlserver` extra.
* The pool keeps up to `DB_POOL_SIZE` idle connections per database. It never blocks:
  under load it opens extra connections and closes the surplus on return. An idle
  connection older than `DB_POOL_MAX_AGE_SECONDS`, or one that fails `SELECT 1`, is
  replaced. A connection that raised is closed, not returned.
* A driver timeout (SQLSTATE class `HYT`) becomes HTTP 504. Any other driver error
  is a 500 with a generic message. The detail goes to the log, never into the response.
* Connections use autocommit. The workload only reads, and without it an implicit
  transaction would stay open while the connection idles in the pool.

## Caching

`TtlCache` stores rows for `CACHE_TTL_SECONDS`. It is single-flight: when several requests
miss on the same key at once, one runs the query and the rest wait for its result. Followers
wait at most the statement timeout plus a margin, so a hung query fails one request and does
not drain the thread pool. Keys contain client text, so the store has a hard entry ceiling;
at the ceiling expired entries go first, then the one that expires soonest. Two routes with
their own cost have their own cache or TTL: the benchmarks route and the consolidated
agent-effort route.

Cached rows are shared objects, so a handler must not modify them. The one that adds a
field builds a new dict for each row.

## Effectiveness series

The nine effectiveness series scan the whole installment history and take seconds each, so
`EffectivenessStore` refreshes them on a timer in a background thread and routes answer from
memory. State is kept per series and per variant (`<series>:<line or all>`): each key has
the time its rows were fetched and the error of its last attempt. A shared "last run" stamp
would let a series that keeps failing look fresh. A route answers 503 until its key has been
loaded once, and sets `meta.quality.stale` when the last refresh of that key failed.

## Security

* Authentication is off by default. With `REQUIRE_API_AUTH=true`, every route except
  `GET /health/live` needs both an `X-API-Key` header and an `Authorization: Bearer` token.
  Both are compared in constant time. Settings refuse to start with the switch on and a
  credential missing.
* The interactive docs follow the same switch: off when authentication is required, unless
  `ENABLE_DOCS` says otherwise.
* Routes under `/dashboard/`, `/effectiveness/`, `/regression/` and `/admin/` are rate
  limited per client address with a sliding window. The limiter runs before the credential
  check, so requests with bad credentials count too. The bucket table is pruned so it cannot
  grow without bound.
* Routers passed as `extra_routers` get the same authentication and rate limit, by their
  first path segment.
* Request bodies are bounded per prefix before they are buffered: the targets upload and
  the regression route have their own ceilings, and a body that does not declare its length
  is counted as it arrives.
* The only code that writes to the database is the index route under `/admin/`. It answers
  403 unless `ENABLE_INDEX_ADMIN` is on, defaults to a dry run, and needs a real connection
  pool.
* Error bodies never contain SQL, driver text or stack traces. Unhandled errors return a
  generic message with the run id; the details go to the log and, if configured, Sentry.
* A client-supplied run id is echoed back only if it is a short run of safe characters.

## Observability

`Telemetry` is one object with two optional sinks, and every call is a no-op when its sink
is off. The NDJSON event log (`ENABLE_TELEMETRY_LOG`) records query starts, ends and
timeouts, auth rejections, rate-limit hits, one line per response and unhandled
exceptions, and rolls over at `TELEMETRY_LOG_MAX_BYTES`. Sentry (`SENTRY_DSN`, environment
only) receives exceptions, structured logs, and metrics for query duration, pool acquire
time and cache hits and misses. It is initialised without local variables or request
bodies, and a malformed DSN is logged instead of aborting start-up. Every response carries
`X-Run-Id`, the same value as `meta.run_id`.

## Generated documents

`contract_doc.export_openapi()` builds the app from `Settings.defaults()` and returns its
OpenAPI document. `docs/openapi.json` is that document. `docs/api-contract.md` is rendered
from it, plus per-route notes on the `meta.filters`, `meta.quality` and `meta.pagination`
keys that a response model cannot express. Both are regenerated with the scripts in
`scripts/`. Tests fail when either file is stale, when a route has no notes, and when the
keys a route really returns differ from its notes.

## What is tested, and what is not

* Query construction is tested without a database: column lists, parameter order, the
  structure of the `UNION ALL` and CTE shapes, the exclusion and dedup fragments.
* A subset of the generated SQL (dedup, per-agent aggregates, rejected-by-debt counting, the
  effectiveness summary) also runs against in-memory SQLite after a syntax-only adapter in
  `tests/sqlite_adapter.py`, with invented rows written in the tests.
  `CROSS`/`OUTER APPLY`, `OFFSET ... FETCH` and `CONVERT(varchar, ..., 120)` have no SQLite
  equivalent, so those are covered by shape tests only.
* The connection path runs against a fake driver: connection string, timeout mapping, pool
  reuse and expiry.
* Nothing in the suite connects to SQL Server. The SQL has not been run against a live
  server in this repository.
