"""Re-encode annotated videos to browser-friendly H.264 with ffmpeg."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

LOGGER = logging.getLogger("queuevision")


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def original_copy_path(video_path: Path) -> Path:
    """Where ``--keep-mp4v`` keeps the original encoding."""
    return video_path.with_name(f"{video_path.stem}_mp4v.mp4")


def reencode_h264(video_path: Path, keep_original: bool = False) -> bool:
    """Re-encode ``video_path`` to H.264 in place; return True on success.

    With ``keep_original`` the mp4v file is kept at :func:`original_copy_path`.
    When ffmpeg is missing or fails, the mp4v file is left untouched and a
    warning is logged.
    """
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        LOGGER.warning("ffmpeg not found; keeping mp4v video %s", video_path)
        return False

    temporary = video_path.with_name(f"{video_path.stem}.h264.tmp.mp4")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-an",
        str(temporary),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not temporary.exists():
        temporary.unlink(missing_ok=True)
        LOGGER.warning("ffmpeg H.264 re-encode failed; keeping mp4v: %s", completed.stderr.strip())
        return False

    if keep_original:
        video_path.replace(original_copy_path(video_path))
    temporary.replace(video_path)
    return True
