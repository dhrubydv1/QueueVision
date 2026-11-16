"""Side-by-side error attribution at IoU 0.5 and 0.3 for one configuration.

Reads the output of ``ai_engine/analyze_queue_errors.py`` (re-run on the saved tracks) and writes
a compact summary per split and window: category totals and per-frame means at each threshold.
Diagnosis only; it never decides anything.

Usage::

    .venv/bin/python -m ai_engine.analyze_queue_errors --clips ... --splits-dir datasets/splits \\
        --output outputs/s9_eval/errors_640.json
    .venv/bin/python scripts/summarize_attribution.py --errors outputs/s9_eval/errors_640.json \\
        --output outputs/s9_eval/attribution_default.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

THRESHOLDS = ("iou_0.5", "iou_0.3")
SCALARS = ("frames", "raw_count_total", "gt_queueing_total", "fp_total", "fn_total")
MEANS = ("mean_raw", "mean_gt", "mean_fp_total", "mean_fn_total", "bias")


def summarize_block(pooled_block: dict) -> dict:
    out: dict = {}
    for threshold in THRESHOLDS:
        for window, data in pooled_block[threshold].items():
            cell = {k: data[k] for k in SCALARS + MEANS}
            cell["totals"] = data["totals"]
            cell["per_frame"] = data["mean_per_frame"]
            out.setdefault(window, {})[threshold] = cell
    return out


def build_summary(errors: dict, variant: str = "as_configured") -> dict:
    entry = errors["variants"][variant]
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "ai_engine/analyze_queue_errors.py on the saved tracks (raw count, not smoothed)",
        "variant": variant,
        "iou_thresholds": errors.get("iou_thresholds"),
        "from_seconds": errors.get("from_seconds"),
        "note": "windows: from5s is the protocol window; all = every annotated frame",
        "splits": {
            group: {"clips": entry["pooled_clip_membership"][group], **summarize_block(block)}
            for group, block in entry["pooled"].items()
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--errors", type=Path, required=True)
    p.add_argument("--variant", default="as_configured")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        summary = build_summary(json.loads(args.errors.read_text(encoding="utf-8")), args.variant)
    except (OSError, KeyError, ValueError) as error:
        print(f"ERROR: {error!r}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
