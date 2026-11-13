"""Replay saved MOT tracks through the queue logic and score the result.

Detection and tracking are the slow, fixed part of the pipeline. This module
feeds the *saved* tracks (``run_tracking.py --save-mot``) frame by frame into a
fresh :class:`~ai_engine.queue.analyzer.QueueAnalyzer`, so queue-logic variants
can be compared in seconds on exactly the same detections.

The replay mirrors ``run_tracking``: every frame ``0..frame_count-1`` is
processed (empty list when nothing was tracked), the timestamp is
``frame_index / fps`` and the time series is sampled with the same
once-per-second rule as ``QueueTimeSeriesWriter``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.queue_eval import nearest, read_annotations
from ai_engine.metrics.queue_outputs import SecondSampler
from ai_engine.queue.analyzer import QueueAnalyzer
from ai_engine.queue.camera_config import (
    AlertSettings,
    CameraConfig,
    CameraConfigError,
    MembershipSettings,
    ServiceRateSettings,
    SmoothingSettings,
    StitchingSettings,
)
from ai_engine.queue.event_schema import QueueEventType
from ai_engine.queue.stitching import StitchDecision

DEFAULT_TOLERANCE_S = 0.6  # annotation-to-prediction time tolerance (Sprint 4 protocol)
FROM_SECONDS = 5.0  # "from 5 s": ignore the start-up transient (Sprint 4 protocol)
SERIES_COLUMNS = ("queue_length", "raw_queue_count")

_OVERRIDE_SECTIONS: dict[str, type] = {
    "membership": MembershipSettings,
    "stitching": StitchingSettings,
    "smoothing": SmoothingSettings,
    "service_rate": ServiceRateSettings,
    "alerts": AlertSettings,
}


# --------------------------------------------------------------------------- tracks


def read_mot_tracks(path: str | Path) -> dict[int, list[PersonDetection]]:
    """Read a MOT file (``frame,id,x,y,w,h,conf,...``, 1-based) into 0-based frame lists."""
    frames: dict[int, list[PersonDetection]] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            fields = line.split(",")
            if len(fields) < 7:
                raise ValueError(f"{path}:{line_number}: expected >= 7 comma-separated fields")
            frame = int(fields[0]) - 1
            if frame < 0:
                raise ValueError(f"{path}:{line_number}: MOT frame numbers are 1-based")
            x, y, w, h = (float(v) for v in fields[2:6])
            bbox = (round(x), round(y), round(x + w), round(y + h))
            frames.setdefault(frame, []).append(PersonDetection(bbox, float(fields[6]), int(fields[1])))
    return frames


# --------------------------------------------------------------------------- overrides


def apply_overrides(camera: CameraConfig, overrides: Mapping[str, Mapping[str, Any]] | None) -> CameraConfig:
    """Return ``camera`` with nested ``{section: {field: value}}`` overrides applied and re-validated."""
    if not overrides:
        return camera
    changes: dict[str, Any] = {}
    for section, values in overrides.items():
        settings_type = _OVERRIDE_SECTIONS.get(section)
        if settings_type is None:
            raise CameraConfigError(
                f"Unknown override section '{section}' (allowed: {', '.join(sorted(_OVERRIDE_SECTIONS))})"
            )
        if not isinstance(values, Mapping):
            raise CameraConfigError(f"Override '{section}' must be a mapping")
        unknown = set(values) - set(settings_type.__dataclass_fields__)
        if unknown:
            raise CameraConfigError(f"Unknown '{section}' settings: {', '.join(sorted(unknown))}")
        changes[section] = replace(getattr(camera, section), **values)
    return replace(camera, **changes)  # CameraConfig.__post_init__ validates


# --------------------------------------------------------------------------- replay


@dataclass(frozen=True)
class SeriesRow:
    """One sampled second of the queue time series (same values as the CSV columns)."""

    time_s: float
    frame: int
    people_in_frame: int
    raw_queue_count: int
    queue_length: float
    service_rate_per_min: float | None
    predicted_wait_s: float | None


@dataclass
class ReplayResult:
    series: list[SeriesRow] = field(default_factory=list)
    events: list[QueueEventType] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)
    frames_replayed: int = 0
    stitch_decisions: list[StitchDecision] = field(default_factory=list)

    def predictions(self, column: str) -> list[tuple[float, float]]:
        if column not in SERIES_COLUMNS:
            raise ValueError(f"column must be one of {SERIES_COLUMNS}")
        return [(row.time_s, float(getattr(row, column))) for row in self.series]


def replay_queue(
    tracks: Mapping[int, Sequence[PersonDetection]],
    camera: CameraConfig,
    fps: float,
    frame_size: tuple[int, int],
    frame_count: int,
    stitching_enabled: bool | None = None,
    stitch_diagnostics: bool | None = None,
) -> ReplayResult:
    """Feed every frame ``0..frame_count-1`` to a fresh analyzer and sample the time series."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    analyzer = QueueAnalyzer(
        camera, stitching_enabled=stitching_enabled, stitch_diagnostics=stitch_diagnostics
    )
    sampler = SecondSampler()
    result = ReplayResult()
    for frame_index in range(frame_count):
        people = list(tracks.get(frame_index, ()))
        timestamp_s = frame_index / fps
        state = analyzer.update(timestamp_s, frame_index, people, frame_size)
        result.events.extend(state.events)
        if sampler.due(timestamp_s):
            result.series.append(
                SeriesRow(
                    time_s=round(timestamp_s, 3),
                    frame=frame_index,
                    people_in_frame=len(people),
                    raw_queue_count=state.raw_count,
                    queue_length=state.queue_length,
                    service_rate_per_min=state.service_rate.per_min,
                    predicted_wait_s=state.prediction.predicted_wait_s,
                )
            )
    result.frames_replayed = frame_count
    duration_s = frame_count / fps
    result.report = analyzer.report(duration_s)
    result.stitch_decisions = list(analyzer.stitcher.decisions)
    return result


def video_properties(path: str | Path) -> tuple[float, tuple[int, int], int]:
    """Read ``(fps, (width, height), frame_count)`` from a video container with OpenCV."""
    import cv2

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f"Cannot open video: {path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    return (fps if fps > 0 else 30.0), (width, height), frames


# --------------------------------------------------------------------------- scoring


@dataclass(frozen=True)
class FrameError:
    """Prediction error for one annotated time: ``predicted - truth`` (raw and rounded)."""

    time_s: float
    error: float
    rounded_error: int


def frame_errors(
    predictions: list[tuple[float, float]],
    annotations: list[tuple[float, int]],
    tolerance_s: float = DEFAULT_TOLERANCE_S,
    from_s: float = 0.0,
) -> tuple[list[FrameError], int]:
    """Per-annotation errors for annotations at ``time_s >= from_s``; also the unmatched count."""
    errors: list[FrameError] = []
    unmatched = 0
    for time_s, truth in annotations:
        if time_s < from_s:
            continue
        predicted = nearest(predictions, time_s, tolerance_s)
        if predicted is None:
            unmatched += 1
            continue
        errors.append(FrameError(time_s, predicted - truth, round(predicted) - truth))
    return errors, unmatched


def metrics_from_errors(errors: Sequence[FrameError], unmatched: int = 0) -> dict[str, Any]:
    """Same metrics (and rounding) as ``compare_counts``, from a list of per-frame errors."""
    if not errors:
        return {"matched": 0, "unmatched": unmatched}
    n = len(errors)
    return {
        "matched": n,
        "unmatched": unmatched,
        "mae": round(sum(abs(e.error) for e in errors) / n, 4),
        "rmse": round(math.sqrt(sum(e.error**2 for e in errors) / n), 4),
        "bias": round(sum(e.error for e in errors) / n, 4),
        "exact_match_rate": round(sum(1 for e in errors if e.rounded_error == 0) / n, 4),
        "within_one_rate": round(sum(1 for e in errors if abs(e.rounded_error) <= 1) / n, 4),
    }


@dataclass
class ClipScores:
    """Errors for one clip, keyed ``<smoothed|raw>_<all|from5s>``."""

    errors: dict[str, list[FrameError]] = field(default_factory=dict)
    unmatched: dict[str, int] = field(default_factory=dict)

    def metrics(self) -> dict[str, dict[str, Any]]:
        return {
            key: metrics_from_errors(errs, self.unmatched.get(key, 0)) for key, errs in self.errors.items()
        }


SCORE_KEYS = tuple(f"{name}_{window}" for name in ("smoothed", "raw") for window in ("all", "from5s"))


def score_series(
    series: ReplayResult | Sequence[SeriesRow],
    annotations: list[tuple[float, int]],
    tolerance_s: float = DEFAULT_TOLERANCE_S,
    from_s: float = FROM_SECONDS,
) -> ClipScores:
    """Score smoothed and raw counts on all annotated frames and on frames with time >= ``from_s``."""
    rows = series.series if isinstance(series, ReplayResult) else list(series)
    result = ClipScores()
    for name, column in (("smoothed", "queue_length"), ("raw", "raw_queue_count")):
        predictions = sorted((row.time_s, float(getattr(row, column))) for row in rows)
        for window, start in (("all", 0.0), ("from5s", from_s)):
            key = f"{name}_{window}"
            errors, unmatched = frame_errors(predictions, annotations, tolerance_s, start)
            result.errors[key] = errors
            result.unmatched[key] = unmatched
    return result


def score_clip(
    result: ReplayResult, annotation_path: str | Path, tolerance_s: float = DEFAULT_TOLERANCE_S
) -> ClipScores:
    annotations, _ = read_annotations(annotation_path)
    return score_series(result, annotations, tolerance_s)


def pool_scores(clips: Sequence[ClipScores]) -> dict[str, dict[str, Any]]:
    """Pooled metrics over individual annotated frames of all clips (not averages of clip metrics)."""
    pooled: dict[str, dict[str, Any]] = {}
    for key in SCORE_KEYS:
        errors = [e for clip in clips for e in clip.errors.get(key, [])]
        unmatched = sum(clip.unmatched.get(key, 0) for clip in clips)
        pooled[key] = metrics_from_errors(errors, unmatched)
    return pooled


def read_split_file(path: str | Path) -> list[str]:
    """Clip names from a split file (one per line; ``#`` starts a comment). Missing file -> empty."""
    path = Path(path)
    if not path.is_file():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names
