"""Evaluate predicted waiting times against manually annotated ground truth.

Example::

    python ai_engine/evaluate_waiting.py \\
        --predicted-waits outputs/events/clip_waits.csv \\
        --ground-truth datasets/annotations/clip_waits_gt.csv \\
        --timeseries outputs/timeseries/clip_queue.csv

People are matched one-to-one by service time (``--tolerance`` seconds).
Writes ``outputs/metrics/<name>_wait_eval.json`` (predicted-wait evaluation) or, for the
observed format, ``<name>_observed_wait_eval.json`` (measured and predicted waits scored separately).

The ground-truth CSV may instead use the observed-wait format (header
``clip_id,track_id,queue_join_seconds,service_start_seconds,
service_complete_seconds,observed_wait_seconds,annotation_confidence``, detected
automatically). Its ``track_id`` is the annotator's number, so people are matched by
queue-join time (``--tolerance``), and the system's measured waits and its predicted
waits are scored separately. Give one or more ``--clip CLIP_ID=WAITS_CSV[:QUEUE_CSV]``
(or ``--predicted-waits``/``--timeseries`` plus ``--clip-id``) and optionally
``--min-confidence``. Real-video use needs ground truth with person ids; none exists yet.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ai_engine import config
from ai_engine.evaluation.wait_eval import (
    CONFIDENCE_LEVELS,
    ClipPredictions,
    completed,
    evaluate_observed_waits,
    evaluate_predictions,
    evaluate_throughput,
    evaluate_waits,
    is_observed_format,
    read_ground_truth_waits,
    read_observed_ground_truth,
    read_predicted_waits,
    read_timeseries_predictions,
)
from ai_engine.metrics.performance import save_json


def _clip_predictions(args: argparse.Namespace) -> dict[str, ClipPredictions]:
    """Predictions per clip id from ``--clip`` entries or the single-clip arguments."""
    clips: dict[str, ClipPredictions] = {}
    for entry in args.clip or []:
        clip_id, separator, files = entry.partition("=")
        if not separator or not clip_id or not files:
            raise ValueError(f"--clip must look like CLIP_ID=WAITS_CSV[:QUEUE_CSV], got {entry!r}")
        waits_path, _, series_path = files.partition(":")
        clips[clip_id] = ClipPredictions(
            read_predicted_waits(waits_path),
            read_timeseries_predictions(series_path) if series_path else None,
        )
    if args.predicted_waits is not None:
        if not args.clip_id:
            raise ValueError("--predicted-waits with the observed-wait format needs --clip-id")
        clips[args.clip_id] = ClipPredictions(
            read_predicted_waits(args.predicted_waits),
            read_timeseries_predictions(args.timeseries) if args.timeseries else None,
        )
    if not clips:
        raise ValueError("give --clip CLIP_ID=WAITS_CSV[:QUEUE_CSV] or --predicted-waits with --clip-id")
    return clips


def _main_observed(args: argparse.Namespace) -> int:
    try:
        truth = read_observed_ground_truth(args.ground_truth, args.min_confidence)
        clips = _clip_predictions(args)
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    metrics = evaluate_observed_waits(truth, clips, args.tolerance, args.estimator)
    name = args.ground_truth.stem
    output = args.output or config.OUTPUTS_DIR / "metrics" / f"{name}_observed_wait_eval.json"
    save_json(
        {
            "project": "QueueVision",
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "ground_truth": str(args.ground_truth),
            "ground_truth_format": "observed_wait",
            "min_confidence": args.min_confidence,
            "metrics": metrics,
        },
        output,
    )
    measured = metrics["measured"]
    print(
        f"{name}: {metrics['valid_observations']}/{metrics['ground_truth_rows']} valid observations, "
        f"measured-wait MAE {measured['mae_s']} s over {measured['samples']} samples -> {output}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare predicted waiting times with ground truth.")
    parser.add_argument("--predicted-waits", type=Path, help="<name>_waits.csv")
    parser.add_argument("--ground-truth", type=Path, required=True, help="Filled <name>_waits_gt.csv")
    parser.add_argument("--timeseries", type=Path, help="<name>_queue.csv, to score the wait predictions")
    parser.add_argument(
        "--duration",
        type=float,
        help="Video duration in seconds for throughput (default: last timeseries time or service time)",
    )
    parser.add_argument("--tolerance", type=float, default=3.0, help="Max service-time difference in seconds")
    parser.add_argument("--output", type=Path, help="Report path (JSON)")
    parser.add_argument(
        "--min-confidence",
        choices=CONFIDENCE_LEVELS,
        help="Observed format: lowest annotation_confidence to use",
    )
    parser.add_argument(
        "--clip",
        action="append",
        metavar="CLIP_ID=WAITS_CSV[:QUEUE_CSV]",
        help="Observed format: predictions of one clip (repeatable)",
    )
    parser.add_argument("--clip-id", help="Observed format: clip id of --predicted-waits")
    parser.add_argument(
        "--estimator", help="Observed format: estimator version recorded with the predicted waits"
    )
    args = parser.parse_args(argv)

    try:
        observed_format = is_observed_format(args.ground_truth)
    except OSError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if observed_format:
        return _main_observed(args)
    if args.predicted_waits is None:
        print("ERROR: --predicted-waits is required for the legacy ground-truth format", file=sys.stderr)
        return 1

    try:
        if args.tolerance < 0:
            raise ValueError("--tolerance must not be negative")
        predicted = read_predicted_waits(args.predicted_waits)
        truth = read_ground_truth_waits(args.ground_truth)
        series = read_timeseries_predictions(args.timeseries) if args.timeseries else None
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    gt_completed, pred_completed = completed(truth), completed(predicted)
    duration = args.duration
    if duration is None:
        candidates = [r.service_s for r in (*gt_completed, *pred_completed)]
        if series:
            candidates.append(series[-1].time_s)
        duration = max(candidates, default=0.0)

    metrics = {
        "waits": evaluate_waits(truth, predicted, args.tolerance),
        "throughput": evaluate_throughput(gt_completed, pred_completed, duration),
    }
    if series is not None:
        metrics["predictions"] = evaluate_predictions(gt_completed, series)

    name = args.predicted_waits.stem.removesuffix("_waits")
    output = args.output or config.OUTPUTS_DIR / "metrics" / f"{name}_wait_eval.json"
    save_json(
        {
            "project": "QueueVision",
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "predicted_waits": str(args.predicted_waits),
            "ground_truth": str(args.ground_truth),
            "timeseries": str(args.timeseries) if args.timeseries else None,
            "tolerance_seconds": args.tolerance,
            "duration_seconds": duration,
            "metrics": metrics,
        },
        output,
    )
    waits = metrics["waits"]
    print(
        f"{name}: matched {waits['matched']}/{waits['gt_completed']} ground-truth waits "
        f"({waits['missed']} missed, {waits['extra']} extra), wait MAE {waits['wait_mae_s']} s, "
        f"bias {waits['wait_bias_s']} s -> {output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
