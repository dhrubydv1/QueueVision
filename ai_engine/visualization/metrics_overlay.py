"""Draw tracked people and a live metrics panel onto video frames."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from ai_engine.detection.detector import PersonDetection

BOX_COLOR = (0, 200, 0)  # BGR green
TEXT_COLOR = (255, 255, 255)
PANEL_COLOR = (20, 20, 20)
BRAND_COLOR = (0, 200, 255)  # BGR amber
FONT = cv2.FONT_HERSHEY_SIMPLEX


@dataclass(frozen=True)
class OverlayStats:
    """Values shown in the metrics panel for one frame."""

    frame_number: int
    total_frames: int
    people_in_frame: int
    inference_fps: float
    processing_fps: float
    device: str


def draw_people(frame: np.ndarray, people: Sequence[PersonDetection]) -> np.ndarray:
    """Draw a green box and an ``ID n  0.87`` label for every person, in place."""
    for person in people:
        x1, y1, x2, y2 = person.bbox
        cv2.rectangle(frame, (x1, y1), (x2, y2), BOX_COLOR, 2)

        label = f"{person.confidence:.2f}"
        if person.track_id is not None:
            label = f"ID {person.track_id}  {label}"
        (text_width, text_height), baseline = cv2.getTextSize(label, FONT, 0.5, 1)
        label_top = max(y1 - text_height - baseline - 4, 0)
        cv2.rectangle(
            frame,
            (x1, label_top),
            (x1 + text_width + 6, label_top + text_height + baseline + 4),
            BOX_COLOR,
            -1,
        )
        cv2.putText(
            frame,
            label,
            (x1 + 3, label_top + text_height + 2),
            FONT,
            0.5,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )
    return frame


def draw_metrics_panel(
    frame: np.ndarray, stats: OverlayStats, opacity: float = 0.6
) -> np.ndarray:
    """Draw a semi-transparent panel with branding and live metrics, in place.

    "People in frame" is the number of tracked people visible in this frame,
    not a queue length; queue counting arrives in a later sprint.
    """
    total = f"/{stats.total_frames}" if stats.total_frames > 0 else ""
    lines = [
        f"People in frame: {stats.people_in_frame}",
        f"Inference FPS: {stats.inference_fps:.1f}",
        f"Processing FPS: {stats.processing_fps:.1f}",
        f"Frame: {stats.frame_number}{total}",
        f"Device: {stats.device.upper()}",
    ]
    line_height = 24
    panel_width = 260
    panel_height = 40 + line_height * len(lines) + 8

    x, y = 10, 10
    x2 = min(x + panel_width, frame.shape[1])
    y2 = min(y + panel_height, frame.shape[0])
    region = frame[y:y2, x:x2]
    background = np.full_like(region, PANEL_COLOR)
    cv2.addWeighted(background, opacity, region, 1 - opacity, 0, dst=region)

    cv2.putText(frame, "QueueVision", (x + 10, y + 28), FONT, 0.8, BRAND_COLOR, 2, cv2.LINE_AA)
    for index, line in enumerate(lines):
        baseline_y = y + 40 + line_height * (index + 1) - 6
        cv2.putText(frame, line, (x + 10, baseline_y), FONT, 0.55, TEXT_COLOR, 1, cv2.LINE_AA)
    return frame
