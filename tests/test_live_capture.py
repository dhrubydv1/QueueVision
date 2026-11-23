"""Live sources: the shared latest-frame reader and the looped clip (no camera, no model)."""

from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path

import cv2
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
    quiet_opencv_logging,
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
            return True, np.full((4, 4, 3), self.delivered, dtype=np.uint8)
        self.consumed.set()  # the previous frame has been stored by now
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


class OverCountedClip:
    """Reports ``reported`` frames but only has ``real``; reading past the end fails."""

    def __init__(self, reported: int, real: int, fps: float = 10.0) -> None:
        self.reported, self.real, self.fps = reported, real, fps
        self.pos = 0

    def isOpened(self) -> bool:
        return True

    def get(self, prop):
        return {cv2.CAP_PROP_FPS: self.fps, cv2.CAP_PROP_FRAME_COUNT: self.reported}.get(prop, 0)

    def set(self, prop, value) -> bool:
        self.pos = int(value)
        return True

    def read(self):
        if self.pos >= self.real:
            return False, None
        self.pos += 1
        return True, np.zeros((4, 4, 3), dtype=np.uint8)

    def release(self) -> None:
        pass


def test_clip_wraps_when_reported_frame_count_is_too_high():
    clock = FakeClock()
    source = LoopedClipSource(
        Path("unused.mp4"), clock=clock, capture_factory=lambda p: OverCountedClip(10, 6)
    )
    source.open()
    times = []
    for _ in range(26):
        _, captured = source.read_latest(0.0)  # must not raise stream_ended
        times.append(captured)
        clock.now += 0.1
    assert source.loop_count == 4  # absolute frame 25 with 6 real frames
    assert all(b > a for a, b in itertools.pairwise(times))


def test_clip_with_no_readable_frame_is_stream_ended():
    source = LoopedClipSource(
        Path("x.mp4"), clock=FakeClock(), capture_factory=lambda p: OverCountedClip(10, 0)
    )
    source.open()
    with pytest.raises(LiveSourceError) as error:
        source.read_latest(0.0)
    assert error.value.reason == "stream_ended"


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


def test_close_stops_thread_promptly():
    capture = FakeCapture(2)
    source = WebcamSource(0, capture_factory=lambda target: capture)
    source.open()
    assert capture.consumed.wait(timeout=5)
    thread = source._thread
    started = time.monotonic()
    source.close()
    assert time.monotonic() - started < 1.0
    assert not thread.is_alive()


def test_rtsp_opens_with_ffmpeg_backend(monkeypatch):
    calls = []

    def fake_capture(*args):
        calls.append(args)
        return FakeCapture(0, opened=False)

    monkeypatch.setattr(cv2, "VideoCapture", fake_capture)
    with pytest.raises(LiveSourceError):
        RtspSource("rtsp://example/stream").open()
    assert calls == [("rtsp://example/stream", cv2.CAP_FFMPEG)]


def test_quiet_opencv_logging_callable():
    quiet_opencv_logging()


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
