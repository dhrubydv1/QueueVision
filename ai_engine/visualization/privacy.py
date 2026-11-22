"""Privacy filters applied to frames before they are saved."""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np

from ai_engine.detection.types import PersonDetection

DEFAULT_HEAD_FRACTION = 0.2
PRIVACY_MODES = ("none", "boxes", "frame")


def pixelate_frame(frame: np.ndarray, block: int | None = None) -> np.ndarray:
    """Pixelate the whole frame in place, so nobody is recognisable, detected or not.

    Downscales by ``block`` px with INTER_AREA and scales back up with INTER_NEAREST. The default
    block is ``max(8, round(width / 40))`` px.
    """
    height, width = frame.shape[:2]
    block = max(8, round(width / 40)) if block is None else block
    if block < 1:
        raise ValueError("block must be >= 1")
    small = cv2.resize(frame, (max(width // block, 1), max(height // block, 1)), interpolation=cv2.INTER_AREA)
    frame[:] = cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST)
    return frame


def blur_heads(
    frame: np.ndarray,
    people: Sequence[PersonDetection],
    fraction: float = DEFAULT_HEAD_FRACTION,
) -> np.ndarray:
    """Gaussian-blur the top ``fraction`` of every person box, in place.

    This approximates the head region without an extra face model. It is a
    best-effort filter: heads outside the top of a box (e.g. people bending
    or partially cropped boxes) may stay visible.
    """
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    height, width = frame.shape[:2]
    for person in people:
        x1, y1, x2, y2 = person.bbox
        x1, x2 = max(x1, 0), min(x2, width)
        y1 = max(y1, 0)
        head_bottom = min(y1 + max(round((y2 - y1) * fraction), 1), height)
        if x2 <= x1 or head_bottom <= y1:
            continue
        region = frame[y1:head_bottom, x1:x2]
        # Kernel scales with box width so distant and near people are both obscured.
        kernel = max(3, (x2 - x1) // 3) | 1
        frame[y1:head_bottom, x1:x2] = cv2.GaussianBlur(region, (kernel, kernel), 0)
    return frame
