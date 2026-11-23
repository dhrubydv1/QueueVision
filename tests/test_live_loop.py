"""run_live with a fake source, fake tracker, fake clock and sleep (no model, no camera)."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ai_engine.live.capture import LiveSourceError
from ai_engine.live.loop import LiveOptions, run_live
from ai_engine.queue.camera_config import load_camera_config
from ai_engine.run_tracking import FrameUpdate
from tests.conftest import FakeTracker


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class ReadySource:
    """Always has a frame; capture time is the fake clock."""

    source_kind = "clip"

    def __init__(self, clock: FakeClock, loop_every: float | None = None) -> None:
        self.clock, self.loop_every = clock, loop_every
        self.loop_count = 0

    def open(self) -> None:
        pass

    def read_latest(self, timeout_s: float):
        if self.loop_every:
            self.loop_count = int(self.clock.now // self.loop_every)
        return np.full((128, 160, 3), 40, dtype=np.uint8), self.clock.now

    def close(self) -> None:
        pass


class SilentSource(ReadySource):
    def read_latest(self, timeout_s: float):
        self.clock.now += timeout_s
        return None


@pytest.fixture
def camera():
    return load_camera_config("configs/cameras/cad_seq25.yaml")


def run(tmp_path, camera, clock, source=None, stop_at=1.0, **option_kwargs):
    updates: list[FrameUpdate] = []
    options = LiveOptions(output_dir=tmp_path, **option_kwargs)
    report = run_live(
        source or ReadySource(clock),
        FakeTracker(),
        camera,
        options,
        on_frame=updates.append,
        should_stop=lambda: clock.now >= stop_at,
        clock=clock,
        sleep=clock.sleep,
    )
    return report, updates


def test_run_live_paces_and_numbers(tmp_path, camera):
    clock = FakeClock()
    _, updates = run(tmp_path, camera, clock, process_fps=10.0)
    assert len(updates) in (10, 11)
    assert [u.frame_index for u in updates] == list(range(len(updates)))
    assert all(u.frames_total is None for u in updates)
    assert all(u.image_privacy == "frame" and u.image is not None for u in updates)
    assert updates[1].timestamp_s == pytest.approx(0.1)


def test_run_live_writes_only_csv_and_metrics(tmp_path, camera):
    clock = FakeClock()
    run(tmp_path, camera, clock)
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == [
        f"metrics/live{camera.camera_id}_metrics.json",
        f"timeseries/live{camera.camera_id}_queue.csv",
    ]
    assert json.loads((tmp_path / files[0]).read_text())["kind"] == "live"


def test_run_live_report_fields(tmp_path, camera):
    clock = FakeClock()
    source = ReadySource(clock, loop_every=0.4)
    report, _ = run(tmp_path, camera, clock, source=source)
    assert report["kind"] == "live"
    assert report["source_kind"] == "clip"
    assert report["stopped_by"] == "operator"
    assert [entry["loop"] for entry in report["loops"]] == [1, 2]
    assert report["outputs"]["timeseries"].endswith(f"live{camera.camera_id}_queue.csv")
    assert report["outputs"]["metrics"].endswith(f"live{camera.camera_id}_metrics.json")
    assert report["queue"]["camera_id"] == camera.camera_id
    assert report["configuration"]["process_fps"] == 10.0
    assert report["configuration"]["preview_privacy_mode"] == "frame"
    assert report["duration_s"] == pytest.approx(1.0, abs=0.2)


def test_max_session_stop(tmp_path, camera):
    clock = FakeClock()
    report, updates = run(tmp_path, camera, clock, stop_at=1e9, max_session_s=0.5)
    assert report["stopped_by"] == "max_session"
    assert 5 <= len(updates) <= 6


def test_no_first_frame_fails_with_camera_unavailable(tmp_path, camera):
    clock = FakeClock()
    with pytest.raises(LiveSourceError) as error:
        run(tmp_path, camera, clock, source=SilentSource(clock), stop_at=1e9)
    assert error.value.reason == "camera_unavailable"
    assert str(error.value) == "camera_unavailable"
    assert clock.now == pytest.approx(10.0)


def test_stop_before_first_frame_allows_zero_frames(tmp_path, camera):
    clock = FakeClock()
    report, updates = run(tmp_path, camera, clock, source=SilentSource(clock), stop_at=0.0)
    assert updates == [] and report["stopped_by"] == "operator"
    assert report["queue"]["camera_id"] == camera.camera_id
