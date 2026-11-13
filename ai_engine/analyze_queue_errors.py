"""Explain where the queue count's error comes from, using per-person ground-truth boxes.

Example::

    python ai_engine/analyze_queue_errors.py \\
        --clips cad_seq10 cad_seq25 --tracks-dir outputs/s5_tracks/cad/tracks \\
        --splits-dir datasets/splits --output outputs/s6_analysis/errors_640.json

Replays saved tracks (see ``replay_queue.py``; ``--variants`` uses the same override mechanism) and
splits the raw count at each annotated frame into tp / fp_label / fp_unmatched / counted_not_visible
and the GT queueing count into tp / fn_* reasons. Diagnosis only; nothing is tuned.
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

from ai_engine.evaluation.count_errors import (
    DEFAULT_IOU,
    FN_CATEGORIES,
    SENSITIVITY_IOU,
    ClipAnalysis,
    FrameAnalysis,
    analyze_clip,
    pooled_protocol_bias,
    read_person_boxes,
    select_frames,
    summarize,
)
from ai_engine.evaluation.replay import (
    FROM_SECONDS,
    apply_overrides,
    read_mot_tracks,
    read_split_file,
    video_properties,
)
from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from ai_engine.replay_queue import BASELINE_NAME, load_variants, variant_overrides

ACTION_BGR = {
    "queueing": (0, 200, 0),
    "waiting": (255, 160, 0),
    "crossing": (0, 200, 255),
    "walking": (255, 0, 255),
    "talking": (200, 200, 0),
    "NA": (160, 160, 160),
}


def _groups(clips: list[str], splits: dict[str, list[str]]) -> dict[str, list[str]]:
    groups = {"all_clips": list(clips)}
    for name, members in splits.items():
        chosen = [c for c in members if c in clips]
        if chosen:
            groups[name] = chosen
    return groups


def run_analysis(
    clips: list[str],
    variants: list[dict[str, Any]],
    tracks_dir: Path,
    video_dir: Path,
    camera_dir: Path,
    annotation_dir: Path,
    splits: dict[str, list[str]],
) -> tuple[dict[str, Any], dict[str, dict[str, dict[float, ClipAnalysis]]]]:
    """Return the JSON-ready results and the raw per-variant, per-clip analyses."""
    cameras = {c: load_camera_config(camera_dir / f"{c}.yaml") for c in clips}
    tracks = {c: read_mot_tracks(tracks_dir / f"{c}_mot.txt") for c in clips}
    props = {c: video_properties(video_dir / f"{c}.mp4") for c in clips}
    gts = {c: read_person_boxes(annotation_dir / f"{c}_person_boxes.csv") for c in clips}
    groups = _groups(clips, splits)
    results: dict[str, Any] = {}
    raw: dict[str, dict[str, dict[float, ClipAnalysis]]] = {}
    for variant in variants:
        overrides = variant_overrides(variant)
        per_clip: dict[str, dict[float, ClipAnalysis]] = {}
        for clip in clips:
            fps, size, frame_count = props[clip]
            camera = apply_overrides(cameras[clip], overrides)
            per_clip[clip] = analyze_clip(tracks[clip], camera, fps, size, frame_count, gts[clip])
        raw[variant["name"]] = per_clip
        entry: dict[str, Any] = {"overrides": overrides, "per_clip": {}, "pooled": {}}
        for threshold in (DEFAULT_IOU, SENSITIVITY_IOU):
            key = f"iou_{threshold}"
            for window, start in (("all", 0.0), ("from5s", FROM_SECONDS)):
                for clip in clips:
                    frames = select_frames(per_clip[clip][threshold].frames, start)
                    entry["per_clip"].setdefault(clip, {}).setdefault(key, {})[window] = summarize(frames)
                for group, members in groups.items():
                    frames = [f for c in members for f in select_frames(per_clip[c][threshold].frames, start)]
                    entry["pooled"].setdefault(group, {}).setdefault(key, {})[window] = summarize(frames)
        for group, members in groups.items():
            analyses = [per_clip[c][DEFAULT_IOU] for c in members]
            entry["pooled"][group]["replay_protocol_raw_from5s"] = pooled_protocol_bias(analyses)
        entry["pooled_clip_membership"] = groups
        entry["per_frame_iou_0.5"] = {
            c: [
                {
                    "time_s": f.time_s,
                    "frame": f.frame,
                    "raw": f.raw_count,
                    "gt_queueing": f.gt_queueing,
                    "error": f.error,
                    "counts": f.counts,
                }
                for f in per_clip[c][DEFAULT_IOU].frames
            ]
            for c in clips
        }
        results[variant["name"]] = entry
    return results, raw


def _row(label: str, s: dict[str, Any], keys: list[str]) -> str:
    cells = "".join(f"{s['totals'].get(k, 0):>9}" for k in keys)
    return f"{label:<14}{s['frames']:>7}{s['raw_count_total']:>7}{s['gt_queueing_total']:>7}{cells}"


def format_tables(results: dict[str, Any], clips: list[str], groups: list[str]) -> str:
    lines: list[str] = []
    for name, entry in results.items():
        labels = sorted(
            {
                k
                for part in [*entry["per_clip"].values(), *entry["pooled"].values()]
                for k in part["iou_0.5"]["from5s"]["totals"]
                if k.startswith("fp_label:")
            }
        )
        keys = [
            "tp",
            *labels,
            "fp_unmatched",
            "counted_not_visible",
            *FN_CATEGORIES,
            "gt_queueing_outside_zone",
        ]
        names = {
            "fp_unmatched": "fp_unm",
            "counted_not_visible": "cnv",
            "fn_no_detection": "fn_nodet",
            "fn_foot_outside_zone": "fn_foot",
            "fn_candidate_dwell": "fn_dwell",
            "fn_candidate_speed": "fn_speed",
            "fn_other": "fn_other",
            "gt_queueing_outside_zone": "gt_outz",
        }
        short = [names.get(k, k.replace("fp_label:", "fpL_")) for k in keys]
        header = f"{'':<14}{'frames':>7}{'raw':>7}{'gt':>7}" + "".join(f"{k:>9}" for k in short)
        for thr in ("iou_0.5", "iou_0.3"):
            lines.append(f"== {name}: category totals from 5 s, {thr} (sum over annotated frames) ==")
            lines.append(header)
            for clip in clips:
                lines.append(_row(clip, entry["per_clip"][clip][thr]["from5s"], keys))
            for group in groups:
                lines.append(_row(f"[{group}]", entry["pooled"][group][thr]["from5s"], keys))
            lines.append("")
        lines.append(f"== {name}: mean per-frame contribution to bias, from 5 s, iou_0.5 ==")
        lines.append(
            f"{'':<14}{'fp_tot':>8}{'fn_tot':>8}{'bias':>8}{'protocol':>10}   fp parts / fn parts (per frame)"
        )
        for group in groups:
            s = entry["pooled"][group]["iou_0.5"]["from5s"]
            m = s["mean_per_frame"]
            proto = entry["pooled"][group]["replay_protocol_raw_from5s"].get("bias")
            fp_parts = ", ".join(
                f"{k.replace('fp_label:', 'label:')}={m[k]:.3f}"
                for k in [*(x for x in m if x.startswith("fp_label:")), "fp_unmatched", "counted_not_visible"]
            )
            fn_parts = ", ".join(f"{k[3:]}={m[k]:.3f}" for k in FN_CATEGORIES)
            proto_text = "-" if proto is None else f"{proto:+.3f}"
            lines.append(
                f"{'[' + group + ']':<14}{s['mean_fp_total']:>8.3f}{s['mean_fn_total']:>8.3f}"
                f"{s['bias']:>+8.3f}{proto_text:>10}   fp: {fp_parts} | fn: {fn_parts}"
            )
        lines.append(
            "  bias = mean(raw - gt) at exact annotated frames; protocol = replay_queue raw_from5s bias"
            " (nearest 1 Hz sample within 0.6 s)."
        )
        lines.append("")
    return "\n".join(lines)


def render_spot_check(
    video_path: Path, frame: FrameAnalysis, zone_polygon_px: Any, out_path: Path, title: str
) -> None:
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(video_path))
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame.frame)
        ok, image = capture.read()
    finally:
        capture.release()
    if not ok:
        raise ValueError(f"cannot read frame {frame.frame} of {video_path}")
    cv2.polylines(image, [np.asarray(zone_polygon_px, dtype=np.int32)], True, (255, 255, 0), 2)
    for box in frame.gt_boxes:
        x1, y1, x2, y2 = (round(v) for v in box.xyxy)
        colour = ACTION_BGR.get(box.action_name, (128, 128, 128))
        cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
        cv2.putText(image, box.action_name, (x1 + 2, y2 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1)
    for tid, (x1, y1, x2, y2), label in frame.counted:
        cv2.rectangle(image, (x1 + 3, y1 + 3), (x2 - 3, y2 - 3), (0, 0, 255), 2)
        cv2.putText(
            image, f"#{tid} {label}", (x1 + 4, y1 + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1
        )
    cv2.rectangle(image, (0, 0), (image.shape[1], 26), (0, 0, 0), -1)
    cv2.putText(image, title, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), image, [cv2.IMWRITE_JPEG_QUALITY, 90])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attribute queue-count error using per-person GT boxes.")
    parser.add_argument("--clips", nargs="+", required=True)
    parser.add_argument("--tracks-dir", type=Path, default=Path("outputs/s5_tracks/cad/tracks"))
    parser.add_argument("--video-dir", type=Path, default=Path("datasets/raw"))
    parser.add_argument("--camera-dir", type=Path, default=Path("configs/cameras"))
    parser.add_argument("--annotation-dir", type=Path, default=Path("datasets/annotations/cad"))
    parser.add_argument("--variants", type=Path, help="YAML list of variants (as in replay_queue.py)")
    parser.add_argument("--splits-dir", type=Path, help="Folder with val.txt and test.txt")
    parser.add_argument("--output", type=Path, help="JSON report path")
    parser.add_argument("--spot-checks", type=int, default=0, help="Save N largest-positive-error frames")
    parser.add_argument("--spot-check-split", default="val", help="Split the spot-check frames come from")
    parser.add_argument("--frames-dir", type=Path, help="Folder for spot-check JPEGs")
    args = parser.parse_args(argv)

    splits: dict[str, list[str]] = {}
    if args.splits_dir is not None:
        for split in ("val", "test"):
            members = read_split_file(args.splits_dir / f"{split}.txt")
            if members:
                splits[split] = members
    try:
        variants = load_variants(args.variants)
        results, raw = run_analysis(
            args.clips,
            variants,
            args.tracks_dir,
            args.video_dir,
            args.camera_dir,
            args.annotation_dir,
            splits,
        )
        groups = list(_groups(args.clips, splits))
        print(format_tables(results, args.clips, groups))
        if args.spot_checks and args.frames_dir is not None:
            members = _groups(args.clips, splits).get(args.spot_check_split, [])
            candidates = sorted(
                (
                    (f.error, c, f)
                    for c in members
                    for f in raw[BASELINE_NAME][c][DEFAULT_IOU].frames
                    if f.time_s >= FROM_SECONDS
                ),
                key=lambda item: (-item[0], item[1], item[2].frame),
            )[: args.spot_checks]
            for rank, (error, clip, frame) in enumerate(candidates, start=1):
                camera = load_camera_config(args.camera_dir / f"{clip}.yaml")
                _, size, _ = video_properties(args.video_dir / f"{clip}.mp4")
                from ai_engine.queue.zone import QueueZone

                polygon = QueueZone(camera.queue_zone).to_pixels(size)
                out = args.frames_dir / f"{rank}_{clip}_f{frame.frame}.jpg"
                title = (
                    f"{clip} f{frame.frame} t={frame.time_s:.1f}s "
                    f"raw={frame.raw_count} gt={frame.gt_queueing} err={error:+d}"
                )
                render_spot_check(args.video_dir / f"{clip}.mp4", frame, polygon, out, title)
                print(f"spot check: {out} ({title}) counts={frame.counts}")
    except (OSError, ValueError, CameraConfigError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "clips": args.clips,
                    "tracks_dir": str(args.tracks_dir),
                    "from_seconds": FROM_SECONDS,
                    "iou_thresholds": [DEFAULT_IOU, SENSITIVITY_IOU],
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
