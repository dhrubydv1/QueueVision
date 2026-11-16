"""Clip-level bootstrap intervals for queue-length error (protocol v1), plus paired comparison.

Usage::

    # 1. per-frame errors of the current default (side file; queue_640.json is unchanged)
    .venv/bin/python -m ai_engine.replay_queue --clips cad_seq10 ... --tracks-dir <tracks> \\
        --splits-dir datasets/splits --frame-errors-output outputs/s9_eval/frame_errors_default.json
    # 2. intervals for one configuration
    .venv/bin/python scripts/evaluate_queue_intervals.py \\
        --frame-errors outputs/s9_eval/frame_errors_default.json \\
        --output outputs/s9_eval/intervals_default.json
    # 3. paired comparison of another configuration against the default
    .venv/bin/python scripts/evaluate_queue_intervals.py --frame-errors <default> --other <other> \\
        --output outputs/s9_eval/paired_<name>.json

Scored series: smoothed queue length, annotated frames from 5 s (the protocol's metric).
The per-frame bootstrap is labelled optimistic and never decides anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_engine.evaluation.bootstrap import (  # noqa: E402
    N_RESAMPLES,
    OPTIMISTIC_LABEL,
    SEED,
    paired_difference,
    pooled_intervals,
)
from ai_engine.evaluation.replay import read_split_file  # noqa: E402

SERIES = "smoothed_from5s"
VARIANT = "as_configured"


def load_frames(path: Path, variant: str) -> dict[str, list[tuple[float, float, int]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        per_clip = data["variants"][variant]
    except KeyError as error:
        raise ValueError(f"{path}: no variant {variant!r}") from error
    return {clip: [tuple(f) for f in series[SERIES]] for clip, series in per_clip.items()}


def groups(frames: dict, splits_dir: Path) -> dict[str, list[str]]:
    out = {"all": sorted(frames)}
    for name in ("val", "test"):
        members = [c for c in read_split_file(splits_dir / f"{name}.txt") if c in frames]
        if members:
            out[name] = members
    return out


def build_report(
    default: dict,
    other: dict | None,
    splits_dir: Path,
    n_resamples: int = N_RESAMPLES,
) -> dict:
    report: dict = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "protocol": "docs/evaluation_protocol.md v1",
        "series": f"{SERIES} (smoothed queue length, annotated frames from 5 s)",
        "bootstrap": {
            "unit": "clip (resample clips within the split, pooled metric over all frames of drawn clips)",
            "n_resamples": n_resamples,
            "seed": SEED,
            "interval": "95 % percentile",
            "per_frame_label": OPTIMISTIC_LABEL,
        },
        "splits": {},
    }
    for name, clips in groups(default, splits_dir).items():
        entry: dict = {"clips": clips}
        for unit in ("clip", "frame"):
            key = "clip_bootstrap" if unit == "clip" else "per_frame_bootstrap_optimistic"
            entry[key] = pooled_intervals(default, clips, unit, n_resamples)
        if other is not None:
            for unit in ("clip", "frame"):
                key = "paired_clip_bootstrap" if unit == "clip" else "paired_per_frame_optimistic"
                entry[key] = paired_difference(other, default, clips, unit, n_resamples)
            mae = entry["paired_clip_bootstrap"]["mae"]
            if mae["point"] is not None:
                entry["adoption_criterion_a"] = {
                    "delta_mae_new_minus_current": mae["point"],
                    "paired_half_width": mae["half_width"],
                    "improvement_beyond_half_width": (-mae["point"]) > mae["half_width"],
                }
        report["splits"][name] = entry
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--frame-errors", type=Path, required=True, help="Default config's frame-errors file")
    p.add_argument("--variant", default=VARIANT, help="Variant name in --frame-errors")
    p.add_argument("--other", type=Path, help="Another configuration's frame-errors file (paired)")
    p.add_argument("--other-variant", default=VARIANT)
    p.add_argument("--splits-dir", type=Path, default=Path("datasets/splits"))
    p.add_argument("--n-resamples", type=int, default=N_RESAMPLES)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        default = load_frames(args.frame_errors, args.variant)
        other = load_frames(args.other, args.other_variant) if args.other else None
        report = build_report(default, other, args.splits_dir, args.n_resamples)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, e in report["splits"].items():
        c = e["clip_bootstrap"]
        print(f"{name}: clips={c['n_clips']} frames={c['n_frames']}", end="")
        for m in ("mae", "bias", "within_one_rate"):
            print(f"  {m} {c[m]['point']:.3f} [{c[m]['low']:.3f}, {c[m]['high']:.3f}]", end="")
        print()
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
