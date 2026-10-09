"""The optional single-page-app hosting, and its path containment."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from collections_api.api.static import resolve_inside

INDEX = "<html><body>app shell</body></html>"
SECRET = "outside the dist directory"


@pytest.fixture()
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX, encoding="utf-8")
    (root / "assets" / "app.js").write_text("console.log('x')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return root


def spa_client(make_app, dist):
    app, _ = make_app({"FRONTEND_DIST_DIR": str(dist)})
    return TestClient(app)


def test_without_a_frontend_the_root_is_a_banner_and_unknown_paths_404(client):
    assert client.get("/").json() == {"status": "ok", "docs": "/docs"}
    assert client.get("/some/page").status_code == 404


def test_banner_hides_the_docs_link_when_docs_are_off(make_app):
    app, _ = make_app({"ENABLE_DOCS": "false"})
    assert TestClient(app).get("/").json() == {"status": "ok", "docs": None}


def test_the_shell_is_served_at_the_root_and_for_client_side_routes(make_app, dist):
    client = spa_client(make_app, dist)
    assert client.get("/").text == INDEX
    assert client.get("/agents/detail").text == INDEX


def test_existing_files_are_served_as_themselves(make_app, dist):
    response = spa_client(make_app, dist).get("/assets/app.js")
    assert response.text == "console.log('x')"


def test_api_prefixes_are_never_answered_with_the_shell(make_app, dist):
    client = spa_client(make_app, dist)
    for url in ("/dashboard/unknown", "/effectiveness/unknown", "/api/unknown", "/health/unknown"):
        response = client.get(url)
        assert response.status_code == 404, url
        assert INDEX not in response.text


@pytest.mark.parametrize(
    "path", ["/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt"]
)
def test_traversal_never_leaves_the_dist_directory(make_app, dist, path):
    response = spa_client(make_app, dist).get(path)
    assert SECRET not in response.text


def test_an_absolute_path_is_not_served(make_app, dist, tmp_path):
    target = str(tmp_path / "secret.txt").replace("\\", "/")
    response = spa_client(make_app, dist).get(f"/{target}")
    assert SECRET not in response.text


def test_resolve_inside(dist, tmp_path):
    assert resolve_inside(dist, "assets/app.js") == (dist / "assets" / "app.js").resolve()
    assert resolve_inside(dist, "") is None
    assert resolve_inside(dist, "../secret.txt") is None
    assert resolve_inside(dist, "assets/../../secret.txt") is None
    assert resolve_inside(dist, str(tmp_path / "secret.txt")) is None


@pytest.mark.skipif(os.name != "nt", reason="drive-qualified paths only exist on Windows")
def test_a_drive_qualified_path_is_refused_on_windows(dist):
    assert resolve_inside(dist, "C:/Windows/win.ini") is None
    assert resolve_inside(dist, "C:secret.txt") is None


def test_a_symlink_that_points_outside_is_refused(dist, tmp_path):
    link = dist / "linked.txt"
    try:
        link.symlink_to(tmp_path / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert resolve_inside(dist, "linked.txt") is None
