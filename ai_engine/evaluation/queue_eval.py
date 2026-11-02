"""Compare predicted queue lengths with manual counts."""

from __future__ import annotations

import csv
import math
from bisect import bisect_left
from pathlib import Path
from typing import Any


def read_predictions(path: str | Path, column: str = "queue_length") -> list[tuple[float, float]]:
    """Read ``(time_s, value)`` pairs from a ``<name>_queue.csv`` time series."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if column not in (reader.fieldnames or []):
            raise ValueError(f"Column '{column}' not found in {path}")
        rows = [(float(row["time_s"]), float(row[column])) for row in reader]
    return sorted(rows)


def read_annotations(path: str | Path) -> tuple[list[tuple[float, int]], int]:
    """Read filled ``time_s, frame, queue_count`` rows; return them and the blank count."""
    labelled, blank = [], 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = (row.get("queue_count") or "").strip()
            if not value:
                blank += 1
                continue
            labelled.append((float(row["time_s"]), int(value)))
    return labelled, blank


def nearest(predictions: list[tuple[float, float]], time_s: float, tolerance_s: float) -> float | None:
    """Predicted value closest in time to ``time_s``, if within ``tolerance_s``."""
    times = [t for t, _ in predictions]
    index = bisect_left(times, time_s)
    candidates = [i for i in (index - 1, index) if 0 <= i < len(times)]
    if not candidates:
        return None
    best = min(candidates, key=lambda i: abs(times[i] - time_s))
    return predictions[best][1] if abs(times[best] - time_s) <= tolerance_s else None


def compare_counts(
    predictions: list[tuple[float, float]],
    annotations: list[tuple[float, int]],
    tolerance_s: float = 0.6,
) -> dict[str, Any]:
    """MAE, RMSE, exact-match and within-one rates over matched annotation times.

    Predictions are rounded to the nearest integer for the exact and
    within-one rates; MAE and RMSE use the raw (possibly fractional) value.
    """
    errors = []
    unmatched = 0
    for time_s, truth in annotations:
        predicted = nearest(predictions, time_s, tolerance_s)
        if predicted is None:
            unmatched += 1
            continue
        errors.append((predicted - truth, round(predicted) - truth))
    if not errors:
        return {"matched": 0, "unmatched": unmatched}
    count = len(errors)
    return {
        "matched": count,
        "unmatched": unmatched,
        "mae": round(sum(abs(e) for e, _ in errors) / count, 4),
        "rmse": round(math.sqrt(sum(e * e for e, _ in errors) / count), 4),
        "bias": round(sum(e for e, _ in errors) / count, 4),
        "exact_match_rate": round(sum(1 for _, r in errors if r == 0) / count, 4),
        "within_one_rate": round(sum(1 for _, r in errors if abs(r) <= 1) / count, 4),
    }
