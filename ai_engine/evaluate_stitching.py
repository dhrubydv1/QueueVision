"""Measure track stitching: identity quality on MOT17 and stitch statistics on CAD clips.

Example::

    python ai_engine/evaluate_stitching.py \\
        --sequences mot17_04 mot17_09 --tracks-dir outputs/s5_tracks/mot17/tracks \\
        --camera configs/cameras/example_queue.yaml \\
        --output outputs/s5_replay/stitch_eval_mot17.json \\
        --cad-clips cad_seq10 cad_seq25 --cad-tracks-dir outputs/s5_tracks/cad/tracks \\
        --cad-output outputs/s5_replay/stitch_stats_cad.json

``mot17_04`` is matched to ``<gt-root>/MOT17-04``. Ground truth is scaled from
the sequence's ``imWidth x imHeight`` to the processed video, like
``evaluate_tracking.py``. Configurations: ``no_stitching``, ``legacy``, ``guarded``.
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

from ai_engine.evaluate_tracking import SequenceMismatchError
from ai_engine.evaluation.mot_eval import (
    evaluated_frames,
    load_ground_truth,
    load_tracks,
    read_seqinfo,
)
from ai_engine.evaluation.replay import read_mot_tracks, video_properties
from ai_engine.evaluation.stitch_eval import (
    CONFIG_NAMES,
    count_unique_tracks,
    evaluate_configuration,
    stitch_statistics,
)
from ai_engine.queue.camera_config import CameraConfigError, load_camera_config

TABLE_COLUMNS = ("mota", "idf1", "idp", "idr", "num_switches", "num_fragmentations")


def evaluate_sequence(
    name: str, tracks_dir: Path, video_dir: Path, gt_root: Path, camera_path: Path
) -> dict[str, Any]:
    sequence_dir = gt_root / f"MOT17-{name.rsplit('_', 1)[1]}"
    seqinfo = read_seqinfo(sequence_dir / "seqinfo.ini")
    fps, size, frame_count = video_properties(video_dir / f"{name}.mp4")
    if frame_count != seqinfo["seq_length"]:
        raise SequenceMismatchError(
            f"{name}: video has {frame_count} frames, sequence has {seqinfo['seq_length']}"
        )
    scale = (size[0] / seqinfo["width"], size[1] / seqinfo["height"])
    ground_truth = load_ground_truth(sequence_dir / "gt" / "gt.txt", scale)
    track_path = tracks_dir / f"{name}_mot.txt"
    detections = read_mot_tracks(track_path)
    tracks = load_tracks(track_path)
    frames = evaluated_frames(seqinfo["seq_length"])
    camera = load_camera_config(camera_path)
    configs = {
        config_name: evaluate_configuration(
            config_name, camera, detections, tracks, ground_truth, fps, size, frames
        )
        for config_name in CONFIG_NAMES
    }
    return {
        "sequence": seqinfo["name"],
        "tracks": str(track_path),
        "ground_truth": str(sequence_dir / "gt" / "gt.txt"),
        "ground_truth_scale": [round(scale[0], 6), round(scale[1], 6)],
        "frames": len(frames),
        "input_tracks": count_unique_tracks(tracks),
        "configs": configs,
    }


def format_table(report: dict[str, Any]) -> str:
    head = f"{'sequence':<16}{'config':<14}" + "".join(f"{c[:9]:>10}" for c in TABLE_COLUMNS)
    head += f"{'zoneIDsw':>10}{'merges':>8}{'correct':>9}{'incorrect':>10}{'unverif':>9}{'precision':>10}"
    lines = [head]
    for seq in report["sequences"].values():
        for name, entry in seq["configs"].items():
            m, mc = entry["metrics"], entry["merge_correctness"]
            precision = "-" if mc["merge_precision"] is None else f"{mc['merge_precision']:.3f}"
            cells = "".join(
                f"{m[c]:>10.3f}" if isinstance(m[c], float) else f"{m[c]:>10}" for c in TABLE_COLUMNS
            )
            lines.append(
                f"{seq['sequence']:<16}{name:<14}{cells}{entry['zone_only']['num_switches']:>10}"
                f"{mc['merges']:>8}{mc['correct']:>9}{mc['incorrect']:>10}{mc['unverifiable']:>9}"
                f"{precision:>10}"
            )
    return "\n".join(lines)


def cad_statistics(clips: list[str], tracks_dir: Path, video_dir: Path, camera_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for clip in clips:
        fps, size, frame_count = video_properties(video_dir / f"{clip}.mp4")
        camera = load_camera_config(camera_dir / f"{clip}.yaml")
        out[clip] = stitch_statistics(
            read_mot_tracks(tracks_dir / f"{clip}_mot.txt"), camera, fps, size, frame_count
        )
    return out


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate track stitching.")
    parser.add_argument("--sequences", nargs="*", default=[], help="MOT17 videos, e.g. mot17_04")
    parser.add_argument("--tracks-dir", type=Path, help="Folder with <sequence>_mot.txt")
    parser.add_argument("--video-dir", type=Path, default=Path("datasets/raw"))
    parser.add_argument("--gt-root", type=Path, default=Path("datasets/annotations/mot17"))
    parser.add_argument("--camera", type=Path, default=Path("configs/cameras/example_queue.yaml"))
    parser.add_argument("--output", type=Path, help="JSON for the MOT17 identity evaluation")
    parser.add_argument("--cad-clips", nargs="*", default=[], help="CAD clips for stitch statistics")
    parser.add_argument("--cad-tracks-dir", type=Path)
    parser.add_argument("--cad-camera-dir", type=Path, default=Path("configs/cameras"))
    parser.add_argument("--cad-output", type=Path, help="JSON for the CAD stitch statistics")
    args = parser.parse_args(argv)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        if args.sequences:
            if args.tracks_dir is None:
                parser.error("--tracks-dir is required with --sequences")
            report = {
                "generated_at": stamp,
                "camera": str(args.camera),
                "iou_threshold": 0.5,
                "note": "IDs rewritten to the stitcher's person keys; HOTA not available "
                "(motmetrics has no HOTA, TrackEval not installed). zone_only filters ground truth and "
                "tracks to boxes whose foot point is in the camera's queue zone. A guarded link takes effect "
                "after min_lost_seconds, so the new track carries a provisional ID until then; "
                "metrics_with_final_keys rewrites every track to its final key instead.",
                "sequences": {
                    s: evaluate_sequence(s, args.tracks_dir, args.video_dir, args.gt_root, args.camera)
                    for s in args.sequences
                },
            }
            print(format_table(report))
            if args.output:
                _write(args.output, report)
        if args.cad_clips:
            if args.cad_tracks_dir is None:
                parser.error("--cad-tracks-dir is required with --cad-clips")
            stats = cad_statistics(args.cad_clips, args.cad_tracks_dir, args.video_dir, args.cad_camera_dir)
            for clip, entry in stats.items():
                print(f"{clip}: legacy {entry['legacy']['merges']} merges; guarded {entry['guarded']}")
            if args.cad_output:
                _write(args.cad_output, {"generated_at": stamp, "clips": stats})
    except (OSError, ValueError, CameraConfigError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
