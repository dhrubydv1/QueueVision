"""Content checks for uploaded videos (the extension alone proves nothing).

Two steps: the container magic bytes must match the file extension, then OpenCV must
open the file, decode the first frame and report sane dimensions and duration. Failures
raise ``UploadRejected`` with a short message that is safe to show to the client.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import cv2

HEADER_BYTES = 16
_MP4_FAMILY = (".mp4", ".mov", ".m4v")
_MATROSKA_MAGIC = b"\x1a\x45\xdf\xa3"
NOT_A_VIDEO = "file is not a readable video"


class UploadRejected(ValueError):
    """The upload failed content validation; ``str(error)`` is client-safe."""


@dataclass(frozen=True)
class VideoProbe:
    """What OpenCV reported about an accepted file."""

    width: int
    height: int
    fps: float
    frame_count: int
    duration_s: float | None
    codec: str


def check_magic(header: bytes, ext: str) -> bool:
    """True when ``header`` (the first bytes of the file) looks like the container ``ext`` names."""
    ext = ext.lower()
    if ext in _MP4_FAMILY:
        return len(header) >= 8 and header[4:8] == b"ftyp"
    if ext == ".avi":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI "
    if ext == ".mkv":
        return header[:4] == _MATROSKA_MAGIC
    return False  # a family we do not know is rejected, never waved through


def _fourcc(code: float) -> str:
    value = int(code or 0)
    text = "".join(chr((value >> (8 * i)) & 0xFF) for i in range(4))
    return text if text.isprintable() and text.strip() else "unknown"


def probe_video(path: Path, *, max_seconds: float, max_pixels: int) -> VideoProbe:
    """Open ``path`` with OpenCV, decode one frame and enforce the duration and frame-size limits."""
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise UploadRejected(NOT_A_VIDEO)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise UploadRejected(NOT_A_VIDEO)
        height, width = int(frame.shape[0]), int(frame.shape[1])
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        codec = _fourcc(capture.get(cv2.CAP_PROP_FOURCC))
    finally:
        capture.release()
    if width <= 0 or height <= 0:
        raise UploadRejected(NOT_A_VIDEO)
    if width * height > max_pixels:
        raise UploadRejected(f"video frame too large: limit {max_pixels} pixels")
    duration = None
    if math.isfinite(fps) and fps > 0 and count > 0:
        duration = count / fps
        if duration > max_seconds:
            raise UploadRejected(f"video too long: limit {max_seconds:g} s")
    return VideoProbe(width, height, fps, count, duration, codec)


def validate_upload(path: Path, ext: str, *, max_seconds: float, max_pixels: int) -> VideoProbe:
    """Full validation of a stored upload; raises ``UploadRejected``."""
    with open(path, "rb") as handle:
        header = handle.read(HEADER_BYTES)
    if not check_magic(header, ext):
        raise UploadRejected(NOT_A_VIDEO)
    return probe_video(path, max_seconds=max_seconds, max_pixels=max_pixels)


MAX_NAME_CHARS = 255


def sanitize_filename(name: str) -> str:
    """Display-safe original filename: basename only, no control characters, NFC, at most 255 chars.

    The stored file never uses this name (it is ``video<id><ext>``); this is only what the
    database keeps and the page shows.
    """
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    name = name.strip()
    if name in ("", ".", ".."):
        return "upload"
    return name[:MAX_NAME_CHARS]
