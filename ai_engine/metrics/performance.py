"""Inference and end-to-end processing FPS measurement."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


def _fps(frames: int, seconds: float) -> float:
    return frames / seconds if seconds > 0 else 0.0


class PerformanceTracker:
    """Accumulate per-frame timings and people counts for one video.

    * Inference time covers YOLO detection plus ByteTrack update.
    * Processing time covers the whole frame: decoding, inference,
      drawing and video writing.

    Averages are total frames divided by total time. The ``rolling_*``
    values average the last ``window`` frames and are meant for the live
    overlay only.
    """

    def __init__(self, window: int = 30) -> None:
        self.frames_processed = 0
        self.total_inference_seconds = 0.0
        self.total_processing_seconds = 0.0
        self.max_people_in_frame = 0
        self.total_people_detections = 0
        self.unique_track_ids: set[int] = set()
        self._recent_inference: deque[float] = deque(maxlen=window)
        self._recent_processing: deque[float] = deque(maxlen=window)

    def record_frame(
        self,
        inference_seconds: float,
        processing_seconds: float,
        people_count: int,
        track_ids: list[int] | None = None,
    ) -> None:
        if inference_seconds < 0 or processing_seconds < 0:
            raise ValueError("Timings cannot be negative")
        self.frames_processed += 1
        self.total_inference_seconds += inference_seconds
        self.total_processing_seconds += processing_seconds
        self.max_people_in_frame = max(self.max_people_in_frame, people_count)
        self.total_people_detections += people_count
        if track_ids:
            self.unique_track_ids.update(track_ids)
        self._recent_inference.append(inference_seconds)
        self._recent_processing.append(processing_seconds)

    @property
    def average_inference_fps(self) -> float:
        return _fps(self.frames_processed, self.total_inference_seconds)

    @property
    def average_processing_fps(self) -> float:
        return _fps(self.frames_processed, self.total_processing_seconds)

    @property
    def rolling_inference_fps(self) -> float:
        return _fps(len(self._recent_inference), sum(self._recent_inference))

    @property
    def rolling_processing_fps(self) -> float:
        return _fps(len(self._recent_processing), sum(self._recent_processing))

    @property
    def average_people_per_frame(self) -> float:
        if self.frames_processed == 0:
            return 0.0
        return self.total_people_detections / self.frames_processed

    def build_report(
        self,
        device: str,
        total_runtime_seconds: float,
        model: str = "YOLO11n",
        tracker: str = "ByteTrack",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the metrics report; all numbers come from recorded frames.

        ``total_runtime_seconds`` is the wall-clock time of the whole run,
        including model loading and warm-up, while
        ``total_processing_seconds`` covers only the per-frame loop.
        """
        report: dict[str, Any] = {
            "project": "QueueVision",
            "model": model,
            "tracker": tracker,
            "device": device,
            "frames_processed": self.frames_processed,
            "performance": {
                "total_runtime_seconds": round(total_runtime_seconds, 4),
                "total_processing_seconds": round(self.total_processing_seconds, 4),
                "total_inference_seconds": round(self.total_inference_seconds, 4),
                "average_inference_fps": round(self.average_inference_fps, 2),
                "average_processing_fps": round(self.average_processing_fps, 2),
            },
            "statistics": {
                "maximum_people_detected_in_a_frame": self.max_people_in_frame,
                "average_people_per_frame": round(self.average_people_per_frame, 2),
                "unique_track_ids_assigned": len(self.unique_track_ids),
            },
        }
        if extra:
            report.update(extra)
        return report


def save_json(data: dict[str, Any], output_path: str | Path) -> Path:
    """Write ``data`` as indented JSON, creating parent folders."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
