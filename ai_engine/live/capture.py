"""Live frame sources: a webcam, an RTSP stream, or a recorded clip replayed as a stand-in camera.

A source hands out only the newest frame (``read_latest``): a slow consumer never builds a backlog,
it just skips frames. Error messages are the bare reason string; a stream URL never appears in an
exception, a log line or ``repr``.
"""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np

from ai_engine.queue.camera_config import LiveSettings

CAMERA_UNAVAILABLE = "camera_unavailable"
STREAM_ENDED = "stream_ended"
MAX_CONSECUTIVE_READ_FAILURES = 30
READ_RETRY_DELAY_S = 0.05


class LiveSourceError(RuntimeError):
    """A live source failed. ``reason`` is ``camera_unavailable`` or ``stream_ended`` (also the message)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LiveSource(Protocol):
    source_kind: str
    loop_count: int  # 0 for anything but a clip

    def open(self) -> None: ...

    def read_latest(self, timeout_s: float) -> tuple[np.ndarray, float] | None:
        """Newest frame not handed out yet and its ``time.monotonic()`` capture time; None on timeout."""
        ...

    def close(self) -> None: ...


class _ThreadedCaptureSource:
    """Shared reader: a thread reads the capture as fast as it delivers and keeps only the newest frame."""

    source_kind = "unknown"

    def __init__(
        self,
        target: Any,
        capture_factory: Callable[[Any], Any] = cv2.VideoCapture,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._target = target  # may hold a URL: never logged, never in repr or errors
        self._capture_factory = capture_factory
        self._clock = clock
        self.loop_count = 0
        self._capture: Any = None
        self._thread: threading.Thread | None = None
        self._condition = threading.Condition()
        self._latest: tuple[np.ndarray, float] | None = None
        self._fresh = False
        self._delivered_any = False
        self._ended = False
        self._closing = False

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"

    def open(self) -> None:
        try:
            capture = self._capture_factory(self._target)
            opened = bool(capture.isOpened())
        except Exception:
            raise LiveSourceError(CAMERA_UNAVAILABLE) from None
        if not opened:
            capture.release()
            raise LiveSourceError(CAMERA_UNAVAILABLE)
        self._capture = capture
        self._thread = threading.Thread(target=self._run, name=f"{self.source_kind}-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        failures = 0
        while not self._closing:
            try:
                ok, frame = self._capture.read()
            except Exception:
                ok, frame = False, None
            if self._closing:
                return
            if ok and frame is not None:
                failures = 0
                with self._condition:
                    self._latest = (frame, self._clock())
                    self._fresh = True
                    self._delivered_any = True
                    self._condition.notify_all()
                continue
            failures += 1
            if failures >= MAX_CONSECUTIVE_READ_FAILURES:
                break
            time.sleep(READ_RETRY_DELAY_S)
        with self._condition:
            self._ended = True
            self._condition.notify_all()

    def read_latest(self, timeout_s: float) -> tuple[np.ndarray, float] | None:
        with self._condition:
            if not self._condition.wait_for(lambda: self._fresh or self._ended, timeout=max(timeout_s, 0.0)):
                return None
            if self._fresh:
                self._fresh = False
                return self._latest
            # The reader gave up: nothing ever arrived (no permission, wrong device) or the stream stopped.
            raise LiveSourceError(STREAM_ENDED if self._delivered_any else CAMERA_UNAVAILABLE)

    def close(self) -> None:
        self._closing = True
        thread, capture = self._thread, self._capture
        self._thread = self._capture = None
        if thread is not None:
            thread.join(timeout=2.0)
        if capture is not None:
            with contextlib.suppress(Exception):
                capture.release()


class WebcamSource(_ThreadedCaptureSource):
    source_kind = "webcam"

    def __init__(self, index: int, **kwargs: Any) -> None:
        super().__init__(index, **kwargs)


class RtspSource(_ThreadedCaptureSource):
    source_kind = "rtsp"

    def __init__(self, url: str, **kwargs: Any) -> None:
        super().__init__(url, **kwargs)


class LoopedClipSource:
    """A recorded clip played back in real time, looping forever.

    The frame served is ``floor(elapsed * fps) % frame_count``, so time keeps moving when the
    consumer is slow. ``loop_count`` grows at each wrap; capture times always increase.
    """

    source_kind = "clip"

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self._path = Path(path)
        self._clock = clock
        self.loop_count = 0
        self._capture: Any = None
        self._fps = 0.0
        self._frame_count = 0
        self._started: float | None = None
        self._position = -1  # index of the frame in ``_current``
        self._current: np.ndarray | None = None

    def open(self) -> None:
        capture = cv2.VideoCapture(str(self._path))
        fps = capture.get(cv2.CAP_PROP_FPS) if capture.isOpened() else 0.0
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) if capture.isOpened() else 0
        if frames < 1:
            capture.release()
            raise LiveSourceError(CAMERA_UNAVAILABLE)
        self._capture = capture
        self._fps = fps if fps and fps > 0 and math.isfinite(fps) else 10.0
        self._frame_count = frames
        self._started = self._clock()

    def _frame_at(self, index: int) -> np.ndarray:
        if index != self._position or self._current is None:
            if index != self._position + 1:
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = self._capture.read()
            if not ok or frame is None:
                raise LiveSourceError(STREAM_ENDED)
            self._position, self._current = index, frame
        return self._current.copy()

    def read_latest(self, timeout_s: float) -> tuple[np.ndarray, float] | None:
        if self._capture is None or self._started is None:
            raise LiveSourceError(CAMERA_UNAVAILABLE)
        now = self._clock()
        absolute = math.floor((now - self._started) * self._fps)
        self.loop_count = absolute // self._frame_count
        return self._frame_at(absolute % self._frame_count), now

    def close(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.release()


def open_source(live: LiveSettings, env: Mapping[str, str]) -> LiveSource:
    """Open the source named by ``live``; the RTSP URL is read from ``env[live.rtsp_url_env]``."""
    source: LiveSource
    if live.source == "webcam":
        source = WebcamSource(live.webcam_index)
    elif live.source == "rtsp":
        url = env.get(live.rtsp_url_env or "")
        if not url:
            raise LiveSourceError(CAMERA_UNAVAILABLE)
        source = RtspSource(url)
    else:
        source = LoopedClipSource(live.clip_path())
    source.open()
    return source
