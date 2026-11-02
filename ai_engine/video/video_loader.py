"""Sequential video reading and annotated video writing with OpenCV."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}


class VideoLoadError(RuntimeError):
    """Raised when a video path is invalid or cannot be decoded."""


@dataclass(frozen=True)
class VideoMetadata:
    """Basic properties of a video file as reported by OpenCV."""

    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_seconds: float

    def to_dict(self) -> dict:
        return asdict(self)


class VideoLoader:
    """Read frames one at a time from a recorded video.

    Frames are never loaded into memory all at once. Use as a context manager
    so the capture is released even when processing fails::

        with VideoLoader("video.mp4") as loader:
            for frame_index, frame in loader:
                ...
    """

    def __init__(self, video_path: str | Path) -> None:
        self.path = Path(video_path).expanduser()
        if not self.path.is_file():
            raise VideoLoadError(f"Video file does not exist: {self.path}")
        if self.path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
            raise VideoLoadError(
                f"Unsupported video type '{self.path.suffix}'. Use one of: {supported}"
            )

        self._capture = cv2.VideoCapture(str(self.path))
        if not self._capture.isOpened():
            self._capture.release()
            raise VideoLoadError(f"OpenCV could not open the video: {self.path}")

        self.metadata = self._read_metadata()
        if self.metadata.width <= 0 or self.metadata.height <= 0:
            self.release()
            raise VideoLoadError(f"Video has no readable frames: {self.path}")

    def _read_metadata(self) -> VideoMetadata:
        fps = float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(self._capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 else 0.0
        return VideoMetadata(
            path=str(self.path),
            width=int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=fps,
            frame_count=frame_count,
            duration_seconds=duration,
        )

    @property
    def is_open(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read(self) -> np.ndarray | None:
        """Return the next frame, or None when the video has ended."""
        if not self.is_open:
            return None
        success, frame = self._capture.read()
        return frame if success else None

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

    def __init__(
        self, output_path: str | Path, fps: float, frame_size: tuple[int, int]
    ) -> None:
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
            raise ValueError(
                f"Frame size {(width, height)} does not match writer size {self.frame_size}"
            )
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
