"""Loopback ingest routes for live workers, and the JobManager upload hold (torch-free)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import db
from api.main import create_app
from tests.conftest import WalkerTracker
from tests.test_api import WAIT, clip, make_client, upload, wait_status  # noqa: F401
from tests.test_api import settings as settings

TOKEN = "tok-1"
HEADERS = {"X-Worker-Token": TOKEN}
LOOPBACK = ("127.0.0.1", 50000)


class StubLive:
    def __init__(self) -> None:
        self.beats: list[tuple] = []
        self.finished_ids: list[int] = []

    def verify(self, video_id: int, token: str) -> bool:
        return token == TOKEN and video_id in (1, 2)

    def heartbeat(self, video_id, frames_processed, processing_fps) -> None:
        self.beats.append((video_id, frames_processed, processing_fps))

    def finished(self, video_id: int) -> None:
        self.finished_ids.append(video_id)


def join_event(person: str = "p1") -> dict:
    return {
        "schema_version": "1.0",
        "event_type": "queue_join",
        "timestamp_s": 1.0,
        "detected_s": 1.2,
        "frame_index": 10,
        "person_key": person,
    }


def service_event(person: str = "p1") -> dict:
    return {
        "schema_version": "1.0",
        "event_type": "service_start",
        "timestamp_s": 9.0,
        "detected_s": 9.2,
        "frame_index": 90,
        "person_key": person,
        "track_ids": [3],
        "join_s": 1.0,
        "wait_s": 8.0,
    }


def congestion_event() -> dict:
    return {
        "schema_version": "1.0",
        "event_type": "congestion_start",
        "timestamp_s": 5.0,
        "detected_s": 5.0,
        "frame_index": 50,
        "condition_since_s": 3.0,
        "reasons": ["queue_length"],
        "queue_length": 6,
        "predicted_wait_s": 120.0,
    }


def batch_body(events=(), seq=None, progress=None) -> dict:
    body = {
        "events": list(events),
        "timeseries": [{"time_s": 1.0, "frame": 10}],
        "progress": progress,
        "frames_processed": 10,
        "captured_at_unix": None,
    }
    if seq is not None:
        body["seq"] = seq
    return body


@pytest.fixture
def app(settings):
    application = create_app(settings)
    application.state.live = StubLive()
    return application


@pytest.fixture
def client(app):
    with TestClient(app, client=LOOPBACK) as test_client:  # TestClient's default host is not loopback
        yield test_client


def make_run(settings, *, kind="live", status="processing") -> int:
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        video_id = db.create_video(conn, "cam", "walker", {}, kind=kind)
        if status != "queued":
            db.set_status(conn, video_id, status)
    finally:
        conn.close()
    return video_id


def stored(settings, table: str, video_id: int) -> int:
    conn = db.connect(settings.db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (video_id,)).fetchone()[0]
    finally:
        conn.close()


def test_ingest_requires_token(settings, client) -> None:
    video_id = make_run(settings)
    for route, kwargs in (
        ("batch", {"json": batch_body()}),
        ("heartbeat", {"json": {"frames_processed": 1, "processing_fps": 1.0}}),
        ("final", {"json": {"status": "completed", "error": None, "report": None}}),
        ("preview", {"content": b"\xff\xd8"}),
    ):
        url = f"/api/ingest/{video_id}/{route}"
        assert client.post(url, **kwargs).status_code == 401
        assert client.post(url, headers={"X-Worker-Token": "wrong"}, **kwargs).status_code == 401
    assert stored(settings, "events", video_id) == 0


def test_ingest_without_supervisor_refused(settings) -> None:
    video_id = make_run(settings)
    with TestClient(create_app(settings), client=LOOPBACK) as bare:
        assert bare.app.state.live is None
        assert (
            bare.post(f"/api/ingest/{video_id}/batch", headers=HEADERS, json=batch_body()).status_code == 401
        )


def test_ingest_wrong_run_token_refused(settings, client) -> None:
    make_run(settings)
    other = make_run(settings)
    third = make_run(settings)  # the stub only knows runs 1 and 2
    assert other == 2 and third == 3
    assert client.post(f"/api/ingest/{third}/batch", headers=HEADERS, json=batch_body()).status_code == 401
    assert client.post(f"/api/ingest/{other}/batch", headers=HEADERS, json=batch_body()).status_code == 204


def test_ingest_unknown_run_404(client) -> None:
    assert client.post("/api/ingest/99/batch", headers=HEADERS, json=batch_body()).status_code == 404


def test_ingest_non_loopback_refused(settings, app) -> None:
    video_id = make_run(settings)
    with TestClient(app, client=("203.0.113.5", 1)) as remote:
        response = remote.post(f"/api/ingest/{video_id}/batch", headers=HEADERS, json=batch_body())
    assert response.status_code == 403
    assert stored(settings, "timeseries", video_id) == 0


def test_ingest_upload_run_refused(settings, client) -> None:
    video_id = make_run(settings, kind="upload")
    assert client.post(f"/api/ingest/{video_id}/batch", headers=HEADERS, json=batch_body()).status_code == 409


def test_ingest_finished_run_refused(settings, client) -> None:
    video_id = make_run(settings, status="completed")
    assert client.post(f"/api/ingest/{video_id}/batch", headers=HEADERS, json=batch_body()).status_code == 409


def test_ingest_preview_size_limit(settings, client) -> None:
    video_id = make_run(settings)
    url = f"/api/ingest/{video_id}/preview"
    assert client.post(url, headers=HEADERS, content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.app.state.jobs.preview.latest(video_id) is None
    assert client.post(url, headers=HEADERS, content=b"\xff\xd8jpeg").status_code == 204
    assert client.app.state.jobs.preview.latest(video_id) == (1, b"\xff\xd8jpeg")
    assert client.post(url, headers=HEADERS, content=b"\xff\xd8two").status_code == 204
    assert client.app.state.jobs.preview.latest(video_id)[0] == 2
    assert client.get(f"/api/videos/{video_id}/preview.jpg").content == b"\xff\xd8two"


def test_batch_stores_events_waits_alerts_timeseries(settings, client) -> None:
    video_id = make_run(settings)
    body = batch_body([join_event(), service_event(), congestion_event()])
    assert client.post(f"/api/ingest/{video_id}/batch", headers=HEADERS, json=body).status_code == 204
    assert stored(settings, "events", video_id) == 3
    assert stored(settings, "waits", video_id) == 1
    assert stored(settings, "alerts", video_id) == 1
    assert stored(settings, "timeseries", video_id) == 1
    assert client.get(f"/api/videos/{video_id}").json()["frames_processed"] == 10
    assert client.app.state.live.beats == [(video_id, 10, 0.0)]


def test_batch_duplicate_seq_ignored(settings, client) -> None:
    video_id = make_run(settings)
    url = f"/api/ingest/{video_id}/batch"
    body = batch_body([join_event()], seq=1)
    assert client.post(url, headers=HEADERS, json=body).status_code == 204
    assert client.post(url, headers=HEADERS, json=body).status_code == 204
    assert stored(settings, "events", video_id) == 1
    assert stored(settings, "timeseries", video_id) == 1
    assert len(client.app.state.live.beats) == 1  # the duplicate did nothing at all
    assert client.post(url, headers=HEADERS, json=batch_body([join_event("p2")], seq=2)).status_code == 204
    assert stored(settings, "events", video_id) == 2
    assert client.post(url, headers=HEADERS, json=batch_body([join_event("p3")])).status_code == 204  # no seq
    assert stored(settings, "events", video_id) == 3


def test_batch_publishes_same_shapes_as_upload(settings, clip) -> None:  # noqa: F811
    app = create_app(settings, WalkerTracker)
    app.state.live = StubLive()
    with TestClient(app, client=LOOPBACK) as client:
        upload_id = upload(client, clip).json()["video_id"]
        reference: dict[str, dict] = {}
        with client.websocket_connect(f"/ws/videos/{upload_id}") as ws:
            while "status" not in reference or reference["status"]["status"] not in ("completed", "failed"):
                message = ws.receive_json()
                reference[message["type"]] = message
                if message["type"] == "status" and message["status"] in ("completed", "failed"):
                    break
        assert {"event", "progress"} <= reference.keys()
        wait_status(client, upload_id)
        live_id = make_run(settings)
        progress = {**reference["progress"], "video_id": live_id}
        with client.websocket_connect(f"/ws/videos/{live_id}") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            body = batch_body([reference["event"]["event"]], progress=progress)
            assert client.post(f"/api/ingest/{live_id}/batch", headers=HEADERS, json=body).status_code == 204
            got = {m["type"]: m for m in (ws.receive_json(), ws.receive_json())}
        assert set(got["event"]) == set(reference["event"]) and got["event"]["video_id"] == live_id
        assert set(got["progress"]) == set(reference["progress"])
        assert app.state.jobs.live_state(live_id) == progress


def test_final_sets_status_and_report(settings, client) -> None:
    video_id = make_run(settings)
    body = {"status": "completed", "error": None, "report": {"stopped_by": "operator"}}
    with client.websocket_connect(f"/ws/videos/{video_id}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        assert client.post(f"/api/ingest/{video_id}/final", headers=HEADERS, json=body).status_code == 204
        message = ws.receive_json()
    assert message["type"] == "status" and message["status"] == "completed"
    run = client.get(f"/api/videos/{video_id}").json()
    assert run["status"] == "completed" and run["finished_at"]
    assert client.get(f"/api/videos/{video_id}/report").json() == {"stopped_by": "operator"}
    assert client.app.state.live.finished_ids == [video_id]
    assert client.app.state.jobs.live_state(video_id) is None
    assert client.post(f"/api/ingest/{video_id}/final", headers=HEADERS, json=body).status_code == 409


def test_final_failed_without_report(settings, client, monkeypatch) -> None:
    video_id = make_run(settings)
    calls: list[int] = []
    monkeypatch.setattr(db, "set_report", lambda *a, **k: calls.append(1))
    body = {"status": "failed", "error": "camera_unavailable", "report": None}
    assert client.post(f"/api/ingest/{video_id}/final", headers=HEADERS, json=body).status_code == 204
    run = client.get(f"/api/videos/{video_id}").json()
    assert run["status"] == "failed" and run["error"] == "camera_unavailable"
    assert calls == []


def test_ingest_not_in_openapi(client) -> None:
    assert not [path for path in client.get("/openapi.json").json()["paths"] if "/ingest/" in path]


def test_uploads_held_while_held(settings, clip) -> None:  # noqa: F811
    with make_client(settings) as client:
        jobs = client.app.state.jobs
        assert jobs.hold_uploads() is True
        video_id = upload(client, clip).json()["video_id"]
        time.sleep(0.5)
        assert client.get(f"/api/videos/{video_id}").json()["status"] == "queued"
        jobs.release_uploads()
        assert wait_status(client, video_id)["status"] == "completed"


def test_hold_refused_while_job_current(settings, clip) -> None:  # noqa: F811
    import threading

    from tests.test_api_jobs import GatedTracker

    reached, release = threading.Event(), threading.Event()
    with make_client(settings, lambda: GatedTracker(reached, release, block_at=2)) as client:
        jobs = client.app.state.jobs
        upload(client, clip)
        assert reached.wait(WAIT)
        assert jobs.hold_uploads() is False
        release.set()
        assert jobs.wait_idle(WAIT)
        assert jobs.hold_uploads() is True
        jobs.release_uploads()
