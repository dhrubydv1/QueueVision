"""Sequential video reading and annotated video writing with OpenCV."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}
FPS_SAMPLE_FRAMES = 30


class VideoLoadError(RuntimeError):
    """Raised when a video path is invalid or cannot be decoded."""


@dataclass(frozen=True)
class VideoMetadata:
    """Basic properties of a video source.

    ``frame_count`` is 0 when unknown (live cameras). ``fps_source`` is
    ``"container"`` when the FPS came from the file/driver and
    ``"measured"`` when it had to be measured from live frame arrival.
    """

    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float
    is_live: bool = False
    fps_source: str = "container"

    def to_dict(self) -> dict:
        return asdict(self)


def parse_source(source: str | Path | int) -> str | Path | int:
    """Turn ``"0"`` into camera index ``0``; leave file paths alone."""
    if isinstance(source, str) and source.strip().isdigit():
        return int(source.strip())
    return source


def source_name(source: str | Path | int) -> str:
    """Short name used for output files: the file stem or ``camera<N>``."""
    source = parse_source(source)
    return f"camera{source}" if isinstance(source, int) else Path(source).stem


class VideoLoader:
    """Read frames one at a time from a recorded video or a camera.

    Frames are never loaded into memory all at once. Use as a context manager
    so the capture is released even when processing fails::

        with VideoLoader("video.mp4") as loader:
            for frame_index, frame in loader:
                ...

    ``capture_factory`` exists so live sources can be tested with a mocked
    ``cv2.VideoCapture``.
    """

    def __init__(
        self,
        source: str | Path | int,
        capture_factory: Callable[[Any], Any] = cv2.VideoCapture,
    ) -> None:
        source = parse_source(source)
        self.is_live = isinstance(source, int)
        self._buffered: deque[np.ndarray] = deque()

        if self.is_live:
            self.path = source
            target: Any = source
        else:
            self.path = Path(source).expanduser()
            if not self.path.is_file():
                raise VideoLoadError(f"Video file does not exist: {self.path}")
            if self.path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
                supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
                raise VideoLoadError(f"Unsupported video type '{self.path.suffix}'. Use one of: {supported}")
            target = str(self.path)

        self._capture = capture_factory(target)
        if not self._capture.isOpened():
            self._capture.release()
            raise VideoLoadError(f"OpenCV could not open the video source: {self.path}")

        self.metadata = self._read_metadata()
        if self.metadata.width <= 0 or self.metadata.height <= 0:
            self.release()
            raise VideoLoadError(f"Video has no readable frames: {self.path}")

    def _read_metadata(self) -> VideoMetadata:
        fps = float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = 0 if self.is_live else int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps_source = "container"
        if fps <= 0 and self.is_live:
            fps = self._measure_fps()
            fps_source = "measured"
        return VideoMetadata(
            path=str(self.path),
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration_seconds=frame_count / fps if fps > 0 and frame_count > 0 else 0.0,
            is_live=self.is_live,
            fps_source=fps_source,
        )

    def _measure_fps(self, sample_frames: int = FPS_SAMPLE_FRAMES) -> float:
        """Measure live frame rate; sampled frames are buffered, not lost."""
        start = time.perf_counter()
        for _ in range(sample_frames):
            success, frame = self._capture.read()
            if not success:
                break
            self._buffered.append(frame)
        elapsed = time.perf_counter() - start
        if len(self._buffered) < 2 or elapsed <= 0:
            return 0.0
        return len(self._buffered) / elapsed

    @property
    def is_open(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read(self) -> np.ndarray | None:
        """Return the next frame, or None when the video has ended."""
        if self._buffered:
            return self._buffered.popleft()
        if not self.is_open:
            return None
        success, frame = self._capture.read()
        return frame if success else None

    def skip(self) -> bool:
        """Advance one frame without decoding it. Returns False at the end."""
        if self._buffered:
            self._buffered.popleft()
            return True
        if not self.is_open:
            return False
        return bool(self._capture.grab())

    def __iter__(self) -> Iterator[tuple[int, np.ndarray]]:
        """Yield ``(frame_index, frame)`` pairs, starting at index 0."""
        frame_index = 0
        while True:
            frame = self.read()
            if frame is None:
                return
            yield frame_index, frame
            frame_index += 1

    def release(self) -> None:
        """Release the capture. Safe to call more than once."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> VideoLoader:
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


class VideoWriter:
    """Write BGR frames to an MP4 file; usable as a context manager."""

    def __init__(self, output_path: str | Path, fps: float, frame_size: tuple[int, int]) -> None:
        self.path = Path(output_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.frame_size = frame_size
        self.frames_written = 0
        # Fall back to 30 FPS when the source reports none, so the file is valid.
        self._writer = cv2.VideoWriter(
            str(self.path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps if fps > 0 else 30.0,
            frame_size,
        )
        if not self._writer.isOpened():
            self._writer.release()
            raise VideoLoadError(f"Could not create the output video: {self.path}")

    def write(self, frame: np.ndarray) -> None:
        height, width = frame.shape[:2]
        if (width, height) != self.frame_size:
            raise ValueError(f"Frame size {(width, height)} does not match writer size {self.frame_size}")
        self._writer.write(frame)
        self.frames_written += 1

    def release(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def __enter__(self) -> VideoWriter:
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
