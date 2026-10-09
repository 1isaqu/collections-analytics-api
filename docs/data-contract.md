# Data contract

The service reads eight tables. It never writes to them, and it never creates them: you
provide tables or views with these names and columns on your own database. Nothing else
in your schema is touched. The one exception is the optional index route, described in
[architecture.md](architecture.md), which is off by default. It creates ordinary indexes,
which SQL Server cannot build on a plain view, so it only works where the contract names
are base tables.

The statements are written in T-SQL (SQL Server). If you run another engine, the query
builders in `src/collections_api/domains/` are the place to adapt; see
[extending.md](extending.md).

## How the tables are found

Every table is addressed with a three-part name, `<database>.<schema>.<table>`.

* The schema is `DB_SCHEMA` (default `dbo`) and is the same for every table.
* The database depends on the product line. `PRODUCT_LINES` maps a line name to a database,
  for example `auto=collections_auto,consumer=collections_consumer`. Each database holds
  the same eight tables with its own data.
* A request for the line `all` either runs once per line and combines the results, or
  reads every configured database in one statement joined with `UNION ALL`. The second
  kind connects to the first database and reaches the others by three-part name, so the
  login needs read access to all of them.

If your data lives in differently named tables, create views with the names below. A
view is enough; the service only ever selects from these names.

```sql
-- Example: expose an existing installments table under the contract name.
CREATE VIEW dbo.agreements AS
SELECT  i.id               AS row_id,
        i.agreement_number AS agreement_id,
        i.wallet_id        AS portfolio_id,
        i.seq              AS installment_no,
        i.plan_size        AS plan_installments,
        i.value            AS amount,
        i.value_paid       AS paid_amount,
        i.created_at       AS issued_at,
        i.due_date         AS due_at,
        i.paid_date        AS paid_at,
        i.broken_date      AS broken_at,
        i.state            AS status_id,
        i.owner_id         AS agent_id
FROM    dbo.installments AS i;
```

## Conventions

* Amounts are plain numbers in one currency. The service never converts or formats them.
* Timestamps are `datetime`-like values without a time zone. The service compares them
  with calendar dates in the server's local day and does not shift them.
* `NULL` means "not known or not applicable". Where a column may be `NULL` it says so.
* The contract has no personal data about the people being collected from: no column for a
  name, a document number, a phone number or an address, and no response carries one.

## Tables

Each table below lists the columns the service reads. Extra columns are ignored. A test
(`tests/test_contract_coverage.py`) generates every statement the service can run, then
fails if a statement reads a column that is not listed here or if a listed column is never
read.

### `agreements`

One row per installment of an agreement. An agreement is identified by the pair
(`agreement_id`, `portfolio_id`): the same number can exist in more than one portfolio.

| Column | Type | Meaning |
|---|---|---|
| `row_id` | integer | Unique row key. Breaks ties between two writes of the same installment: the larger value is the later write. |
| `agreement_id` | integer | Agreement number. |
| `portfolio_id` | integer | Portfolio the agreement belongs to. |
| `installment_no` | integer | Installment number inside the agreement. `0` is the first installment (the down payment). |
| `plan_installments` | integer | Number of installments in the agreement's plan. |
| `amount` | decimal | Amount due for this installment. |
| `paid_amount` | decimal | Amount paid for this installment. `0` or `NULL` while unpaid. |
| `issued_at` | datetime | When this row was written. Also the date the agreement counts for in every window. |
| `due_at` | datetime | Due date of the installment. |
| `paid_at` | datetime | When it was paid. `NULL` while unpaid. |
| `broken_at` | datetime | When the agreement was recorded as broken. `NULL` otherwise. |
| `status_id` | integer | Status of the agreement, one of the ids in `STATUS_IDS`. |
| `agent_id` | integer | Agent who made the agreement. Refers to `agents.agent_id`. |

The same installment can be written more than once, for example by a retry in an upstream
system. Every aggregate keeps one row per (`agreement_id`, `portfolio_id`,
`installment_no`): the one with the latest `issued_at`, with `row_id` as the tie breaker.
The business filters (window, status) are applied before that ranking, so a later write
that falls outside the filter cannot hide an earlier write that is inside it.

### `agreement_debts`

Links an agreement to the debts it covers. One agreement can cover several debts.

| Column | Type | Meaning |
|---|---|---|
| `agreement_id` | integer | Refers to `agreements.agreement_id`. |
| `portfolio_id` | integer | Refers to `agreements.portfolio_id`. Always join on both columns. |
| `debt_id` | integer | Refers to `debts.debt_id` and `debt_attributes.debt_id`. |

### `debts`

The debts being collected.

| Column | Type | Meaning |
|---|---|---|
| `debt_id` | integer | Debt key. |
| `balance` | decimal | Current balance. Used to compare an agreement's value with what was owed. |
| `due_at` | datetime | When the debt fell due. Used for the average age of the debts behind an agreement. |

### `debt_attributes`

Extra attributes of a debt. The service reads one: the portfolio label.

| Column | Type | Meaning |
|---|---|---|
| `debt_id` | integer | Refers to `debts.debt_id`. |
| `portfolio_label` | text | Name of the portfolio the debt belongs to, for example `Portfolio A-01`. `NULL` or blank when unknown. |

An agreement takes one label from its debts: among the non-null ones, the label that sorts
first. Agreements whose debts have no label are left out of the by-portfolio charts and can
be listed with the `exceptions-without-portfolio` route.

### `contact_events`

One row per outreach attempt: a call, a message, any contact the agent logged.

| Column | Type | Meaning |
|---|---|---|
| `debt_owner_id` | integer | The account that was contacted. Effort is counted in distinct values of this column, so five attempts on one account count once. |
| `result_id` | integer | Outcome of the attempt. Refers to `contact_results.result_id`. |
| `agent_id` | integer | Agent who made the attempt. Refers to `agents.agent_id`. |
| `occurred_at` | datetime | When the attempt happened. |

### `contact_results`

The catalogue of outcomes, with the two flags the funnel is built on.

| Column | Type | Meaning |
|---|---|---|
| `result_id` | integer | Outcome key. |
| `answered` | bit | `1` when someone answered. |
| `right_party` | bit | `1` when the person reached was the right one. |

Counts follow a funnel: an attempt is counted in `attempt_count`; it is also an answer
when `answered = 1`; it is also a right-party contact (CPC) when `answered = 1` and
`right_party = 1`. So `attempt_count >= answer_count >= cpc_count` for any agent and
window.

### `agreement_statuses`

The status catalogue. The service uses it only to show a label next to a status id.

| Column | Type | Meaning |
|---|---|---|
| `status_id` | integer | Status key. Must match the ids you give in `STATUS_IDS`. |
| `label` | text | Display label. |

### `agents`

| Column | Type | Meaning |
|---|---|---|
| `agent_id` | integer | Agent key. |
| `display_name` | text | Name shown in responses. |
| `login` | text | Login. Together with `display_name` it is matched by the agent exclusion patterns and by the `team` filter. |

## Columns you will probably have but the service does not read

A fuller version of this schema would also hold `contact_events.event_id`,
`debts.portfolio_id`, `debts.contract_no`, `debts.days_overdue` and
`contact_results.label`. The service never selects them, and the coverage test does not
require them.

## Statuses

An agreement is always in exactly one of seven statuses. The names are fixed; the ids are
yours, set once in `STATUS_IDS` (the defaults are invented).

| Name | Meaning |
|---|---|
| `ACTIVE` | The agreement is in force. |
| `BROKEN` | The agreement was broken by hand. |
| `PAID` | Paid off. |
| `PENDING_EXCEPTION` | Waiting for approval of an exception. |
| `REJECTED` | A supervisor or the creditor refused it. |
| `AUTO_BROKEN` | Broken by the system, for example when a grace period ran out. |
| `PAID_MANUAL` | Paid off, and recorded by hand. |

The queries work with sets built from these names, not with single statuses. The default
sets are listed in [configuration.md](configuration.md#status-sets) together with the
variables that replace them.

## Rules the queries apply

* Every read uses the hint in `DB_READ_HINT` (default `WITH (NOLOCK)`), so a long
  analytical query does not block writers. The cost is that a read can see rows that are
  still being written.
* Agents whose `login` or `display_name` matches one of the `EXCLUDED_AGENT_PATTERNS` are
  left out in SQL, never after the fact. The effectiveness routes use their own list,
  `EFFECTIVENESS_EXCLUDED_AGENT_PATTERNS`.
* A slip is "paid in grace" when `paid_amount > 0` and `paid_at` is at most `GRACE_DAYS`
  days after `due_at`.
* A right-party contact needs both flags on the result, never `right_party` alone.
