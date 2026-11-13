"""Job manager tests: fake tracker, tiny generated clips, temporary data dir (torch-free)."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from api import db
from api.jobs import SUBSCRIBER_QUEUE_SIZE, JobManager, progress_message
from api.settings import ApiSettings
from tests.conftest import FakeTracker, WalkerTracker, write_synthetic_video

CAMERA_YAML = """\
camera_id: walker
queue_zone: [[0.0, 0.0], [0.6, 0.0], [0.6, 1.0], [0.0, 1.0]]
service_line: [[0.8, 0.0], [0.8, 1.0]]
membership:
  min_dwell_seconds: 1.0
  max_speed_heights_per_second: 0.5
  exit_grace_seconds: 0.3
alerts:
  max_queue_length: 0.5
  min_duration_seconds: 1.0
  cooldown_seconds: 5.0
"""
WAIT = 10.0


class GatedTracker(WalkerTracker):
    """Blocks inside ``update`` at a chosen frame until the test releases it."""

    def __init__(self, reached: threading.Event, release: threading.Event, block_at: int = 3) -> None:
        super().__init__()
        self.reached, self.release, self.block_at = reached, release, block_at

    def update(self, frame):
        if self.updates == self.block_at:
            self.reached.set()
            assert self.release.wait(WAIT)
        return super().update(frame)


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    camera_dir = tmp_path / "cameras"
    camera_dir.mkdir()
    (camera_dir / "walker.yaml").write_text(CAMERA_YAML)
    return ApiSettings(data_dir=tmp_path / "data", camera_dir=camera_dir)


@pytest.fixture
def conn(settings):
    connection = db.connect(settings.db_path)
    db.init_db(connection)
    yield connection
    connection.close()


def make_manager(settings, factory=WalkerTracker):
    manager = JobManager(settings, factory)
    manager.start()
    return manager


def add_video(settings, conn, *, junk: bool = False, options: dict | None = None) -> int:
    video_id = db.create_video(conn, "clip.mp4", "walker", options or {"frame_stride": 1})
    folder = settings.uploads_dir / str(video_id)
    folder.mkdir(parents=True)
    path = folder / f"video{video_id}.mp4"
    if junk:
        path.write_bytes(b"this is not a video" * 50)
    else:
        write_synthetic_video(path, frames=60)
    return video_id


@pytest.fixture
def manager(settings, conn):
    created = []

    def build(factory=WalkerTracker):
        created.append(make_manager(settings, factory))
        return created[-1]

    yield build
    for item in created:
        item.stop(timeout=WAIT)


def test_job_completes_and_persists(settings, conn, manager) -> None:
    jobs = manager()
    video_id = add_video(settings, conn)
    jobs.submit(video_id)
    assert jobs.wait_idle(WAIT)
    record = db.get_video(conn, video_id)
    assert record["status"] == "completed"
    assert record["frames_processed"] == 60
    assert record["report"]["stopped_early"] is False
    assert record["fps"] == pytest.approx(10.0)
    assert len(db.list_timeseries(conn, video_id)) == 6
    events_file = Path(record["report"]["outputs"]["event_log"])
    expected = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert expected
    stored = db.list_events(conn, video_id)  # ordered by timestamp, the file by emission
    assert sorted(stored, key=json.dumps) == sorted(expected, key=json.dumps)
    assert db.list_waits(conn, video_id)
    assert not jobs.is_active(video_id)


def test_subscriber_receives_progress_events_and_final_status(settings, conn, manager) -> None:
    jobs = manager()
    video_id = add_video(settings, conn)

    async def scenario() -> list[dict]:
        queue, unsubscribe = jobs.subscribe(video_id, asyncio.get_running_loop())
        jobs.submit(video_id)
        messages = []
        while True:
            message = await asyncio.wait_for(queue.get(), WAIT)
            messages.append(message)
            if message["type"] == "status" and message["status"] in ("completed", "failed", "cancelled"):
                break
        unsubscribe()
        return messages

    messages = asyncio.run(scenario())
    assert all(m["schema_version"] == "1.0" for m in messages)
    kinds = [m["type"] for m in messages]
    assert kinds[-1] == "status" and messages[-1]["status"] == "completed"
    assert {"progress", "event"} <= set(kinds)
    assert kinds.count("progress") == 6
    first = next(m for m in messages if m["type"] == "progress")
    assert first["video_id"] == video_id and first["frames_total"] == 60
    assert jobs.live_state(video_id)["type"] == "progress"


def test_unreadable_video_fails_and_worker_continues(settings, conn, manager) -> None:
    jobs = manager()
    bad = add_video(settings, conn, junk=True)
    good = add_video(settings, conn)
    jobs.submit(bad)
    jobs.submit(good)
    assert jobs.wait_idle(WAIT)
    failed = db.get_video(conn, bad)
    assert failed["status"] == "failed" and failed["error"]
    assert db.get_video(conn, good)["status"] == "completed"


def test_second_upload_waits_for_first(settings, conn, manager) -> None:
    reached, release = threading.Event(), threading.Event()
    jobs = manager(lambda: GatedTracker(reached, release))
    first = add_video(settings, conn)
    second = add_video(settings, conn)
    jobs.submit(first)
    assert reached.wait(WAIT)
    jobs.submit(second)
    assert db.get_video(conn, first)["status"] == "processing"
    assert db.get_video(conn, second)["status"] == "queued"
    assert jobs.queue_depth == 1 and jobs.is_active(second)
    release.set()
    assert jobs.wait_idle(WAIT)
    assert db.get_video(conn, first)["status"] == "completed"
    assert db.get_video(conn, second)["status"] == "completed"


def test_cancel_queued_removes_it(settings, conn, manager) -> None:
    reached, release = threading.Event(), threading.Event()
    jobs = manager(lambda: GatedTracker(reached, release))
    first = add_video(settings, conn)
    second = add_video(settings, conn)
    jobs.submit(first)
    assert reached.wait(WAIT)
    jobs.submit(second)
    assert jobs.cancel(second) is True
    assert db.get_video(conn, second)["status"] == "cancelled"
    assert jobs.queue_depth == 0 and not jobs.is_active(second)
    assert jobs.cancel(second) is False
    release.set()
    assert jobs.wait_idle(WAIT)
    assert db.get_video(conn, first)["status"] == "completed"
    assert db.get_video(conn, second)["status"] == "cancelled"


def test_cancel_processing_marks_cancelled(settings, conn, manager) -> None:
    reached, release = threading.Event(), threading.Event()
    jobs = manager(lambda: GatedTracker(reached, release, block_at=25))
    video_id = add_video(settings, conn)
    jobs.submit(video_id)
    assert reached.wait(WAIT)
    assert jobs.cancel(video_id) is True
    release.set()
    assert jobs.wait_idle(WAIT)
    record = db.get_video(conn, video_id)
    assert record["status"] == "cancelled"
    assert 0 < record["frames_processed"] < 60
    assert len(db.list_timeseries(conn, video_id)) >= 2
    assert record["report"]["stopped_early"] is True


def test_stop_cancels_current_job(settings, conn, manager) -> None:
    reached, release = threading.Event(), threading.Event()
    jobs = manager(lambda: GatedTracker(reached, release, block_at=25))
    video_id = add_video(settings, conn)
    jobs.submit(video_id)
    assert reached.wait(WAIT)
    stopper = threading.Thread(target=jobs.stop, kwargs={"timeout": WAIT})
    stopper.start()
    assert jobs._stop.wait(WAIT)  # the stop flag is set while the job is still blocked
    release.set()
    stopper.join(WAIT)
    assert not stopper.is_alive()
    record = db.get_video(conn, video_id)
    assert record["status"] == "cancelled"
    assert 0 < record["frames_processed"] < 60


def test_frame_stride_progress_counts_source_frames(settings, conn, manager) -> None:
    jobs = manager(FakeTracker)
    video_id = add_video(settings, conn, options={"frame_stride": 2})

    async def scenario() -> list[dict]:
        queue, unsubscribe = jobs.subscribe(video_id, asyncio.get_running_loop())
        jobs.submit(video_id)
        out = []
        while True:
            message = await asyncio.wait_for(queue.get(), WAIT)
            out.append(message)
            if message["type"] == "status" and message["status"] == "completed":
                break
        unsubscribe()
        return out

    messages = asyncio.run(scenario())
    last = [m for m in messages if m["type"] == "progress"][-1]
    record = db.get_video(conn, video_id)
    assert record["frames_total"] == 60
    assert record["frames_processed"] == 59  # last processed source frame index 58, plus one
    assert last["frames_processed"] <= 59 and last["frames_processed"] > 30
    assert last["percent"] == pytest.approx(100.0 * last["frames_processed"] / 60, abs=0.1)


def test_flush_is_one_transaction(settings, conn, manager, monkeypatch) -> None:
    commits = []
    real_connect = db.connect

    class Spy:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *exc):
            commits.append(1)
            return self._inner.__exit__(*exc)

    monkeypatch.setattr(db, "connect", lambda path: Spy(real_connect(path)))
    jobs = manager(FakeTracker)
    video_id = add_video(settings, conn)
    commits.clear()
    jobs.submit(video_id)
    assert jobs.wait_idle(WAIT)
    # 6 per-second flushes + final flush + processing/set_media/report/completed: far fewer than 3 per flush
    flushes = len(db.list_timeseries(conn, video_id)) + 1
    assert len(commits) <= flushes + 6


def test_full_queue_never_drops_event_or_status() -> None:
    from api.jobs import _put, event_message

    async def scenario() -> None:
        queue: asyncio.Queue = asyncio.Queue()
        for _ in range(SUBSCRIBER_QUEUE_SIZE):
            _put(queue, progress_message(1, _update()))
        _put(queue, progress_message(1, _update()))
        assert queue.qsize() == SUBSCRIBER_QUEUE_SIZE
        _put(queue, event_message(1, {"event_type": "x"}))
        _put(queue, {"type": "status", "status": "completed"})
        assert queue.qsize() == SUBSCRIBER_QUEUE_SIZE + 2

    asyncio.run(scenario())


def test_worker_survives_db_failure_while_recording_failure(settings, conn, manager, monkeypatch) -> None:
    real_progress, real_status = db.set_progress, db.set_status
    state = {"progress_failed": False}

    def bad_progress(*args, **kwargs):
        if not state["progress_failed"]:
            state["progress_failed"] = True
            raise RuntimeError("disk error")
        return real_progress(*args, **kwargs)

    def bad_status(conn_, video_id, status, error=None, **kwargs):
        if status == "failed":
            raise RuntimeError("disk error again")
        return real_status(conn_, video_id, status, error, **kwargs)

    monkeypatch.setattr(db, "set_progress", bad_progress)
    monkeypatch.setattr(db, "set_status", bad_status)
    jobs = manager(FakeTracker)
    first = add_video(settings, conn)
    second = add_video(settings, conn)
    jobs.submit(first)
    jobs.submit(second)
    assert jobs.wait_idle(WAIT)
    assert db.get_video(conn, second)["status"] == "completed"


def test_start_raises_when_database_unavailable(tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    bad = ApiSettings(data_dir=blocker / "data", camera_dir=tmp_path)
    jobs = JobManager(bad, FakeTracker)
    with pytest.raises(OSError):
        jobs.start()


def test_tracker_factory_error_fails_job(settings, conn, manager) -> None:
    def broken():
        raise RuntimeError("no model")

    jobs = manager(broken)
    video_id = add_video(settings, conn)
    jobs.submit(video_id)
    assert jobs.wait_idle(WAIT)
    record = db.get_video(conn, video_id)
    # the exception text stays in the log, not in the record
    assert record["status"] == "failed" and record["error"] == "processing failed (RuntimeError)"


def test_restart_marks_interrupted(settings, conn, manager) -> None:
    video_id = db.create_video(conn, "old.mp4", "walker", {})
    db.set_status(conn, video_id, "processing")
    manager()
    record = db.get_video(conn, video_id)
    assert record["status"] == "failed"
    assert record["error"] == "interrupted by server restart"


def test_full_subscriber_queue_drops_progress_but_keeps_status(settings, conn, manager) -> None:
    jobs = manager(FakeTracker)
    video_id = add_video(settings, conn)

    async def scenario() -> dict:
        queue, unsubscribe = jobs.subscribe(video_id, asyncio.get_running_loop())
        for _ in range(SUBSCRIBER_QUEUE_SIZE):
            queue.put_nowait(progress_message(video_id, _update()))
        jobs.submit(video_id)
        assert await asyncio.get_running_loop().run_in_executor(None, jobs.wait_idle, WAIT)
        await asyncio.sleep(0.05)  # let call_soon_threadsafe callbacks run
        last = None
        while not queue.empty():
            last = queue.get_nowait()
        unsubscribe()
        return last

    last = asyncio.run(scenario())
    assert last["type"] == "status" and last["status"] == "completed"


def _update():
    from ai_engine.run_tracking import FrameUpdate

    return FrameUpdate(
        frame_index=0, frames_total=10, timestamp_s=0.0, people_in_frame=0, processing_fps=1.0, queue=None
    )
