# Data sources

This repository contains no data. It ships code that reads a database you provide. This page
lists every place a number or a row comes from, grouped into tiers by how far you can trust
and reproduce it. Last reviewed on 2026-10-09.

| Tier | What it is | In this repository |
|---|---|---|
| 0 | Invented rows written inside the test code | Yes, in `tests/` |
| 1 | A public dataset downloaded at run time | None used |
| 2 | Your own database | Never committed |

## Tier 0: invented rows in the tests

Every test that needs rows writes them inline, in the test module, with made-up ids,
amounts, dates, agent logins (`agent_01`, `agent_02`) and portfolio labels (`Portfolio A-01`).
There are no fixture files: `git ls-files tests` lists only Python modules.

Two helpers build synthetic inputs in code:

* `tests/sqlite_adapter.py` creates the eight contract tables in an in-memory SQLite
  database, rewrites the T-SQL syntax that SQLite lacks, and runs a subset of the generated
  statements against rows the test inserts. It changes syntax only, never business logic.
* `tests/targets_pdf.py` draws a one-page PDF from invented words with plain text operators,
  so the PDF path of the targets extractor runs without a binary fixture.

The values are arbitrary. None was fitted to, tuned against or checked against results from
a real system, and no test asserts a figure that came from outside this repository. CI runs
on this tier only. No test downloads anything or opens a network connection.

Two things this tier does not give you: there is no demo database for the service itself, and
no script that fills one. The SQL Server statements need a SQL Server database, and the SQLite
adapter cannot run `CROSS APPLY`, `OFFSET ... FETCH` or `CONVERT(varchar, ..., 120)`.

## Tier 1: public datasets

None. The service answers questions about collection agreements, outreach attempts and
installment payments. No open dataset has that shape, and mapping an unrelated one onto the
eight tables would produce numbers that look meaningful and are not. If you add a public
dataset later, document its source, license and the date you checked the terms here, and say
what it cannot test.

## Tier 2: your own data

Connect a SQL Server database through environment variables, as described in the
[README](../README.md#connect-your-own-data). The rules:

* Provide the eight tables or views listed in [data-contract.md](data-contract.md). The
  contract names the columns the service reads and nothing else.
* Use a login that can only read those tables. Put its host, user and password in a `.env`
  file or in the process environment. `.env` is ignored by git, and `.env.example` holds
  placeholders only.
* Run `GET /health/db` and `GET /health/ready` first. They prove that each product line
  accepts a connection, not that the columns exist or that the values are sensible. A missing
  column shows up as a 500 on the first route that reads it.
* Check the numbers for one agent and one day by hand against your source system before
  anyone relies on a dashboard.

The service does not model names, document numbers, phone numbers or addresses of the people
being collected from, so your tables need not expose them.

## Where the constants come from

Every default in `settings.py` is arbitrary and was picked so the examples run: the grace
days, the break-curve band edges, the minimum sample sizes, the agent exclusion patterns, the
status ids, the row caps, and the operational numbers (query timeout, pool size and
connection age, cache lifetimes and entry ceiling, rate limit). The operational ones are
small round numbers, not measurements of any load. None comes from a public source and none
was calibrated on data. The right values depend on how your operation records its work and
on how much traffic you serve. Calibrate them on your own data, and do not read a number the
service returns with the defaults as a finding.

The examples in the README and in [api-contract.md](api-contract.md) use invented rows.
`docs/openapi.json` and `docs/api-contract.md` are generated from the code and contain no
data at all.
