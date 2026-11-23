"""LiveSupervisor and the public live routes, against a real fake-worker subprocess (torch-free).

The app runs under ``uvicorn.Server`` in a thread on a free loopback port so the child process can
post to its ingest routes. Every spawned child is killed in fixture teardown, even when a test fails.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from api import db
from api.main import create_app
from api.settings import ApiSettings
from tests.conftest import WalkerTracker, write_synthetic_video
from tests.test_api_jobs import CAMERA_YAML

SECRET_URL = "rtsp://user:secret@203.0.113.9/s"
WORKER = (sys.executable, "-m", "tests.fake_worker")
WAIT = 15.0


class Server:
    def __init__(self, tmp_path: Path, live_options: dict | None = None) -> None:
        camera_dir = tmp_path / "cameras"
        camera_dir.mkdir()
        (camera_dir / "clipcam.yaml").write_text(CAMERA_YAML + "live:\n  source: clip\n  clip: x.mp4\n")
        (camera_dir / "nolive.yaml").write_text(CAMERA_YAML)
        (camera_dir / "rtspcam.yaml").write_text(
            CAMERA_YAML + "live:\n  source: rtsp\n  rtsp_url_env: QV_TEST_RTSP\n"
        )
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.settings = ApiSettings(data_dir=tmp_path / "data", camera_dir=camera_dir, port=self.port)
        self.app = create_app(
            self.settings,
            WalkerTracker,
            {"worker_cmd": WORKER, "watchdog_interval_s": 0.2, **(live_options or {})},
        )
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="warning"))
        self.thread = threading.Thread(target=lambda: self.server.run(sockets=[self.sock]), daemon=True)
        self.url = f"http://127.0.0.1:{self.port}"
        self.http = httpx.Client(base_url=self.url, timeout=30)

    @property
    def live(self):
        return self.app.state.live

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + WAIT
        while not self.server.started:
            assert time.time() < deadline, "server did not start"
            time.sleep(0.02)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(40)
        self.http.close()
        self.sock.close()

    def run(self, video_id: int, wanted=("completed", "failed", "cancelled")) -> dict:
        deadline = time.time() + WAIT
        while time.time() < deadline:
            run = self.http.get(f"/api/videos/{video_id}").json()
            if run["status"] in wanted:
                return run
            time.sleep(0.05)
        raise AssertionError(f"run {video_id} did not reach {wanted}")

    def start_live(self, camera: str = "clipcam") -> httpx.Response:
        return self.http.post(f"/api/cameras/{camera}/live")


@pytest.fixture
def clip(tmp_path):
    return write_synthetic_video(tmp_path / "clip.mp4", frames=60)


@pytest.fixture
def make_server(tmp_path, monkeypatch):
    servers: list[Server] = []

    def make(mode: str = "normal", **live_options) -> Server:
        monkeypatch.setenv("FAKE_WORKER_MODE", mode)
        server = Server(tmp_path, live_options)
        servers.append(server)
        server.start()
        return server

    yield make
    for server in servers:
        try:
            if server.thread.is_alive():
                server.stop()
        finally:
            for proc in server.live.spawned:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)


def queue_upload(server: Server, clip) -> int:
    with open(clip, "rb") as handle:
        response = server.http.post(
            "/api/videos", files={"file": ("clip.mp4", handle, "video/mp4")}, data={"camera_id": "nolive"}
        )
    assert response.status_code == 202
    return response.json()["video_id"]


def error_of(run: dict) -> str | None:
    return run["error"]


def test_start_stream_stop_completes(make_server) -> None:
    server = make_server()
    response = server.start_live()
    assert response.status_code == 201
    run = response.json()
    video_id = run["video_id"]
    assert run["kind"] == "live" and run["source_kind"] == "clip" and run["status"] == "processing"
    assert run["options"]["process_fps"] == 10.0
    with connect(f"ws://127.0.0.1:{server.port}/ws/videos/{video_id}", open_timeout=5) as ws:
        seen = None
        deadline = time.time() + WAIT
        while time.time() < deadline:
            message = json.loads(ws.recv(timeout=5))
            if message["type"] == "progress":
                seen = message
                break
        assert seen is not None and seen["frames_processed"] > 0
    live = server.http.get("/api/live").json()
    assert [s["video_id"] for s in live] == [video_id]
    assert live[0]["camera_id"] == "clipcam" and live[0]["source_kind"] == "clip"
    assert live[0]["heartbeat_age_s"] is not None
    stopped = server.http.post(f"/api/videos/{video_id}/stop")
    assert stopped.status_code == 200
    final = server.run(video_id)
    assert final["status"] == "completed" and final["kind"] == "live" and final["source_kind"] == "clip"
    assert server.http.get(f"/api/videos/{video_id}/report").json()["stopped_by"] == "operator"
    assert server.http.get("/api/live").json() == []
    assert (server.settings.outputs_dir / str(video_id) / "worker.log").is_file()


def test_start_refusals(make_server, monkeypatch) -> None:
    server = make_server()
    assert server.start_live("nope").status_code == 404
    refused = server.start_live("nolive")
    assert refused.status_code == 409 and refused.json()["detail"] == "no_live_settings"
    monkeypatch.delenv("QV_TEST_RTSP", raising=False)
    refused = server.start_live("rtspcam")
    assert refused.status_code == 409 and refused.json()["detail"] == "rtsp_env_unset"
    first = server.start_live()
    assert first.status_code == 201
    second = server.start_live()
    assert second.status_code == 409 and second.json()["detail"] == "already_live"
    assert server.http.post("/api/videos/9999/stop").status_code == 404
    server.http.post(f"/api/videos/{first.json()['video_id']}/stop")


def test_concurrent_starts_only_one_wins(make_server) -> None:
    server = make_server()
    codes: list[int] = []
    barrier = threading.Barrier(2)

    def go() -> None:
        barrier.wait()
        codes.append(httpx.post(f"{server.url}/api/cameras/clipcam/live", timeout=30).status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(codes) == [201, 409]
    (session,) = server.http.get("/api/live").json()
    server.http.post(f"/api/videos/{session['video_id']}/stop")


def test_stop_during_startup_and_twice(make_server) -> None:
    server = make_server("slow_start")
    video_id = server.start_live().json()["video_id"]
    assert server.http.post(f"/api/videos/{video_id}/stop").status_code == 200
    run = server.run(video_id)
    assert run["status"] == "completed" and run["frames_processed"] == 0
    again = server.http.post(f"/api/videos/{video_id}/stop")
    assert again.status_code == 409 and again.json()["detail"] == "run is not live"


def test_cancel_on_live_run_stops_it(make_server) -> None:
    server = make_server()
    video_id = server.start_live().json()["video_id"]
    assert server.http.post(f"/api/videos/{video_id}/cancel").status_code == 200
    assert server.run(video_id)["status"] == "completed"
    assert server.http.post(f"/api/videos/{video_id}/cancel").status_code == 409


def test_worker_crash_marks_failed(make_server, clip) -> None:
    server = make_server("crash")
    video_id = server.start_live().json()["video_id"]
    upload_id = queue_upload(server, clip)  # held while the session runs, released when it ends
    started = time.time()
    run = server.run(video_id)
    assert run["status"] == "failed" and error_of(run) == "worker_exited"
    assert time.time() - started < 3
    assert server.http.get("/api/live").json() == []
    assert server.run(upload_id)["status"] == "completed"


def test_silent_worker_marks_failed_and_killed(make_server, clip) -> None:
    server = make_server("silent", heartbeat_timeout_s=0.6)
    video_id = server.start_live().json()["video_id"]
    upload_id = queue_upload(server, clip)
    run = server.run(video_id)
    assert run["status"] == "failed" and error_of(run) == "no_heartbeat"
    (proc,) = server.live.spawned
    assert proc.wait(timeout=5) is not None  # killed and reaped
    assert server.run(upload_id)["status"] == "completed"  # the hold was released


def test_stop_timeout_kills_and_fails(make_server, clip) -> None:
    server = make_server("deaf", stop_timeout_s=1.0, heartbeat_timeout_s=30.0)
    video_id = server.start_live().json()["video_id"]
    upload_id = queue_upload(server, clip)
    assert server.http.post(f"/api/videos/{video_id}/stop").status_code == 200
    run = server.run(video_id)
    assert run["status"] == "failed" and error_of(run) == "stop_timeout"
    (proc,) = server.live.spawned
    assert proc.poll() is not None
    assert server.run(upload_id)["status"] == "completed"  # the hold was released


def test_upload_waits_for_live_session(make_server, clip) -> None:
    server = make_server()
    video_id = server.start_live().json()["video_id"]
    with open(clip, "rb") as handle:
        response = server.http.post(
            "/api/videos", files={"file": ("clip.mp4", handle, "video/mp4")}, data={"camera_id": "nolive"}
        )
    assert response.status_code == 202
    upload_id = response.json()["video_id"]
    time.sleep(1.0)
    assert server.http.get(f"/api/videos/{upload_id}").json()["status"] == "queued"
    server.http.post(f"/api/videos/{video_id}/stop")
    assert server.run(video_id)["status"] == "completed"
    assert server.run(upload_id)["status"] == "completed"


def test_failed_spawn_releases_hold_and_fails_run(make_server, clip) -> None:
    server = make_server(worker_cmd=("/nonexistent/qv-worker",))
    response = server.start_live()
    assert response.status_code == 500 and response.json()["detail"] == "spawn_failed"
    (run,) = server.http.get("/api/videos").json()
    assert run["status"] == "failed" and run["kind"] == "live"
    assert server.http.get("/api/live").json() == []
    with open(clip, "rb") as handle:  # the upload hold was given back: an upload runs to completion
        upload = server.http.post(
            "/api/videos", files={"file": ("clip.mp4", handle, "video/mp4")}, data={"camera_id": "nolive"}
        )
    assert server.run(upload.json()["video_id"])["status"] == "completed"


def test_startup_fails_leftover_live_run(tmp_path, make_server, monkeypatch) -> None:
    settings = ApiSettings(data_dir=tmp_path / "data", camera_dir=tmp_path / "cameras")
    settings.data_dir.mkdir(parents=True)
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    leftover = db.create_video(conn, "live:clipcam", "clipcam", {}, kind="live", source_kind="clip")
    db.set_status(conn, leftover, "processing")
    conn.close()
    server = make_server()
    run = server.http.get(f"/api/videos/{leftover}").json()
    assert run["status"] == "failed" and run["kind"] == "live"


def test_shutdown_stops_workers(make_server) -> None:
    server = make_server()
    video_id = server.start_live().json()["video_id"]
    (proc,) = server.live.spawned
    assert proc.poll() is None
    server.stop()
    assert proc.poll() is not None  # no worker survives the app's lifespan
    conn = db.connect(server.settings.db_path)
    try:
        assert db.get_video(conn, video_id)["status"] in ("completed", "failed")
    finally:
        conn.close()


def test_supervisor_end_cleans_up(make_server, clip) -> None:
    server = make_server("silent", heartbeat_timeout_s=0.5)
    video_id = server.start_live().json()["video_id"]
    upload_id = queue_upload(server, clip)
    server.app.state.ingest_seq[video_id] = 7
    server.app.state.jobs.set_live_state(video_id, {"type": "progress"})
    assert server.run(video_id)["status"] == "failed"
    assert video_id not in server.app.state.ingest_seq
    assert server.app.state.jobs.live_state(video_id) is None
    assert server.run(upload_id)["status"] == "completed"  # the hold was released


def test_verify_uses_the_run_token(make_server) -> None:
    server = make_server()
    video_id = server.start_live().json()["video_id"]
    live = server.live
    token = live._sessions[video_id].token
    assert live.verify(video_id, token) and not live.verify(video_id, token + "x")
    assert not live.verify(video_id, "tökén") and not live.verify(video_id + 1, token)
    server.http.post(f"/api/videos/{video_id}/stop")
    server.run(video_id)
    assert not live.verify(video_id, token)


def test_rtsp_url_never_leaks(make_server, monkeypatch, caplog) -> None:
    monkeypatch.setenv("QV_TEST_RTSP", SECRET_URL)
    caplog.set_level("DEBUG")
    server = make_server()
    response = server.start_live("rtspcam")
    assert response.status_code == 201
    video_id = response.json()["video_id"]
    bodies = [
        response.text,
        server.http.get("/api/live").text,
        server.http.get(f"/api/videos/{video_id}").text,
        server.http.get("/api/cameras").text,
    ]
    server.http.post(f"/api/videos/{video_id}/stop")
    server.run(video_id)
    bodies.append(server.http.get("/api/videos").text)
    assert "secret" not in "".join(bodies)
    assert "secret" not in caplog.text
    (proc,) = server.live.spawned
    assert "secret" not in " ".join(map(str, proc.args))
    assert server.live._sessions == {}
    server.stop()
    for path in server.settings.data_dir.glob("queuevision.db*"):
        assert b"secret" not in path.read_bytes()
    log = server.settings.outputs_dir / str(video_id) / "worker.log"
    assert "secret" not in log.read_text()
