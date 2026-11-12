"""Dashboard static export served at / and the legacy page at /legacy."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import ApiSettings
from tests.conftest import WalkerTracker


def make_settings(tmp_path: Path, dashboard_dir: Path) -> ApiSettings:
    camera_dir = tmp_path / "cameras"
    camera_dir.mkdir(exist_ok=True)
    return ApiSettings(data_dir=tmp_path / "data", camera_dir=camera_dir, dashboard_dir=dashboard_dir)


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    (out / "_next" / "static").mkdir(parents=True)
    (out / "index.html").write_text("<html>INDEX-PAGE</html>")
    (out / "live.html").write_text("<html>LIVE-PAGE</html>")
    (out / "404.html").write_text("<html>NOT-FOUND-PAGE</html>")
    (out / "_next" / "static" / "x.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("TOP-SECRET")
    return out


def test_dashboard_routes(tmp_path: Path, export_dir: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path, export_dir), WalkerTracker)) as client:
        assert "INDEX-PAGE" in client.get("/").text
        live = client.get("/live?id=3")
        assert live.status_code == 200 and "LIVE-PAGE" in live.text
        assert "LIVE-PAGE" in client.get("/live.html").text
        js = client.get("/_next/static/x.js")
        assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_dashboard_catch_all_safety(tmp_path: Path, export_dir: Path) -> None:
    (export_dir / "escape.txt").symlink_to(tmp_path / "secret.txt")
    with TestClient(create_app(make_settings(tmp_path, export_dir), WalkerTracker)) as client:
        for path in ("/../api/main.py", "/%2e%2e/%2e%2e/etc/passwd", "/..%2fsecret.txt", "/escape.txt"):
            response = client.get(path)
            assert response.status_code == 404, path
            assert "TOP-SECRET" not in response.text and "root:" not in response.text
        nope = client.get("/nope")
        assert nope.status_code == 404 and "NOT-FOUND-PAGE" in nope.text
        assert client.get("/api/health").status_code == 200
        for path in ("/api/nope", "/ws/nope", "/api"):
            response = client.get(path)
            assert response.status_code == 404 and response.json() == {"detail": "Not Found"}, path
        assert client.post("/nope").status_code == 405


def test_dashboard_missing_shows_build_message(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path, tmp_path / "absent"), WalkerTracker)) as client:
        for path in ("/", "/live"):
            response = client.get(path)
            assert response.status_code == 200 and "text/html" in response.headers["content-type"]
            assert "npm run build" in response.text and "/legacy" in response.text
        assert client.get("/api/nope").json() == {"detail": "Not Found"}


def test_legacy_page_served(tmp_path: Path, export_dir: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path, export_dir), WalkerTracker)) as client:
        response = client.get("/legacy/")
        assert response.status_code == 200 and "QueueVision" in response.text
        assert "INDEX-PAGE" not in response.text
