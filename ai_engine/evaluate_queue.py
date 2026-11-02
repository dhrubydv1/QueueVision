"""Evaluate predicted queue length against manual counts.

Example::

    python ai_engine/evaluate_queue.py \\
        --predicted outputs/timeseries/clip_queue.csv \\
        --annotations datasets/annotations/clip_queue_counts.csv

Writes ``outputs/metrics/<name>_queue_eval.json``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ai_engine import config
from ai_engine.evaluation.queue_eval import compare_counts, read_annotations, read_predictions
from ai_engine.metrics.performance import save_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare queue-length predictions with manual counts.")
    parser.add_argument("--predicted", type=Path, required=True, help="<name>_queue.csv time series")
    parser.add_argument("--annotations", type=Path, required=True, help="Filled <name>_queue_counts.csv")
    parser.add_argument(
        "--column",
        default="queue_length",
        choices=["queue_length", "raw_queue_count"],
        help="Prediction column to evaluate (default: smoothed queue_length)",
    )
    parser.add_argument("--tolerance", type=float, default=0.6, help="Max time difference in seconds")
    parser.add_argument("--output", type=Path, help="Report path")
    args = parser.parse_args(argv)

    try:
        predictions = read_predictions(args.predicted, args.column)
        annotations, blank = read_annotations(args.annotations)
    except (OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not annotations:
        print(f"ERROR: no filled queue_count values in {args.annotations}", file=sys.stderr)
        return 1

    metrics = compare_counts(predictions, annotations, args.tolerance)
    name = args.predicted.stem.removesuffix("_queue")
    output = args.output or config.OUTPUTS_DIR / "metrics" / f"{name}_queue_eval.json"
    save_json(
        {
            "project": "QueueVision",
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "predicted": str(args.predicted),
            "annotations": str(args.annotations),
            "column": args.column,
            "tolerance_seconds": args.tolerance,
            "blank_annotation_rows": blank,
            "metrics": metrics,
        },
        output,
    )
    print(f"{name}: {metrics} -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
