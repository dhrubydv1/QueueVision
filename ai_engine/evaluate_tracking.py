"""Evaluate a MOT-format track file against MOT17 ground truth.

Example::

    python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --save-mot
    python ai_engine/evaluate_tracking.py \\
        --tracks outputs/tracks/mot17_09_mot.txt \\
        --sequence-dir datasets/annotations/mot17/MOT17-09 \\
        --video datasets/raw/mot17_09.mp4

Ground truth is scaled from ``seqinfo.ini``'s ``imWidth x imHeight`` to the
video's resolution, because the local MOT17 videos are 960x540 previews of
1920x1080 sequences.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Running the file directly puts ai_engine/ on sys.path, where ai_engine/queue
    # would shadow the standard-library ``queue`` module. Use the project root instead.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ai_engine import config
from ai_engine.evaluation.mot_eval import (
    IOU_THRESHOLD,
    evaluate_tracks,
    evaluated_frames,
    load_ground_truth,
    load_tracks,
    read_seqinfo,
)
from ai_engine.metrics.performance import save_json
from ai_engine.video.video_loader import VideoLoader


class SequenceMismatchError(ValueError):
    """The video does not correspond frame-for-frame to the ground truth."""


def evaluate(
    tracks_path: Path,
    sequence_dir: Path,
    video_path: Path,
    frame_stride: int = 1,
) -> dict[str, Any]:
    """Return the evaluation report for one sequence."""
    seqinfo = read_seqinfo(sequence_dir / "seqinfo.ini")
    with VideoLoader(video_path) as loader:
        metadata = loader.metadata
    if metadata.frame_count != seqinfo["seq_length"]:
        raise SequenceMismatchError(
            f"Video has {metadata.frame_count} frames but {seqinfo['name']} has "
            f"{seqinfo['seq_length']}; refusing to evaluate misaligned frames."
        )
    scale = (metadata.width / seqinfo["width"], metadata.height / seqinfo["height"])
    ground_truth = load_ground_truth(sequence_dir / "gt" / "gt.txt", scale)
    frames = evaluated_frames(seqinfo["seq_length"], frame_stride)
    metrics = evaluate_tracks(ground_truth, load_tracks(tracks_path), frames)
    return {
        "project": "QueueVision",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sequence": seqinfo["name"],
        "tracks": str(tracks_path),
        "ground_truth": str(sequence_dir / "gt" / "gt.txt"),
        "video": str(video_path),
        "ground_truth_scale": [round(scale[0], 6), round(scale[1], 6)],
        "frame_stride": frame_stride,
        "frames_evaluated": len(frames),
        "iou_threshold": IOU_THRESHOLD,
        "scored": "class 1 (pedestrian) with consider flag 1; hypotheses matched to "
        "distractor classes 2, 7, 8, 12 removed before scoring",
        "metrics": metrics,
    }


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate tracks against MOT17 ground truth.")
    parser.add_argument("--tracks", type=Path, required=True, help="MOT-format track file")
    parser.add_argument(
        "--sequence-dir",
        type=Path,
        required=True,
        help="Folder containing seqinfo.ini and gt/gt.txt",
    )
    parser.add_argument("--video", type=Path, required=True, help="Video the tracks came from")
    parser.add_argument("--frame-stride", type=int, default=1, help="Stride used when tracking")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Report path (default: outputs/metrics/<tracks stem>_tracking_eval.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        report = evaluate(args.tracks, args.sequence_dir, args.video, args.frame_stride)
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    stem = args.tracks.stem.removesuffix("_mot")
    output = args.output or config.OUTPUTS_DIR / "metrics" / f"{stem}_tracking_eval.json"
    save_json(report, output)
    metrics = report["metrics"]
    print(
        f"{report['sequence']}: MOTA {metrics['mota']:.3f}  IDF1 {metrics['idf1']:.3f}  "
        f"ID switches {metrics['num_switches']}  precision {metrics['precision']:.3f}  "
        f"recall {metrics['recall']:.3f}  -> {output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
