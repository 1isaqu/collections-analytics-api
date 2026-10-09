"""POST /regression/agents: validation, ceilings and the shape of the answer."""

from __future__ import annotations

import json

from tests.test_regression_models import points


def test_the_route_cleans_fits_and_reports(client):
    body = client.post("/regression/agents", json={"points": points()}).json()
    data = body["data"]
    assert [m["id"] for m in data["models"]] == ["simple", "loglog", "poly2", "volume", "full"]
    assert data["cleaning"]["clean_count"] == 20
    assert data["models"][0]["intercept"] == 3.0
    assert body["meta"]["sources"] == ["request"]
    assert body["meta"]["filters"] == {"point_count": 20}
    assert body["meta"]["total_rows"] == 1


def test_the_same_request_gives_the_same_answer(client):
    first = client.post("/regression/agents", json={"points": points()}).json()["data"]
    second = client.post("/regression/agents", json={"points": points()}).json()["data"]
    assert first == second


def test_incomplete_points_are_dropped_not_rejected(client):
    sent = points()
    sent[0]["cpc"] = None
    sent[1]["efficiency"] = None
    del sent[2]["value"]
    data = client.post("/regression/agents", json={"points": sent}).json()["data"]
    assert data["cleaning"]["removed_nulls"] == 3


def test_too_few_clean_points_give_an_empty_model_list(client):
    data = client.post("/regression/agents", json={"points": points(2)}).json()["data"]
    assert data["models"] == [] and data["cleaning"]["clean_count"] == 2


def test_the_body_must_have_points(client):
    assert client.post("/regression/agents", json={"points": []}).status_code == 422
    assert client.post("/regression/agents", json={}).status_code == 422
    assert (
        client.post("/regression/agents", json={"points": [{"efficiency": "abc"}]}).status_code
        == 422
    )


def test_get_is_not_allowed(client):
    assert client.get("/regression/agents").status_code == 405


def test_more_points_than_the_limit_are_a_413(client_for):
    client = client_for({"REGRESSION_MAX_POINTS": "10"})
    assert client.post("/regression/agents", json={"points": points(10)}).status_code == 200
    response = client.post("/regression/agents", json={"points": points(11)})
    assert response.status_code == 413
    assert "At most 10 points" in response.json()["detail"]


def padded(size):
    return json.dumps({"points": [{"id": "x" * size}]}).encode()


def test_a_body_over_the_byte_ceiling_is_refused_before_it_is_read(client_for):
    client = client_for({"REGRESSION_MAX_POINTS": "10"})  # 10 * 1024 bytes
    response = client.post(
        "/regression/agents", content=padded(12_000), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    body = response.json()
    assert body["data"] == [] and "byte limit" in body["detail"] and body["meta"]["run_id"]
    assert client.executor.calls == []


def test_a_chunked_body_is_cut_off_at_the_ceiling(client_for):
    client = client_for({"REGRESSION_MAX_POINTS": "10"})
    payload = padded(12_000)

    def chunks():
        for start in range(0, len(payload), 1000):
            yield payload[start : start + 1000]

    response = client.post(
        "/regression/agents", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_the_ceiling_does_not_apply_to_other_routes_or_to_reads(client_for):
    client = client_for({"REGRESSION_MAX_POINTS": "10"})
    assert client.get("/dashboard/portfolios/auto").status_code == 200
    assert client.post("/regression/agents", json={"points": points(10)}).status_code == 200


def test_the_api_alias_has_the_same_ceiling(client_for):
    client = client_for({"REGRESSION_MAX_POINTS": "10"})
    response = client.post(
        "/api/regression/agents",
        content=padded(12_000),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
