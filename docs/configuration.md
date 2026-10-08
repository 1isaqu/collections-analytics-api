# Configuration

Every setting is an environment variable. A `.env` file in the working directory is read when the service starts through `python -m collections_api`; real environment variables win over it. `.env.example` lists all of them with placeholder values.

Every default below is invented. Several of them (grace days, band edges, minimum sample sizes, agent patterns) were calibrated on real data in the system this project was modelled on. Calibrate them on yours before trusting a number.

Invalid values stop the service at start-up with a message that names the variable.

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `local` | Free-text environment label. Sent to Sentry as the environment name. |
| `PRODUCT_LINES` | `auto=collections_auto,consumer=collections_consumer` | Product lines as `name=database` pairs. The name is what clients put in `{line}` path segments; `all` is reserved. The first database is the connection target for queries that span every line (they use three-part names). |
| `DB_SCHEMA` | `dbo` | Schema that holds the contract tables, used in three-part names. |
| `DB_READ_HINT` | `WITH (NOLOCK)` | Table hint added to every read. Only `WITH (NOLOCK)`, `WITH (READUNCOMMITTED)` or empty are accepted. |
| `DB_DRIVER` | `ODBC Driver 18 for SQL Server` | ODBC driver name. |
| `DB_HOST` | `localhost` | SQL Server host. |
| `DB_PORT` | `1433` | SQL Server port. `0` leaves the port out of the connection string. |
| `DB_USER` | (empty) | Database login. Required for any route that reads data. |
| `DB_PASSWORD` | (empty) | Database password. Required for any route that reads data. Never logged. |
| `DB_TRUST_SERVER_CERT` | `false` | Maps to `TrustServerCertificate`. Leave off outside a lab. |
| `DB_CONNECT_TIMEOUT` | `10` | Login timeout in seconds. |
| `DB_QUERY_TIMEOUT` | `60` | Statement timeout in seconds. A timeout surfaces as HTTP 504. |
| `DB_POOL_SIZE` | `6` | Idle connections kept per database. The pool never blocks: it opens extra connections under load and closes the surplus when they come back. |
| `DB_POOL_MAX_AGE_SECONDS` | `1800` | Idle connections older than this are closed instead of reused. |
| `CACHE_TTL_SECONDS` | `60` | TTL of the response-row cache. `0` disables caching (concurrent identical requests still share one query). |
| `CACHE_MAX_ENTRIES` | `500` | Hard ceiling on cache entries. Keys contain client text, so the store has to be bounded. |
| `BENCHMARKS_CACHE_TTL_SECONDS` | `1800` | Separate TTL for the benchmarks route, which scans months of contact events. |
| `AGENT_EFFORT_CACHE_TTL_SECONDS` | `60` | TTL of the consolidated agent-effort route. |
| `REQUIRE_API_AUTH` | `false` | Require `X-API-Key` and `Authorization: Bearer` on every protected route. |
| `API_KEY` | (empty) | Expected `X-API-Key`. Needed when auth is required. |
| `API_TOKEN` | (empty) | Expected bearer token. Needed when auth is required. |
| `RATE_LIMIT_REQUESTS` | `120` | Requests allowed per window and client address on rate-limited prefixes. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Length of the rate-limit window. |
| `RATE_LIMIT_MAX_BUCKETS` | `5000` | Distinct callers tracked before expired windows are pruned. |
| `CORS_ALLOW_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed origins. The upload route also rejects an `Origin` header that is not in this list. |
| `CORS_ALLOW_CREDENTIALS` | `false` | Allow credentialed cross-origin requests. Forced off when the list contains `*`. |
| `ENABLE_DOCS` | (empty) | `true` or `false` to force the interactive docs on or off. Empty follows `REQUIRE_API_AUTH` (docs are off when auth is required). |
| `FRONTEND_DIST_DIR` | (empty) | Directory with a built single-page app. When set, the API serves it and falls back to its `index.html`. Empty disables it. |
| `ENABLE_INDEX_ADMIN` | `false` | Enable the admin index routes. They are the only code in the service that can write to the database. |
| `EFFECTIVENESS_ETL_ENABLED` | `true` | Run the background refresh that feeds the effectiveness series routes. |
| `EFFECTIVENESS_ETL_INTERVAL_SECONDS` | `1800` | Pause between background refreshes. |
| `ENABLE_TELEMETRY_LOG` | `false` | Write NDJSON events (query timings, auth rejections) to `TELEMETRY_LOG_PATH`. |
| `TELEMETRY_LOG_PATH` | `logs/telemetry.ndjson` | Event log file. |
| `TELEMETRY_LOG_MAX_BYTES` | `5242880` | At this size the log rolls over to `<path>.1`, replacing the previous roll. |
| `SENTRY_DSN` | (empty) | Enables Sentry when set. Read from the environment only. Local variables and request bodies are never attached. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1` | Trace sampling rate, 0 to 1. |
| `SENTRY_ENABLE_LOGS` | `true` | Forward structured logs to Sentry when it is active. |
| `LOG_LEVEL` | `INFO` | Python log level used by `python -m collections_api`. |
| `STATUS_IDS` | see below | Status catalogue as `NAME=id` pairs. All seven names are required: ACTIVE, BROKEN, PAID, PENDING_EXCEPTION, REJECTED, AUTO_BROKEN, PAID_MANUAL. The ids are invented; map them to yours. |
| `GRACE_DAYS` | `3` | A slip counts as paid in grace when paid within this many days after its due date. |
| `EXCLUDED_AGENT_PATTERNS` | `system%,test%` | SQL `LIKE` patterns for agents to leave out of KPI routes. Applied to login and display name, case-insensitive. Letters, digits, `_ % . - space` only. |
| `EFFECTIVENESS_EXCLUDED_AGENT_PATTERNS` | `%system%,%test%` | Same, for the effectiveness routes. Kept separate because the two lists are allowed to differ. |
| `EFFECTIVENESS_FLOOR_DATE` | `2020-01-01` | Earliest date the effectiveness series start from (ISO date). |
| `EFFECTIVENESS_DETAIL_ROW_LIMIT` | `250` | Row cap of the slip detail route. The response says when it was applied. |
| `BREAK_CURVE_EDGES` | `7,21,45,90` | Upper edges, in days since the due date, of the break-curve bands. `7,21,45,90` gives `0-7`, `8-21`, `22-45`, `46-90`, `91+`. |
| `BENCHMARK_MIN_DAILY_ATTEMPTS` | `4` | An agent-day needs at least this many outreach attempts to enter the benchmarks. |
| `BENCHMARK_MIN_ACTIVE_DAYS` | `6` | An agent needs at least this many qualifying days to enter the benchmarks. |
| `EFFECTIVENESS_MIN_SLIPS_PER_AGENT_MONTH` | `8` | Minimum slips for an agent-month row in the by-due-date agent series. |
| `PERFORMANCE_DEFAULT_DAYS` | `30` | Window of the performance table when no dates are given: this many days up to and including today. |
| `PRODUCTIVITY_QUERY_OPTION` | (empty) | Optional `OPTION (...)` query hint appended to the productivity query, for example `OPTION (MAXDOP 2)`. Empty adds nothing. |
| `AGREEMENTS_DEFAULT_LIMIT` | `200` | Page size of the agreements-today route when `limit` is omitted. |
| `AGREEMENTS_MAX_LIMIT` | `2000` | Largest page size accepted. |
| `TARGETS_DIR` | `data/targets` | Directory where the latest targets document and per-period snapshots are stored. |
| `TARGETS_UPLOAD_MAX_BYTES` | `10485760` | Largest accepted upload. |
| `TARGET_GROUP_NAMES` | `Group A,Group B` | Group labels the PDF extractor accepts in the group column. Rows with any other group are treated as stray text. |
| `REGRESSION_MAX_POINTS` | `5000` | Largest number of points the regression route accepts. |

The default for `STATUS_IDS` is `ACTIVE=21,BROKEN=22,PAID=23,PENDING_EXCEPTION=24,REJECTED=25,AUTO_BROKEN=26,PAID_MANUAL=27`.

## Status sets

`STATUS_IDS` defines seven statuses. The queries work with sets built from them:

| Set | Default members |
|---|---|
| `approved` | ACTIVE, PAID, PAID_MANUAL |
| `broken` | BROKEN |
| `auto_broken` | AUTO_BROKEN |
| `exception` | PENDING_EXCEPTION |
| `rejected` | REJECTED |
| `generated` | approved + broken + auto-broken |
| `universe` | generated + exception |
| `rollup` | approved + exception + rejected + broken + auto-broken |
| `break_curve` | approved + broken |

Each set can be replaced with `STATUS_SET_<NAME>` holding a comma-separated list of status names, for example `STATUS_SET_GENERATED=ACTIVE,PAID`. Sets derived from others (`generated`, `universe`, `rollup`, `break_curve`) are built from the overridden values unless they are overridden themselves.

| Variable | Replaces |
|---|---|
| `STATUS_SET_APPROVED` | the `approved` set |
| `STATUS_SET_BROKEN` | the `broken` set |
| `STATUS_SET_AUTO_BROKEN` | the `auto_broken` set |
| `STATUS_SET_EXCEPTION` | the `exception` set |
| `STATUS_SET_REJECTED` | the `rejected` set |
| `STATUS_SET_GENERATED` | the `generated` set |
| `STATUS_SET_UNIVERSE` | the `universe` set |
| `STATUS_SET_ROLLUP` | the `rollup` set |
| `STATUS_SET_BREAK_CURVE` | the `break_curve` set |

The `generated` set is the base of every value KPI: an agreement generated in a period counts even if it breaks later. The `universe` set is what the aggregation subqueries pre-filter on, so broken agreements survive the filter and reach the KPIs that need them.
