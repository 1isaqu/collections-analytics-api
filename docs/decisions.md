# Decisions

Each entry says what was decided and why. They are the choices that shape the code; the
smaller ones are in the module docstrings.

## The service only reads

Every route is a `SELECT`. The one exception, creating recommended indexes, is a separate
router that answers 403 unless `ENABLE_INDEX_ADMIN` is set, runs as a dry run unless asked
otherwise, and needs a real connection pool. Everything else works with a database login
that can read the eight tables of the data contract and nothing more.

## Business rules live in SQL fragments

The rules that decide a number are written once, in `core/sql.py`: the de-duplication of
installments, the half-open date window, the status sets and the agent exclusion. Every
builder that counts agreements takes them from there, and handlers only resolve parameters,
call a builder and wrap the rows. The card, the chart and the drill-down list are still
separate builders, so they can disagree wherever their shapes differ. The portfolio label
has a single pick rule for that reason (next entry), and a test puts one agreement under
two labels to check that the chart and the lists still agree. The places where two routes
count differently on purpose are listed under "Counting grains are explicit".

## Aggregates are separate CTEs

A statement that joins contact events to agreements multiplies rows, because both are
many-sided per agent. The per-agent builders compute each aggregate in its own CTE and join
the results on the agent. An agent with agreements and no contact events still appears,
with zero effort. The cost is longer statements. In exchange, a join added later cannot
change a count.

## Portfolio is resolved with `CROSS APPLY ... TOP 1`

An agreement can cover several debts, each carrying a portfolio label. A join would turn
one agreement into several rows and inflate every sum. `CROSS APPLY (SELECT TOP 1 ...)`
returns one non-null label per agreement and keeps the cardinality. When the debts carry
different labels, the label that sorts first wins: the `ORDER BY` makes the pick
repeatable, because `TOP 1` without one may return a different row from run to run. The
match is on the pair (`agreement_id`, `portfolio_id`) because an agreement number can exist
in more than one portfolio. Agreements with no label drop out of the by-portfolio charts
(`CROSS APPLY`), and a separate route lists them so totals can be reconciled.

The agreement lists behind the charts (`domains/charts/details.py`) get the same label from
a `ROW_NUMBER()` subquery and a join instead of `APPLY`, because the apply form re-ran the
lookup per row on long windows. The ranking uses the same order, and the list for one
portfolio filters on the label after the pick, so it holds the agreements the chart counts
under that portfolio and no others.

Where the portfolio is a filter rather than a column, as in the optional portfolio
parameter of the productivity route, an `EXISTS` subquery on the same pair does the job.
That filter matches an agreement under every label its debts carry. For an agreement
whose debts span two labels, the productivity route filtered by either label counts it,
while the by-portfolio charts count it only under the first.

## Installments are de-duplicated, and filters run first

The same installment can be written more than once by an upstream retry. Aggregates keep one
row per (`agreement_id`, `portfolio_id`, `installment_no`), the latest by `issued_at` with
`row_id` as a tie breaker. The window and status filters go inside the derived table, before
`ROW_NUMBER()`. Filtering after the ranking lets a later write that falls outside the
filter win the rank and hide the valid earlier row.

## "Generated" is the base of every value measure

Value measures (agreement value, first installment, average ticket, counts) use the
`GENERATED` status set: approved, broken and auto-broken agreements. An agreement made in a
window counts there even if it breaks later, because breaking is an outcome that happens
afterwards and is reported separately. The pre-filter inside the aggregate subqueries is the
`UNIVERSE` set, generated plus exceptions, so broken agreements survive the filter and
reach the measures that need them. Statuses are configuration: you give the ids once and
can replace any derived set.

## Excluded agents are filtered in SQL

Test accounts and system users are removed by `NOT LIKE` clauses on login and display name
inside the statement, never after the rows are fetched. Post-filtering would leave their
rows inside sums, averages and `TOP` lists. The patterns are validated when settings load,
since they are written into the SQL text. The effectiveness routes use a separate list on
purpose: merging the two lists would change effectiveness numbers, and that is a business
decision rather than a refactor.

## Dates are inlined as literals

`Period` renders its bounds as `CAST('YYYYMMDD' AS DATE)`. A `DECLARE`d local variable hides
its value from the optimizer at compile time, which can turn an index seek on the window
columns into a scan. The values come from Python `date` objects, never from client text, so
inlining them is safe.

## Counting grains are explicit

`agreement_count` counts agreements. `agreement_count_by_contract` counts an agreement once
for every debt it covers, and exists for one headline card. Per-agent rows, rankings,
average ticket and conversion use the first. Dividing value by contracts would understate
the ticket, so the second is documented as never a denominator.

Two routes also count the first grain differently. The productivity route counts one per
(agent, agreement, portfolio, status) group, and the performance table counts
`DISTINCT agreement_id` per agent. The numbers differ only when an agreement number shows up
under more than one portfolio id, or under more than one status, inside the window. Both
are agreements, and neither is a contract count.

## Conversion and slip effectiveness are different measures

`conversion_pct` is agreements over right-party contacts. The share of slips paid in grace
is a different question and has its own names (`paid_in_grace_pct`,
`slip_effectiveness_pct`, `paid_per_cpc_pct`). Sharing one name between them makes two
different numbers hard to tell apart, so a field name is tied to one formula, and the
glossary gives the formula of each.

## Right-party contact needs both flags

A contact result is a right-party contact when `answered` and `right_party` are both set.
Using `right_party` alone lets through results that are not answers, and the funnel stops
being monotonic (attempts, then answers, then CPC, then agreements). With both flags a test
can assert the ordering.

## No identifiers of debtors in the contract

No column, query or response field carries a name, document number, phone number or
address. Effort is counted on an opaque `debt_owner_id`. This is enforced where it is cheap
to enforce: a test asserts that no response model has a field with such a name.

## Explicit context instead of module singletons

Routes receive an `AppContext` through `Depends`. There are no module-level pools, caches or
settings to monkeypatch. A test builds an app around a fake executor in a few lines, and
generating the OpenAPI document builds the same app with no database. The price is one extra
parameter on each handler.

## The database is a protocol

`QueryExecutor` has one method. The SQL Server implementation imports `pyodbc` lazily and
sits behind a small `Driver` protocol, so the pool and the timeout mapping are tested with a
fake driver. A deployment on another engine replaces the executor and adapts the builders
(see [extending.md](extending.md)).

## The pool never blocks

`DB_POOL_SIZE` caps idle connections, not concurrency. A caller that finds no idle
connection opens one; a connection returned to a full pool is closed. Under bursts this
trades extra connections for no queueing inside the service. A connection that raised is
closed rather than returned, and an idle one older than `DB_POOL_MAX_AGE_SECONDS` or failing
`SELECT 1` is replaced.

## The cache is single-flight and bounded

Identical concurrent requests share one query. Followers wait a little longer than the
statement timeout, so a leader that times out reports its own 504 before its followers give
up, and one hung query cannot drain the worker threads. Keys include client text, so the
store has a hard ceiling with expired-first eviction. A TTL of zero disables storage but
keeps the single-flight behaviour.

## Slow series are precomputed, with state per key

The effectiveness series scan the full installment history. They refresh on a timer and are
served from memory. Freshness and the last error are kept per series and variant rather than
in one shared stamp, because a shared stamp lets a series that keeps failing look fresh.
Until a key has loaded once, its route answers 503 instead of an empty list that would read
as "no data".

## Response models document, they do not validate

Handlers return the rows of the statement as they are. Models are attached with
`responses=` so they appear in the OpenAPI document, but nothing validates a response at run
time: validation would cost time on large lists and turn a harmless extra column into a
failed request. To keep the models honest, tests compare each model's fields with the
columns of the statement that feeds it, and compare each route's `meta` keys with the notes
in the contract.

## The contract documents are generated and tested

`docs/openapi.json` and `docs/api-contract.md` come from the running app. A test fails when
either file differs from a fresh export, when a route lacks notes, when a model field lacks
a description, and when the export would need a driver, a database or an `.env` file. A
front end can be built against these two files before any database exists, which only works
if they cannot go stale.

## One error shape

A failed request answers the usual envelope with an empty `data` and one item in `errors`,
plus a top-level `detail` string for simple clients. Messages never contain SQL, driver text
or stack traces. Unhandled errors return a fixed message and the run id; the cause goes to
the log and to Sentry if it is configured.

## Calibrated numbers are configuration

Grace days, break-curve edges, minimum sample sizes for benchmarks, row caps, agent patterns
and the status ids are settings with invented defaults. The right values depend on
the operation and on how its data is recorded, and no data ships with this repository.
Calibrate them on yours before you trust a number.
