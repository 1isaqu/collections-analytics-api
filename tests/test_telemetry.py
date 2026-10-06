import json
import sys
import types

from collections_api.core.telemetry import Telemetry, init_sentry
from collections_api.settings import Settings


class FakeScope:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_tag(self, key, value):
        self.sink.append(("tag", key, value))


def fake_sentry(init_error=None):
    sink: list = []
    module = types.ModuleType("sentry_sdk")

    def init(**kwargs):
        if init_error:
            raise init_error
        sink.append(("init", kwargs))

    module.init = init
    module.push_scope = lambda: FakeScope(sink)
    module.capture_exception = lambda exc: sink.append(("capture", type(exc).__name__))
    module.logger = types.SimpleNamespace(
        error=lambda message, attributes: sink.append(("log", "error", message, attributes)),
        warning=lambda message, attributes: sink.append(("log", "warning", message, attributes)),
    )
    module.metrics = types.SimpleNamespace(
        count=lambda name, value, unit, attributes: sink.append(
            ("count", name, value, unit, attributes)
        ),
        distribution=lambda name, value, unit, attributes: sink.append(
            ("dist", name, value, unit, attributes)
        ),
    )
    module.sink = sink
    return module


def settings_with(**env):
    return Settings.from_env(env)


def test_without_configuration_every_call_is_a_no_op(tmp_path):
    telemetry = Telemetry.from_settings(settings_with())
    assert not telemetry.sentry_active and not telemetry.log_enabled
    telemetry.event("x", {"a": 1})
    telemetry.log("error", "m")
    telemetry.metric("count", "n", 1)
    telemetry.capture_exception(RuntimeError("boom"))


def test_sentry_init_keeps_locals_and_bodies_out_of_events(monkeypatch):
    module = fake_sentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", module)
    settings = settings_with(SENTRY_DSN="https://public@o0.example.com/1", APP_ENV="staging")
    assert init_sentry(settings) is module
    _, kwargs = module.sink[0]
    assert kwargs["dsn"] == "https://public@o0.example.com/1"
    assert kwargs["environment"] == "staging"
    assert kwargs["include_local_variables"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["send_default_pii"] is False


def test_malformed_dsn_never_aborts_boot(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "sentry_sdk", fake_sentry(init_error=ValueError("Unsupported scheme"))
    )
    assert init_sentry(settings_with(SENTRY_DSN="not-a-dsn")) is None


def test_missing_package_is_tolerated(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # makes the import raise ImportError
    assert init_sentry(settings_with(SENTRY_DSN="https://public@o0.example.com/1")) is None


def test_empty_dsn_never_imports_sentry(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert init_sentry(settings_with()) is None


def test_sentry_calls_are_forwarded(monkeypatch):
    module = fake_sentry()
    monkeypatch.setitem(sys.modules, "sentry_sdk", module)
    telemetry = Telemetry.from_settings(settings_with(SENTRY_DSN="https://public@o0.example.com/1"))
    telemetry.log("error", "Query failed.", database="db_a")
    telemetry.metric("distribution", "db.query_duration_ms", 12.5, unit="millisecond", context="c")
    telemetry.capture_exception(ValueError("x"), run_id="srv-1")
    sink = module.sink
    assert ("log", "error", "Query failed.", {"database": "db_a"}) in sink
    assert ("dist", "db.query_duration_ms", 12.5, "millisecond", {"context": "c"}) in sink
    assert ("tag", "run_id", "srv-1") in sink and ("capture", "ValueError") in sink


def test_event_log_writes_ndjson(tmp_path):
    path = tmp_path / "logs" / "events.ndjson"
    telemetry = Telemetry(log_path=path)
    telemetry.event("query_end", {"rows": 3}, run_id="srv-9")
    telemetry.event("other")
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["query_end", "other"]
    assert lines[0]["run_id"] == "srv-9" and lines[0]["data"] == {"rows": 3}
    assert lines[1]["run_id"] == "runtime"


def test_event_log_rolls_over_at_the_size_cap(tmp_path):
    path = tmp_path / "events.ndjson"
    telemetry = Telemetry(log_path=path, log_max_bytes=200)
    for index in range(20):
        telemetry.event("tick", {"n": index})
    assert path.exists() and (tmp_path / "events.ndjson.1").exists()
    assert path.stat().st_size < 400


def test_unwritable_log_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    Telemetry(log_path=blocker / "events.ndjson").event("x")
