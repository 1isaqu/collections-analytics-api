# API contract

This file is generated from the running application by `scripts/export_api_contract.py`.
Do not edit it by hand: change the code or the route notes in
`src/collections_api/contract_doc.py` and run the script again. A test fails when the
file is stale. The machine-readable form of the same contract is `docs/openapi.json`.

Service: **Collections Analytics API**, version `0.1.0`.

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

## Envelope types

### Meta

| Field | Type | Always present | Description |
|---|---|---|---|
| `generated_at` | string | yes | UTC timestamp of this response, ISO 8601. |
| `total_rows` | integer | yes | Rows in `data`. An object payload counts as 1. |
| `sources` | list of string | yes | Product lines the data came from. |
| `filters` | object | yes | The filters that were applied, echoed back. |
| `run_id` | string, nullable | yes | Request id, the same as the `X-Run-Id` header. Quote it in a bug report. |
| `quality` | object | yes | Route-specific data-quality facts (staleness, truncation, cast failures). |
| `pagination` | [`Pagination`](#pagination), nullable | no | Present on paged routes and on routes with a row cap. |

### Pagination

| Field | Type | Always present | Description |
|---|---|---|---|
| `limit` | integer, nullable | no | Page size that was applied. |
| `offset` | integer, nullable | no | Offset that was applied. |
| `total` | integer, nullable | no | Rows available before paging. |
| `returned` | integer, nullable | no | Rows in this response. |
| `totals_by_line` | map of text to integer, nullable | no | Rows available per product line (only for `all`). |
| `truncated` | boolean, nullable | no | True when the row cap was reached and rows may be missing. |

### ErrorItem

| Field | Type | Always present | Description |
|---|---|---|---|
| `message` | string | yes | Human-readable message. Never contains SQL or driver text. |
| `source` | string, nullable | no | Product line or series the message is about, when there is one. |

### ErrorBody

| Field | Type | Always present | Description |
|---|---|---|---|
| `detail` | string | yes | One-line reason, the same text as `errors[0].message`. |
| `meta` | [`Meta`](#meta) | yes | Response metadata. |
| `data` | list of any | yes | Always empty. |
| `errors` | list of [`ErrorItem`](#erroritem) | yes | Problems, one item each. |

## Endpoints

| Method | Path | Summary |
|---|---|---|
| `GET` | [`/health/live`](#get-healthlive) | Liveness |
| `GET` | [`/health/ready`](#get-healthready) | Readiness |
| `GET` | [`/health/db`](#get-healthdb) | Database check, every line |
| `GET` | [`/health/db/{line}`](#get-healthdbline) | Database check, one line |
| `GET` | [`/dashboard/agreements-today/{line}`](#get-dashboardagreements-todayline) | Installments of today's approved agreements |
| `GET` | [`/dashboard/agreements-table/{line}`](#get-dashboardagreements-tableline) | Today's agreements, one row each |
| `GET` | [`/dashboard/performance-table/{line}`](#get-dashboardperformance-tableline) | Agent performance over a period |
| `GET` | [`/dashboard/portfolios/{line}`](#get-dashboardportfoliosline) | Portfolio labels |
| `GET` | [`/dashboard/productivity/{line}`](#get-dashboardproductivityline) | Per-agent productivity for a period |
| `GET` | [`/dashboard/load-status/{line}`](#get-dashboardload-statusline) | Did today's data load? |
| `GET` | [`/dashboard/agent-performance/{line}`](#get-dashboardagent-performanceline) | Agent comparison |
| `GET` | [`/dashboard/agent-effort`](#get-dashboardagent-effort) | Today's effort, one row per person |
| `GET` | [`/dashboard/benchmarks/{line}`](#get-dashboardbenchmarksline) | Internal benchmarks |
| `GET` | [`/dashboard/exceptions-by-portfolio/{line}`](#get-dashboardexceptions-by-portfolioline) | Exceptions by portfolio |
| `GET` | [`/dashboard/agreements-by-portfolio/{line}`](#get-dashboardagreements-by-portfolioline) | Generated agreements by portfolio |
| `GET` | [`/dashboard/rejected-by-portfolio/{line}`](#get-dashboardrejected-by-portfolioline) | Rejected agreements by portfolio |
| `GET` | [`/dashboard/broken-by-portfolio/{line}`](#get-dashboardbroken-by-portfolioline) | Broken agreements by portfolio |
| `GET` | [`/dashboard/first-installment-by-portfolio/{line}`](#get-dashboardfirst-installment-by-portfolioline) | First installments by portfolio |
| `GET` | [`/dashboard/exceptions-by-agent/{line}`](#get-dashboardexceptions-by-agentline) | Exceptions by agent |
| `GET` | [`/dashboard/first-installment-by-agent/{line}`](#get-dashboardfirst-installment-by-agentline) | First installments by agent |
| `GET` | [`/dashboard/first-installment-summary/{line}`](#get-dashboardfirst-installment-summaryline) | First-installment card |
| `GET` | [`/dashboard/portfolio-rollup/{line}`](#get-dashboardportfolio-rollupline) | Portfolio by status rollup |
| `GET` | [`/dashboard/received-by-portfolio/{line}`](#get-dashboardreceived-by-portfolioline) | Cash received by portfolio |
| `GET` | [`/dashboard/exceptions-without-portfolio/{line}`](#get-dashboardexceptions-without-portfolioline) | Exceptions with no portfolio label |
| `GET` | [`/dashboard/agreements-detail/{line}/{portfolio}`](#get-dashboardagreements-detaillineportfolio) | Detail of generated agreements in one portfolio |
| `GET` | [`/dashboard/agreements-detail-all/{line}`](#get-dashboardagreements-detail-allline) | Detail of generated agreements, every portfolio |
| `GET` | [`/dashboard/agreements-detail-by-agent/{line}/{agent}`](#get-dashboardagreements-detail-by-agentlineagent) | Detail of generated agreements of one agent |
| `GET` | [`/dashboard/exceptions-detail/{line}/{portfolio}`](#get-dashboardexceptions-detaillineportfolio) | Detail of agreements pending approval in one portfolio |
| `GET` | [`/dashboard/exceptions-detail-all/{line}`](#get-dashboardexceptions-detail-allline) | Detail of agreements pending approval, every portfolio |
| `GET` | [`/dashboard/exceptions-detail-by-agent/{line}/{agent}`](#get-dashboardexceptions-detail-by-agentlineagent) | Detail of agreements pending approval of one agent |
| `GET` | [`/dashboard/rejected-detail/{line}/{portfolio}`](#get-dashboardrejected-detaillineportfolio) | Detail of rejected agreements in one portfolio |
| `GET` | [`/dashboard/rejected-detail-all/{line}`](#get-dashboardrejected-detail-allline) | Detail of rejected agreements, every portfolio |
| `GET` | [`/dashboard/rejected-detail-by-agent/{line}/{agent}`](#get-dashboardrejected-detail-by-agentlineagent) | Detail of rejected agreements of one agent |
| `GET` | [`/dashboard/broken-detail/{line}/{portfolio}`](#get-dashboardbroken-detaillineportfolio) | Detail of broken agreements in one portfolio |
| `GET` | [`/dashboard/broken-detail-all/{line}`](#get-dashboardbroken-detail-allline) | Detail of broken agreements, every portfolio |
| `GET` | [`/dashboard/broken-detail-by-agent/{line}/{agent}`](#get-dashboardbroken-detail-by-agentlineagent) | Detail of broken agreements of one agent |
| `GET` | [`/dashboard/targets`](#get-dashboardtargets) | Latest targets |
| `POST` | [`/dashboard/targets/upload`](#post-dashboardtargetsupload) | Upload a targets PDF |
| `GET` | [`/effectiveness/daily-first`](#get-effectivenessdaily-first) | First installments by issue day |
| `GET` | [`/effectiveness/monthly-first`](#get-effectivenessmonthly-first) | First installments by issue month |
| `GET` | [`/effectiveness/daily-later`](#get-effectivenessdaily-later) | Later installments by issue day |
| `GET` | [`/effectiveness/monthly-later`](#get-effectivenessmonthly-later) | Later installments by issue month |
| `GET` | [`/effectiveness/daily-later-by-due`](#get-effectivenessdaily-later-by-due) | Later installments by due day |
| `GET` | [`/effectiveness/monthly-later-by-due`](#get-effectivenessmonthly-later-by-due) | Later installments by due month |
| `GET` | [`/effectiveness/monthly-agent-first`](#get-effectivenessmonthly-agent-first) | First installments by agent and issue month |
| `GET` | [`/effectiveness/monthly-agent-later`](#get-effectivenessmonthly-agent-later) | Later installments by agent and issue month |
| `GET` | [`/effectiveness/monthly-agent-later-by-due`](#get-effectivenessmonthly-agent-later-by-due) | Later installments by agent and due month, with broken counts |
| `GET` | [`/effectiveness/summary`](#get-effectivenesssummary) | Effectiveness of slips due in a period |
| `GET` | [`/effectiveness/slips`](#get-effectivenessslips) | The slips behind one KPI bucket |
| `GET` | [`/effectiveness/break-curve`](#get-effectivenessbreak-curve) | Break rate by age of the slip |
| `GET` | [`/effectiveness/portfolio-summary`](#get-effectivenessportfolio-summary) | Effectiveness of one portfolio |
| `GET` | [`/effectiveness/portfolio-ranking`](#get-effectivenessportfolio-ranking) | Effectiveness of every portfolio |
| `GET` | [`/effectiveness/generation-ranking`](#get-effectivenessgeneration-ranking) | Agreements generated per portfolio |
| `POST` | [`/regression/agents`](#post-regressionagents) | Fit regression models on agent points |
| `GET` | [`/admin/indexes/status/{line}`](#get-adminindexesstatusline) | Which recommended indexes exist |
| `POST` | [`/admin/indexes/apply/{line}`](#post-adminindexesapplyline) | Create the missing recommended indexes |

## Health

Liveness and readiness. Plain status bodies.

### `GET /health/live`

**Liveness**

Operation `health_live`. Needs credentials when authentication is on: no.

The process is running. Does not touch the database and never needs authentication.

**Parameters**

_No parameters._

**Responses** (plain status body, not an envelope)

**200**: The process is up. Body `LiveStatus`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | yes | Always `ok`. |

### `GET /health/ready`

**Readiness**

Operation `health_ready`. Needs credentials when authentication is on: yes.

State of every product line, plus how fresh the effectiveness series are.

**Parameters**

_No parameters._

**Responses** (plain status body, not an envelope)

**200**: Every product line answers. Body `ReadyStatus`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | yes | `ok`, or `degraded` (HTTP 503) when a line is down. |
| `product_lines` | map of text to string | yes | `ok` or a short reason, per line. |
| `effectiveness_refresh` | [`EffectivenessRefreshStatus`](#effectivenessrefreshstatus) | yes | State of the background refresh of the effectiveness series. |

**503**: At least one product line is down. Body `ReadyStatus`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | yes | `ok`, or `degraded` (HTTP 503) when a line is down. |
| `product_lines` | map of text to string | yes | `ok` or a short reason, per line. |
| `effectiveness_refresh` | [`EffectivenessRefreshStatus`](#effectivenessrefreshstatus) | yes | State of the background refresh of the effectiveness series. |

### `GET /health/db`

**Database check, every line**

Operation `health_db_all`. Needs credentials when authentication is on: yes.

Runs `SELECT 1` against each configured product line. 503 if any fails.

**Parameters**

_No parameters._

**Responses** (plain status body, not an envelope)

**200**: Every product line answers. Body `AllDatabasesStatus`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | yes | `ok`. |
| `product_lines` | list of string | yes | Product lines that were checked. |
| `connection` | string | yes | `connected`. |

**503**: At least one product line is down. Body: the error body.

### `GET /health/db/{line}`

**Database check, one line**

Operation `health_db_line`. Needs credentials when authentication is on: yes.

Runs `SELECT 1` against one product line. 503 if it fails.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`. `all` is not accepted. |

**Responses** (plain status body, not an envelope)

**200**: The line answers. Body `DatabaseStatus`:

| Field | Type | Always present | Description |
|---|---|---|---|
| `status` | string | yes | `ok`. |
| `product_line` | string | yes | Product line the row belongs to. |
| `connection` | string | yes | `connected`. |

**400**: A parameter is not acceptable. Body: the error body.

**503**: The line is down. Body: the error body.

## Agreements

Agreement lists, performance table, portfolio labels.

### `GET /dashboard/agreements-today/{line}`

**Installments of today's approved agreements**

Operation `agreements_today`. Needs credentials when authentication is on: yes.

One row per installment of every approved agreement issued today.

With `all`, each line is paged with the same window and the rows are concatenated;
`meta.pagination.totals_by_line` has the per-line totals.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `limit` | query | integer, nullable | no | Page size. Values above `AGREEMENTS_MAX_LIMIT` are lowered to it. |
| `offset` | query | integer, nullable | no | Rows to skip. |

**Response 200.** `data` is a list of `AgreementTodayRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `product_line` | string | yes | Product line the installment belongs to. |
| `agent_name` | string, nullable | yes | Display name of the agent who made the agreement. |
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `current_debt_value` | number | yes | Current balance of the debts the agreement covers. |
| `agreement_total_value` | number | yes | Sum of every installment of the agreement. |
| `discount_granted` | number | yes | Debt balance minus agreement total. |
| `installment_count` | integer, nullable | yes | Installments the plan has. |
| `installment_no` | integer | yes | 0 is the first installment (the down payment). |
| `issued_at` | string | yes | Timestamp the agreement row was issued. |
| `due_at` | string | yes | Due timestamp of the installment. |
| `installment_amount` | number | yes | Amount of this installment. |
| `status_label` | string, nullable | yes | Label from `agreement_statuses`. |
| `paid_at` | string, nullable | yes | Timestamp of the payment, or `null` while unpaid. |
| `payment_state` | string | yes | `PAID` when `paid_at` is set, otherwise `OPEN`. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | Always `today`. |

`meta.pagination` keys

| Key | Meaning |
|---|---|
| `limit` | Page size that was applied. |
| `offset` | Offset that was applied. |
| `total` | Rows available before paging. |
| `returned` | Rows in this response. |
| `totals_by_line` | Rows available per product line. Present only for `all`. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/agreements-table/{line}`

**Today's agreements, one row each**

Operation `agreements_table`. Needs credentials when authentication is on: yes.

Agreements issued today with their first-installment and total values.

Exceptions (agreements pending approval) come last.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `agent_name` | query | string, nullable | no | Exact agent display name. Empty means every agent. |
| `team` | query | string, nullable | no | Keep agents whose login or display name contains this text (case-insensitive). Only the part after the last colon is used. Empty or `all` means everyone. |

**Response 200.** `data` is a list of `AgreementTableRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `agreement_type` | string, nullable | yes | Status label of the agreement. |
| `is_exception` | integer | yes | 1 when the agreement is pending approval, else 0. |
| `first_installment_due` | string, nullable | yes | Due date of the first installment. |
| `first_installment_value` | number, nullable | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `later_installment_value` | number, nullable | yes | Amount of installment 1. |
| `installment_count` | integer, nullable | yes | Number of installments of the agreement. |
| `agreement_total_value` | number | yes | Sum of every installment of the agreement. |
| `issued_at` | string | yes | Timestamp the agreement row was issued. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | Always `today`. |
| `team` | The `team` text as it was sent, or `all`. |
| `agent_name` | The agent filter after trimming, or `null`. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/performance-table/{line}`

**Agent performance over a period**

Operation `performance_table`. Needs credentials when authentication is on: yes.

Effort, results and slip payment per agent.

Without dates the window is the last `PERFORMANCE_DEFAULT_DAYS` days up to and
including today. With `all`, counts are summed per agent and the ratios are
recomputed from the sums.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |
| `agent_name` | query | string, nullable | no | Exact agent display name. Empty means every agent. |

**Response 200.** `data` is a list of `PerformanceRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `attempt_count` | integer | yes | Distinct debt owners contacted in the window. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `slips_issued` | integer | yes | Installments already due in the window. |
| `slips_paid` | integer | yes | Of those, paid within the grace period. |
| `conversion_pct` | number | yes | agreement_count / cpc_count * 100. 0 when no CPC. |
| `paid_per_cpc_pct` | number | yes | slips_paid / cpc_count * 100. A different metric from conversion. |
| `agreement_value` | number | yes | Sum of the amounts of every installment of the counted agreements. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `rejected_count` | integer | yes | Number of rejected agreements. |
| `answer_count` | integer | yes | Of those, how many answered. |
| `cpc_count` | integer | yes | Right-party contacts: answered and right party. |
| `cpc_rate_pct` | number | yes | cpc_count / answer_count * 100. 0 when no answers. |
| `exception_count` | integer | yes | Number of agreements pending approval (exceptions). |
| `exception_value` | number | yes | Sum of the amounts of the exception agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `agent_name` | The agent filter after trimming, or `null`. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/portfolios/{line}`

**Portfolio labels**

Operation `portfolio_labels`. Needs credentials when authentication is on: yes.

Every distinct, non-blank portfolio label, for filter pickers.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |

**Response 200.** `data` is a list of `PortfolioLabelRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |

`meta.sources`: The product lines that were read.

## Productivity

Per-agent effort and results, load status, benchmarks.

### `GET /dashboard/productivity/{line}`

**Per-agent productivity for a period**

Operation `productivity`. Needs credentials when authentication is on: yes.

Effort, agreements, exceptions and rejections per agent, for one product line.

The window is `date_from`/`date_to`, or today when neither is given. Effort counts
distinct debt owners over the whole window. `meta.quality` reports how many numeric
values could not be read and were replaced by zero.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`. `all` is not accepted. |
| `team` | query | string, nullable | no | Keep agents whose login or display name contains this text (case-insensitive). Only the part after the last colon is used. Empty or `all` means everyone. |
| `portfolio` | query | string, nullable | no | Restrict agreement counts and values to this portfolio label. Outreach effort cannot be attributed to a portfolio, so the ratios built on it are `null`. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `ProductivityRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_login` | string | yes | Agent login, as stored in `agents.login`. |
| `agent_name` | string | yes | Agent display name, as stored in `agents.display_name`. |
| `attempt_count` | integer | yes | Distinct debt owners contacted in the window. |
| `answer_count` | integer | yes | Of those, how many answered. |
| `cpc_count` | integer | yes | Of those, how many were the right party. |
| `cpc_rate_pct` | number, nullable | yes | cpc_count / answer_count * 100, rounded up. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `agreement_count_by_contract` | integer | yes | Agreements counted per debt: one agreement covering N debts counts N. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid` | integer | yes | Of those, slips paid within the grace period. |
| `agreements_per_attempt_pct` | number, nullable | yes | agreement_count / attempt_count * 100. `null` without attempts. |
| `agreement_value` | number | yes | Sum of the amounts of every installment of the counted agreements. |
| `avg_ticket` | number | yes | Average agreement value. |
| `avg_installments` | number | yes | Average number of installments of the agreement plans. |
| `avg_balance_ratio_pct` | number | yes | Agreement value over original debt balance. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `first_installment_received` | number | yes | Amount paid on the first installments. |
| `exception_count` | integer | yes | Number of agreements pending approval (exceptions). |
| `exception_value` | number | yes | Sum of the amounts of the exception agreements. |
| `exception_first_installment_value` | number | yes | First-installment amount of the exception agreements. |
| `rejected_count` | integer | yes | Distinct rejected debts. |
| `rejected_value` | number | yes | Sum of the amounts of the rejected agreements. |
| `rejected_first_installment_value` | number | yes | First-installment amount of the rejected agreements. |
| `avg_debt_age_days` | number | yes | Average days from the earliest covered debt's due date to the window start. |
| `hours_worked` | number | yes | Proxy: first to last generated agreement of each day, summed. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `team` | The `team` text as it was sent, or `all`. |
| `portfolio` | The portfolio label filter, or `null`. |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `rows` | Rows returned by the query, before the `team` filter. |
| `numeric_cast_failures` | Values that could not be read as numbers and became 0. |
| `portfolio_partial` | `true` when a portfolio filter nulled the effort ratios. |
| `elapsed_ms` | Time spent on the request, in milliseconds. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/load-status/{line}`

**Did today's data load?**

Operation `load_status`. Needs credentials when authentication is on: yes.

Totals of today's productivity rows per line, to confirm the load before reading KPIs.

`meta.quality.status` is `ok`, or `partial` when a line failed or a value could not
be read as a number. The route fails (500) only when no line produced anything.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `team` | query | string, nullable | no | Keep agents whose login or display name contains this text (case-insensitive). Only the part after the last colon is used. Empty or `all` means everyone. |

**Response 200.** `data` is a list of `LoadStatusRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `product_line` | string | yes | Line name, or `all` for the summed row. |
| `agent_count` | integer | yes | Agents that have a row for the line today. |
| `attempt_count` | integer | yes | Distinct debt owners contacted. |
| `cpc_count` | integer | yes | Of those, how many were the right party. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `agreement_value` | number | yes | Sum of the amounts of every installment of the counted agreements. |
| `exception_count` | integer | yes | Number of agreements pending approval (exceptions). |
| `exception_value` | number | yes | Sum of the amounts of the exception agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | Always `today`. |
| `team` | The `team` text as it was sent, or `all`. |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `status` | `ok`, or `partial` when a line failed or a value was unreadable. |
| `lines_requested` | Product lines the request covered. |
| `lines_ok` | Product lines that produced a row. |
| `numeric_cast_failures` | Values that could not be read as numbers and became 0. |

`meta.sources`: The product lines that were read.

`errors`: One item per failed line (`source` is the line), and one for unreadable values.

### `GET /dashboard/agent-performance/{line}`

**Agent comparison**

Operation `agent_performance`. Needs credentials when authentication is on: yes.

One row per agent and product line, with effort, results and slip effectiveness.

The same login in two lines gives two rows; clients that want one row per person
group on `agent_login`. Effort here counts distinct (day, debt owner) pairs, so a
long window counts a debtor once for every day they were contacted.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgentPerformanceRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `product_line` | string | yes | Product line the row belongs to. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agent_login` | string, nullable | yes | Agent login, as stored in `agents.login`. |
| `attempt_count` | integer | yes | Distinct (day, debt owner) pairs contacted. |
| `answer_count` | integer | yes | Of those, how many answered. |
| `cpc_count` | integer | yes | Of those, how many were the right party. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid` | integer | yes | Of those, slips paid within the grace period. |
| `agreement_value` | number | yes | Sum of the amounts of every installment of the counted agreements. |
| `avg_ticket` | number | yes | Average agreement value. |
| `avg_installments` | number | yes | Average number of installments of the agreement plans. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `first_installment_received` | number | yes | Amount paid on the first installments. |
| `slip_effectiveness_pct` | number, nullable | yes | slips_paid / slips_issued * 100. |
| `cpc_rate_pct` | integer, nullable | yes | cpc_count / answer_count * 100, rounded up. `null` without answers. |
| `avg_balance_ratio_pct` | number | yes | Average of agreement value over the balance of the debts it covers. |
| `exception_count` | integer | yes | Number of agreements pending approval (exceptions). |
| `exception_value` | number | yes | Sum of the amounts of the exception agreements. |
| `exception_first_installment_value` | number | yes | First-installment amount of the exception agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/agent-effort`

**Today's effort, one row per person**

Operation `agent_effort`. Needs credentials when authentication is on: yes.

Outreach effort of today summed over every product line, with the per-line split.

Lines are queried in parallel and the payload is cached for
`AGENT_EFFORT_CACHE_TTL_SECONDS`. `meta.quality.cache_age_seconds` is 0 for a fresh
computation.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `force_refresh` | query | boolean | no | Skip the cached payload and query every line again. Default `False`. |

**Response 200.** `data` is a list of `AgentEffortRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_key` | string | yes | Lower-cased login, or display name when there is none. Identifies one person. |
| `agent_login` | string | yes | Agent login, as stored in `agents.login`. |
| `agent_name` | string | yes | Agent display name, as stored in `agents.display_name`. |
| `by_line` | map of text to [`EffortCounts`](#effortcounts) | yes | Counts per product line. |
| `total` | [`EffortCounts`](#effortcounts) | yes | Counts summed over every line. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | Always `all`. |
| `period` | Always `today`. |
| `force_refresh` | Whether the cached payload was bypassed. |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `cache_age_seconds` | 0 for a fresh computation, else the age of the cache. |

`meta.sources`: Every configured product line.

### `GET /dashboard/benchmarks/{line}`

**Internal benchmarks**

Operation `benchmarks`. Needs credentials when authentication is on: yes.

Quartiles of per-agent averages over a look-back window.

Agent-days with fewer than `BENCHMARK_MIN_DAILY_ATTEMPTS` attempts, and agents with
fewer than `BENCHMARK_MIN_ACTIVE_DAYS` qualifying days, are left out so that tiny
samples cannot move a quartile. The scan is wide, so the result is cached for
`BENCHMARKS_CACHE_TTL_SECONDS`.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`. `all` is not accepted. |
| `lookback_months` | query | integer | no | How many months before today to look at. Default `3`. Minimum 1. Maximum 12. |

**Response 200.** `data` is one `BenchmarkData` object.

| Field | Type | Always present | Description |
|---|---|---|---|
| `cpc_rate_pct` | [`Quartiles`](#quartiles) | yes | Per-agent cpc_count / answer_count * 100, summarized. |
| `conversion_pct` | [`Quartiles`](#quartiles) | yes | Per-agent agreement_count / cpc_count * 100, summarized. |
| `cash_effectiveness_pct` | [`Quartiles`](#quartiles) | yes | Per-agent first-installment cash received over first-installment value, summarized. |
| `exception_pct` | [`Quartiles`](#quartiles) | yes | Per-agent exception value over total agreement value, summarized. |
| `agent_count` | integer | yes | Agents that passed the minimum-sample rules. |
| `lookback_months` | integer | yes | Look-back window that was applied, in months. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `lookback_months` | The look-back that was applied. |

`meta.sources`: The product lines that were read.

## Charts

Aggregates behind the charts and cards.

### `GET /dashboard/exceptions-by-portfolio/{line}`

**Exceptions by portfolio**

Operation `exceptions_by_portfolio`. Needs credentials when authentication is on: yes.

Agreements pending approval, counted by portfolio label, with their first installments.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `ExceptionsByPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `exception_count` | integer | yes | Number of agreements pending approval (exceptions). |
| `exception_value` | number | yes | Sum of the amounts of the exception agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/agreements-by-portfolio/{line}`

**Generated agreements by portfolio**

Operation `agreements_by_portfolio`. Needs credentials when authentication is on: yes.

Generated agreements (approved, broken and auto-broken), by portfolio label.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementsByPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `agreement_value` | number | yes | Sum of the amounts of every installment of the counted agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/rejected-by-portfolio/{line}`

**Rejected agreements by portfolio**

Operation `rejected_by_portfolio`. Needs credentials when authentication is on: yes.

Agreements a supervisor or the creditor rejected, by portfolio label.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `RejectedByPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `rejected_count` | integer | yes | Number of rejected agreements. |
| `rejected_value` | number | yes | Sum of the amounts of the rejected agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/broken-by-portfolio/{line}`

**Broken agreements by portfolio**

Operation `broken_by_portfolio`. Needs credentials when authentication is on: yes.

Agreements broken by hand, by portfolio label.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `BrokenByPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `broken_count` | integer | yes | Number of broken agreements. |
| `broken_value` | number | yes | Sum of the amounts of the broken agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/first-installment-by-portfolio/{line}`

**First installments by portfolio**

Operation `first_installment_by_portfolio`. Needs credentials when authentication is on: yes.

First-installment value of generated agreements, by portfolio label, largest first.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `FirstInstallmentByPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/exceptions-by-agent/{line}`

**Exceptions by agent**

Operation `exceptions_by_agent`. Needs credentials when authentication is on: yes.

Agreements pending approval, counted per agent.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `team` | query | string, nullable | no | Keep agents whose login or display name contains this text (case-insensitive). Only the part after the last colon is used. Empty or `all` means everyone. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `ExceptionsByAgentRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `exception_count` | integer | yes | Number of agreements pending approval (exceptions). |
| `exception_value` | number | yes | Sum of the amounts of the exception agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `team` | The `team` text as it was sent, or `all`. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/first-installment-by-agent/{line}`

**First installments by agent**

Operation `first_installment_by_agent`. Needs credentials when authentication is on: yes.

First-installment value of generated agreements per agent, largest first.

Only first installments (`installment_no = 0`) of the latest write of each installment are counted, so an agreement is one row however many installments it has. Excluded agents are filtered in SQL. With `all`, per-line results are summed per group.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `team` | query | string, nullable | no | Keep agents whose login or display name contains this text (case-insensitive). Only the part after the last colon is used. Empty or `all` means everyone. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `FirstInstallmentByAgentRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `first_installment_count` | integer | yes | Agreements with a first installment in the window. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `team` | The `team` text as it was sent, or `all`. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/first-installment-summary/{line}`

**First-installment card**

Operation `first_installment_summary`. Needs credentials when authentication is on: yes.

Total first-installment value and agreement count of generated agreements.

One row. Both fields are `null` when nothing matched.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `team` | query | string, nullable | no | Keep agents whose login or display name contains this text (case-insensitive). Only the part after the last colon is used. Empty or `all` means everyone. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `FirstInstallmentSummaryRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `first_installment_value` | number, nullable | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `agreement_count` | integer, nullable | yes | Number of agreements counted. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `team` | The `team` text as it was sent, or `all`. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/portfolio-rollup/{line}`

**Portfolio by status rollup**

Operation `portfolio_rollup`. Needs credentials when authentication is on: yes.

One scan grouped by portfolio label and status id.

A client can rebuild the by-portfolio charts by slicing on `status_id`
(see `docs/configuration.md` for the status sets). `agreement_count` is exact per
status; adding it across statuses can count one agreement twice.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `PortfolioRollupRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `status_id` | integer | yes | Agreement status id, as configured in `STATUS_IDS`. |
| `agreement_count` | integer | yes | Exact per status. Summing over several statuses can count an agreement twice. |
| `total_value` | number | yes | Sum of the installment amounts of the agreements in this status. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/received-by-portfolio/{line}`

**Cash received by portfolio**

Operation `received_by_portfolio`. Needs credentials when authentication is on: yes.

Cash paid inside the window, by payment date, per portfolio.

This is the "actual" side of a target-versus-actual view. The window applies to
`paid_at`, not to the date the agreement was issued.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `ReceivedByPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `received_value` | number | yes | Cash paid inside the window, by payment date. |
| `first_installment_received` | number | yes | Amount paid on the first installments. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/exceptions-without-portfolio/{line}`

**Exceptions with no portfolio label**

Operation `exceptions_without_portfolio`. Needs credentials when authentication is on: yes.

Exception agreements that none of the by-portfolio charts can show.

Lists the agreements whose debts carry no portfolio label, so the by-portfolio
totals can be reconciled with the all-agreements total.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `ExceptionWithoutPortfolioRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment of the agreement, each installment once. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

## Details

Agreement lists behind the charts.

### `GET /dashboard/agreements-detail/{line}/{portfolio}`

**Detail of generated agreements in one portfolio**

Operation `agreements_detail`. Needs credentials when authentication is on: yes.

The generated agreements whose debts carry the given portfolio label.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `portfolio` | path | string | yes | Exact portfolio label. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `portfolio` | The label as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/agreements-detail-all/{line}`

**Detail of generated agreements, every portfolio**

Operation `agreements_detail_all`. Needs credentials when authentication is on: yes.

All generated agreements in the window, each with its portfolio label (`null` when none is known).

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailAllRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |
| `portfolio_label` | string, nullable | yes | `null` when no debt carries a label. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/agreements-detail-by-agent/{line}/{agent}`

**Detail of generated agreements of one agent**

Operation `agreements_detail_by_agent`. Needs credentials when authentication is on: yes.

The generated agreements one agent made in the window.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `agent` | path | string | yes | Exact agent display name. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `agent` | The agent name as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/exceptions-detail/{line}/{portfolio}`

**Detail of agreements pending approval in one portfolio**

Operation `exceptions_detail`. Needs credentials when authentication is on: yes.

The agreements pending approval whose debts carry the given portfolio label.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `portfolio` | path | string | yes | Exact portfolio label. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `portfolio` | The label as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/exceptions-detail-all/{line}`

**Detail of agreements pending approval, every portfolio**

Operation `exceptions_detail_all`. Needs credentials when authentication is on: yes.

All agreements pending approval in the window, each with its portfolio label (`null` when none is known).

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailAllRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |
| `portfolio_label` | string, nullable | yes | `null` when no debt carries a label. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/exceptions-detail-by-agent/{line}/{agent}`

**Detail of agreements pending approval of one agent**

Operation `exceptions_detail_by_agent`. Needs credentials when authentication is on: yes.

The agreements pending approval one agent made in the window.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `agent` | path | string | yes | Exact agent display name. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `agent` | The agent name as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/rejected-detail/{line}/{portfolio}`

**Detail of rejected agreements in one portfolio**

Operation `rejected_detail`. Needs credentials when authentication is on: yes.

The rejected agreements whose debts carry the given portfolio label.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `portfolio` | path | string | yes | Exact portfolio label. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `portfolio` | The label as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/rejected-detail-all/{line}`

**Detail of rejected agreements, every portfolio**

Operation `rejected_detail_all`. Needs credentials when authentication is on: yes.

All rejected agreements in the window, each with its portfolio label (`null` when none is known).

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailAllRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |
| `portfolio_label` | string, nullable | yes | `null` when no debt carries a label. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/rejected-detail-by-agent/{line}/{agent}`

**Detail of rejected agreements of one agent**

Operation `rejected_detail_by_agent`. Needs credentials when authentication is on: yes.

The rejected agreements one agent made in the window.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `agent` | path | string | yes | Exact agent display name. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `agent` | The agent name as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/broken-detail/{line}/{portfolio}`

**Detail of broken agreements in one portfolio**

Operation `broken_detail`. Needs credentials when authentication is on: yes.

The broken agreements whose debts carry the given portfolio label.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `portfolio` | path | string | yes | Exact portfolio label. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `portfolio` | The label as sent. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/broken-detail-all/{line}`

**Detail of broken agreements, every portfolio**

Operation `broken_detail_all`. Needs credentials when authentication is on: yes.

All broken agreements in the window, each with its portfolio label (`null` when none is known).

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailAllRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |
| `portfolio_label` | string, nullable | yes | `null` when no debt carries a label. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /dashboard/broken-detail-by-agent/{line}/{agent}`

**Detail of broken agreements of one agent**

Operation `broken_detail_by_agent`. Needs credentials when authentication is on: yes.

The broken agreements one agent made in the window.

One row per agreement, largest first installment first. `total_value` and `installment_count` cover every installment of the agreement, each counted once. No debtor identity is returned.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. |
| `agent` | path | string | yes | Exact agent display name. May contain slashes. |
| `date_from` | query | string (date), nullable | no | First day of the window, inclusive (`YYYY-MM-DD`). Give both dates or neither. |
| `date_to` | query | string (date), nullable | no | Last day of the window, inclusive (`YYYY-MM-DD`). |

**Response 200.** `data` is a list of `AgreementDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `first_installment_value` | number | yes | First-installment amount (installment 0); a total on aggregate routes. |
| `total_value` | number | yes | Sum of every installment, once each. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | YYYY-MM-DD. |
| `due_date` | string | yes | YYYY-MM-DD, of the first installment. |
| `installment_count` | integer | yes | Number of installments of the agreement. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `agent` | The agent name as sent. |

`meta.sources`: The product lines that were read.

## Targets

Quarterly targets: read the latest, upload a PDF.

### `GET /dashboard/targets`

**Latest targets**

Operation `targets`. Needs credentials when authentication is on: yes.

The targets of the latest uploaded document, one row per portfolio.

Before any upload the answer is 200 with an empty `data` list and one entry in
`errors`, so a page can show an empty state without treating it as a failure.

**Parameters**

_No parameters._

**Response 200.** `data` is a list of `TargetRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `office` | string, nullable | yes | Office name as written in the document. |
| `portfolio` | string | yes | Portfolio name. |
| `group` | string, nullable | yes | One of the configured group names. |
| `headcount` | number, nullable | yes | Agents the portfolio is staffed with, as written in the document. |
| `cash_target` | map of text to number | yes | Cash target per month, keyed `YYYYMM`. |
| `recovery_count_target` | map of text to integer | yes | Recovery count target per month, keyed `YYYYMM`. |
| `recovery_value_target` | map of text to number | yes | Recovery value target per month, keyed `YYYYMM`. |
| `total_target` | map of text to number | yes | Cash target plus recovery value target. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `period` | Quarter label such as `3Q26`. Absent before the first upload. |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `validated` | Whether the document passed both checksums. |
| `months` | The three months the targets cover, as `YYYYMM`. |
| `extracted_at` | When the document was extracted, UTC ISO 8601. |

`meta.sources`: The name of the uploaded file. Empty before the first upload.

`errors`: Before the first upload: one item with a `message` and no `source`.

### `POST /dashboard/targets/upload`

**Upload a targets PDF**

Operation `targets_upload`. Needs credentials when authentication is on: yes.

Extract the table from a PDF and make it the latest targets document.

The extraction is checked twice: each row's total must equal cash plus recovery
value, and the rows must add up to the document's TOTAL row. If either check fails
nothing is stored and the response is 422 with the reason. The size limit is
`TARGETS_UPLOAD_MAX_BYTES`. Requires the `pdf` extra on the server.

**Parameters**

_No parameters._

**Request body** (`multipart/form-data`, `Body_targets_upload`)

| Field | Type | Always present | Description |
|---|---|---|---|
| `file` | string | yes | The targets PDF (`multipart/form-data`). |

**Response 200.** `data` is one `TargetUploadResult` object.

| Field | Type | Always present | Description |
|---|---|---|---|
| `period` | string | yes | Quarter label such as `3Q26`. |
| `row_count` | integer | yes | Rows stored. |

`meta.sources`: The name of the uploaded file.

**Further statuses**

- **403**: The request origin is not allowed.
- **413**: The upload is larger than the limit.

## Effectiveness

Are issued payment slips being paid in time?

### `GET /effectiveness/daily-first`

**First installments by issue day**

Operation `effectiveness_daily_first`. Needs credentials when authentication is on: yes.

First installments by issue day.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByIssueDayRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `issue_date` | string | yes | YYYY-MM-DD. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/monthly-first`

**First installments by issue month**

Operation `effectiveness_monthly_first`. Needs credentials when authentication is on: yes.

First installments by issue month.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByMonthRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `year` | integer | yes | Calendar year. |
| `month` | integer | yes | Calendar month, 1 to 12. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/daily-later`

**Later installments by issue day**

Operation `effectiveness_daily_later`. Needs credentials when authentication is on: yes.

Later installments by issue day.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByIssueDayRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `issue_date` | string | yes | YYYY-MM-DD. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/monthly-later`

**Later installments by issue month**

Operation `effectiveness_monthly_later`. Needs credentials when authentication is on: yes.

Later installments by issue month.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByMonthRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `year` | integer | yes | Calendar year. |
| `month` | integer | yes | Calendar month, 1 to 12. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/daily-later-by-due`

**Later installments by due day**

Operation `effectiveness_daily_later_by_due`. Needs credentials when authentication is on: yes.

Later installments by due day.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByDueDayRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `due_date` | string | yes | YYYY-MM-DD. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/monthly-later-by-due`

**Later installments by due month**

Operation `effectiveness_monthly_later_by_due`. Needs credentials when authentication is on: yes.

Later installments by due month.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByMonthRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `year` | integer | yes | Calendar year. |
| `month` | integer | yes | Calendar month, 1 to 12. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/monthly-agent-first`

**First installments by agent and issue month**

Operation `effectiveness_monthly_agent_first`. Needs credentials when authentication is on: yes.

First installments by agent and issue month.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByAgentMonthRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_login` | string, nullable | yes | Agent login, as stored in `agents.login`. |
| `year` | integer | yes | Calendar year. |
| `month` | integer | yes | Calendar month, 1 to 12. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/monthly-agent-later`

**Later installments by agent and issue month**

Operation `effectiveness_monthly_agent_later`. Needs credentials when authentication is on: yes.

Later installments by agent and issue month.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByAgentMonthRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_login` | string, nullable | yes | Agent login, as stored in `agents.login`. |
| `year` | integer | yes | Calendar year. |
| `month` | integer | yes | Calendar month, 1 to 12. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/monthly-agent-later-by-due`

**Later installments by agent and due month, with broken counts**

Operation `effectiveness_monthly_agent_later_by_due`. Needs credentials when authentication is on: yes.

Later installments by agent and due month, with broken counts.

A slip is one installment row. It counts as paid in grace when a positive amount was paid no later than `GRACE_DAYS` days after its due date. The rows are refreshed in the background every `EFFECTIVENESS_ETL_INTERVAL_SECONDS`; the first request after a start can answer 503 until the first refresh finishes. `meta.quality.fetched_at` says when the rows were read and `meta.quality.stale` is true when the last refresh of this series failed.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `SeriesByAgentMonthBrokenRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agent_login` | string, nullable | yes | Agent login, as stored in `agents.login`. |
| `year` | integer | yes | Calendar year. |
| `month` | integer | yes | Calendar month, 1 to 12. |
| `slips_issued` | integer | yes | Slips (installments) counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | integer | yes | Whole percent of slips paid in grace, rounded half up. |
| `broken_count` | integer | yes | Slips whose agreement is in a broken status. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `series` | The series key, for example `daily-first`. |
| `line` | The variant that was served: a configured name or `all`. |
| `from` | First date the series covers (`EFFECTIVENESS_FLOOR_DATE`). |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `fetched_at` | When the rows were read from the database, UTC ISO 8601. |
| `stale` | `true` when the last refresh of this series failed and the rows are older. |

`meta.sources`: The product lines that were read.

`errors`: When the last refresh failed: one item with `source` `<series>:<line>`.

**Further statuses**

- **503**: The first refresh has not finished.

### `GET /effectiveness/summary`

**Effectiveness of slips due in a period**

Operation `effectiveness_summary`. Needs credentials when authentication is on: yes.

KPI block, one point per due day, and the best and worst day.

The buckets partition the slips: to mature (due today or later, unpaid), in grace
(overdue, grace still running), overdue and unpaid (grace over) and paid in grace. A
slip paid after its grace period counts as overdue and unpaid: the figure measures
on-time payment, not eventual payment. Uses the effectiveness agent exclusion list.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `date_from` | query | string (date) | yes | First due date to include (`YYYY-MM-DD`). |
| `date_to` | query | string (date) | yes | Last due date to include (`YYYY-MM-DD`). |
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |
| `installment_type` | query | `first` or `later` | no | `first` is the down payment (installment 0). `later` is every other one. Default `first`. |
| `portfolio_id` | query | integer, nullable | no | Only slips of this portfolio id. |

**Response 200.** `data` is one `EffectivenessSummaryData` object.

| Field | Type | Always present | Description |
|---|---|---|---|
| `kpis` | [`EffectivenessKpis`](#effectivenesskpis) | yes | The KPI block for the whole window. |
| `daily` | list of [`EffectivenessDay`](#effectivenessday) | yes | One point per due day. |
| `best_day` | [`EffectivenessDay`](#effectivenessday), nullable | yes | Day with the highest effectiveness. |
| `worst_day` | [`EffectivenessDay`](#effectivenessday), nullable | yes | Day with the lowest effectiveness. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `installment_type` | `first` or `later`. |
| `portfolio_id` | The portfolio id filter, or `null`. |

`meta.sources`: The product lines that were read.

### `GET /effectiveness/slips`

**The slips behind one KPI bucket**

Operation `effectiveness_slips`. Needs credentials when authentication is on: yes.

Slips due in the period that fall in one bucket, largest first.

The list is capped at `EFFECTIVENESS_DETAIL_ROW_LIMIT` rows. When the list reaches the
cap, `meta.quality.truncated` and `meta.pagination.truncated` are true and rows may be
missing. A list with exactly that many rows is flagged too, because the query cannot
tell it from a cut one, so read the flag as "may be truncated".

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `kind` | query | `to-mature` or `in-grace` or `overdue-unpaid` or `broken` or `paid-in-grace` | yes | Which KPI bucket to list. |
| `date_from` | query | string (date) | yes | First due date to include (`YYYY-MM-DD`). |
| `date_to` | query | string (date) | yes | Last due date to include (`YYYY-MM-DD`). |
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |
| `installment_type` | query | `first` or `later` | no | `first` is the down payment (installment 0). `later` is every other one. Default `first`. |

**Response 200.** `data` is a list of `SlipDetailRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `agreement_id` | integer | yes | Agreement number. Unique only together with `portfolio_id`. |
| `portfolio_id` | integer | yes | Portfolio id of the agreement. |
| `slip_amount` | number | yes | Amount of the slip (the installment). |
| `total_value` | number | yes | Sum of every installment of the agreement, each installment once. |
| `agent_name` | string, nullable | yes | Agent display name, as stored in `agents.display_name`. |
| `agreement_date` | string | yes | Date the agreement was issued, `YYYY-MM-DD`. |
| `due_date` | string | yes | Due date, `YYYY-MM-DD`. |
| `installment_count` | integer | yes | Number of installments of the agreement. |
| `paid_installment_count` | integer | yes | Installments of the agreement with a payment. |
| `broken_date` | string, nullable | yes | Date the break was recorded, `YYYY-MM-DD`, or `null`. |
| `portfolio_label` | string, nullable | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `original_debt_value` | number, nullable | yes | Sum of the balances of the debts the agreement covers. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `installment_type` | `first` or `later`. |
| `kind` | The bucket that was listed. |

`meta.quality` keys

| Key | Meaning |
|---|---|
| `truncated` | `true` when the row cap was reached and rows may be missing. |

`meta.pagination` keys

| Key | Meaning |
|---|---|
| `limit` | The row cap (`EFFECTIVENESS_DETAIL_ROW_LIMIT`). |
| `truncated` | Same as `quality.truncated`. |

`meta.sources`: The product lines that were read.

### `GET /effectiveness/break-curve`

**Break rate by age of the slip**

Operation `effectiveness_break_curve`. Needs credentials when authentication is on: yes.

Share of first-installment slips already broken, by days since the due date.

Bands come from `BREAK_CURVE_EDGES`. Age runs from the due date to today; the date a
break was recorded is not used, because batch processes can pile recorded breaks onto
a few days.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `date_from` | query | string (date) | yes | First due date to include (`YYYY-MM-DD`). |
| `date_to` | query | string (date) | yes | Last due date to include (`YYYY-MM-DD`). |
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `BreakCurveRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `band` | string | yes | Days since the due date, for example `0-7 days` or `91+ days`. |
| `slips_total` | integer | yes | Slips counted. |
| `slips_broken` | integer | yes | Slips whose agreement is in a broken status. |
| `break_rate_pct` | number, nullable | yes | slips_broken / slips_total * 100. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

### `GET /effectiveness/portfolio-summary`

**Effectiveness of one portfolio**

Operation `effectiveness_portfolio_summary`. Needs credentials when authentication is on: yes.

Slip counts and cash for the slips due in the period whose debts carry the label.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `portfolio` | query | string | yes | Exact portfolio label. |
| `date_from` | query | string (date) | yes | First due date to include (`YYYY-MM-DD`). |
| `date_to` | query | string (date) | yes | Last due date to include (`YYYY-MM-DD`). |
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |
| `installment_type` | query | `first` or `later` | no | `first` is the down payment (installment 0). `later` is every other one. Default `first`. |

**Response 200.** `data` is a list of `PortfolioEffectivenessSummaryRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `slips_total` | integer | yes | Slips counted. |
| `slips_paid_in_grace` | integer, nullable | yes | Slips paid, for a positive amount, within the grace period. |
| `amount_maturing` | number | yes | Sum of the slip amounts. |
| `amount_received` | number | yes | Sum paid on the slips, whenever it was paid. |
| `effectiveness_pct` | number, nullable | yes | amount_received / amount_maturing * 100. `null` when nothing matures. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `installment_type` | `first` or `later`. |
| `portfolio` | The label as sent. |

`meta.sources`: The product lines that were read.

### `GET /effectiveness/portfolio-ranking`

**Effectiveness of every portfolio**

Operation `effectiveness_portfolio_ranking`. Needs credentials when authentication is on: yes.

One call for every portfolio label, largest maturing amount first.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `date_from` | query | string (date) | yes | First due date to include (`YYYY-MM-DD`). |
| `date_to` | query | string (date) | yes | Last due date to include (`YYYY-MM-DD`). |
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |
| `installment_type` | query | `first` or `later` | no | `first` is the down payment (installment 0). `later` is every other one. Default `first`. |

**Response 200.** `data` is a list of `PortfolioEffectivenessRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `slips_total` | integer | yes | Slips counted. |
| `slips_paid_in_grace` | integer, nullable | yes | Slips paid, for a positive amount, within the grace period. |
| `amount_maturing` | number | yes | Sum of the slip amounts. |
| `amount_received` | number | yes | Sum paid on the slips, whenever it was paid. |
| `effectiveness_pct` | number, nullable | yes | amount_received / amount_maturing * 100. `null` when nothing matures. |
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |
| `installment_type` | `first` or `later`. |

`meta.sources`: The product lines that were read.

### `GET /effectiveness/generation-ranking`

**Agreements generated per portfolio**

Operation `effectiveness_generation_ranking`. Needs credentials when authentication is on: yes.

Generated agreements per portfolio by issue date, largest value first.

Unlike the slip views this window is on the issue date: a generated agreement counts
whatever happens to it afterwards. It uses the standard agent exclusion list.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `date_from` | query | string (date) | yes | First issue date to include (`YYYY-MM-DD`). |
| `date_to` | query | string (date) | yes | Last issue date to include (`YYYY-MM-DD`). |
| `line` | query | string | no | Product line name as configured in `PRODUCT_LINES`, or `all` for every line. With `all`, measures are summed across lines. Default `all`. |

**Response 200.** `data` is a list of `GenerationRankingRow`.

| Field | Type | Always present | Description |
|---|---|---|---|
| `portfolio_label` | string | yes | Portfolio label, from `debt_attributes.portfolio_label`. |
| `agreement_count` | integer | yes | Number of agreements counted. |
| `agreement_value` | number | yes | Sum of the amounts of every installment of the counted agreements. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `period` | `today`, or `YYYY-MM-DD/YYYY-MM-DD` with the first and last day, both inclusive. |

`meta.sources`: The product lines that were read.

## Regression

Regression of agent results on effort.

### `POST /regression/agents`

**Fit regression models on agent points**

Operation `regression_agents`. Needs credentials when authentication is on: yes.

Clean the points and fit five least-squares models.

Points with a missing field, repeated ids and interquartile-range outliers are
dropped and counted in `cleaning`. Models are fitted on a seeded 70/30 split, so the
same input always gives the same output. Fewer than three clean points gives an
empty `models` list. At most `REGRESSION_MAX_POINTS` points are accepted.

The handler is synchronous on purpose: the fit is CPU work, and FastAPI runs a
plain `def` route in its thread pool instead of blocking the event loop.

**Parameters**

_No parameters._

**Request body** (`application/json`, `RegressionRequest`)

| Field | Type | Always present | Description |
|---|---|---|---|
| `points` | list of [`RegressionPoint`](#regressionpoint) | yes | One point per agent. A point with a missing field is dropped and counted. |

**Response 200.** `data` is one `RegressionData` object.

| Field | Type | Always present | Description |
|---|---|---|---|
| `cleaning` | [`CleaningSummaryModel`](#cleaningsummarymodel) | yes | What the cleaning step removed. |
| `models` | list of [`RegressionModelFit`](#regressionmodelfit) | yes | Empty when fewer than 3 clean points. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `point_count` | Points received, before cleaning. |

`meta.sources`: Always `["request"]`: nothing is read from a database.

**Further statuses**

- **413**: Too many points or a body above the limit.

## Admin

Index administration. Disabled unless enabled in settings.

### `GET /admin/indexes/status/{line}`

**Which recommended indexes exist**

Operation `admin_indexes_status`. Needs credentials when authentication is on: yes.

Read-only. For each recommended index, whether it exists in the line's database.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`. `all` is not accepted. |

**Response 200.** `data` is one `IndexStatusData` object.

| Field | Type | Always present | Description |
|---|---|---|---|
| `product_line` | string | yes | Product line the row belongs to. |
| `total_recommended` | integer | yes | Indexes the service recommends. |
| `existing` | integer | yes | Of those, how many exist. |
| `missing` | integer | yes | Of those, how many are missing. |
| `indexes` | list of [`IndexInfo`](#indexinfo) | yes | One entry per recommended index. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |

`meta.sources`: The product lines that were read.

### `POST /admin/indexes/apply/{line}`

**Create the missing recommended indexes**

Operation `admin_indexes_apply`. Needs credentials when authentication is on: yes.

Create the recommended indexes that are missing. Existing ones are skipped.

A failed statement is reported on its own step and does not stop the others. With
`dry_run=false` the service must run with `REQUIRE_API_AUTH=true`.

**Parameters**

| Name | In | Type | Required | Description |
|---|---|---|---|---|
| `line` | path | string | yes | Product line name as configured in `PRODUCT_LINES`. `all` is not accepted. |
| `dry_run` | query | boolean | no | Return the statements without running them. Default true. Default `True`. |
| `online` | query | boolean | no | Use `ONLINE = ON`. Needs a SQL Server edition that supports it. Default `False`. |
| `update_statistics` | query | boolean | no | Refresh statistics (full scan) on the tables that gained an index. Default `False`. |

**Response 200.** `data` is one `IndexApplyData` object.

| Field | Type | Always present | Description |
|---|---|---|---|
| `product_line` | string | yes | Product line the row belongs to. |
| `dry_run` | boolean | yes | Whether statements were only listed. |
| `online` | boolean | yes | Whether `ONLINE = ON` was used. |
| `update_statistics` | boolean | yes | Whether statistics were refreshed. |
| `elapsed_ms` | number | yes | Time the whole operation took, in milliseconds. |
| `indexes` | list of [`IndexStep`](#indexstep) | yes | One entry per recommended index. |
| `statistics` | list of [`StatisticsStep`](#statisticsstep) | yes | One entry per statistics update. |

`meta.filters` keys

| Key | Meaning |
|---|---|
| `line` | The product line that was queried: a configured name or `all`. |
| `dry_run` | Whether statements were only listed. |
| `online` | Whether `ONLINE = ON` was requested. |
| `update_statistics` | Whether statistics were refreshed. |

`meta.sources`: The product lines that were read.

## Nested types

Types that appear inside the rows above.

### Body_targets_upload

| Field | Type | Always present | Description |
|---|---|---|---|
| `file` | string | yes | The targets PDF (`multipart/form-data`). |

### CleaningSummaryModel

| Field | Type | Always present | Description |
|---|---|---|---|
| `raw_count` | integer | yes | Points received. |
| `clean_count` | integer | yes | Points left after cleaning. |
| `removed_nulls` | integer | yes | Points dropped because a field was missing or not a number. |
| `removed_duplicates` | integer | yes | Points dropped because their id repeated. |
| `removed_outliers` | integer | yes | Points dropped as interquartile-range outliers. |

### CoefficientModel

| Field | Type | Always present | Description |
|---|---|---|---|
| `name` | string | yes | Driver name. |
| `value` | number | yes | Fitted coefficient. |
| `se` | number | yes | Standard error from the training rows. |

### EffectivenessDay

| Field | Type | Always present | Description |
|---|---|---|---|
| `due_date` | string | yes | Due date, `YYYY-MM-DD`. |
| `slips_total` | integer | yes | Slips counted. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `paid_in_grace_pct` | number, nullable | yes | slips_paid_in_grace / slips_total * 100. |
| `amount_maturing` | number | yes | Sum of the slip amounts. |
| `amount_received` | number | yes | Sum paid on the slips, whenever it was paid. |
| `effectiveness_pct` | number, nullable | yes | amount_received / amount_maturing * 100. `null` when nothing matures. |

### EffectivenessKpis

| Field | Type | Always present | Description |
|---|---|---|---|
| `slips_total` | integer | yes | Slips counted. |
| `agreements_total` | integer | yes | Distinct agreements among the slips. |
| `slips_to_mature` | integer | yes | Due today or later and not yet paid. |
| `slips_in_grace` | integer | yes | Overdue, still inside the grace period. |
| `slips_overdue_unpaid` | integer | yes | Overdue with the grace period over. |
| `slips_paid_in_grace` | integer | yes | Slips paid, for a positive amount, within the grace period. |
| `slips_broken` | integer | yes | Slips whose agreement is in a broken status. |
| `paid_in_grace_pct` | number, nullable | yes | slips_paid_in_grace / slips_total * 100. |
| `amount_maturing` | number | yes | Sum of the slip amounts. |
| `amount_received` | number | yes | Sum paid on the slips, whenever it was paid. |
| `effectiveness_pct` | number, nullable | yes | amount_received / amount_maturing * 100. |

### EffectivenessRefreshStatus

| Field | Type | Always present | Description |
|---|---|---|---|
| `enabled` | boolean | yes | Whether the background refresh is switched on. |
| `last_run_age_seconds` | number, nullable | yes | Seconds since a refresh last produced rows. `null` before the first one. |
| `interval_seconds` | number | yes | Pause between refreshes, in seconds. |

### EffortCounts

| Field | Type | Always present | Description |
|---|---|---|---|
| `attempt_count` | integer | yes | Distinct debt owners contacted. |
| `answer_count` | integer | yes | Of those, how many answered. |
| `cpc_count` | integer | yes | Of those, how many were the right party. |

### IndexInfo

| Field | Type | Always present | Description |
|---|---|---|---|
| `name` | string | yes | Index name. |
| `table` | string | yes | Table the object belongs to. |
| `key_columns` | list of string | yes | Key columns of the index, in order. |
| `include_columns` | list of string | yes | Included (non-key) columns. |
| `purpose` | string | yes | Why the index is recommended. |
| `exists` | boolean | yes | Whether the index already exists. |

### IndexStep

| Field | Type | Always present | Description |
|---|---|---|---|
| `name` | string | yes | Index name. |
| `table` | string | yes | Table the object belongs to. |
| `sql` | string | yes | The statement. |
| `action` | string | yes | `created`, `would_create`, `skipped_existing` or `failed`. |
| `error` | string, nullable | yes | Why the statement failed, or `null`. |

### Quartiles

| Field | Type | Always present | Description |
|---|---|---|---|
| `q1` | number, nullable | yes | First quartile. |
| `median` | number, nullable | yes | Median. |
| `q3` | number, nullable | yes | Third quartile. |
| `top10_mean` | number, nullable | yes | Mean of the best 10% (at least one agent). |
| `mean` | number, nullable | yes | Mean. |

### RegressionModelFit

| Field | Type | Always present | Description |
|---|---|---|---|
| `id` | string | yes | `simple`, `loglog`, `poly2`, `volume` or `full`. |
| `label` | string | yes | Short model name. |
| `description` | string | yes | The model written as a formula. |
| `drawable` | boolean | yes | True when the model has one driver and can be plotted. |
| `r2_train` | number | yes | R squared on the training rows. |
| `r2_test` | number | yes | R squared on the held-out rows. |
| `adj_r2` | number | yes | R squared on the training rows, adjusted for the number of drivers. |
| `train_n` | integer | yes | Rows used to fit the model. |
| `test_n` | integer | yes | Rows held out for the test score. |
| `intercept` | number | yes | Fitted intercept. |
| `intercept_se` | number | yes | Standard error of the intercept. |
| `coefficients` | list of [`CoefficientModel`](#coefficientmodel) | yes | Fitted coefficients, one per driver. |

### RegressionPoint

| Field | Type | Always present | Description |
|---|---|---|---|
| `id` | string or integer, nullable | no | Opaque id. Repeated ids are dropped. |
| `efficiency` | number, nullable | no | Efficiency of the agent: the main driver of the simple models. |
| `value` | number, nullable | no | The outcome the models explain. |
| `attempts` | number, nullable | no | Outreach attempts of the agent. |
| `contacts` | number, nullable | no | Answered contacts of the agent. |
| `cpc` | number, nullable | no | Right-party contacts of the agent. |
| `conversion` | number, nullable | no | Conversion percentage of the agent. |

### RegressionRequest

| Field | Type | Always present | Description |
|---|---|---|---|
| `points` | list of [`RegressionPoint`](#regressionpoint) | yes | One point per agent. A point with a missing field is dropped and counted. |

### StatisticsStep

| Field | Type | Always present | Description |
|---|---|---|---|
| `target` | string | yes | Table whose statistics are updated. |
| `sql` | string | yes | The statement. |
| `action` | string | yes | `updated`, `would_update` or `failed`. |
| `error` | string, nullable | yes | Why the statement failed, or `null`. |
