"""REST + WebSocket tests: fake tracker, tiny generated clips, temporary data dir (torch-free)."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import db
from api.main import create_app
from api.settings import ApiSettings
from tests.conftest import WalkerTracker, write_synthetic_video
from tests.test_api_jobs import CAMERA_YAML, GatedTracker

WAIT = 15.0


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    camera_dir = tmp_path / "cameras"
    camera_dir.mkdir()
    (camera_dir / "walker.yaml").write_text(CAMERA_YAML)
    (camera_dir / "broken.yaml").write_text("camera_id: broken\nqueue_zone: [[0, 0]]\n")
    return ApiSettings(data_dir=tmp_path / "data", camera_dir=camera_dir)


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    return write_synthetic_video(tmp_path / "clip.mp4", frames=60)


def make_client(settings, factory=WalkerTracker):
    return TestClient(create_app(settings, factory))


def upload(client, clip: Path, camera: str = "walker", name: str = "clip.mp4", **data):
    with open(clip, "rb") as handle:
        return client.post(
            "/api/videos", files={"file": (name, handle, "video/mp4")}, data={"camera_id": camera, **data}
        )


def wait_status(client, video_id: int, wanted=("completed", "failed", "cancelled")) -> dict:
    deadline = time.time() + WAIT
    while time.time() < deadline:
        run = client.get(f"/api/videos/{video_id}").json()
        if run["status"] in wanted:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {video_id} did not reach {wanted}")


def test_health_and_cameras(settings) -> None:
    with make_client(settings) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok" and health["queue_depth"] == 0
        cams = {c["id"]: c for c in client.get("/api/cameras").json()}
        assert cams["walker"]["error"] is None and cams["walker"]["has_service_point"] is True
        assert cams["walker"]["alerts_enabled"] is True
        assert cams["walker"]["max_queue_length"] == 0.5
        assert cams["walker"]["smoothing_window_s"] == 2.0
        assert cams["broken"]["error"]


def test_health_reports_loaded_device(settings, clip) -> None:
    def factory():
        return WalkerTracker()

    factory.loaded_device = lambda: "fake-dev" if state["loaded"] else None  # type: ignore[attr-defined]
    state = {"loaded": False}
    with make_client(settings, factory) as client:
        health = client.get("/api/health").json()
        assert health["device"] is None and health["configured_device"] == settings.device
        state["loaded"] = True
        health = client.get("/api/health").json()
        assert health["device"] == "fake-dev" and health["configured_device"] == settings.device
    with make_client(settings) as client:  # a factory without a device getter reports null
        assert client.get("/api/health").json()["device"] is None


def test_cancel_after_last_frame_stays_completed(settings, clip, monkeypatch) -> None:
    real_set_report = db.set_report
    holder = {}

    def cancelling_set_report(conn, video_id, report, **kwargs):
        real_set_report(conn, video_id, report, **kwargs)
        holder["jobs"].cancel(video_id)  # arrives after process_video returned

    monkeypatch.setattr("api.jobs.db.set_report", cancelling_set_report)
    with make_client(settings) as client:
        holder["jobs"] = client.app.state.jobs
        video_id = upload(client, clip).json()["video_id"]
        run = wait_status(client, video_id)
        assert run["status"] == "completed"
        assert client.get(f"/api/videos/{video_id}/video").status_code == 200


def test_upload_with_invalid_and_good_camera(settings, clip) -> None:
    with make_client(settings) as client:
        assert upload(client, clip, camera="broken").status_code == 400
        assert upload(client, clip, camera="nope").status_code == 400
        assert upload(client, clip, camera="../walker").status_code == 400
        assert client.get("/api/videos").json() == []
        assert upload(client, clip).status_code == 202


def test_video_404_before_completion(settings, clip) -> None:
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=2)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        assert client.get(f"/api/videos/{video_id}").json()["status"] == "processing"
        assert client.get(f"/api/videos/{video_id}/video").status_code == 404
        release.set()
        assert wait_status(client, video_id)["status"] == "completed"
        assert client.get(f"/api/videos/{video_id}/video").status_code == 200


def test_full_run_results_match_db(settings, clip) -> None:
    with make_client(settings) as client:
        response = upload(client, clip, frame_stride="1", max_frames="")
        assert response.status_code == 202, response.text
        body = response.json()
        video_id = body["video_id"]
        assert body["status"] in ("queued", "processing") and body["options"]["blur_heads"] is True
        run = wait_status(client, video_id)
        assert run["status"] == "completed" and run["percent"] == 100.0
        assert run["summary"]["camera_id"] == "walker"
        conn = db.connect(settings.db_path)
        try:
            assert client.get(f"/api/videos/{video_id}/events").json() == db.list_events(conn, video_id)
            assert len(client.get(f"/api/videos/{video_id}/events?type=service_start").json()) >= 1
            assert len(client.get(f"/api/videos/{video_id}/events?limit=1").json()) == 1
            waits = client.get(f"/api/videos/{video_id}/waits").json()
            assert waits == db.list_waits(conn, video_id) and waits
            assert client.get(f"/api/videos/{video_id}/alerts").json() == db.list_alerts(conn, video_id)
            ts = client.get(f"/api/videos/{video_id}/timeseries").json()
            assert ts == db.list_timeseries(conn, video_id) and len(ts) == 6
            report = client.get(f"/api/videos/{video_id}/report").json()
            assert report == db.get_video(conn, video_id)["report"]
        finally:
            conn.close()
        ranged = client.get(f"/api/videos/{video_id}/video", headers={"Range": "bytes=0-99"})
        assert ranged.status_code == 206 and len(ranged.content) == 100
        assert ranged.headers["content-range"].startswith("bytes 0-99/")
        assert [r["video_id"] for r in client.get("/api/videos").json()] == [video_id]


def test_timeseries_csv_download(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert wait_status(client, video_id)["status"] == "completed"
        response = client.get(f"/api/videos/{video_id}/timeseries.csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert f"run{video_id}_timeseries.csv" in response.headers["content-disposition"]
        assert response.text.splitlines()[0].startswith("time_s,frame")
        # the run's output file is gone: 404, not a 500
        conn = db.connect(settings.db_path)
        try:
            path = Path(db.get_video(conn, video_id)["report"]["outputs"]["timeseries"])
        finally:
            conn.close()
        path.unlink()
        missing = client.get(f"/api/videos/{video_id}/timeseries.csv")
        assert missing.status_code == 404 and missing.json()["detail"] == "time series not available"


def test_timeseries_csv_404_without_report(settings, clip) -> None:
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=2)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        assert client.get(f"/api/videos/{video_id}/timeseries.csv").status_code == 404
        release.set()
        wait_status(client, video_id)
        assert client.get("/api/videos/99/timeseries.csv").status_code == 404


def test_completed_stride_run_reports_100_percent(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = upload(client, clip, frame_stride="3").json()["video_id"]
        run = wait_status(client, video_id)
        assert run["status"] == "completed" and run["percent"] == 100.0


def test_upload_rejections_leave_nothing(settings, clip) -> None:
    small = replace(settings, max_upload_bytes=1000)
    with make_client(small) as client:
        assert upload(client, clip).status_code == 413
        assert upload(client, clip, name="clip.txt").status_code == 400
        assert upload(client, clip, frame_stride="0").status_code == 422
        assert upload(client, clip, frame_stride="11").status_code == 422
        assert upload(client, clip, max_frames="0").status_code == 422
        assert client.get("/api/videos").json() == []
        assert not any(small.uploads_dir.rglob("*.*"))
        assert not list(small.uploads_dir.iterdir())


def test_content_length_over_limit_is_413(settings, clip) -> None:
    # The file fits the limit, so only the declared Content-Length can trigger the 413.
    small = replace(settings, max_upload_bytes=clip.stat().st_size + 10)
    with make_client(small) as client:
        with open(clip, "rb") as handle:
            response = client.post(
                "/api/videos",
                files={"file": ("clip.mp4", handle, "video/mp4")},
                data={"camera_id": "walker"},
                headers={"Content-Length": str(small.max_upload_bytes + 2 * 1024 * 1024)},
            )
        assert response.status_code == 413
        assert client.get("/api/videos").json() == []
        assert not list(small.uploads_dir.iterdir())


def test_unknown_ids_404(settings) -> None:
    with make_client(settings) as client:
        for suffix in ("", "/events", "/waits", "/alerts", "/timeseries", "/report", "/video"):
            assert client.get(f"/api/videos/99{suffix}").status_code == 404
        assert client.delete("/api/videos/99").status_code == 404
        assert client.post("/api/videos/99/cancel").status_code == 404


def test_websocket_sequence(settings, clip) -> None:
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=2)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        with client.websocket_connect(f"/ws/videos/{video_id}") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot" and first["schema_version"] == "1.0"
            assert first["run"]["video_id"] == video_id
            release.set()
            messages = []
            while True:
                message = ws.receive_json()
                messages.append(message)
                if message["type"] == "status" and message["status"] in ("completed", "failed"):
                    break
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()  # the server closes the socket after the final status
        assert messages[-1]["status"] == "completed" and messages[-1]["percent"] == 100.0
        assert {"progress", "event"} <= {m["type"] for m in messages}


def test_websocket_finished_run_and_unknown(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = upload(client, clip).json()["video_id"]
        wait_status(client, video_id)
        with client.websocket_connect(f"/ws/videos/{video_id}") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            final = ws.receive_json()
            assert final["type"] == "status" and final["status"] == "completed"
            with pytest.raises(Exception) as closed:
                ws.receive_json()
            assert type(closed.value).__name__ == "WebSocketDisconnect"
        with client.websocket_connect("/ws/videos/12345") as ws:
            with pytest.raises(Exception) as closed:
                ws.receive_json()
            assert closed.value.code == 4404


def test_ws_disconnect_mid_run_does_not_break_job(settings, clip) -> None:
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=2)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        with client.websocket_connect(f"/ws/videos/{video_id}") as ws:
            assert ws.receive_json()["type"] == "snapshot"
        release.set()
        assert wait_status(client, video_id)["status"] == "completed"
        jobs = client.app.state.jobs
        deadline = time.time() + WAIT
        while time.time() < deadline and jobs._subs:
            time.sleep(0.05)
        assert jobs._subs == {}


def test_delete_rules_and_cancel(settings, clip) -> None:
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=2)) as client:
        first = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        second = upload(client, clip).json()["video_id"]
        assert client.delete(f"/api/videos/{first}").status_code == 409
        assert client.delete(f"/api/videos/{second}").status_code == 204  # queued: cancelled, then deleted
        assert client.get(f"/api/videos/{second}").status_code == 404
        assert not (settings.uploads_dir / str(second)).exists()
        release.set()
        assert wait_status(client, first)["status"] == "completed"
        assert client.post(f"/api/videos/{first}/cancel").status_code == 409
        assert (settings.outputs_dir / str(first)).exists()
        assert client.delete(f"/api/videos/{first}").status_code == 204
        assert not (settings.outputs_dir / str(first)).exists()
        assert not (settings.uploads_dir / str(first)).exists()
        assert client.get(f"/api/videos/{first}").status_code == 404


def test_cancel_endpoint(settings, clip) -> None:
    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=25)) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)
        assert client.post(f"/api/videos/{video_id}/cancel").status_code == 200
        release.set()
        run = wait_status(client, video_id)
        assert run["status"] == "cancelled"
        assert client.get(f"/api/videos/{video_id}/video").status_code == 404


def test_test_page_is_served_and_offline(settings) -> None:
    with make_client(settings) as client:
        response = client.get("/legacy/")
        assert response.status_code == 200 and "text/html" in response.headers["content-type"]
        for element_id in ("upload-form", "progress", "live-numbers", "events", "player", "runs"):
            assert f'id="{element_id}"' in response.text
        assert "http://" not in response.text and "https://" not in response.text
        assert client.get("/api/health").status_code == 200  # the static mount does not shadow the API


def test_launcher_help_exits_zero() -> None:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "api", "--help"], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0 and "--port" in result.stdout and "--reload" in result.stdout


def test_import_does_not_load_torch() -> None:
    code = "import sys, api.main; sys.exit(1 if 'torch' in sys.modules else 0)"
    root = Path(__file__).resolve().parents[1]
    assert subprocess.run([sys.executable, "-c", code], cwd=root).returncode == 0


@pytest.mark.integration
def test_real_yolo_upload_of_cad_clip(tmp_path: Path) -> None:
    """Default tracker factory (real YOLO11n) processes 60 frames of a local CAD clip."""
    pytest.importorskip("ultralytics")
    repo = Path(__file__).resolve().parent.parent
    clip_path = repo / "datasets" / "raw" / "cad_seq25.mp4"
    if not clip_path.exists():
        pytest.skip("datasets/raw/cad_seq25.mp4 not available (local, gitignored dataset clip)")
    settings = ApiSettings(data_dir=tmp_path / "data", camera_dir=repo / "configs" / "cameras")
    with TestClient(create_app(settings)) as client:
        response = upload(client, clip_path, camera="cad_seq25", name="cad_seq25.mp4", max_frames="60")
        assert response.status_code == 202, response.text
        video_id = response.json()["video_id"]
        deadline = time.time() + 120
        run = client.get(f"/api/videos/{video_id}").json()
        while run["status"] not in ("completed", "failed", "cancelled") and time.time() < deadline:
            time.sleep(0.5)
            run = client.get(f"/api/videos/{video_id}").json()
        assert run["status"] == "completed", run
        assert run["frames_processed"] >= 59
        assert len(client.get(f"/api/videos/{video_id}/timeseries").json()) >= 1
        assert client.get(f"/api/videos/{video_id}/video").status_code == 200


def _repoint_outputs(settings, video_id: int, **outputs) -> None:
    conn = db.connect(settings.db_path)
    try:
        report = db.get_video(conn, video_id)["report"]
        report["outputs"].update(outputs)
        db.set_report(conn, video_id, report)
    finally:
        conn.close()


def test_report_paths_outside_data_dir_are_not_served(settings, clip, tmp_path, caplog) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET")
    with make_client(settings) as client:
        video_id = upload(client, clip).json()["video_id"]
        assert wait_status(client, video_id)["status"] == "completed"
        assert client.get(f"/api/videos/{video_id}/video").status_code == 200  # normal run
        assert client.get(f"/api/videos/{video_id}/timeseries.csv").status_code == 200
        for target in (str(outside), str(settings.outputs_dir / ".." / ".." / "outside.txt")):
            _repoint_outputs(settings, video_id, video=target, timeseries=target)
            with caplog.at_level("WARNING"):
                video = client.get(f"/api/videos/{video_id}/video")
                csv = client.get(f"/api/videos/{video_id}/timeseries.csv")
            assert video.status_code == 404 and video.json()["detail"] == "annotated video not available"
            assert csv.status_code == 404 and csv.json()["detail"] == "time series not available"
            assert "SECRET" not in video.text + csv.text
        assert "outside the data directory" in caplog.text
