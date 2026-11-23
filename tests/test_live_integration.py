"""Real ``python -m worker`` (YOLO11n) against the threaded API, on the looped CAD clip. Local only."""

from __future__ import annotations

import csv
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from api.main import create_app
from api.settings import ApiSettings
from tests.test_api_live import Server

ROOT = Path(__file__).resolve().parent.parent
CLIP = ROOT / "datasets/raw/cad_seq25.mp4"
CAMERAS = ROOT / "configs/cameras"
WEIGHTS = ROOT / "ai_engine/models/yolo11n.pt"

pytestmark = pytest.mark.integration


class RealServer(Server):
    """The threaded loopback server of ``test_api_live``, with the real camera configs and worker."""

    def __init__(self, tmp_path: Path) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.settings = ApiSettings(data_dir=tmp_path / "data", camera_dir=CAMERAS, port=self.port)
        self.app = create_app(self.settings)
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.url = f"http://127.0.0.1:{self.port}"
        self.http = httpx.Client(base_url=self.url, timeout=60)


@pytest.fixture
def real_server(tmp_path):
    if not CLIP.exists() or not WEIGHTS.exists():
        pytest.skip("CAD clip or YOLO weights are not present (local only)")
    server = RealServer(tmp_path)
    server.start()
    try:
        yield server
    finally:
        try:
            for session in server.http.get("/api/live").json():
                server.http.post(f"/api/videos/{session['video_id']}/stop")
        except httpx.HTTPError:
            pass
        try:
            if server.thread.is_alive():
                server.stop()
        finally:
            for proc in server.live.spawned:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)


def test_real_worker_on_looped_clip(real_server) -> None:
    server = real_server
    response = server.start_live("cad_seq25")
    assert response.status_code == 201
    video_id = response.json()["video_id"]
    time.sleep(20)  # a 20 s session; model load time is included
    assert server.http.post(f"/api/videos/{video_id}/stop").status_code == 200
    deadline = time.time() + 60
    run = server.http.get(f"/api/videos/{video_id}").json()
    while run["status"] not in ("completed", "failed", "cancelled") and time.time() < deadline:
        time.sleep(0.25)
        run = server.http.get(f"/api/videos/{video_id}").json()
    assert run["status"] == "completed", run
    assert run["frames_processed"] >= 150
    report = server.http.get(f"/api/videos/{video_id}/report").json()
    assert "loops" in report and report["stopped_by"] == "operator"
    assert report["outputs"]["timeseries"]
    rows = server.http.get(f"/api/videos/{video_id}/timeseries").json()
    assert rows
    out = server.settings.outputs_dir / str(video_id)
    files = [p for p in out.rglob("*") if p.is_file()]
    assert not [p for p in files if p.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png")]
    csvs = [p for p in files if p.suffix == ".csv"]
    assert len(csvs) == 1 and len(list(csv.reader(csvs[0].open()))) > 1
    assert {p.suffix for p in files if p not in csvs} <= {".json", ".log"}
    assert server.http.get("/api/live").json() == []
    for proc in server.live.spawned:
        assert proc.wait(timeout=15) is not None
