"""In-memory live preview of a processing run: head-blurred JPEG frames, never written to disk."""

from __future__ import annotations

import threading
from collections.abc import Sequence

import cv2
import numpy as np

from ai_engine.detection.types import PersonDetection
from ai_engine.visualization.privacy import blur_heads

PREVIEW_MAX_WIDTH = 960
PREVIEW_JPEG_QUALITY = 70
PREVIEW_MIN_INTERVAL_S = 0.2
MJPEG_BOUNDARY = "frame"


def render_preview(image: np.ndarray, people: Sequence[PersonDetection]) -> np.ndarray:
    """Blur heads (always, whatever the run's blur option) on a copy, then downscale to 960 px wide."""
    frame = blur_heads(image.copy(), people)
    height, width = frame.shape[:2]
    if width > PREVIEW_MAX_WIDTH:
        new_height = max(1, round(height * PREVIEW_MAX_WIDTH / width))
        frame = cv2.resize(frame, (PREVIEW_MAX_WIDTH, new_height), interpolation=cv2.INTER_AREA)
    return frame


def mjpeg_part(jpeg: bytes) -> bytes:
    header = f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
    return header.encode("ascii") + jpeg + b"\r\n"


class PreviewStore:
    """Latest encoded preview per run, rate-limited; safe to call from the worker and request threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frames: dict[int, tuple[int, bytes]] = {}
        self._last_encode: dict[int, float] = {}

    def offer(self, video_id: int, image: np.ndarray, people: Sequence[PersonDetection], now: float) -> bool:
        """Encode this frame unless one was encoded less than 0.2 s ago; True when it was encoded."""
        with self._lock:
            last = self._last_encode.get(video_id)
            if last is not None and now - last < PREVIEW_MIN_INTERVAL_S:
                return False
            self._last_encode[video_id] = now
        ok, buffer = cv2.imencode(
            ".jpg", render_preview(image, people), [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY]
        )
        if not ok:
            return False
        with self._lock:
            if video_id not in self._last_encode:  # dropped while encoding
                return False
            sequence = self._frames.get(video_id, (0, b""))[0] + 1
            self._frames[video_id] = (sequence, buffer.tobytes())
        return True

    def latest(self, video_id: int) -> tuple[int, bytes] | None:
        with self._lock:
            return self._frames.get(video_id)

    def drop(self, video_id: int) -> None:
        with self._lock:
            self._frames.pop(video_id, None)
            self._last_encode.pop(video_id, None)
