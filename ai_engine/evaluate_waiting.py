"""Evaluate predicted waiting times against manually annotated ground truth.

Example::

    python ai_engine/evaluate_waiting.py \\
        --predicted-waits outputs/events/clip_waits.csv \\
        --ground-truth datasets/annotations/clip_waits_gt.csv \\
        --timeseries outputs/timeseries/clip_queue.csv

People are matched one-to-one by service time (``--tolerance`` seconds).
Writes ``outputs/metrics/<name>_wait_eval.json``.
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
    completed,
    evaluate_predictions,
    evaluate_throughput,
    evaluate_waits,
    read_ground_truth_waits,
    read_predicted_waits,
    read_timeseries_predictions,
)
from ai_engine.metrics.performance import save_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare predicted waiting times with ground truth.")
    parser.add_argument("--predicted-waits", type=Path, required=True, help="<name>_waits.csv")
    parser.add_argument("--ground-truth", type=Path, required=True, help="Filled <name>_waits_gt.csv")
    parser.add_argument("--timeseries", type=Path, help="<name>_queue.csv, to score the wait predictions")
    parser.add_argument(
        "--duration",
        type=float,
        help="Video duration in seconds for throughput (default: last timeseries time or service time)",
    )
    parser.add_argument("--tolerance", type=float, default=3.0, help="Max service-time difference in seconds")
    parser.add_argument("--output", type=Path, help="Report path (JSON)")
    args = parser.parse_args(argv)

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
