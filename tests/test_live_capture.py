"""Live sources: the shared latest-frame reader and the looped clip (no camera, no model)."""

from __future__ import annotations

import itertools
import threading
from pathlib import Path

import numpy as np
import pytest

import api.jobs
from ai_engine import run_messages
from ai_engine.live.capture import (
    LiveSourceError,
    LoopedClipSource,
    RtspSource,
    WebcamSource,
    open_source,
)
from ai_engine.queue.camera_config import LiveSettings
from tests.conftest import write_synthetic_video


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeCapture:
    """Yields ``frames`` frames immediately, then blocks until released."""

    def __init__(self, frames: int, opened: bool = True) -> None:
        self.frames, self.opened = frames, opened
        self.delivered = 0
        self.consumed = threading.Event()
        self.released = threading.Event()

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        if self.delivered < self.frames:
            self.delivered += 1
            if self.delivered == self.frames:
                self.consumed.set()
            return True, np.full((4, 4, 3), self.delivered, dtype=np.uint8)
        self.released.wait(timeout=5)
        return False, None

    def release(self) -> None:
        self.released.set()


def test_messages_moved_unchanged():
    assert api.jobs.progress_message is run_messages.progress_message
    assert api.jobs.event_message is run_messages.event_message
    assert api.jobs._timeseries_row is run_messages.timeseries_row
    assert api.jobs.SCHEMA_VERSION is run_messages.SCHEMA_VERSION


def test_looped_clip_time_increases_and_counts_loops(tmp_path: Path):
    clip = write_synthetic_video(tmp_path / "clip.mp4", frames=10)
    clock = FakeClock()
    source = LoopedClipSource(clip, clock=clock)
    source.open()
    times = []
    try:
        for _ in range(26):
            frame, captured = source.read_latest(0.0)
            assert frame.shape[2] == 3
            times.append(captured)
            clock.now += 0.1
        assert source.loop_count == 2
    finally:
        source.close()
    assert all(b > a for a, b in itertools.pairwise(times))
    assert source.source_kind == "clip"


def test_latest_frame_only():
    capture = FakeCapture(5)
    source = WebcamSource(0, capture_factory=lambda target: capture)
    source.open()
    try:
        assert capture.consumed.wait(timeout=5)
        frame, _ = source.read_latest(1.0)
        assert int(frame[0, 0, 0]) == 5
        assert source.read_latest(0.05) is None  # nothing newer
    finally:
        source.close()


def test_closed_capture_is_camera_unavailable():
    source = WebcamSource(0, capture_factory=lambda target: FakeCapture(0, opened=False))
    with pytest.raises(LiveSourceError) as error:
        source.open()
    assert error.value.reason == "camera_unavailable"
    assert str(error.value) == "camera_unavailable"


def test_open_source_rtsp_never_leaks_url():
    url = "rtsp://user:secret@10.0.0.5/stream"
    live = LiveSettings(source="rtsp", rtsp_url_env="RTSP_URL_CAM1")
    with pytest.raises(LiveSourceError) as missing:
        open_source(live, {})
    assert missing.value.reason == "camera_unavailable"

    source = RtspSource(url, capture_factory=lambda target: FakeCapture(0, opened=False))
    assert url not in repr(source)
    with pytest.raises(LiveSourceError) as error:
        source.open()
    assert url not in str(error.value) and url not in repr(error.value)


def test_open_source_picks_clip(tmp_path: Path):
    clip = write_synthetic_video(tmp_path / "clip.mp4", frames=3)
    source = open_source(LiveSettings(source="clip", clip=str(clip)), {})
    try:
        assert source.source_kind == "clip"
    finally:
        source.close()
