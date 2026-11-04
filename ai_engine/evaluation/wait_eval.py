"""Compare predicted waiting times with manually annotated ground truth."""

from __future__ import annotations

import csv
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRUE_VALUES = {"true", "1", "yes"}
FALSE_VALUES = {"false", "0", "no", ""}
PREDICTION_LOOKUP_TOLERANCE_S = 1.0


@dataclass(frozen=True)
class WaitRecord:
    """One person's wait; ``service_s`` is None for someone who abandoned."""

    person: str
    join_s: float
    service_s: float | None
    abandoned: bool

    @property
    def wait_s(self) -> float | None:
        return None if self.service_s is None else self.service_s - self.join_s


@dataclass(frozen=True)
class TimeseriesPrediction:
    time_s: float
    predicted_wait_s: float | None
    status: str


def _parse_float(text: str, field: str, row_number: int, path: Path) -> float:
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"{path}: row {row_number}: {field} must be a number, got {text!r}") from error


def _parse_bool(text: str, row_number: int, path: Path) -> bool:
    value = text.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError(f"{path}: row {row_number}: abandoned must be true/false, got {text!r}")


def _require_columns(reader: csv.DictReader, columns: tuple[str, ...], path: Path) -> None:
    missing = [c for c in columns if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{path}: missing column(s) {', '.join(missing)}")


def read_ground_truth_waits(path: str | Path) -> list[WaitRecord]:
    """Read ``person, join_s, service_s, abandoned`` rows; ``service_s`` may be blank when abandoned."""
    path = Path(path)
    records = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, ("person", "join_s", "service_s", "abandoned"), path)
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            person = (row["person"] or "").strip() or f"row{row_number}"
            join_text = (row["join_s"] or "").strip()
            service_text = (row["service_s"] or "").strip()
            abandoned = _parse_bool(row["abandoned"] or "", row_number, path)
            if not join_text:
                raise ValueError(f"{path}: row {row_number}: join_s is required")
            join_s = _parse_float(join_text, "join_s", row_number, path)
            if not service_text:
                if not abandoned:
                    raise ValueError(f"{path}: row {row_number}: service_s is required unless abandoned")
                service_s = None
            else:
                service_s = _parse_float(service_text, "service_s", row_number, path)
                if service_s < join_s:
                    raise ValueError(f"{path}: row {row_number}: service_s is before join_s")
            records.append(WaitRecord(person, join_s, service_s, abandoned))
    return records


def read_predicted_waits(path: str | Path) -> list[WaitRecord]:
    """Read our ``<name>_waits.csv`` (completed rows have ``service_s``, abandoned rows ``left_s``)."""
    path = Path(path)
    records = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, ("person_key", "join_s", "service_s", "abandoned"), path)
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            abandoned = _parse_bool(row["abandoned"] or "", row_number, path)
            service_text = (row["service_s"] or "").strip()
            service_s = (
                None
                if abandoned or not service_text
                else _parse_float(service_text, "service_s", row_number, path)
            )
            if service_s is None and not abandoned:
                raise ValueError(f"{path}: row {row_number}: service_s is required unless abandoned")
            join_s = _parse_float((row["join_s"] or "").strip(), "join_s", row_number, path)
            records.append(WaitRecord(row["person_key"], join_s, service_s, abandoned))
    return records


def read_timeseries_predictions(path: str | Path) -> list[TimeseriesPrediction]:
    """Read ``time_s``, ``predicted_wait_s`` and ``prediction_status`` from ``<name>_queue.csv``."""
    path = Path(path)
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, ("time_s", "predicted_wait_s", "prediction_status"), path)
        for row_number, row in enumerate(reader, start=2):
            wait_text = (row["predicted_wait_s"] or "").strip()
            wait = _parse_float(wait_text, "predicted_wait_s", row_number, path) if wait_text else None
            rows.append(
                TimeseriesPrediction(
                    _parse_float(row["time_s"], "time_s", row_number, path), wait, row["prediction_status"]
                )
            )
    return sorted(rows, key=lambda r: r.time_s)


def completed(records: list[WaitRecord]) -> list[WaitRecord]:
    return [r for r in records if not r.abandoned and r.service_s is not None]


def match_by_service_time(
    gt_completed: list[WaitRecord], pred_completed: list[WaitRecord], tolerance_s: float = 3.0
) -> tuple[list[tuple[WaitRecord, WaitRecord]], list[WaitRecord], list[WaitRecord]]:
    """One-to-one greedy matching by ascending |service time difference| within the tolerance.

    Returns ``(matches, missed_gt, extra_predictions)``.
    """
    candidates = sorted(
        (abs(g.service_s - p.service_s), gi, pi)
        for gi, g in enumerate(gt_completed)
        for pi, p in enumerate(pred_completed)
        if abs(g.service_s - p.service_s) <= tolerance_s
    )
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matches = []
    for _, gi, pi in candidates:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        matches.append((gt_completed[gi], pred_completed[pi]))
    matches.sort(key=lambda pair: pair[0].service_s)
    missed = [g for i, g in enumerate(gt_completed) if i not in used_gt]
    extra = [p for i, p in enumerate(pred_completed) if i not in used_pred]
    return matches, missed, extra


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def evaluate_waits(
    gt: list[WaitRecord], predicted: list[WaitRecord], tolerance_s: float = 3.0
) -> dict[str, Any]:
    """Wait-time errors over people matched by service time (predicted minus true wait)."""
    gt_completed, pred_completed = completed(gt), completed(predicted)
    matches, missed, extra = match_by_service_time(gt_completed, pred_completed, tolerance_s)
    errors = [p.wait_s - g.wait_s for g, p in matches]
    result: dict[str, Any] = {
        "gt_completed": len(gt_completed),
        "predicted_completed": len(pred_completed),
        "matched": len(matches),
        "missed": len(missed),
        "extra": len(extra),
        "gt_abandoned": sum(1 for r in gt if r.abandoned),
        "predicted_abandoned": sum(1 for r in predicted if r.abandoned),
        "wait_mae_s": None,
        "wait_median_abs_error_s": None,
        "wait_bias_s": None,
    }
    if errors:
        result["wait_mae_s"] = round(sum(abs(e) for e in errors) / len(errors), 3)
        result["wait_median_abs_error_s"] = round(_median([abs(e) for e in errors]), 3)
        result["wait_bias_s"] = round(sum(errors) / len(errors), 3)
    return result


def evaluate_predictions(
    gt_completed: list[WaitRecord], timeseries: list[TimeseriesPrediction]
) -> dict[str, Any]:
    """Compare the predicted wait shown when each person joined with their true wait.

    The prediction is taken from the time-series row nearest to the join time
    (within 1 s); people without a usable prediction count as unknown.
    """
    times = [r.time_s for r in timeseries]
    abs_errors, pct_errors, unknown = [], [], 0
    for record in gt_completed:
        index = bisect_left(times, record.join_s)
        near = [i for i in (index - 1, index) if 0 <= i < len(times)]
        best = min(near, key=lambda i: abs(times[i] - record.join_s)) if near else None
        row = timeseries[best] if best is not None else None
        if (
            row is None
            or abs(row.time_s - record.join_s) > PREDICTION_LOOKUP_TOLERANCE_S
            or row.status != "ok"
            or row.predicted_wait_s is None
        ):
            unknown += 1
            continue
        error = abs(row.predicted_wait_s - record.wait_s)
        abs_errors.append(error)
        if record.wait_s > 0:
            pct_errors.append(error / record.wait_s * 100)
    return {
        "evaluated": len(abs_errors),
        "unknown_predictions": unknown,
        "prediction_mae_s": round(sum(abs_errors) / len(abs_errors), 3) if abs_errors else None,
        "prediction_mape_percent": round(sum(pct_errors) / len(pct_errors), 3) if pct_errors else None,
    }


def evaluate_throughput(
    gt_completed: list[WaitRecord], pred_completed: list[WaitRecord], duration_s: float
) -> dict[str, Any]:
    """People served per minute over ``duration_s``, predicted versus ground truth."""
    if duration_s <= 0:
        return {"gt_per_min": None, "predicted_per_min": None, "abs_error_per_min": None}
    gt_rate = len(gt_completed) / (duration_s / 60)
    pred_rate = len(pred_completed) / (duration_s / 60)
    return {
        "gt_per_min": round(gt_rate, 3),
        "predicted_per_min": round(pred_rate, 3),
        "abs_error_per_min": round(abs(pred_rate - gt_rate), 3),
    }
