"""Response models.

They document the API; they do not validate it. Routes return plain dictionaries and
declare these classes only through ``responses=`` (see ``api/common.py``), so a row the
database returns is never rewritten or dropped by a schema. Their job is the OpenAPI
document and ``docs/api-contract.md``, which the UI is built against. A test compares
every model with the column list of the SQL builder behind it, so a renamed column
fails the build instead of a client.

Conventions: amounts are plain numbers (no currency symbol); timestamps are ISO 8601
strings; ``*_pct`` fields are percentages from 0 to 100 and are ``null`` when their
denominator is zero; ``*_count`` fields are integers.
"""

from __future__ import annotations

from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from collections_api.api.field_help import describe

# --------------------------------------------------------------------------- envelope


def _fill_descriptions(schema: dict[str, Any], model: type[BaseModel]) -> None:
    """Give every property that has no description of its own the registered default."""
    for name, prop in schema.get("properties", {}).items():
        if not prop.get("description"):
            text = describe(model.__name__, name)
            if text:
                prop["description"] = text


class ContractModel(BaseModel):
    """Base of every documented model: fills in default field descriptions."""

    model_config = ConfigDict(json_schema_extra=_fill_descriptions)


class ErrorItem(ContractModel):
    message: str = Field(description="Human-readable message. Never contains SQL or driver text.")
    source: str | None = Field(
        default=None, description="Product line or series the message is about, when there is one."
    )


class Pagination(ContractModel):
    limit: int | None = Field(default=None, description="Page size that was applied.")
    offset: int | None = Field(default=None, description="Offset that was applied.")
    total: int | None = Field(default=None, description="Rows available before paging.")
    returned: int | None = Field(default=None, description="Rows in this response.")
    totals_by_line: dict[str, int] | None = Field(
        default=None, description="Rows available per product line (only for `all`)."
    )
    truncated: bool | None = Field(
        default=None, description="True when the row cap was reached and rows may be missing."
    )


class Meta(ContractModel):
    generated_at: str = Field(description="UTC timestamp of this response, ISO 8601.")
    total_rows: int = Field(description="Rows in `data`. An object payload counts as 1.")
    sources: list[str] = Field(description="Product lines the data came from.")
    filters: dict[str, Any] = Field(description="The filters that were applied, echoed back.")
    run_id: str | None = Field(
        description="Request id, the same as the `X-Run-Id` header. Quote it in a bug report."
    )
    quality: dict[str, Any] = Field(
        description="Route-specific data-quality facts (staleness, truncation, cast failures)."
    )
    pagination: Pagination | None = Field(
        default=None, description="Present on paged routes and on routes with a row cap."
    )


class ErrorBody(ContractModel):
    detail: str = Field(description="One-line reason, the same text as `errors[0].message`.")
    meta: Meta
    data: list[Any] = Field(description="Always empty.")
    errors: list[ErrorItem]


@cache
def envelope(row: type[BaseModel], *, many: bool = True) -> type[BaseModel]:
    """``{meta, data, errors}`` with ``data`` typed as ``list[row]`` or as ``row``."""
    suffix = "Envelope" if many else "ObjectEnvelope"
    data_type: Any = list[row] if many else row  # type: ignore[valid-type]
    return create_model(
        f"{row.__name__}{suffix}",
        meta=(Meta, ...),
        data=(data_type, Field(description="The payload.")),
        errors=(
            list[ErrorItem],
            Field(description="Non-fatal problems. Empty when everything worked."),
        ),
    )


# --------------------------------------------------------------------------- agreements


class AgreementTodayRow(ContractModel):
    product_line: str = Field(description="Product line the installment belongs to.")
    agent_name: str | None = Field(description="Display name of the agent who made the agreement.")
    agreement_id: int
    portfolio_id: int
    current_debt_value: float = Field(
        description="Current balance of the debts the agreement covers."
    )
    agreement_total_value: float = Field(description="Sum of every installment of the agreement.")
    discount_granted: float = Field(description="Debt balance minus agreement total.")
    installment_count: int | None = Field(description="Installments the plan has.")
    installment_no: int = Field(description="0 is the first installment (the down payment).")
    issued_at: str
    due_at: str
    installment_amount: float
    status_label: str | None = Field(description="Label from `agreement_statuses`.")
    paid_at: str | None
    payment_state: str = Field(description="`PAID` when `paid_at` is set, otherwise `OPEN`.")


class AgreementTableRow(ContractModel):
    agent_name: str | None
    agreement_id: int
    portfolio_id: int
    agreement_type: str | None = Field(description="Status label of the agreement.")
    is_exception: int = Field(description="1 when the agreement is pending approval, else 0.")
    first_installment_due: str | None
    first_installment_value: float | None
    later_installment_value: float | None = Field(description="Amount of installment 1.")
    installment_count: int | None
    agreement_total_value: float
    issued_at: str


class PerformanceRow(ContractModel):
    agent_name: str | None
    attempt_count: int = Field(description="Distinct debt owners contacted in the window.")
    agreement_count: int
    slips_issued: int = Field(description="Installments already due in the window.")
    slips_paid: int = Field(description="Of those, paid within the grace period.")
    conversion_pct: float = Field(description="agreement_count / cpc_count * 100. 0 when no CPC.")
    paid_per_cpc_pct: float = Field(
        description="slips_paid / cpc_count * 100. A different metric from conversion."
    )
    agreement_value: float
    first_installment_value: float
    rejected_count: int
    answer_count: int
    cpc_count: int = Field(description="Right-party contacts: answered and right party.")
    cpc_rate_pct: float = Field(description="cpc_count / answer_count * 100. 0 when no answers.")
    exception_count: int
    exception_value: float


# --------------------------------------------------------------------------- productivity


class ProductivityRow(ContractModel):
    agent_login: str
    agent_name: str
    attempt_count: float = Field(description="Distinct debt owners contacted in the window.")
    answer_count: float = Field(description="Of those, how many answered.")
    cpc_count: float = Field(description="Of those, how many were the right party.")
    cpc_rate_pct: float | None = Field(description="cpc_count / answer_count * 100, rounded up.")
    agreement_count: float
    agreement_count_by_contract: float = Field(
        description="Agreements counted per debt: one agreement covering N debts counts N."
    )
    slips_issued: float
    slips_paid: float
    agreements_per_attempt_pct: float | None
    agreement_value: float
    avg_ticket: float = Field(description="Average agreement value.")
    avg_installments: float
    avg_balance_ratio_pct: float = Field(description="Agreement value over original debt balance.")
    first_installment_value: float
    first_installment_received: float
    exception_count: float
    exception_value: float
    exception_first_installment_value: float
    rejected_count: float = Field(description="Distinct rejected debts.")
    rejected_value: float
    rejected_first_installment_value: float
    avg_debt_age_days: float
    hours_worked: float = Field(
        description="Proxy: first to last generated agreement of each day, summed."
    )


class LoadStatusRow(ContractModel):
    product_line: str = Field(description="Line name, or `all` for the summed row.")
    agent_count: int
    attempt_count: int
    cpc_count: int
    agreement_count: int
    agreement_value: float
    exception_count: int
    exception_value: float


class AgentPerformanceRow(ContractModel):
    product_line: str
    agent_name: str | None
    agent_login: str | None
    attempt_count: int = Field(description="Distinct (day, debt owner) pairs contacted.")
    answer_count: int
    cpc_count: int
    agreement_count: int
    slips_issued: int
    slips_paid: int
    agreement_value: float
    avg_ticket: float
    avg_installments: float
    first_installment_value: float
    first_installment_received: float
    slip_effectiveness_pct: float | None = Field(description="slips_paid / slips_issued * 100.")
    cpc_rate_pct: int | None
    avg_balance_ratio_pct: float
    exception_count: int
    exception_value: float
    exception_first_installment_value: float


class EffortCounts(ContractModel):
    attempt_count: int
    answer_count: int
    cpc_count: int


class AgentEffortRow(ContractModel):
    agent_key: str = Field(
        description="Lower-cased login, or display name when there is none. Identifies one person."
    )
    agent_login: str
    agent_name: str
    by_line: dict[str, EffortCounts] = Field(description="Counts per product line.")
    total: EffortCounts = Field(description="Counts summed over every line.")


class Quartiles(ContractModel):
    q1: float | None
    median: float | None
    q3: float | None
    top10_mean: float | None = Field(description="Mean of the best 10% (at least one agent).")
    mean: float | None


class BenchmarkData(ContractModel):
    cpc_rate_pct: Quartiles
    conversion_pct: Quartiles
    cash_effectiveness_pct: Quartiles
    exception_pct: Quartiles
    agent_count: int = Field(description="Agents that passed the minimum-sample rules.")
    lookback_months: int


# --------------------------------------------------------------------------- charts


class FirstInstallmentSummaryRow(ContractModel):
    first_installment_value: float | None
    agreement_count: int | None


class PortfolioLabelRow(ContractModel):
    portfolio_label: str


class ExceptionsByPortfolioRow(ContractModel):
    portfolio_label: str
    exception_count: int
    exception_value: float


class AgreementsByPortfolioRow(ContractModel):
    portfolio_label: str
    agreement_count: int
    agreement_value: float


class RejectedByPortfolioRow(ContractModel):
    portfolio_label: str
    rejected_count: int
    rejected_value: float


class BrokenByPortfolioRow(ContractModel):
    portfolio_label: str
    broken_count: int
    broken_value: float


class FirstInstallmentByPortfolioRow(ContractModel):
    portfolio_label: str
    agreement_count: int
    first_installment_value: float


class ExceptionsByAgentRow(ContractModel):
    agent_name: str | None
    exception_count: int
    exception_value: float


class FirstInstallmentByAgentRow(ContractModel):
    agent_name: str | None
    first_installment_count: int
    first_installment_value: float


class PortfolioRollupRow(ContractModel):
    portfolio_label: str
    status_id: int
    agreement_count: int = Field(
        description="Exact per status. Summing over several statuses can count an agreement twice."
    )
    total_value: float


class ReceivedByPortfolioRow(ContractModel):
    portfolio_label: str
    agreement_count: int
    received_value: float = Field(description="Cash paid inside the window, by payment date.")
    first_installment_received: float


class ExceptionWithoutPortfolioRow(ContractModel):
    agreement_id: int
    portfolio_id: int
    first_installment_value: float
    total_value: float
    agent_name: str | None


class AgreementDetailRow(ContractModel):
    agreement_id: int
    portfolio_id: int
    first_installment_value: float
    total_value: float = Field(description="Sum of every installment, once each.")
    agent_name: str | None
    agreement_date: str = Field(description="YYYY-MM-DD.")
    due_date: str = Field(description="YYYY-MM-DD, of the first installment.")
    installment_count: int


class AgreementDetailAllRow(AgreementDetailRow):
    portfolio_label: str | None = Field(description="`null` when no debt carries a label.")


# --------------------------------------------------------------------------- effectiveness


class SeriesByIssueDayRow(ContractModel):
    issue_date: str = Field(description="YYYY-MM-DD.")
    slips_issued: int
    slips_paid_in_grace: int
    conversion_pct: int = Field(description="Whole percent, rounded half up.")


class SeriesByDueDayRow(ContractModel):
    due_date: str = Field(description="YYYY-MM-DD.")
    slips_issued: int
    slips_paid_in_grace: int
    conversion_pct: int


class SeriesByMonthRow(ContractModel):
    year: int
    month: int
    slips_issued: int
    slips_paid_in_grace: int
    conversion_pct: int


class SeriesByAgentMonthRow(ContractModel):
    agent_login: str | None
    year: int
    month: int
    slips_issued: int
    slips_paid_in_grace: int
    conversion_pct: int


class SeriesByAgentMonthBrokenRow(SeriesByAgentMonthRow):
    broken_count: int


class EffectivenessKpis(ContractModel):
    slips_total: int
    agreements_total: int
    slips_to_mature: int = Field(description="Due today or later and not yet paid.")
    slips_in_grace: int = Field(description="Overdue, still inside the grace period.")
    slips_overdue_unpaid: int = Field(description="Overdue with the grace period over.")
    slips_paid_in_grace: int
    slips_broken: int
    conversion_pct: float | None = Field(description="slips_paid_in_grace / slips_total * 100.")
    amount_maturing: float
    amount_received: float
    effectiveness_pct: float | None = Field(description="amount_received / amount_maturing * 100.")


class EffectivenessDay(ContractModel):
    due_date: str
    slips_total: int
    slips_paid_in_grace: int
    conversion_pct: float | None
    amount_maturing: float
    amount_received: float
    effectiveness_pct: float | None


class EffectivenessSummaryData(ContractModel):
    kpis: EffectivenessKpis
    daily: list[EffectivenessDay]
    best_day: EffectivenessDay | None = Field(description="Day with the highest effectiveness.")
    worst_day: EffectivenessDay | None = Field(description="Day with the lowest effectiveness.")


class SlipDetailRow(ContractModel):
    agreement_id: int
    portfolio_id: int
    slip_amount: float
    total_value: float
    agent_name: str | None
    agreement_date: str
    due_date: str
    installment_count: int
    paid_installment_count: int
    broken_date: str | None
    portfolio_label: str | None
    original_debt_value: float | None


class BreakCurveRow(ContractModel):
    band: str = Field(description="Days since the due date, for example `0-7 days` or `91+ days`.")
    slips_total: int
    slips_broken: int
    break_rate_pct: float | None


class PortfolioEffectivenessSummaryRow(ContractModel):
    slips_total: int
    slips_paid_in_grace: int | None
    amount_maturing: float
    amount_received: float
    effectiveness_pct: float | None


class PortfolioEffectivenessRow(PortfolioEffectivenessSummaryRow):
    portfolio_label: str


class GenerationRankingRow(ContractModel):
    portfolio_label: str
    agreement_count: int
    agreement_value: float


# --------------------------------------------------------------------------- targets


class TargetRow(ContractModel):
    office: str | None
    portfolio: str
    group: str | None = Field(description="One of the configured group names.")
    headcount: float | None
    cash_target: dict[str, float] = Field(description="Cash target per month, keyed `YYYYMM`.")
    recovery_count_target: dict[str, int]
    recovery_value_target: dict[str, float]
    total_target: dict[str, float] = Field(description="Cash target plus recovery value target.")


class TargetUploadResult(ContractModel):
    period: str = Field(description="Quarter label such as `3Q26`.")
    row_count: int


# --------------------------------------------------------------------------- regression


class RegressionPoint(ContractModel):
    id: str | int | None = Field(default=None, description="Opaque id. Repeated ids are dropped.")
    efficiency: float | None = None
    value: float | None = Field(default=None, description="The outcome the models explain.")
    attempts: float | None = None
    contacts: float | None = None
    cpc: float | None = None
    conversion: float | None = None


class RegressionRequest(ContractModel):
    points: list[RegressionPoint] = Field(
        min_length=1,
        description="One point per agent. A point with a missing field is dropped and counted.",
    )


class CleaningSummaryModel(ContractModel):
    raw_count: int
    clean_count: int
    removed_nulls: int
    removed_duplicates: int
    removed_outliers: int


class CoefficientModel(ContractModel):
    name: str
    value: float
    se: float = Field(description="Standard error from the training rows.")


class RegressionModelFit(ContractModel):
    id: str = Field(description="`simple`, `loglog`, `poly2`, `volume` or `full`.")
    label: str
    description: str
    drawable: bool = Field(description="True when the model has one driver and can be plotted.")
    r2_train: float
    r2_test: float
    adj_r2: float
    train_n: int
    test_n: int
    intercept: float
    intercept_se: float
    coefficients: list[CoefficientModel]


class RegressionData(ContractModel):
    cleaning: CleaningSummaryModel
    models: list[RegressionModelFit] = Field(description="Empty when fewer than 3 clean points.")


# --------------------------------------------------------------------------- admin


class IndexInfo(ContractModel):
    name: str
    table: str
    key_columns: list[str]
    include_columns: list[str]
    purpose: str
    exists: bool


class IndexStatusData(ContractModel):
    product_line: str
    total_recommended: int
    existing: int
    missing: int
    indexes: list[IndexInfo]


class IndexStep(ContractModel):
    name: str
    table: str
    sql: str
    action: str = Field(description="`created`, `would_create`, `skipped_existing` or `failed`.")
    error: str | None


class StatisticsStep(ContractModel):
    target: str
    sql: str
    action: str = Field(description="`updated`, `would_update` or `failed`.")
    error: str | None


class IndexApplyData(ContractModel):
    product_line: str
    dry_run: bool
    online: bool
    update_statistics: bool
    elapsed_ms: float
    indexes: list[IndexStep]
    statistics: list[StatisticsStep]


# --------------------------------------------------------------------------- health


class LiveStatus(ContractModel):
    status: str = Field(description="Always `ok`.")


class DatabaseStatus(ContractModel):
    status: str = Field(description="`ok`.")
    product_line: str
    connection: str = Field(description="`connected`.")


class AllDatabasesStatus(ContractModel):
    status: str = Field(description="`ok`.")
    product_lines: list[str]
    connection: str


class EffectivenessRefreshStatus(ContractModel):
    enabled: bool
    last_run_age_seconds: float | None = Field(
        description="Seconds since a refresh last produced rows. `null` before the first one."
    )
    interval_seconds: float


class ReadyStatus(ContractModel):
    status: str = Field(description="`ok`, or `degraded` (HTTP 503) when a line is down.")
    product_lines: dict[str, str] = Field(description="`ok` or a short reason, per line.")
    effectiveness_refresh: EffectivenessRefreshStatus
