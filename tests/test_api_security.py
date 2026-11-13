"""Security hardening tests: upload content validation, limits, ids, error disclosure, startup cleanup."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import db
from api.__main__ import is_loopback, warn_if_exposed
from api.main import MAX_VIDEO_ID, create_app, remove_partial_uploads
from api.settings import ApiSettings, SettingsError
from api.upload_validation import check_magic, sanitize_filename
from tests.conftest import WalkerTracker, write_synthetic_video
from tests.test_api import make_client, upload, wait_status
from tests.test_api_jobs import CAMERA_YAML, WAIT, GatedTracker

BIG_ID = 99999999999999999999


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


def no_leftovers(settings: ApiSettings) -> None:
    conn = db.connect(settings.db_path)
    try:
        assert db.list_videos(conn) == []
    finally:
        conn.close()
    assert [p for p in settings.uploads_dir.rglob("*") if p.is_file()] == []
    assert list(settings.uploads_dir.glob("*")) == []  # no per-run folder either


# --- content validation --------------------------------------------------------


def test_valid_upload_accepted_without_part_file(settings, clip) -> None:
    with make_client(settings) as client:
        response = upload(client, clip)
        assert response.status_code == 202, response.text
        video_id = response.json()["video_id"]
        assert wait_status(client, video_id)["status"] == "completed"
    files = [p.name for p in settings.uploads_dir.rglob("*") if p.is_file()]
    assert files == [f"video{video_id}.mp4"]


@pytest.mark.parametrize("ext", [".mp4", ".avi", ".mov", ".mkv", ".m4v"])
def test_non_video_bytes_rejected_and_cleaned_up(settings, tmp_path, ext) -> None:
    junk = tmp_path / f"junk{ext}"
    junk.write_bytes(b"this is definitely not a video file at all" * 50)
    with make_client(settings) as client:
        response = upload(client, junk, name=f"junk{ext}")
        assert response.status_code == 400
        assert response.json()["detail"] == "file is not a readable video"
        assert client.get("/api/videos").json() == []
    no_leftovers(settings)


def test_right_magic_but_undecodable_rejected(settings, tmp_path) -> None:
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"\x00\x00\x00\x1cftypisom" + b"\x00" * 200)
    with make_client(settings) as client:
        response = upload(client, fake, name="fake.mp4")
        assert response.status_code == 400 and response.json()["detail"] == "file is not a readable video"
    no_leftovers(settings)


def test_wrong_magic_for_extension_rejected(settings, clip) -> None:
    with make_client(settings) as client:
        for name in ("clip.avi", "clip.mkv"):  # a real MP4 labelled as another container
            response = upload(client, clip, name=name)
            assert response.status_code == 400, name
            assert response.json()["detail"] == "file is not a readable video"
    no_leftovers(settings)


def test_check_magic_families() -> None:
    mp4 = b"\x00\x00\x00\x18ftypmp42"
    avi = b"RIFF\x00\x00\x00\x00AVI LIST"
    mkv = b"\x1a\x45\xdf\xa3\x01\x00"
    for ext in (".mp4", ".mov", ".m4v", ".MP4"):
        assert check_magic(mp4, ext) and not check_magic(avi, ext)
    assert check_magic(avi, ".avi") and not check_magic(mp4, ".avi")
    assert check_magic(mkv, ".mkv") and not check_magic(mp4, ".mkv")
    assert not check_magic(mp4, ".webm") and not check_magic(b"", ".mp4")  # unknown family is rejected


def test_too_long_video_rejected(settings, clip) -> None:
    short = replace(settings, max_video_seconds=2.0)  # the clip is 6 s (60 frames at 10 fps)
    with make_client(short) as client:
        response = upload(client, clip)
        assert response.status_code == 400
        assert response.json()["detail"] == "video too long: limit 2 s"
    no_leftovers(short)


def test_oversized_frame_rejected(settings, clip) -> None:
    small = replace(settings, max_frame_pixels=100 * 100)  # the clip is 160x128
    with make_client(small) as client:
        response = upload(client, clip)
        assert response.status_code == 400 and "frame too large" in response.json()["detail"]
    no_leftovers(small)


def test_filename_sanitized_and_storage_name_generated(settings, clip) -> None:
    with make_client(settings) as client:
        response = upload(client, clip, name="../../etc/passwd.mp4")
        assert response.status_code == 202, response.text
        run = response.json()
        assert run["original_name"] == "passwd.mp4"
        wait_status(client, run["video_id"])
    stored = [p for p in settings.uploads_dir.rglob("*") if p.is_file()]
    assert [p.name for p in stored] == [f"video{run['video_id']}.mp4"]
    assert stored[0].parent == settings.uploads_dir / str(run["video_id"])


def test_sanitize_filename_cases() -> None:
    assert sanitize_filename("..\\..\\win\\evil.mp4") == "evil.mp4"
    assert sanitize_filename("/") == "upload" and sanitize_filename("..") == "upload"
    assert sanitize_filename("") == "upload" and sanitize_filename("\x00\x01") == "upload"
    assert sanitize_filename("é.mp4") == "é.mp4"  # NFC
    long = sanitize_filename("a" * 400 + ".mp4")
    assert len(long) == 255


# --- partial files -------------------------------------------------------------


def test_no_part_file_after_interrupted_write(settings, clip, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk exploded at /secret/path")

    monkeypatch.setattr("api.main.os.replace", boom)
    with make_client(settings) as client, pytest.raises(OSError):
        upload(client, clip)  # the TestClient re-raises unexpected errors
    no_leftovers(settings)


def test_startup_removes_only_stale_part_files(settings) -> None:
    folder = settings.uploads_dir / "7"
    folder.mkdir(parents=True)
    stale = folder / "video7.mp4.part"
    stale.write_bytes(b"half")
    keep = folder / "video7.mp4"
    keep.write_bytes(b"whole")
    other = settings.uploads_dir / "notes.txt"
    other.write_text("keep")
    with make_client(settings):
        pass
    assert not stale.exists() and keep.exists() and other.exists()
    assert remove_partial_uploads(settings.uploads_dir) == 0


def test_startup_logs_removed_count(settings, caplog) -> None:
    folder = settings.uploads_dir / "3"
    folder.mkdir(parents=True)
    (folder / "video3.mp4.part").write_bytes(b"x")
    with caplog.at_level(logging.INFO, logger="queuevision.api"), make_client(settings):
        pass
    assert any("Removed 1 leftover partial upload" in r.getMessage() for r in caplog.records)


# --- queue limit ---------------------------------------------------------------


def test_queue_limit_returns_429_before_reading_body(settings, clip) -> None:
    limited = replace(settings, max_queued_jobs=1)
    reached, release = threading.Event(), threading.Event()
    with make_client(limited, lambda: GatedTracker(reached, release, block_at=2)) as client:
        first = upload(client, clip).json()["video_id"]
        assert reached.wait(WAIT)  # first run is processing; the queue itself is empty
        second = upload(client, clip)
        assert second.status_code == 202  # depth 0 < 1: one waiting run is allowed
        assert client.get("/api/health").json()["queue_depth"] == 1
        third = upload(client, clip)
        assert third.status_code == 429
        assert third.json() == {"detail": "too many queued runs; try again later"}
        assert len(client.get("/api/videos").json()) == 2  # the rejected upload left nothing
        release.set()
        assert wait_status(client, first)["status"] == "completed"
        assert wait_status(client, second.json()["video_id"])["status"] == "completed"


def test_queue_limit_checked_before_body_is_parsed(settings) -> None:
    limited = replace(settings, max_queued_jobs=0)  # always full: any upload is refused
    with make_client(limited) as client:
        response = client.post("/api/videos", content=b"not even multipart", headers={"content-type": "x/y"})
        assert response.status_code == 429  # a body that would be a 422 is never looked at


def test_oversize_content_length_rejected_early(settings, clip) -> None:
    tiny = replace(settings, max_upload_bytes=10)
    with make_client(tiny) as client:
        assert upload(client, clip).status_code == 413
    no_leftovers(tiny)


# --- settings ------------------------------------------------------------------


def env_settings(monkeypatch, **env) -> ApiSettings:
    for name in ("MAX_QUEUED_JOBS", "MAX_VIDEO_SECONDS", "MAX_FRAME_PIXELS"):
        monkeypatch.delenv(f"QUEUEVISION_{name}", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(f"QUEUEVISION_{name}", value)
    return ApiSettings.from_env()


def test_new_settings_defaults_and_env(monkeypatch) -> None:
    default = env_settings(monkeypatch)
    assert (default.max_queued_jobs, default.max_video_seconds, default.max_frame_pixels) == (
        5,
        1800.0,
        3840 * 2160,
    )
    custom = env_settings(monkeypatch, MAX_QUEUED_JOBS="2", MAX_VIDEO_SECONDS="12.5", MAX_FRAME_PIXELS="1000")
    assert (custom.max_queued_jobs, custom.max_video_seconds, custom.max_frame_pixels) == (2, 12.5, 1000)


@pytest.mark.parametrize("name", ["MAX_QUEUED_JOBS", "MAX_VIDEO_SECONDS", "MAX_FRAME_PIXELS"])
@pytest.mark.parametrize("bad", ["0", "-3", "abc", "nan", "inf"])
def test_bad_new_settings_rejected(monkeypatch, name, bad) -> None:
    with pytest.raises(SettingsError, match=f"QUEUEVISION_{name}"):
        env_settings(monkeypatch, **{name: bad})


def test_fractional_queue_jobs_rejected(monkeypatch) -> None:
    with pytest.raises(SettingsError):
        env_settings(monkeypatch, MAX_QUEUED_JOBS="1.5")


# --- ids -----------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [BIG_ID, MAX_VIDEO_ID + 1, 0, -1])
def test_out_of_range_video_id_is_422_on_every_route(settings, bad_id) -> None:
    with make_client(settings) as client:
        for method, suffix in [
            ("get", ""),
            ("get", "/events"),
            ("get", "/waits"),
            ("get", "/alerts"),
            ("get", "/timeseries"),
            ("get", "/report"),
            ("get", "/video"),
            ("post", "/cancel"),
            ("delete", ""),
        ]:
            response = getattr(client, method)(f"/api/videos/{bad_id}{suffix}")
            assert response.status_code == 422, (method, suffix, response.status_code)


def test_max_video_id_is_a_clean_404(settings) -> None:
    with make_client(settings) as client:
        assert client.get(f"/api/videos/{MAX_VIDEO_ID}").status_code == 404


def test_websocket_out_of_range_id_closes_cleanly(settings) -> None:
    with make_client(settings) as client:
        with pytest.raises(WebSocketDisconnect) as info, client.websocket_connect(f"/ws/videos/{BIG_ID}"):
            pass
        assert info.value.code == 1008  # policy violation: rejected before accept
        with client.websocket_connect(f"/ws/videos/{MAX_VIDEO_ID}") as ws:  # in range, simply not found
            assert ws.receive()["code"] == 4404
        assert client.get("/api/health").status_code == 200  # the server is still fine


# --- error disclosure ----------------------------------------------------------


def test_job_failure_message_is_generic(settings, clip, caplog) -> None:
    class Leaky:
        def __init__(self) -> None:
            raise RuntimeError("cannot open /Users/secret/models/yolo.pt")

    with caplog.at_level(logging.ERROR, logger="queuevision.api"), make_client(settings, Leaky) as client:
        video_id = upload(client, clip).json()["video_id"]
        run = wait_status(client, video_id)
        assert run["status"] == "failed" and run["error"] == "processing failed (RuntimeError)"
        with client.websocket_connect(f"/ws/videos/{video_id}") as ws:
            snapshot, status = ws.receive_json(), ws.receive_json()
        assert "/Users" not in str(snapshot) + str(status)
    assert "/Users/secret/models/yolo.pt" in caplog.text  # details stay in the server log


def test_unknown_and_broken_camera_messages_have_no_details(settings, clip) -> None:
    with make_client(settings) as client:
        missing = upload(client, clip, camera="nope")
        broken = upload(client, clip, camera="broken")
        assert missing.status_code == broken.status_code == 400
        assert missing.json()["detail"] == "unknown or invalid camera 'nope'"
        assert broken.json()["detail"] == "unknown or invalid camera 'broken'"
        assert str(settings.camera_dir) not in missing.text + broken.text
        cams = {c["id"]: c for c in client.get("/api/cameras").json()}
        assert cams["broken"]["error"] == "invalid camera config"
        assert str(settings.camera_dir) not in str(cams)


def test_unexpected_exception_returns_generic_500(settings, caplog) -> None:
    app = create_app(settings, WalkerTracker)

    def boom() -> None:
        raise RuntimeError("secret /etc/internal detail")

    def http() -> None:
        raise HTTPException(418, "teapot")

    app.add_api_route("/api/_boom", boom)
    app.add_api_route("/api/_http", http)
    app.router.routes[:] = app.router.routes[-2:] + app.router.routes[:-2]  # ahead of the static mount

    with TestClient(app, raise_server_exceptions=False) as client:
        with caplog.at_level(logging.ERROR, logger="queuevision.api"):
            response = client.get("/api/_boom")
        assert response.status_code == 500 and response.json() == {"detail": "internal server error"}
        assert "secret /etc/internal detail" in caplog.text and "Traceback" in caplog.text
        assert client.get("/api/_http").status_code == 418  # HTTPException is unaffected
        assert client.get("/api/videos/abc").status_code == 422  # so are validation errors
        assert client.get("/api/videos/1").status_code == 404


# --- hosts, CORS, access -------------------------------------------------------


def test_loopback_detection_and_warning(caplog) -> None:
    assert all(is_loopback(h) for h in ("127.0.0.1", "::1", "localhost", "127.0.0.2"))
    assert not any(is_loopback(h) for h in ("0.0.0.0", "192.168.1.5", "example.com", "::"))
    with caplog.at_level(logging.WARNING, logger="queuevision.api"):
        assert warn_if_exposed("127.0.0.1") is False and not caplog.records
        assert warn_if_exposed("0.0.0.0") is True
    assert "NO authentication" in caplog.text


def test_no_cors_headers(settings) -> None:
    with make_client(settings) as client:
        response = client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in response.headers
        preflight = client.options(
            "/api/videos",
            headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in preflight.headers


def test_frame_probe_helpers_use_real_video(tmp_path) -> None:
    # a 1-frame clip is readable (duration is computed from the count when fps is known)
    path = tmp_path / "one.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 48))
    writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    from api.upload_validation import probe_video

    probe = probe_video(path, max_seconds=5, max_pixels=10_000)
    assert (probe.width, probe.height, probe.frame_count) == (64, 48, 1)
    assert probe.fps == 10.0 and probe.duration_s == pytest.approx(0.1)


# --- transactions --------------------------------------------------------------


def test_report_and_final_status_are_stored_atomically(settings, clip, monkeypatch) -> None:
    real_set_status = db.set_status

    def failing_final_status(conn, video_id, status, error=None, commit=True):
        if status == "completed":
            raise RuntimeError("disk full")
        real_set_status(conn, video_id, status, error, commit=commit)

    monkeypatch.setattr("api.jobs.db.set_status", failing_final_status)
    with make_client(settings) as client:
        video_id = upload(client, clip).json()["video_id"]
        run = wait_status(client, video_id)
        # the failed commit rolled the report back, so no completed-looking half record exists
        assert run["status"] == "failed" and run["summary"] is None
        assert client.get(f"/api/videos/{video_id}/report").status_code == 404


# --- WebSocket Origin check ------------------------------------------------------


def _completed_run(client, clip) -> int:
    video_id = upload(client, clip).json()["video_id"]
    wait_status(client, video_id)
    return video_id


def test_ws_origin_allowed_default(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = _completed_run(client, clip)
        with client.websocket_connect(
            f"/ws/videos/{video_id}", headers={"origin": "http://localhost:3000"}
        ) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_ws_origin_rejected(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = _completed_run(client, clip)
        with (
            pytest.raises(WebSocketDisconnect) as info,
            client.websocket_connect(f"/ws/videos/{video_id}", headers={"origin": "http://evil.example"}),
        ):
            pass
        assert info.value.code == 1008


def test_ws_no_origin_allowed(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = _completed_run(client, clip)
        with client.websocket_connect(f"/ws/videos/{video_id}") as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_default_origins_follow_host_and_port(settings) -> None:
    custom = replace(settings, port=9123)
    assert "http://127.0.0.1:9123" in custom.effective_allowed_origins()
    assert "http://localhost:9123" in custom.effective_allowed_origins()
    assert "http://localhost:3000" in custom.effective_allowed_origins()
    exposed = replace(settings, host="192.168.1.5", port=9123).effective_allowed_origins()
    assert "http://192.168.1.5:9123" in exposed and "http://localhost:9123" not in exposed


def test_allowed_origins_env_parsing(monkeypatch) -> None:
    monkeypatch.setenv("QUEUEVISION_ALLOWED_ORIGINS", " http://a:1/, ,http://b:2")
    allowed = ApiSettings.from_env().effective_allowed_origins()
    assert allowed == {"http://a:1", "http://b:2"}


@pytest.mark.parametrize("origin", ["", "null", "http://localhost:30000", "http://localhost.evil:3000"])
def test_ws_origin_edge_cases_rejected(settings, clip, origin) -> None:
    with make_client(settings) as client:
        video_id = _completed_run(client, clip)
        with (
            pytest.raises(WebSocketDisconnect) as info,
            client.websocket_connect(f"/ws/videos/{video_id}", headers={"origin": origin}),
        ):
            pass
        assert info.value.code == 1008


def test_ws_origin_case_insensitive(settings, clip) -> None:
    with make_client(settings) as client:
        video_id = _completed_run(client, clip)
        with client.websocket_connect(
            f"/ws/videos/{video_id}", headers={"origin": "HTTP://LocalHost:3000"}
        ) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_origin_config_normalised_and_ipv6(settings, monkeypatch) -> None:
    monkeypatch.setenv("QUEUEVISION_ALLOWED_ORIGINS", "HTTP://Example.COM:8080/")
    assert ApiSettings.from_env().effective_allowed_origins() == {"http://example.com:8080"}
    monkeypatch.setenv("QUEUEVISION_ALLOWED_ORIGINS", ",")
    assert ApiSettings.from_env().effective_allowed_origins() == frozenset()  # rejects every browser origin
    v6 = replace(settings, host="::1", port=9123).effective_allowed_origins()
    assert "http://[::1]:9123" in v6 and "http://::1:9123" not in v6
    assert not settings.origin_allowed("http://localhost:30000")
    assert settings.origin_allowed("http://LOCALHOST:3000")
