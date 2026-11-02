"""Draw tracked people, the queue zone and a live metrics panel onto frames."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from ai_engine.detection.types import PersonDetection

# BGR colours
BOX_COLOR = (0, 200, 0)  # green: tracked, not queuing
CANDIDATE_COLOR = (0, 230, 255)  # yellow: in zone, not yet confirmed
IN_QUEUE_COLOR = (0, 140, 255)  # orange: confirmed queue member
ZONE_COLOR = (255, 160, 0)  # blue: queue zone outline/fill
TEXT_COLOR = (255, 255, 255)
PANEL_COLOR = (20, 20, 20)
BRAND_COLOR = (0, 200, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX
PANEL_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
STATUS_COLORS = {"candidate": CANDIDATE_COLOR, "in_queue": IN_QUEUE_COLOR}


@dataclass(frozen=True)
class OverlayStats:
    """Values shown in the metrics panel for one frame.

    ``queue_length`` is None when no queue zone is configured, in which
    case the panel shows only the people-in-frame count.
    """

    frame_number: int
    total_frames: int
    people_in_frame: int
    inference_fps: float
    processing_fps: float
    device: str
    queue_length: float | None = None


def draw_people(
    frame: np.ndarray,
    people: Sequence[PersonDetection],
    statuses: Mapping[int, str] | None = None,
) -> np.ndarray:
    """Draw a box, ``ID n  0.87`` label and foot point for every person, in place.

    Colours follow queue status when ``statuses`` (track ID -> status value)
    is given: orange in queue, yellow candidate, green otherwise.
    """
    statuses = statuses or {}
    for person in people:
        status = statuses.get(person.track_id) if person.track_id is not None else None
        color = STATUS_COLORS.get(getattr(status, "value", status), BOX_COLOR)
        x1, y1, x2, y2 = person.bbox
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        cv2.circle(frame, ((x1 + x2) // 2, y2), 4, color, -1)

        label = f"{person.confidence:.2f}"
        if person.track_id is not None:
            label = f"ID {person.track_id}  {label}"
        (text_width, text_height), baseline = cv2.getTextSize(label, FONT, 0.5, 1)
        label_top = max(y1 - text_height - baseline - 4, 0)
        cv2.rectangle(
            frame,
            (x1, label_top),
            (x1 + text_width + 6, label_top + text_height + baseline + 4),
            color,
            -1,
        )
        cv2.putText(frame, label, (x1 + 3, label_top + text_height + 2), FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return frame


def draw_queue_zone(frame: np.ndarray, polygon_px: np.ndarray, opacity: float = 0.18) -> np.ndarray:
    """Draw a semi-transparent queue zone with an outline and label, in place."""
    overlay = frame.copy()
    cv2.fillPoly(overlay, [polygon_px], ZONE_COLOR)
    cv2.addWeighted(overlay, opacity, frame, 1 - opacity, 0, dst=frame)
    cv2.polylines(frame, [polygon_px], True, ZONE_COLOR, 2, cv2.LINE_AA)
    top = polygon_px[np.argmin(polygon_px[:, 1])]
    cv2.putText(
        frame,
        "QUEUE ZONE",
        (int(top[0]) + 4, max(int(top[1]) - 6, 14)),
        FONT,
        0.55,
        ZONE_COLOR,
        2,
        cv2.LINE_AA,
    )
    return frame


def draw_metrics_panel(
    frame: np.ndarray,
    stats: OverlayStats,
    opacity: float = 0.6,
    position: str = "top-left",
) -> np.ndarray:
    """Draw a semi-transparent panel with branding and live metrics, in place.

    "People in frame" counts every tracked person visible in this frame;
    "Queue length" is the smoothed number of confirmed queue members. They
    are shown separately because they are different quantities.
    """
    if position not in PANEL_POSITIONS:
        raise ValueError(f"position must be one of {PANEL_POSITIONS}")
    total = f"/{stats.total_frames}" if stats.total_frames > 0 else ""
    lines = []
    if stats.queue_length is not None:
        lines.append(f"Queue length: {stats.queue_length:g}")
    lines += [
        f"People in frame: {stats.people_in_frame}",
        f"Inference FPS: {stats.inference_fps:.1f}",
        f"Processing FPS: {stats.processing_fps:.1f}",
        f"Frame: {stats.frame_number}{total}",
        f"Device: {stats.device.upper()}",
    ]
    line_height = 24
    panel_width = 260
    panel_height = 40 + line_height * len(lines) + 8
    frame_height, frame_width = frame.shape[:2]

    margin = 10
    x = margin if position.endswith("left") else max(frame_width - panel_width - margin, 0)
    y = margin if position.startswith("top") else max(frame_height - panel_height - margin, 0)
    x2 = min(x + panel_width, frame_width)
    y2 = min(y + panel_height, frame_height)
    region = frame[y:y2, x:x2]
    background = np.full_like(region, PANEL_COLOR)
    cv2.addWeighted(background, opacity, region, 1 - opacity, 0, dst=region)

    cv2.putText(frame, "QueueVision", (x + 10, y + 28), FONT, 0.8, BRAND_COLOR, 2, cv2.LINE_AA)
    for index, line in enumerate(lines):
        baseline_y = y + 40 + line_height * (index + 1) - 6
        is_queue_line = stats.queue_length is not None and index == 0
        color = IN_QUEUE_COLOR if is_queue_line else TEXT_COLOR
        thickness = 2 if is_queue_line else 1
        cv2.putText(frame, line, (x + 10, baseline_y), FONT, 0.55, color, thickness, cv2.LINE_AA)
    return frame
