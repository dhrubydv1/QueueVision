"""Attribute the queue count's error to false positives, misses and their causes.

Saved tracks are replayed through :class:`~ai_engine.queue.analyzer.QueueAnalyzer` (same loop and
once-per-second sampling as :mod:`ai_engine.evaluation.replay`). At every annotated frame the
visible tracks are matched to the per-person ground-truth boxes (``cad_seqNN_person_boxes.csv``) by
IoU with the Hungarian algorithm, and the raw count is split into categories::

    raw_count = tp + sum(fp_label:*) + fp_unmatched + counted_not_visible
    gt_queueing = tp + sum(fn_*)
    raw_count - gt_queueing = (fp_label + fp_unmatched + counted_not_visible) - fn

Diagnosis only: nothing here changes queue behaviour.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.replay import (
    FROM_SECONDS,
    SeriesRow,
    frame_errors,
    metrics_from_errors,
)
from ai_engine.metrics.queue_outputs import SecondSampler
from ai_engine.queue.analyzer import QueueAnalyzer
from ai_engine.queue.camera_config import CameraConfig
from ai_engine.queue.membership import MemberStatus, foot_point

QUEUEING_ACTION = 4
DEFAULT_IOU = 0.5
SENSITIVITY_IOU = 0.3
DUPLICATE_IOU = 0.1  # unmatched counted box overlapping any GT box at least this much = duplicate/split
FN_CATEGORIES = (
    "fn_no_detection",
    "fn_foot_outside_zone",
    "fn_candidate_dwell",
    "fn_candidate_speed",
    "fn_other",
)
BOX_COLUMNS = ("time_s", "frame", "x", "y", "w", "h", "action", "action_name", "pose")


@dataclass(frozen=True)
class GtBox:
    x: float
    y: float
    w: float
    h: float
    action: int
    action_name: str

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def foot(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h)


def read_person_boxes(path: str | Path) -> dict[int, tuple[float, list[GtBox]]]:
    """``cad_seqNN_person_boxes.csv`` -> frame index -> (time_s, boxes)."""
    frames: dict[int, tuple[float, list[GtBox]]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(BOX_COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        for row in reader:
            frame = int(row["frame"])
            box = GtBox(
                float(row["x"]),
                float(row["y"]),
                float(row["w"]),
                float(row["h"]),
                int(row["action"]),
                row["action_name"],
            )
            frames.setdefault(frame, (float(row["time_s"]), []))[1].append(box)
    return frames


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match_boxes(
    tracks: Sequence[tuple[float, float, float, float]],
    gt: Sequence[tuple[float, float, float, float]],
    threshold: float,
) -> dict[int, int]:
    """Hungarian matching maximising total IoU; only pairs with IoU >= threshold are kept."""
    if not tracks or not gt:
        return {}
    cost = np.array([[-iou(t, g) for g in gt] for t in tracks])
    rows, cols = linear_sum_assignment(cost)
    return {int(r): int(c) for r, c in zip(rows, cols, strict=True) if -cost[r, c] >= threshold}


@dataclass
class FrameAnalysis:
    """Category breakdown for one annotated frame (``counts`` keys are category names)."""

    time_s: float
    frame: int
    raw_count: int
    gt_queueing: int
    counts: dict[str, int]
    counted: list[tuple[int, tuple[int, int, int, int], str]] = field(default_factory=list)
    gt_boxes: list[GtBox] = field(default_factory=list)

    @property
    def fp_total(self) -> int:
        return sum(v for k, v in self.counts.items() if k.startswith("fp_label:") or k in _FP_PLAIN)

    @property
    def fn_total(self) -> int:
        return sum(self.counts.get(k, 0) for k in FN_CATEGORIES)

    @property
    def error(self) -> int:
        return self.raw_count - self.gt_queueing


_FP_PLAIN = ("fp_unmatched", "counted_not_visible")


def analyze_frame(
    time_s: float,
    frame: int,
    gt_boxes: Sequence[GtBox],
    people: Sequence[PersonDetection],
    statuses: Mapping[int, MemberStatus],
    raw_count: int,
    foot_in_zone: Callable[[tuple[float, float]], bool],
    blocker: Callable[[int], str | None],
    iou_threshold: float = DEFAULT_IOU,
) -> FrameAnalysis:
    """Split one frame's raw count and the GT queueing count into error categories."""
    visible = [p for p in people if p.track_id is not None]
    counted_ids = {tid for tid, status in statuses.items() if status is MemberStatus.IN_QUEUE}
    if len(counted_ids) != raw_count:
        raise ValueError(f"frame {frame}: {len(counted_ids)} IN_QUEUE tracks but raw count is {raw_count}")
    gt = list(gt_boxes)
    matches = match_boxes([tuple(map(float, p.bbox)) for p in visible], [g.xyxy for g in gt], iou_threshold)
    track_to_gt = {visible[r].track_id: gt[c] for r, c in matches.items()}
    visible_by_id = {p.track_id: p for p in visible}

    counts: defaultdict[str, int] = defaultdict(int)
    counted: list[tuple[int, tuple[int, int, int, int], str]] = []
    for tid in sorted(counted_ids):
        person = visible_by_id.get(tid)
        if person is None:
            counts["counted_not_visible"] += 1
            continue
        match = track_to_gt.get(tid)
        if match is None:
            counts["fp_unmatched"] += 1
            overlap = any(iou(tuple(map(float, person.bbox)), g.xyxy) >= DUPLICATE_IOU for g in gt)
            counts["fp_unmatched_overlaps_gt" if overlap else "fp_unmatched_no_gt_overlap"] += 1
            counted.append((tid, person.bbox, "fp_unmatched"))
        elif match.action == QUEUEING_ACTION:
            counts["tp"] += 1
            counted.append((tid, person.bbox, "tp"))
        else:
            counts[f"fp_label:{match.action_name}"] += 1
            counted.append((tid, person.bbox, f"fp_label:{match.action_name}"))

    gt_queueing = 0
    gt_to_track = {c: visible[r] for r, c in matches.items()}
    for index, box in enumerate(gt):
        if box.action != QUEUEING_ACTION:
            continue
        gt_queueing += 1
        if not foot_in_zone(box.foot):
            counts["gt_queueing_outside_zone"] += 1
        track = gt_to_track.get(index)
        if track is not None and track.track_id in counted_ids:
            continue  # already counted as tp
        if track is None:
            counts["fn_no_detection"] += 1
        elif not foot_in_zone(foot_point(track)):
            counts["fn_foot_outside_zone"] += 1
        else:
            reason = blocker(track.track_id) if track.track_id is not None else None
            counts[
                {"dwell": "fn_candidate_dwell", "speed": "fn_candidate_speed"}.get(reason or "", "fn_other")
            ] += 1

    result = FrameAnalysis(time_s, frame, raw_count, gt_queueing, dict(counts), counted, gt)
    expected_raw = (
        counts["tp"]
        + sum(v for k, v in counts.items() if k.startswith("fp_label:"))
        + counts["fp_unmatched"]
        + counts["counted_not_visible"]
    )
    if expected_raw != raw_count:
        raise AssertionError(f"frame {frame}: categories sum to {expected_raw}, raw count is {raw_count}")
    if counts["tp"] + result.fn_total != gt_queueing:
        raise AssertionError(f"frame {frame}: tp + fn != GT queueing count {gt_queueing}")
    return result


@dataclass
class ClipAnalysis:
    """All annotated-frame analyses for one clip at one IoU threshold plus the replay raw series."""

    frames: list[FrameAnalysis] = field(default_factory=list)
    series: list[SeriesRow] = field(default_factory=list)
    skipped_frames: int = 0


def analyze_clip(
    tracks: Mapping[int, Sequence[PersonDetection]],
    camera: CameraConfig,
    fps: float,
    frame_size: tuple[int, int],
    frame_count: int,
    gt: Mapping[int, tuple[float, list[GtBox]]],
    iou_thresholds: Sequence[float] = (DEFAULT_IOU, SENSITIVITY_IOU),
) -> dict[float, ClipAnalysis]:
    """Replay ``tracks`` and analyse every annotated frame at each IoU threshold."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    analyzer = QueueAnalyzer(camera)
    sampler = SecondSampler()
    results = {t: ClipAnalysis() for t in iou_thresholds}
    for frame_index in range(frame_count):
        people = list(tracks.get(frame_index, ()))
        timestamp_s = frame_index / fps
        state = analyzer.update(timestamp_s, frame_index, people, frame_size)
        if sampler.due(timestamp_s):
            row = SeriesRow(
                round(timestamp_s, 3),
                frame_index,
                len(people),
                state.raw_count,
                state.queue_length,
                None,
                None,
            )
            for result in results.values():
                result.series.append(row)
        if frame_index not in gt:
            continue
        time_s, boxes = gt[frame_index]
        blocker_fn = getattr(analyzer.membership, "candidate_blocker", None)

        def blocker(tid: int, fn: Any = blocker_fn, now: float = timestamp_s) -> str | None:
            return fn(tid, now) if fn else None

        for threshold, result in results.items():
            result.frames.append(
                analyze_frame(
                    time_s,
                    frame_index,
                    boxes,
                    people,
                    state.statuses,
                    state.raw_count,
                    lambda foot: analyzer.zone.contains(foot, frame_size),
                    blocker,
                    threshold,
                )
            )
    for frame, _ in gt.items():
        if frame >= frame_count:
            for result in results.values():
                result.skipped_frames += 1
    return results


# --------------------------------------------------------------------------- aggregation

CATEGORY_ORDER = (
    "tp",
    "fp_label:*",
    "fp_unmatched",
    "counted_not_visible",
    *FN_CATEGORIES,
)


def select_frames(frames: Sequence[FrameAnalysis], from_s: float) -> list[FrameAnalysis]:
    return [f for f in frames if f.time_s >= from_s]


def summarize(frames: Sequence[FrameAnalysis]) -> dict[str, Any]:
    """Totals, per-frame means and the bias identity for a set of frames."""
    n = len(frames)
    totals: defaultdict[str, int] = defaultdict(int)
    for f in frames:
        for key, value in f.counts.items():
            totals[key] += value
    raw = sum(f.raw_count for f in frames)
    gt = sum(f.gt_queueing for f in frames)
    fp_labels = {k: v for k, v in totals.items() if k.startswith("fp_label:")}
    fp_total = sum(fp_labels.values()) + totals["fp_unmatched"] + totals["counted_not_visible"]
    fn_total = sum(totals[k] for k in FN_CATEGORIES)
    if fp_total - fn_total != raw - gt:
        raise AssertionError("fp - fn does not equal raw - gt")
    mean = (lambda v: round(v / n, 4)) if n else (lambda v: None)
    category_totals = {
        "tp": totals["tp"],
        **dict(sorted(fp_labels.items())),
        "fp_unmatched": totals["fp_unmatched"],
        "fp_unmatched_overlaps_gt": totals["fp_unmatched_overlaps_gt"],
        "fp_unmatched_no_gt_overlap": totals["fp_unmatched_no_gt_overlap"],
        "counted_not_visible": totals["counted_not_visible"],
        **{k: totals[k] for k in FN_CATEGORIES},
        "gt_queueing_outside_zone": totals["gt_queueing_outside_zone"],
    }
    return {
        "frames": n,
        "raw_count_total": raw,
        "gt_queueing_total": gt,
        "totals": category_totals,
        "fp_total": fp_total,
        "fn_total": fn_total,
        "mean_per_frame": {k: mean(v) for k, v in category_totals.items()},
        "mean_raw": mean(raw),
        "mean_gt": mean(gt),
        "mean_fp_total": mean(fp_total),
        "mean_fn_total": mean(fn_total),
        "bias": mean(raw - gt),
    }


def protocol_bias(analysis: ClipAnalysis, from_s: float) -> dict[str, Any]:
    """Raw-count metrics under the replay protocol (nearest 1 Hz sample within 0.6 s), for reconciliation."""
    annotations = [(f.time_s, f.gt_queueing) for f in analysis.frames]
    predictions = sorted((r.time_s, float(r.raw_queue_count)) for r in analysis.series)
    errors, unmatched = frame_errors(predictions, annotations, from_s=from_s)
    return {"errors": errors, "unmatched": unmatched}


def pooled_protocol_bias(analyses: Sequence[ClipAnalysis], from_s: float = FROM_SECONDS) -> dict[str, Any]:
    errors, unmatched = [], 0
    for analysis in analyses:
        part = protocol_bias(analysis, from_s)
        errors.extend(part["errors"])
        unmatched += part["unmatched"]
    return metrics_from_errors(errors, unmatched)
