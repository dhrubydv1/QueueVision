"""Real-time pacing option: stored on the run, paces to video time, never changes results."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from api import db
from api.jobs import JobManager
from tests.conftest import WalkerTracker, write_synthetic_video
from tests.test_api import clip, make_client, settings, upload, wait_status  # noqa: F401

WAIT = 15.0
FPS = 10.0


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.waits: list[float] = []

    def now(self) -> float:
        return self.t

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.t += seconds


def run_job(
    cfg,
    tracker_factory,
    clock: FakeClock,
    realtime: bool,
    frames: int = 60,
) -> tuple[int, dict]:
    jobs = JobManager(cfg, tracker_factory, clock=clock.now, wait=clock.wait)
    jobs.start()  # starting first: start() fails rows left queued by an earlier server
    conn = db.connect(cfg.db_path)
    options = {"frame_stride": 1, "blur_heads": True, "max_frames": None, "realtime": realtime}
    video_id = db.create_video(conn, "clip.mp4", "walker", options)
    folder = cfg.uploads_dir / str(video_id)
    folder.mkdir(parents=True)
    write_synthetic_video(folder / f"video{video_id}.mp4", frames=frames)
    try:
        jobs.submit(video_id)
        assert jobs.wait_idle(WAIT)
    finally:
        jobs.stop(timeout=WAIT)
    record = db.get_video(conn, video_id)
    assert record["status"] == "completed", record["error"]
    result = {
        "record": record,
        "events": [{k: v for k, v in r.items() if k != "video_id"} for r in db.list_events(conn, video_id)],
        "series": [
            {k: v for k, v in r.items() if k != "video_id"} for r in db.list_timeseries(conn, video_id)
        ],
        "waits": [{k: v for k, v in r.items() if k != "video_id"} for r in db.list_waits(conn, video_id)],
    }
    conn.close()
    return video_id, result


def test_realtime_option_stored_and_exposed(settings, clip) -> None:  # noqa: F811
    with make_client(settings) as client:
        response = upload(client, clip, realtime="true")
        assert response.status_code == 202, response.text
        assert response.json()["options"]["realtime"] is True
        video_id = response.json()["video_id"]
        assert client.get(f"/api/videos/{video_id}").json()["options"]["realtime"] is True
        default = upload(client, clip)
        assert default.json()["options"]["realtime"] is False
        assert upload(client, clip, realtime="maybe").status_code == 422


def test_old_run_without_realtime_option(settings) -> None:  # noqa: F811
    with make_client(settings) as client:
        conn = db.connect(settings.db_path)
        try:
            video_id = db.create_video(
                conn, "a.mp4", "walker", {"frame_stride": 1, "blur_heads": True, "max_frames": None}
            )
        finally:
            conn.close()
        listing = client.get("/api/videos")
        assert listing.status_code == 200
        assert [r["options"]["realtime"] for r in listing.json() if r["video_id"] == video_id] == [False]
        detail = client.get(f"/api/videos/{video_id}")
        assert detail.status_code == 200 and detail.json()["options"]["realtime"] is False


def test_realtime_outputs_identical(settings) -> None:  # noqa: F811
    id_a, plain = run_job(settings, WalkerTracker, FakeClock(), realtime=False)
    id_b, paced = run_job(settings, WalkerTracker, FakeClock(), realtime=True)
    outputs_a = settings.outputs_dir / str(id_a)
    outputs_b = settings.outputs_dir / str(id_b)

    def texts(root: Path, video_id: int) -> dict[str, bytes]:  # file names carry the upload's stem
        return {
            str(p.relative_to(root)).replace(f"video{video_id}", "video"): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and p.suffix in (".csv", ".jsonl")
        }

    files_a, files_b = texts(outputs_a, id_a), texts(outputs_b, id_b)
    assert {Path(n).suffix for n in files_a} >= {".csv", ".jsonl"}
    assert files_a == files_b
    assert plain["events"] == paced["events"] and plain["events"]
    assert plain["series"] == paced["series"] and plain["series"]
    assert plain["waits"] == paced["waits"] and plain["waits"]


def test_realtime_paces_to_video_time(settings) -> None:  # noqa: F811
    plain_clock, paced_clock = FakeClock(), FakeClock()
    run_job(settings, WalkerTracker, plain_clock, realtime=False)
    run_job(settings, WalkerTracker, paced_clock, realtime=True)
    assert plain_clock.waits == []
    last_timestamp = 59 / FPS
    assert sum(paced_clock.waits) == pytest.approx(last_timestamp, abs=1 / FPS)
    assert all(w > 0 for w in paced_clock.waits)


def test_realtime_never_accumulates_lag(settings) -> None:  # noqa: F811
    clock = FakeClock()

    class SlowTracker(WalkerTracker):
        def update(self, frame):
            clock.t += 2 / FPS  # two frame-durations of work per frame
            return super().update(frame)

    run_job(settings, SlowTracker, clock, realtime=True)
    assert clock.waits == []  # behind schedule from the first delay on: wait is never called


def test_realtime_wait_interrupted_by_cancel(settings, tmp_path) -> None:  # noqa: F811
    import cv2
    import numpy as np

    slow_clip = tmp_path / "slow.mp4"  # 0.2 fps: each pacing wait is 5 s
    writer = cv2.VideoWriter(str(slow_clip), cv2.VideoWriter_fourcc(*"mp4v"), 0.2, (64, 48))
    for _ in range(6):
        writer.write(np.full((48, 64, 3), 40, dtype=np.uint8))
    writer.release()
    with make_client(settings) as client:
        video_id = upload(client, slow_clip, realtime="true").json()["video_id"]
        deadline = time.time() + WAIT
        while client.get(f"/api/videos/{video_id}").json()["frames_processed"] < 1:
            assert time.time() < deadline, "run never handled a frame"
            time.sleep(0.02)
        assert client.get(f"/api/videos/{video_id}").json()["status"] == "processing"
        started = time.monotonic()
        assert client.post(f"/api/videos/{video_id}/cancel").status_code in (200, 202)
        run = wait_status(client, video_id)
        assert run["status"] == "cancelled"
        assert time.monotonic() - started < 1.0
