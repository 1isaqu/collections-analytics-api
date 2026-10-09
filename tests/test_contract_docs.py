"""docs/openapi.json and docs/api-contract.md: fresh, complete and database-free."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from collections_api import __version__, contract_doc
from collections_api.api.field_help import FIELD_HELP

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def schema():
    return contract_doc.export_openapi()


def operations(schema):
    for path, item in schema["paths"].items():
        for method in HTTP_METHODS:
            if method in item:
                yield method, path, item[method]


# ---------------------------------------------------------------- freshness


def test_the_committed_openapi_document_is_current(schema):
    committed = (DOCS / "openapi.json").read_text(encoding="utf-8")
    assert committed == contract_doc.render_openapi_json(schema), (
        "docs/openapi.json is stale. Run: python scripts/export_openapi.py"
    )


def test_the_committed_contract_is_current(schema):
    committed = (DOCS / "api-contract.md").read_text(encoding="utf-8")
    assert committed == contract_doc.render_contract(schema), (
        "docs/api-contract.md is stale. Run: python scripts/export_api_contract.py"
    )


def test_rendering_twice_gives_the_same_text(schema):
    assert contract_doc.render_contract(schema) == contract_doc.render_contract(schema)
    first = contract_doc.render_openapi_json(contract_doc.export_openapi())
    assert first == contract_doc.render_openapi_json(contract_doc.export_openapi())


def test_the_committed_files_use_unix_line_endings():
    for name in ("openapi.json", "api-contract.md"):
        assert b"\r" not in (DOCS / name).read_bytes()


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("script", ["export_openapi", "export_api_contract"])
def test_the_export_scripts_write_and_check(script, tmp_path, monkeypatch, capsys):
    module = load_script(script)
    target = tmp_path / "docs" / Path(module.TARGET).name
    monkeypatch.setattr(module, "TARGET", target)
    monkeypatch.setattr(module, "ROOT", tmp_path)

    monkeypatch.setattr(sys, "argv", [script, "--check"])
    assert module.main() == 1  # nothing written yet
    assert "stale" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", [script])
    assert module.main() == 0 and target.is_file()
    assert target.read_text(encoding="utf-8") == (DOCS / target.name).read_text(encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [script, "--check"])
    assert module.main() == 0
    target.write_text("edited by hand", encoding="utf-8")
    assert module.main() == 1


# ---------------------------------------------------------------- no database, no environment


def run_python(code, tmp_path, **env):
    full_env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), **env}
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=full_env,
        timeout=120,
    )


def test_the_openapi_export_needs_neither_a_driver_nor_a_database(tmp_path):
    code = (
        "import sys\n"
        "from collections_api.contract_doc import export_openapi\n"
        "document = export_openapi()\n"
        "print(len(document['paths']), 'pyodbc' in sys.modules)\n"
    )
    result = run_python(code, tmp_path)
    assert result.returncode == 0, result.stderr
    count, driver_loaded = result.stdout.split()
    assert int(count) > 50 and driver_loaded == "False"


def test_the_export_ignores_the_environment_and_any_env_file(tmp_path):
    (tmp_path / ".env").write_text("DB_PORT=not-a-number\nREQUIRE_API_AUTH=true\n")
    code = (
        "from collections_api.contract_doc import export_openapi\n"
        "print(len(export_openapi()['paths']))\n"
    )
    result = run_python(code, tmp_path, DB_PORT="x", REQUIRE_API_AUTH="true", API_KEY="")
    assert result.returncode == 0, result.stderr


def test_the_app_object_itself_imports_without_a_database(tmp_path):
    code = (
        "import sys\n"
        "from collections_api.main import create_app\n"
        "from collections_api.settings import Settings\n"
        "create_app(Settings.defaults())\n"
        "print('pyodbc' in sys.modules)\n"
    )
    assert run_python(code, tmp_path).stdout.strip() == "False"


# ---------------------------------------------------------------- the document is complete


def test_every_operation_has_a_unique_id_a_summary_a_tag_and_a_response(schema):
    ids = []
    for _, path, operation in operations(schema):
        ids.append(operation["operationId"])
        assert operation["summary"] and operation["tags"], path
        assert "200" in operation["responses"], path
    assert len(ids) == len(set(ids)) == 56


def test_every_operation_has_route_notes_and_every_note_has_an_operation(schema):
    ids = {operation["operationId"] for _, _, operation in operations(schema)}
    assert set(contract_doc.ROUTE_NOTES) == ids


def test_every_reference_resolves(schema):
    schemas = schema["components"]["schemas"]
    for ref in contract_doc._refs(schema):
        assert ref.startswith("#/components/schemas/") and ref.rsplit("/", 1)[-1] in schemas


def test_every_field_of_every_exported_model_has_a_description(schema):
    for name, model in schema["components"]["schemas"].items():
        if name in contract_doc.HIDDEN_SCHEMAS or name.endswith(contract_doc.ENVELOPE_SUFFIXES):
            continue
        for field, prop in model.get("properties", {}).items():
            assert prop.get("description"), f"{name}.{field} has no description"


def test_every_registered_description_is_used_by_some_field(schema):
    used = set()
    for name, model in schema["components"]["schemas"].items():
        for field in model.get("properties", {}):
            used.update({field, f"{name}.{field}"})
    unused = [key for key in FIELD_HELP if key not in used]
    assert not unused, f"descriptions for fields that do not exist: {unused}"


def test_the_markdown_has_a_section_for_every_endpoint_and_every_parameter(schema):
    text = contract_doc.render_contract(schema)
    for method, path, operation in operations(schema):
        assert f"### `{method.upper()} {path}`" in text
        assert f"Operation `{operation['operationId']}`" in text
        for parameter in operation.get("parameters", ()):
            assert f"| `{parameter['name']}` | {parameter['in']} |" in text


def test_the_markdown_documents_the_meta_keys_of_a_route_that_has_them(schema):
    text = contract_doc.render_contract(schema)
    assert "`meta.quality` keys" in text and "`numeric_cast_failures`" in text
    assert "`meta.pagination` keys" in text and "`totals_by_line`" in text


def test_the_document_identifies_the_service_and_declares_the_credentials(schema):
    assert schema["info"]["version"] == __version__
    schemes = schema["components"]["securitySchemes"]
    assert set(schemes) == {"ApiKeyAuth", "BearerAuth"}
    open_paths = [p for _, p, o in operations(schema) if "security" not in o]
    assert open_paths == ["/health/live"]


def test_no_field_models_a_personal_identifier(schema):
    banned = ("document_number", "tax_id", "debtor_name", "phone", "email", "registration")
    for name, model in schema["components"]["schemas"].items():
        for field in model.get("properties", {}):
            assert not any(word in field.lower() for word in banned), f"{name}.{field}"


def test_the_json_document_round_trips(schema):
    assert json.loads(contract_doc.render_openapi_json(schema)) == schema
