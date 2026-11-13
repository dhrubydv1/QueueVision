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


# --- observed-wait ground truth (clip_id/track_id format) -----------------------------------------
#
# Ground truth in this format comes from an annotator timing each person. ``track_id`` is the
# annotator's own number, NOT a ByteTrack id, so people are matched to the system's persons by
# queue-join time (greedy one-to-one within a tolerance), never by id.

OBSERVED_COLUMNS = (
    "clip_id",
    "track_id",
    "queue_join_seconds",
    "service_start_seconds",
    "service_complete_seconds",
    "observed_wait_seconds",
    "annotation_confidence",
)
CONFIDENCE_LEVELS = ("low", "medium", "high")  # ascending
OBSERVED_WAIT_TOLERANCE_S = 0.5
JOIN_MATCH_TOLERANCE_S = 3.0
EXCLUSION_REASONS = (
    "unparseable_number",
    "invalid_confidence",
    "below_min_confidence",
    "missing_join",
    "not_served",
    "service_before_join",
    "missing_observed_wait",
    "inconsistent_observed_wait",
)


@dataclass(frozen=True)
class ObservedWait:
    """One complete, valid ground-truth observation (join and service start both present)."""

    clip_id: str
    track_id: str
    join_s: float
    service_start_s: float
    observed_wait_s: float
    service_complete_s: float | None
    confidence: str


@dataclass(frozen=True)
class ObservedGroundTruth:
    rows_read: int
    valid: list[ObservedWait]
    excluded: dict[str, int]
    inconsistent: list[dict[str, Any]]


def is_observed_format(path: str | Path) -> bool:
    """True when the CSV header is the observed-wait format (detected by its join column)."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    return "queue_join_seconds" in [h.strip() for h in header]


def _optional_float(text: str) -> float | None:
    text = text.strip()
    return float(text) if text else None


def read_observed_ground_truth(path: str | Path, min_confidence: str | None = None) -> ObservedGroundTruth:
    """Read the observed-wait CSV; rows that cannot enter accuracy are counted by reason."""
    path = Path(path)
    if min_confidence is not None and min_confidence not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"min_confidence must be one of {', '.join(CONFIDENCE_LEVELS)}, got {min_confidence!r}"
        )
    excluded = dict.fromkeys(EXCLUSION_REASONS, 0)
    valid: list[ObservedWait] = []
    inconsistent: list[dict[str, Any]] = []
    rows_read = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require_columns(reader, OBSERVED_COLUMNS, path)
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            rows_read += 1
            clip, track = (row["clip_id"] or "").strip(), (row["track_id"] or "").strip()
            confidence = (row["annotation_confidence"] or "").strip().lower()
            if confidence not in CONFIDENCE_LEVELS:
                excluded["invalid_confidence"] += 1
                continue
            try:
                join_s = _optional_float(row["queue_join_seconds"] or "")
                start_s = _optional_float(row["service_start_seconds"] or "")
                complete_s = _optional_float(row["service_complete_seconds"] or "")
                observed = _optional_float(row["observed_wait_seconds"] or "")
            except ValueError:
                excluded["unparseable_number"] += 1
                continue
            if min_confidence is not None and CONFIDENCE_LEVELS.index(confidence) < CONFIDENCE_LEVELS.index(
                min_confidence
            ):
                excluded["below_min_confidence"] += 1
                continue
            if join_s is None:
                excluded["missing_join"] += 1
            elif start_s is None:
                excluded["not_served"] += 1
            elif start_s < join_s:
                excluded["service_before_join"] += 1
            elif observed is None:
                excluded["missing_observed_wait"] += 1
            elif abs(observed - (start_s - join_s)) > OBSERVED_WAIT_TOLERANCE_S:
                excluded["inconsistent_observed_wait"] += 1
                inconsistent.append(
                    {
                        "row": row_number,
                        "clip_id": clip,
                        "track_id": track,
                        "observed_wait_seconds": observed,
                        "service_start_minus_join": round(start_s - join_s, 3),
                    }
                )
            else:
                valid.append(ObservedWait(clip, track, join_s, start_s, observed, complete_s, confidence))
    return ObservedGroundTruth(rows_read, valid, excluded, inconsistent)


def match_by_join_time(
    observations: list[ObservedWait], predicted: list[WaitRecord], tolerance_s: float = JOIN_MATCH_TOLERANCE_S
) -> tuple[list[tuple[ObservedWait, WaitRecord]], list[ObservedWait], list[WaitRecord]]:
    """One-to-one greedy matching by ascending |join-time difference| within the tolerance.

    Returns ``(matches, unmatched_observations, extra_predictions)``.
    """
    candidates = sorted(
        (abs(o.join_s - p.join_s), oi, pi)
        for oi, o in enumerate(observations)
        for pi, p in enumerate(predicted)
        if abs(o.join_s - p.join_s) <= tolerance_s
    )
    used_o: set[int] = set()
    used_p: set[int] = set()
    matches = []
    for _, oi, pi in candidates:
        if oi in used_o or pi in used_p:
            continue
        used_o.add(oi)
        used_p.add(pi)
        matches.append((observations[oi], predicted[pi]))
    matches.sort(key=lambda pair: pair[0].join_s)
    return (
        matches,
        [o for i, o in enumerate(observations) if i not in used_o],
        [p for i, p in enumerate(predicted) if i not in used_p],
    )


def error_statistics(errors: list[float]) -> dict[str, Any]:
    """Count, MAE, RMSE, median absolute error and mean bias of signed errors (predicted - observed)."""
    stats: dict[str, Any] = {
        "samples": len(errors),
        "mae_s": None,
        "rmse_s": None,
        "median_abs_error_s": None,
        "bias_s": None,
    }
    if errors:
        stats["mae_s"] = round(sum(abs(e) for e in errors) / len(errors), 3)
        stats["rmse_s"] = round((sum(e * e for e in errors) / len(errors)) ** 0.5, 3)
        stats["median_abs_error_s"] = round(_median([abs(e) for e in errors]), 3)
        stats["bias_s"] = round(sum(errors) / len(errors), 3)
    return stats


@dataclass(frozen=True)
class ClipPredictions:
    """The system's outputs for one clip: its waits and, optionally, the predicted-wait time series."""

    waits: list[WaitRecord]
    timeseries: list[TimeseriesPrediction] | None = None


def _lookup_prediction(timeseries: list[TimeseriesPrediction], time_s: float) -> TimeseriesPrediction | None:
    times = [r.time_s for r in timeseries]
    index = bisect_left(times, time_s)
    near = [i for i in (index - 1, index) if 0 <= i < len(times)]
    if not near:
        return None
    best = min(near, key=lambda i: abs(times[i] - time_s))
    return timeseries[best] if abs(times[best] - time_s) <= PREDICTION_LOOKUP_TOLERANCE_S else None


def evaluate_observed_waits(
    truth: ObservedGroundTruth,
    predictions: dict[str, ClipPredictions],
    join_tolerance_s: float = JOIN_MATCH_TOLERANCE_S,
    estimator: str | None = None,
) -> dict[str, Any]:
    """Score the system against observed waits, measured and predicted waits kept apart.

    * ``measured``: the wait the system measured for the matched person (service start minus
      join) versus the observed wait; error = system - observed.
    * ``predicted``: the time-series ``predicted_wait_s`` at the observed join time versus the
      observed wait; each record keeps the predicted value, prediction timestamp, horizon
      (observed service start minus prediction time), status and estimator version.

    Clips without predictions are listed in ``clips_without_predictions`` and their valid
    observations count as unmatched, never as zero error.
    """
    by_clip: dict[str, list[ObservedWait]] = {}
    for observation in truth.valid:
        by_clip.setdefault(observation.clip_id, []).append(observation)

    measured_errors: list[float] = []
    predicted_errors: list[float] = []
    per_clip: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    unmatched = extra = unknown_predictions = 0
    for clip_id in sorted(by_clip):
        observations = by_clip[clip_id]
        clip = predictions.get(clip_id)
        if clip is None:
            unmatched += len(observations)
            per_clip[clip_id] = {
                "valid_observations": len(observations),
                "matched": 0,
                "measured": error_statistics([]),
                "predicted": error_statistics([]),
            }
            continue
        matches, missed, extra_predictions = match_by_join_time(observations, clip.waits, join_tolerance_s)
        unmatched += len(missed)
        extra += len(extra_predictions)
        clip_measured = [p.wait_s - o.observed_wait_s for o, p in matches if p.wait_s is not None]
        clip_predicted: list[float] = []
        if clip.timeseries is not None:
            for observation in observations:
                row = _lookup_prediction(clip.timeseries, observation.join_s)
                usable = row is not None and row.status == "ok" and row.predicted_wait_s is not None
                record: dict[str, Any] = {
                    "clip_id": clip_id,
                    "track_id": observation.track_id,
                    "join_s": observation.join_s,
                    "observed_wait_s": observation.observed_wait_s,
                    "predicted_wait_s": row.predicted_wait_s if row is not None and usable else None,
                    "prediction_time_s": row.time_s if row is not None else None,
                    "horizon_s": round(observation.service_start_s - row.time_s, 3) if row else None,
                    "status": row.status if row is not None else "no_prediction_row",
                    "estimator": estimator,
                }
                records.append(record)
                if usable and row is not None and row.predicted_wait_s is not None:
                    clip_predicted.append(row.predicted_wait_s - observation.observed_wait_s)
                else:
                    unknown_predictions += 1
        measured_errors += clip_measured
        predicted_errors += clip_predicted
        per_clip[clip_id] = {
            "valid_observations": len(observations),
            "matched": len(matches),
            "measured": error_statistics(clip_measured),
            "predicted": error_statistics(clip_predicted),
        }
    return {
        "ground_truth_rows": truth.rows_read,
        "valid_observations": len(truth.valid),
        "excluded_rows": dict(truth.excluded),
        "inconsistent_rows": truth.inconsistent,
        "matching": {
            "rule": "greedy one-to-one by queue-join time",
            "tolerance_s": join_tolerance_s,
            "unmatched_observations": unmatched,
            "extra_predicted_persons": extra,
        },
        "clips_without_predictions": sorted(set(by_clip) - set(predictions)),
        "measured": error_statistics(measured_errors),
        "predicted": {
            **error_statistics(predicted_errors),
            "unknown_or_missing": unknown_predictions,
            "estimator": estimator,
            "records": records,
        },
        "per_clip": per_clip,
    }
