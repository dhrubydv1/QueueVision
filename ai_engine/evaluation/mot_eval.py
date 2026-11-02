"""MOTChallenge-format track export and CLEAR-MOT / IDF1 evaluation.

Ground truth rows are ``frame,id,x,y,w,h,consider,class,visibility``. Only
pedestrians (class 1) marked ``consider == 1`` are scored. As in the
official MOT16/17 devkit, predicted boxes that best match a distractor
(person on vehicle, static person, distractor, reflection) are removed
before scoring instead of being counted as false positives.
"""

from __future__ import annotations

import configparser
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ai_engine.detection.types import PersonDetection

PEDESTRIAN_CLASS = 1
DISTRACTOR_CLASSES = frozenset({2, 7, 8, 12})
IOU_THRESHOLD = 0.5
REPORTED_METRICS = (
    "mota",
    "motp",
    "idf1",
    "idp",
    "idr",
    "precision",
    "recall",
    "num_switches",
    "num_false_positives",
    "num_misses",
    "num_objects",
    "num_unique_objects",
    "mostly_tracked",
    "partially_tracked",
    "mostly_lost",
    "num_fragmentations",
    "num_frames",
)
COUNT_METRICS = frozenset(
    name for name in REPORTED_METRICS if name.startswith(("num_", "mostly_", "partially_"))
)


class MotWriter:
    """Write tracks as ``frame,id,x,y,w,h,conf,-1,-1,-1`` (1-based frames)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        self.rows_written = 0

    def write(self, frame_number: int, people: Iterable[PersonDetection]) -> None:
        if frame_number < 1:
            raise ValueError("MOT frame numbers are 1-based")
        for person in people:
            if person.track_id is None:
                continue
            x1, y1, x2, y2 = person.bbox
            self._file.write(
                f"{frame_number},{person.track_id},{x1:.2f},{y1:.2f},{x2 - x1:.2f},"
                f"{y2 - y1:.2f},{person.confidence:.4f},-1,-1,-1\n"
            )
            self.rows_written += 1

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> MotWriter:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@dataclass
class FrameBoxes:
    """IDs and ``(x, y, w, h)`` boxes for one frame."""

    ids: list[int] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)

    def array(self) -> np.ndarray:
        return np.asarray(self.boxes, dtype=float).reshape(-1, 4)


@dataclass
class GroundTruth:
    pedestrians: dict[int, FrameBoxes]
    distractors: dict[int, FrameBoxes]


def read_seqinfo(path: str | Path) -> dict[str, Any]:
    """Read ``seqinfo.ini`` (image size, frame rate, sequence length)."""
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise FileNotFoundError(f"seqinfo.ini not found: {path}")
    section = parser["Sequence"]
    return {
        "name": section.get("name"),
        "frame_rate": section.getfloat("frameRate"),
        "seq_length": section.getint("seqLength"),
        "width": section.getint("imWidth"),
        "height": section.getint("imHeight"),
    }


def _read_rows(path: str | Path) -> np.ndarray:
    data = np.loadtxt(path, delimiter=",", ndmin=2)
    return data if data.size else np.zeros((0, 10))


def load_ground_truth(path: str | Path, scale: tuple[float, float] = (1.0, 1.0)) -> GroundTruth:
    """Load MOT ``gt.txt``, keep scored pedestrians and distractors, scale boxes.

    ``scale`` is ``(video_width / imWidth, video_height / imHeight)`` and
    maps ground-truth coordinates onto the video actually processed.
    """
    sx, sy = scale
    pedestrians: dict[int, FrameBoxes] = {}
    distractors: dict[int, FrameBoxes] = {}
    for row in _read_rows(path):
        frame, track_id, x, y, w, h, consider, cls = row[:8]
        box = (x * sx, y * sy, w * sx, h * sy)
        if int(cls) == PEDESTRIAN_CLASS and int(consider) == 1:
            target = pedestrians.setdefault(int(frame), FrameBoxes())
        elif int(cls) in DISTRACTOR_CLASSES:
            target = distractors.setdefault(int(frame), FrameBoxes())
        else:
            continue
        target.ids.append(int(track_id))
        target.boxes.append(box)
    return GroundTruth(pedestrians, distractors)


def load_tracks(path: str | Path) -> dict[int, FrameBoxes]:
    """Load a MOT-format track file written by :class:`MotWriter`."""
    tracks: dict[int, FrameBoxes] = {}
    for row in _read_rows(path):
        frame, track_id, x, y, w, h = row[:6]
        entry = tracks.setdefault(int(frame), FrameBoxes())
        entry.ids.append(int(track_id))
        entry.boxes.append((x, y, w, h))
    return tracks


def iou_matrix(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    """Pairwise IoU between two ``(N, 4)`` and ``(M, 4)`` arrays of ``x, y, w, h``."""
    if len(boxes_a) == 0 or len(boxes_b) == 0:
        return np.zeros((len(boxes_a), len(boxes_b)))
    a = boxes_a[:, None, :]
    b = boxes_b[None, :, :]
    left = np.maximum(a[..., 0], b[..., 0])
    top = np.maximum(a[..., 1], b[..., 1])
    right = np.minimum(a[..., 0] + a[..., 2], b[..., 0] + b[..., 2])
    bottom = np.minimum(a[..., 1] + a[..., 3], b[..., 1] + b[..., 3])
    intersection = np.clip(right - left, 0, None) * np.clip(bottom - top, 0, None)
    union = a[..., 2] * a[..., 3] + b[..., 2] * b[..., 3] - intersection
    return np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)


def iou_distance(gt_boxes: np.ndarray, hyp_boxes: np.ndarray, threshold: float = IOU_THRESHOLD) -> np.ndarray:
    """``1 - IoU`` with NaN where IoU < threshold (motmetrics' "no match")."""
    distances = 1.0 - iou_matrix(gt_boxes, hyp_boxes)
    distances[distances > 1.0 - threshold] = np.nan
    return distances


def drop_distractor_matches(
    hyp: FrameBoxes, pedestrians: FrameBoxes, distractors: FrameBoxes, threshold: float = IOU_THRESHOLD
) -> FrameBoxes:
    """Remove hypotheses whose best one-to-one match is a distractor."""
    if not hyp.ids or not distractors.ids:
        return hyp
    from scipy.optimize import linear_sum_assignment

    all_gt = np.vstack([pedestrians.array(), distractors.array()])
    iou = iou_matrix(all_gt, hyp.array())
    gt_rows, hyp_cols = linear_sum_assignment(-iou)
    drop = {
        int(col)
        for row, col in zip(gt_rows, hyp_cols, strict=True)
        if iou[row, col] >= threshold and row >= len(pedestrians.ids)
    }
    return FrameBoxes(
        ids=[i for k, i in enumerate(hyp.ids) if k not in drop],
        boxes=[b for k, b in enumerate(hyp.boxes) if k not in drop],
    )


def evaluate_tracks(
    ground_truth: GroundTruth,
    tracks: dict[int, FrameBoxes],
    frames: Sequence[int],
    threshold: float = IOU_THRESHOLD,
) -> dict[str, float]:
    """Compute MOTA, IDF1, ID switches, precision, recall, ... over ``frames``."""
    import motmetrics as mm

    accumulator = mm.MOTAccumulator(auto_id=False)
    empty = FrameBoxes()
    for frame in frames:
        pedestrians = ground_truth.pedestrians.get(frame, empty)
        hyp = drop_distractor_matches(
            tracks.get(frame, empty), pedestrians, ground_truth.distractors.get(frame, empty), threshold
        )
        accumulator.update(
            pedestrians.ids, hyp.ids, iou_distance(pedestrians.array(), hyp.array(), threshold), frameid=frame
        )
    summary = mm.metrics.create().compute(accumulator, metrics=list(REPORTED_METRICS), name="seq")
    values = summary.loc["seq"].to_dict()
    return {
        key: int(value) if key in COUNT_METRICS else round(float(value), 4) for key, value in values.items()
    }


def evaluated_frames(seq_length: int, frame_stride: int = 1) -> list[int]:
    """1-based frame numbers that were processed with the given stride."""
    return list(range(1, seq_length + 1, frame_stride))
