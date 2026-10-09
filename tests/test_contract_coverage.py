"""The documents match the code: models vs columns, route notes vs responses, SQL vs contract.

Three links are checked here, each one a place where a rename in one layer used to be
able to pass unnoticed.

1. The columns a query selects are the fields its response model documents. Responses
   are not validated at run time, so nothing else would notice a drift.
2. The keys a route really puts in ``meta`` are the keys ``contract_doc.ROUTE_NOTES``
   says it does.
3. The tables and columns the generated SQL reads are the ones ``docs/data-contract.md``
   lists, and every listed column is read somewhere.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from collections_api import contract_doc
from collections_api.api import models
from collections_api.api.routers.charts import AGENT_CHARTS, PORTFOLIO_CHARTS
from collections_api.api.routers.effectiveness import SERIES_MODELS
from collections_api.domains.agreements import queries as agreement_queries
from collections_api.domains.charts import details as detail_queries
from collections_api.domains.charts import queries as chart_queries
from collections_api.domains.effectiveness import queries as effectiveness_queries
from collections_api.domains.productivity import queries as productivity_queries
from collections_api.main import create_app
from collections_api.settings import Settings
from tests import targets_pdf
from tests.conftest import TODAY
from tests.fakes import RecordingExecutor
from tests.test_admin import ENABLE, admin_client
from tests.test_regression_models import points

DATA_CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "data-contract.md"


def fields(model) -> list[str]:
    return list(model.model_fields)


# ---------------------------------------------------------------- 1. columns and models

COLUMN_MODELS = [
    ("agreements_table", agreement_queries.AGREEMENTS_TABLE_COLUMNS, models.AgreementTableRow),
    ("performance_table", agreement_queries.PERFORMANCE_TABLE_COLUMNS, models.PerformanceRow),
    ("first_installment_summary", chart_queries.SUMMARY_COLUMNS, models.FirstInstallmentSummaryRow),
    ("portfolio_labels", chart_queries.PORTFOLIO_LABEL_COLUMNS, models.PortfolioLabelRow),
    ("portfolio_rollup", chart_queries.ROLLUP_COLUMNS, models.PortfolioRollupRow),
    ("received_by_portfolio", chart_queries.RECEIVED_COLUMNS, models.ReceivedByPortfolioRow),
    (
        "exceptions_without_portfolio",
        chart_queries.WITHOUT_PORTFOLIO_COLUMNS,
        models.ExceptionWithoutPortfolioRow,
    ),
    ("agreement_detail", detail_queries.DETAIL_COLUMNS, models.AgreementDetailRow),
    ("agreement_detail_all", detail_queries.DETAIL_ALL_COLUMNS, models.AgreementDetailAllRow),
    ("effectiveness_kpis", effectiveness_queries.SUMMARY_COLUMNS, models.EffectivenessKpis),
    ("effectiveness_day", effectiveness_queries.DAILY_COLUMNS, models.EffectivenessDay),
    ("slip_detail", effectiveness_queries.DETAIL_COLUMNS, models.SlipDetailRow),
    ("break_curve", effectiveness_queries.BREAK_CURVE_COLUMNS, models.BreakCurveRow),
    (
        "portfolio_effectiveness_summary",
        effectiveness_queries.PORTFOLIO_SUMMARY_COLUMNS,
        models.PortfolioEffectivenessSummaryRow,
    ),
    (
        "generation_ranking",
        effectiveness_queries.GENERATION_RANKING_COLUMNS,
        models.GenerationRankingRow,
    ),
    ("productivity", productivity_queries.PRODUCTIVITY_COLUMNS, models.ProductivityRow),
    (
        "agent_performance",
        productivity_queries.AGENT_PERFORMANCE_COLUMNS,
        models.AgentPerformanceRow,
    ),
]


@pytest.mark.parametrize(
    ("name", "columns", "model"), COLUMN_MODELS, ids=[c[0] for c in COLUMN_MODELS]
)
def test_a_query_selects_exactly_the_fields_its_model_documents(name, columns, model):
    assert list(columns) == fields(model)


def test_the_route_adds_the_product_line_to_the_agreements_today_rows():
    selected = list(agreement_queries.AGREEMENTS_TODAY_COLUMNS)
    assert ["product_line", *selected] == fields(models.AgreementTodayRow)


def test_the_ranking_model_is_the_summary_model_plus_the_label():
    ranking = set(effectiveness_queries.PORTFOLIO_RANKING_COLUMNS)
    assert ranking == set(fields(models.PortfolioEffectivenessRow))
    assert ranking - set(effectiveness_queries.PORTFOLIO_SUMMARY_COLUMNS) == {"portfolio_label"}


@pytest.mark.parametrize("kind", sorted(PORTFOLIO_CHARTS))
def test_each_by_portfolio_chart_selects_the_fields_of_its_model(kind):
    model = PORTFOLIO_CHARTS[kind][0]
    spec = chart_queries.PORTFOLIO_KINDS[kind]
    assert fields(model) == ["portfolio_label", spec.count, spec.value]


@pytest.mark.parametrize("kind", sorted(AGENT_CHARTS))
def test_each_by_agent_chart_selects_the_fields_of_its_model(kind):
    model = AGENT_CHARTS[kind][0]
    spec = chart_queries.AGENT_KINDS[kind]
    assert fields(model) == ["agent_name", spec.count, spec.value]


def test_every_chart_kind_has_a_route_model_and_the_other_way_round():
    assert set(PORTFOLIO_CHARTS) == set(chart_queries.PORTFOLIO_KINDS)
    assert set(AGENT_CHARTS) == set(chart_queries.AGENT_KINDS)


@pytest.mark.parametrize("key", sorted(effectiveness_queries.SERIES))
def test_each_effectiveness_series_selects_the_fields_of_its_model(key):
    assert list(effectiveness_queries.SERIES[key].columns) == fields(SERIES_MODELS[key])


def test_every_effectiveness_series_has_a_model():
    assert set(SERIES_MODELS) == set(effectiveness_queries.SERIES)


def test_the_agent_effort_row_is_built_from_the_effort_columns_per_line():
    effort = set(productivity_queries.AGENT_EFFORT_COLUMNS)
    counts = set(fields(models.EffortCounts))
    assert {"agent_login", "agent_name"} <= effort
    assert effort - {"agent_login", "agent_name"} == counts
    assert {"by_line", "total"} <= set(fields(models.AgentEffortRow))


# ---------------------------------------------------------------- 2. route notes and meta


def operations_of(schema, *, method):
    for path, item in schema["paths"].items():
        if method in item:
            yield path, item[method]


def request_for(path, operation, line):
    """Fill in the path and the required query parameters of an operation."""
    query = {}
    for parameter in operation.get("parameters", ()):
        name, spec = parameter["name"], parameter["schema"]
        if parameter["in"] == "path":
            path = path.replace("{" + name + "}", line if name == "line" else "x")
        elif parameter.get("required"):
            if "enum" in spec:
                query[name] = spec["enum"][0]
            elif spec.get("format") == "date":
                query[name] = "2026-03-10"
            else:
                query[name] = "x"
    return path, query


@pytest.fixture(scope="module")
def swept(openapi_schema, tmp_path_factory):
    """Call every data route once per line, and the routes that need a body or a pool."""
    executor = RecordingExecutor()
    settings = Settings.from_env(
        {
            "EFFECTIVENESS_ETL_ENABLED": "false",
            "TARGETS_DIR": str(tmp_path_factory.mktemp("targets")),
        }
    )
    app = create_app(settings, executor=executor, today=lambda: TODAY)
    app.state.ctx.effectiveness.refresh()
    client = TestClient(app)
    bodies: dict[str, list[dict]] = defaultdict(list)

    def keep(operation_id, response):
        if response.status_code == 200:
            bodies[operation_id].append(response.json())

    pdf = tmp_path_factory.mktemp("pdf") / "targets.pdf"
    targets_pdf.write_pdf(pdf, targets_pdf.document_lines())
    keep(
        "targets_upload",
        client.post(
            "/dashboard/targets/upload",
            files={"file": ("targets.pdf", pdf.read_bytes(), "application/pdf")},
        ),
    )
    keep("regression_agents", client.post("/regression/agents", json={"points": points()}))
    for path, operation in operations_of(openapi_schema, method="get"):
        operation_id = operation["operationId"]
        if operation_id.startswith(("health_", "admin_")):
            continue
        for line in ("auto", "all"):
            url, query = request_for(path, operation, line)
            keep(operation_id, client.get(url, params=query))
    return bodies, executor


def merged(bodies, section):
    """Union of the keys found in ``meta.<section>`` over every call of a route."""
    keys: set[str] = set()
    for body in bodies:
        keys |= set(body["meta"].get(section, {}))
    return keys


def test_every_data_route_answered_at_least_once(openapi_schema, swept):
    bodies, _ = swept
    expected = {
        operation["operationId"]
        for method in ("get", "post")
        for _, operation in operations_of(openapi_schema, method=method)
        if not operation["operationId"].startswith(("health_", "admin_"))
    }
    assert expected - set(bodies) == set(), "routes the sweep could not call"


def test_the_notes_name_the_keys_each_route_puts_in_meta(swept):
    bodies, _ = swept
    problems = []
    for operation_id, answers in sorted(bodies.items()):
        notes = contract_doc.ROUTE_NOTES[operation_id]
        for section in ("filters", "quality", "pagination"):
            documented = {key for key, _ in getattr(notes, section)}
            if merged(answers, section) != documented:
                problems.append(
                    f"{operation_id}.{section}: sent {sorted(merged(answers, section))},"
                    f" documented {sorted(documented)}"
                )
    assert problems == []


def test_a_route_reports_pagination_only_when_the_notes_say_it_pages(swept):
    bodies, _ = swept
    paged = {
        operation_id for operation_id, notes in contract_doc.ROUTE_NOTES.items() if notes.pagination
    }
    assert paged == {"agreements_today", "effectiveness_slips"}
    for operation_id, answers in bodies.items():
        has_pagination = any("pagination" in body["meta"] for body in answers)
        assert has_pagination == (operation_id in paged), operation_id


def test_the_all_variant_adds_the_per_line_totals(swept):
    bodies, _ = swept
    keys = [set(body["meta"]["pagination"]) for body in bodies["agreements_today"]]
    assert {"limit", "offset", "total", "returned"} in keys
    assert {"limit", "offset", "total", "returned", "totals_by_line"} in keys


def test_the_admin_routes_put_the_documented_keys_in_meta(make_app):
    client, _ = admin_client(make_app, ENABLE)
    status = client.get("/admin/indexes/status/auto").json()
    apply = client.post("/admin/indexes/apply/auto").json()
    for operation_id, body in (("admin_indexes_status", status), ("admin_indexes_apply", apply)):
        documented = {key for key, _ in contract_doc.ROUTE_NOTES[operation_id].filters}
        assert set(body["meta"]["filters"]) == documented, operation_id


def test_the_health_routes_are_plain_status_bodies_in_the_notes():
    plain = {name for name, notes in contract_doc.ROUTE_NOTES.items() if notes.plain}
    assert plain == {"health_live", "health_ready", "health_db_all", "health_db_line"}


# ---------------------------------------------------------------- 3. SQL and the data contract

TABLE_REFERENCE = re.compile(r"\b\w+\.\w+\.(\w+) AS (\w+)")
ALIASED_COLUMN = re.compile(r"\b(\w+)\.(\w+)\b")
# Names a statement defines for itself: the dedup rank and flags computed in CTEs.
DERIVED = {"rn", "answer_flag", "cpc_flag", "product_line"}


def documented_contract() -> dict[str, set[str]]:
    """``{table: columns}`` read from the ``### `table` `` sections of the contract."""
    contract: dict[str, set[str]] = {}
    table = None
    for line in DATA_CONTRACT.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^### `(\w+)`", line)
        if heading:
            table = heading.group(1)
            contract[table] = set()
        elif line.startswith("## "):
            table = None
        elif table and (cell := re.match(r"^\| `(\w+)` \|", line)):
            contract[table].add(cell.group(1))
    return contract


def columns_read(sql: str):
    """``(alias, tables, column)`` for every ``alias.column`` of a table in the statement."""
    aliases: dict[str, set[str]] = defaultdict(set)
    for table, alias in TABLE_REFERENCE.findall(sql):
        aliases[alias].add(table)
    for alias, column in ALIASED_COLUMN.findall(sql):
        if alias in aliases:
            yield alias, aliases[alias], column


def test_the_contract_document_lists_the_eight_tables():
    assert set(documented_contract()) == {
        "agreements",
        "agreement_debts",
        "debts",
        "debt_attributes",
        "contact_events",
        "contact_results",
        "agreement_statuses",
        "agents",
    }


def test_every_table_the_sql_reads_is_in_the_contract(swept):
    _, executor = swept
    contract = documented_contract()
    read = {table for call in executor.calls for table, _ in TABLE_REFERENCE.findall(call["sql"])}
    assert read <= set(contract), read - set(contract)


def test_every_column_the_sql_reads_is_documented(swept):
    _, executor = swept
    contract = documented_contract()
    undocumented = set()
    for call in executor.calls:
        for alias, tables, column in columns_read(call["sql"]):
            if column in DERIVED or any(column in contract[t] for t in tables if t in contract):
                continue
            undocumented.add((alias, tuple(sorted(tables)), column))
    assert undocumented == set()


def test_every_documented_column_is_read_by_some_statement(swept):
    _, executor = swept
    everything = "\n".join(call["sql"] for call in executor.calls)
    for table, columns in documented_contract().items():
        for column in columns:
            assert re.search(rf"\b{column}\b", everything), f"{table}.{column} is never read"
