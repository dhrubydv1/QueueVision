"""Replay saved tracks through queue-logic variants and compare them against manual counts.

Example::

    python ai_engine/replay_queue.py \\
        --clips cad_seq10 cad_seq25 --tracks-dir outputs/s5_tracks/cad/tracks \\
        --variants outputs/s5_replay/variants_a.yaml --splits-dir datasets/splits \\
        --output outputs/s5_replay/queue_variants_a.json

``variants.yaml`` is a list of ``{name, membership: {...}, stitching: {...}, smoothing: {...}}``.
A variant called ``as_configured`` (no overrides) is always included.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

import yaml

from ai_engine.evaluation.replay import (
    SCORE_KEYS,
    ClipScores,
    apply_overrides,
    pool_scores,
    read_mot_tracks,
    read_split_file,
    replay_queue,
    score_clip,
    video_properties,
)
from ai_engine.queue.camera_config import CameraConfig, CameraConfigError, load_camera_config
from ai_engine.queue.stitching import write_decisions_jsonl

BASELINE_NAME = "as_configured"
METRIC_NAMES = ("mae", "rmse", "bias", "within_one_rate", "matched")


def load_variants(path: Path | None) -> list[dict[str, Any]]:
    """Variants from YAML, with the ``as_configured`` baseline always first."""
    variants: list[dict[str, Any]] = [{"name": BASELINE_NAME}]
    if path is None:
        return variants
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of variants")
    for item in data:
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError(f"{path}: every variant needs a 'name'")
        if item["name"] == BASELINE_NAME:
            continue
        variants.append(item)
    names = [v["name"] for v in variants]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: duplicate variant names")
    return variants


def variant_overrides(variant: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: value for key, value in variant.items() if key != "name"}


def _fmt(value: Any, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _metric_cells(metrics: dict[str, Any]) -> list[str]:
    if not metrics.get("matched"):
        return ["-"] * len(METRIC_NAMES)
    return [
        _fmt(metrics.get("mae"), ".3f"),
        _fmt(metrics.get("rmse"), ".3f"),
        _fmt(metrics.get("bias"), "+.3f"),
        _fmt(metrics.get("within_one_rate"), ".3f"),
        str(metrics["matched"]),
    ]


def format_table(results: dict[str, Any], groups: list[str]) -> str:
    """Compact pooled table (one block per group) plus per-clip MAE from 5 s."""
    lines: list[str] = []
    width = max([24, *(len(name) + 2 for name in results)])
    for group in groups:
        lines.append(f"== pooled: {group} ==")
        header = f"{'variant':<{width}}{'series':<10}{'window':<7}" + "".join(
            f"{n:>16}" for n in METRIC_NAMES
        )
        lines.append(header)
        for name, entry in results.items():
            pooled = entry["pooled"].get(group)
            if pooled is None:
                continue
            for key in SCORE_KEYS:
                series, window = key.split("_")
                cells = _metric_cells(pooled[key])
                lines.append(f"{name:<{width}}{series:<10}{window:<7}" + "".join(f"{c:>16}" for c in cells))
        lines.append("")
    clips = list(next(iter(results.values()))["per_clip"]) if results else []
    lines.append("== per-clip MAE from 5 s (smoothed / raw) ==")
    lines.append(f"{'variant':<{width}}" + "".join(f"{c:>20}" for c in clips))
    for name, entry in results.items():
        cells = []
        for clip in clips:
            m = entry["per_clip"][clip]
            s, r = m["smoothed_from5s"].get("mae"), m["raw_from5s"].get("mae")
            cells.append(f"{_fmt(s, '.3f')} / {_fmt(r, '.3f')}")
        lines.append(f"{name:<{width}}" + "".join(f"{c:>20}" for c in cells))
    return "\n".join(lines)


def run_variants(
    clips: list[str],
    variants: list[dict[str, Any]],
    tracks_dir: Path,
    video_dir: Path,
    camera_dir: Path,
    annotation_dir: Path,
    splits: dict[str, list[str]],
    stitch_diagnostics_dir: Path | None = None,
) -> dict[str, Any]:
    cameras: dict[str, CameraConfig] = {c: load_camera_config(camera_dir / f"{c}.yaml") for c in clips}
    tracks = {c: read_mot_tracks(tracks_dir / f"{c}_mot.txt") for c in clips}
    props = {c: video_properties(video_dir / f"{c}.mp4") for c in clips}
    results: dict[str, Any] = {}
    for variant in variants:
        overrides = variant_overrides(variant)
        scores: dict[str, ClipScores] = {}
        extras: dict[str, Any] = {}
        for clip in clips:
            fps, size, frame_count = props[clip]
            camera = apply_overrides(cameras[clip], overrides)
            replay = replay_queue(
                tracks[clip],
                camera,
                fps,
                size,
                frame_count,
                stitch_diagnostics=True if stitch_diagnostics_dir is not None else None,
            )
            if stitch_diagnostics_dir is not None:
                write_decisions_jsonl(
                    stitch_diagnostics_dir / variant["name"] / f"{clip}_stitch_diagnostics.jsonl",
                    replay.stitch_decisions,
                )
            scores[clip] = score_clip(replay, annotation_dir / f"{clip}_queue_counts.csv")
            counts: dict[str, int] = {}
            for event in replay.events:
                counts[event.event_type] = counts.get(event.event_type, 0) + 1
            extras[clip] = {
                "event_counts": counts,
                "membership_state_counts_at_end": replay.report.get("membership", {}).get(
                    "state_counts_at_end"
                ),
            }
        pooled = {"all_clips": pool_scores(list(scores.values()))}
        for split_name, members in splits.items():
            chosen = [scores[c] for c in members if c in scores]
            if chosen:
                pooled[split_name] = pool_scores(chosen)
        results[variant["name"]] = {
            "overrides": overrides,
            "per_clip": {c: s.metrics() for c, s in scores.items()},
            "pooled": pooled,
            "pooled_clip_membership": {k: [c for c in v if c in scores] for k, v in splits.items()},
            "clip_extras": extras,
        }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay saved MOT tracks through queue-logic variants.")
    parser.add_argument("--clips", nargs="+", required=True, help="Clip names, e.g. cad_seq10")
    parser.add_argument("--tracks-dir", type=Path, required=True, help="Folder with <clip>_mot.txt")
    parser.add_argument("--video-dir", type=Path, default=Path("datasets/raw"))
    parser.add_argument("--camera-dir", type=Path, default=Path("configs/cameras"))
    parser.add_argument("--annotation-dir", type=Path, default=Path("datasets/annotations/cad"))
    parser.add_argument("--variants", type=Path, help="YAML list of variants")
    parser.add_argument("--splits-dir", type=Path, help="Folder with val.txt and test.txt")
    parser.add_argument("--output", type=Path, help="JSON report path")
    parser.add_argument(
        "--stitch-diagnostics-dir",
        type=Path,
        help="Write <dir>/<variant>/<clip>_stitch_diagnostics.jsonl (guarded stitching only)",
    )
    args = parser.parse_args(argv)

    splits: dict[str, list[str]] = {}
    if args.splits_dir is not None:
        for split in ("val", "test"):
            members = read_split_file(args.splits_dir / f"{split}.txt")
            if members:
                splits[split] = members
            else:
                print(f"note: no clips for split '{split}' in {args.splits_dir}", file=sys.stderr)
    try:
        variants = load_variants(args.variants)
        results = run_variants(
            args.clips,
            variants,
            args.tracks_dir,
            args.video_dir,
            args.camera_dir,
            args.annotation_dir,
            splits,
            args.stitch_diagnostics_dir,
        )
    except (OSError, ValueError, CameraConfigError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    groups = ["all_clips", *splits]
    print(format_table(results, groups))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "clips": args.clips,
                    "tracks_dir": str(args.tracks_dir),
                    "tolerance_seconds": 0.6,
                    "from_seconds": 5.0,
                    "variants": results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
