"""Targets: the store, the read route, and the upload route with its guards."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from collections_api.domains.targets import extractor
from collections_api.domains.targets.store import TargetsStore
from tests import targets_pdf as t

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def document(period="3Q26"):
    built = extractor.build_document([t.page(t.document_lines())], f"targets_{period}.pdf", now=NOW)
    built["meta"]["period"] = period
    return built


# ---------------------------------------------------------------- store


def test_the_store_has_nothing_before_the_first_save(tmp_path):
    assert TargetsStore(tmp_path / "targets").latest() is None


def test_save_writes_the_latest_and_a_snapshot_for_the_period(tmp_path):
    store = TargetsStore(tmp_path / "targets")
    assert store.save(document("3Q26")) == "3Q26"
    assert store.latest()["meta"]["period"] == "3Q26"
    assert sorted(p.name for p in store.directory.iterdir()) == ["latest.json", "targets_3Q26.json"]


def test_a_second_upload_for_a_period_replaces_the_latest_but_keeps_the_first_snapshot(tmp_path):
    store = TargetsStore(tmp_path)
    first = document("3Q26")
    store.save(first)
    second = document("3Q26")
    second["meta"]["row_count"] = 99
    store.save(second)
    assert store.latest()["meta"]["row_count"] == 99
    snapshot = json.loads((tmp_path / "targets_3Q26.json").read_text(encoding="utf-8"))
    assert snapshot["meta"]["row_count"] == first["meta"]["row_count"]


def test_a_new_period_becomes_the_latest(tmp_path):
    store = TargetsStore(tmp_path)
    store.save(document("3Q26"))
    store.save(document("4Q26"))
    assert store.latest()["meta"]["period"] == "4Q26"
    assert (tmp_path / "targets_3Q26.json").exists() and (tmp_path / "targets_4Q26.json").exists()


@pytest.mark.parametrize("period", ["", "3Q2026", "../x", "5q26", "3Q26/../../x"])
def test_a_period_label_that_is_not_a_quarter_is_refused(tmp_path, period):
    bad = document()
    bad["meta"]["period"] = period
    with pytest.raises(ValueError):
        TargetsStore(tmp_path).save(bad)
    assert list(tmp_path.iterdir()) == []


def test_writes_leave_no_temporary_files_behind(tmp_path):
    store = TargetsStore(tmp_path)
    store.save(document())
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_scratch_paths_are_unique_and_inside_the_store(tmp_path):
    store = TargetsStore(tmp_path / "targets")
    first, second = store.scratch_path(), store.scratch_path()
    assert first != second and first.parent == store.directory and first.suffix == ".pdf"


# ---------------------------------------------------------------- GET /dashboard/targets


def test_before_any_upload_the_answer_is_an_empty_success_with_one_note(client):
    response = client.get("/dashboard/targets")
    body = response.json()
    assert response.status_code == 200 and body["data"] == []
    assert body["errors"] == [{"message": "No targets document has been uploaded yet."}]


def test_the_latest_document_is_served_row_by_row(client_for, tmp_path):
    client = client_for()
    client.app.state.ctx.targets.save(document())
    body = client.get("/dashboard/targets").json()
    assert [r["portfolio"] for r in body["data"]] == ["Port A1", "Port B1"]
    assert body["meta"]["sources"] == ["targets_3Q26.pdf"]
    assert body["meta"]["filters"] == {"period": "3Q26"}
    assert body["meta"]["quality"]["validated"] is True
    assert body["meta"]["quality"]["months"] == ["202601", "202602", "202603"]
    assert body["errors"] == []


# ---------------------------------------------------------------- POST /dashboard/targets/upload


@pytest.fixture()
def pdf_bytes(tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "source.pdf"
    t.write_pdf(path, t.document_lines())
    return path.read_bytes()


def upload(client, content, name="targets_3Q2026.pdf", **headers):
    return client.post(
        "/dashboard/targets/upload",
        files={"file": (name, content, "application/pdf")},
        headers=headers,
    )


def scratch_files(client):
    directory = client.app.state.ctx.targets.directory
    return (
        [p for p in directory.iterdir() if p.name.startswith("_upload_")]
        if directory.exists()
        else []
    )


def test_a_valid_pdf_becomes_the_latest_targets(client_for, pdf_bytes):
    client = client_for()
    body = upload(client, pdf_bytes).json()
    assert body["data"] == {"period": "3Q26", "row_count": 2}
    assert body["meta"]["sources"] == ["targets_3Q2026.pdf"]
    latest = client.get("/dashboard/targets").json()
    assert [r["portfolio"] for r in latest["data"]] == ["Port A1", "Port B1"]
    assert scratch_files(client) == []


def test_the_upload_is_checked_and_nothing_is_stored_when_it_fails(client_for, tmp_path):
    pytest.importorskip("pdfplumber")
    path = tmp_path / "bad.pdf"
    t.write_pdf(path, [t.HEADER, t.GROUP_A, t.GROUP_B, ["", "TOTAL", *[""] * 12, "1", "1", "1"]])
    client = client_for()
    response = upload(client, path.read_bytes())
    assert response.status_code == 422 and "Validation failed" in response.json()["detail"]
    assert client.app.state.ctx.targets.latest() is None
    assert scratch_files(client) == []


def test_a_file_that_is_not_a_pdf_by_name_is_refused(client_for):
    client = client_for()
    assert upload(client, b"x", name="targets.csv").status_code == 400
    assert upload(client, b"x", name="").status_code in (400, 422)


def test_a_directory_in_the_file_name_is_ignored(client_for, pdf_bytes):
    response = upload(client_for(), pdf_bytes, name="../../elsewhere/targets_3Q2026.pdf")
    assert response.status_code == 200 and response.json()["meta"]["sources"] == [
        "targets_3Q2026.pdf"
    ]


def test_garbage_with_a_pdf_name_is_a_clear_error_and_leaves_no_scratch_file(client_for):
    pytest.importorskip("pdfplumber")
    client = client_for()
    response = upload(client, b"not a pdf at all")
    assert response.status_code == 422
    assert response.json()["detail"] == "The file could not be read as a PDF"
    assert scratch_files(client) == []


def test_an_upload_above_the_size_limit_is_a_413(client_for):
    client = client_for({"TARGETS_UPLOAD_MAX_BYTES": "2048"})
    response = upload(client, b"%PDF-" + b"x" * 5000)
    assert response.status_code == 413
    assert scratch_files(client) == []


def test_a_browser_post_from_an_origin_off_the_list_is_forbidden(client_for, pdf_bytes):
    client = client_for()
    assert upload(client, pdf_bytes, Origin="https://elsewhere.invalid").status_code == 403
    assert client.app.state.ctx.targets.latest() is None
    assert upload(client, pdf_bytes, Origin="http://localhost:5173").status_code == 200


def test_a_request_without_an_origin_header_is_not_a_browser_form_post(client_for, pdf_bytes):
    assert upload(client_for(), pdf_bytes).status_code == 200


def test_the_wildcard_origin_list_allows_any_origin(client_for, pdf_bytes):
    client = client_for({"CORS_ALLOW_ORIGINS": "*"})
    assert upload(client, pdf_bytes, Origin="https://anywhere.invalid").status_code == 200


def test_the_group_names_come_from_the_settings(client_for, pdf_bytes):
    client = client_for({"TARGET_GROUP_NAMES": "Team X,Team Y"})
    response = upload(client, pdf_bytes)
    assert response.status_code == 422  # no row belongs to those groups


def test_the_upload_needs_credentials_when_authentication_is_on(client_for, pdf_bytes):
    env = {"REQUIRE_API_AUTH": "true", "API_KEY": "test-key-1", "API_TOKEN": "test-token-1"}
    client = client_for(env)
    assert upload(client, pdf_bytes).status_code == 401
    good = {"X-API-Key": "test-key-1", "Authorization": "Bearer test-token-1"}
    assert upload(client, pdf_bytes, **good).status_code == 200


def test_the_request_body_has_its_own_ceiling_above_the_file_limit(client_for):
    client = client_for({"TARGETS_UPLOAD_MAX_BYTES": "2048"})
    response = upload(client, b"x" * (3 * 1024 * 1024))
    assert response.status_code == 413
