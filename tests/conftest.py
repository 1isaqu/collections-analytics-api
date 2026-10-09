"""Shared fixtures. All data is invented and lives in the test code."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from collections_api.core.sql import Period, QueryConfig
from collections_api.main import create_app
from collections_api.settings import Settings
from tests import sqlite_adapter as sq
from tests.fakes import RecordingExecutor

TODAY = date(2026, 3, 18)
ISSUED = "2026-03-18 09:30:00"


@pytest.fixture(scope="session")
def openapi_schema() -> dict:
    """The OpenAPI document of the default app. Generating it takes seconds, so once."""
    return create_app(Settings.defaults(), executor=RecordingExecutor()).openapi()


@pytest.fixture()
def settings() -> Settings:
    return Settings.defaults()


@pytest.fixture()
def make_app(tmp_path):
    """Factory for an app around a fake executor, with the background refresh off.

    ``env`` overrides settings, ``answers`` maps a context fragment to rows. Returns
    ``(app, executor)``.
    """

    def build(env=None, *, answers=None, executor=None, extra_routers=()):
        values = {
            "EFFECTIVENESS_ETL_ENABLED": "false",
            "TARGETS_DIR": str(tmp_path / "targets"),
        }
        values.update(env or {})
        recorder = executor or RecordingExecutor(answers)
        app = create_app(
            Settings.from_env(values),
            executor=recorder,
            extra_routers=extra_routers,
            today=lambda: TODAY,
        )
        return app, recorder

    return build


@pytest.fixture()
def client_for(make_app):
    """Factory for a ``TestClient`` around ``make_app``. The fake executor is ``client.executor``."""

    def build(env=None, **options):
        app, executor = make_app(env, **options)
        test_client = TestClient(app)
        test_client.executor = executor
        return test_client

    return build


@pytest.fixture()
def client(client_for):
    return client_for()


@pytest.fixture()
def cfg(settings) -> QueryConfig:
    return QueryConfig.from_settings(settings)


@pytest.fixture()
def period() -> Period:
    return Period.day(TODAY)


@pytest.fixture()
def sqlite_cfg(cfg) -> QueryConfig:
    return sq.sqlite_config(cfg)


@pytest.fixture()
def db():
    """In-memory database with a small portfolio of agreements for one day.

    Agents: 1 and 2 are regular; 3 matches the default exclusion pattern ``system%``.

    =========  =========  ======  =====================  ==============================
    agreement  portfolio  agent   status                 notes
    =========  =========  ======  =====================  ==============================
    5001       1          1       ACTIVE                 3 installments (1000, 500, 500);
                                                         installment 0 is written twice
    5002       1          2       PENDING_EXCEPTION      first installment 800
    5003       1          2       REJECTED               first installment 300
    5004       1          1       BROKEN                 first installment 200
    5005       1          3       ACTIVE                 excluded agent, 999
    5006       2          1       ACTIVE                 first installment 400
    5007       1          2       PENDING_EXCEPTION      no portfolio label, 150
    =========  =========  ======  =====================  ==============================
    """
    conn = sq.connect()
    sq.with_agents(
        conn,
        (1, "Agent 01", "agent_01"),
        (2, "Agent 02", "agent_02"),
        (3, "System Bot", "system_bot"),
    )
    rows = [
        sq.installment(
            row_id=1, agreement_id=5001, installment_no=0, amount=1000.0, plan_installments=3
        ),
        # The same installment written a second time by an upstream error.
        sq.installment(
            row_id=2, agreement_id=5001, installment_no=0, amount=1000.0, plan_installments=3
        ),
        sq.installment(
            row_id=3, agreement_id=5001, installment_no=1, amount=500.0, plan_installments=3
        ),
        sq.installment(
            row_id=4, agreement_id=5001, installment_no=2, amount=500.0, plan_installments=3
        ),
        sq.installment(
            row_id=5, agreement_id=5002, amount=800.0, status_id=sq.PENDING_EXCEPTION, agent_id=2
        ),
        sq.installment(
            row_id=6, agreement_id=5003, amount=300.0, status_id=sq.REJECTED, agent_id=2
        ),
        sq.installment(row_id=7, agreement_id=5004, amount=200.0, status_id=sq.BROKEN, agent_id=1),
        sq.installment(row_id=8, agreement_id=5005, amount=999.0, agent_id=3),
        sq.installment(row_id=9, agreement_id=5006, portfolio_id=2, amount=400.0, agent_id=1),
        sq.installment(
            row_id=10, agreement_id=5007, amount=150.0, status_id=sq.PENDING_EXCEPTION, agent_id=2
        ),
    ]
    sq.insert(conn, "agreements", rows)
    sq.insert(
        conn,
        "agreement_debts",
        [
            {"agreement_id": 5001, "portfolio_id": 1, "debt_id": 11},
            {"agreement_id": 5002, "portfolio_id": 1, "debt_id": 12},
            {"agreement_id": 5003, "portfolio_id": 1, "debt_id": 13},
            {"agreement_id": 5004, "portfolio_id": 1, "debt_id": 14},
            {"agreement_id": 5005, "portfolio_id": 1, "debt_id": 15},
            {"agreement_id": 5006, "portfolio_id": 2, "debt_id": 16},
            {"agreement_id": 5007, "portfolio_id": 1, "debt_id": 17},
        ],
    )
    sq.insert(
        conn,
        "debt_attributes",
        [
            {"debt_id": 11, "portfolio_label": "Portfolio A-01"},
            {"debt_id": 12, "portfolio_label": "Portfolio A-01"},
            {"debt_id": 13, "portfolio_label": "Portfolio A-02"},
            {"debt_id": 14, "portfolio_label": "Portfolio A-02"},
            {"debt_id": 15, "portfolio_label": "Portfolio A-01"},
            {"debt_id": 16, "portfolio_label": "Portfolio A-02"},
        ],
    )
    sq.insert(
        conn,
        "debts",
        [
            {
                "debt_id": d,
                "portfolio_id": 1,
                "contract_no": f"C{d}",
                "balance": 2000.0,
                "due_at": "2025-12-01 00:00:00",
            }
            for d in range(11, 18)
        ],
    )
    return conn
