"""Extract frames for manual queue counting and write a CSV template.

Example::

    python ai_engine/tools/make_annotation_kit.py --video datasets/raw/clip.mp4 \\
        --camera-config configs/cameras/clip.yaml --every 5

Writes ``datasets/annotations/<name>_frames/t0005.0_f000150.png`` (zone drawn,
time/frame stamped) and ``datasets/annotations/<name>_queue_counts.csv`` with
columns ``time_s, frame, queue_count``. A person fills in ``queue_count``
by counting the people queuing in each image.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[2])

import cv2

from ai_engine import config
from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from ai_engine.queue.zone import QueueZone
from ai_engine.video.video_loader import VideoLoader, VideoLoadError, source_name
from ai_engine.visualization.metrics_overlay import draw_queue_zone

ANNOTATIONS_DIR = config.PROJECT_ROOT / "datasets" / "annotations"
CSV_COLUMNS = ("time_s", "frame", "queue_count")


def make_kit(
    video: Path,
    every_seconds: float = 5.0,
    camera_config: Path | None = None,
    output_dir: Path = ANNOTATIONS_DIR,
    force: bool = False,
) -> tuple[Path, Path, int]:
    """Create the frame folder and CSV template; return their paths and frame count."""
    if every_seconds <= 0:
        raise ValueError("--every must be positive")
    name = source_name(video)
    csv_path = output_dir / f"{name}_queue_counts.csv"
    frames_dir = output_dir / f"{name}_frames"
    if csv_path.exists() and not force:
        raise FileExistsError(f"{csv_path} exists (it may hold manual counts); use --force")
    zone = QueueZone(load_camera_config(camera_config).queue_zone) if camera_config else None
    frames_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    with VideoLoader(video) as loader:
        fps = loader.metadata.fps
        if fps <= 0:
            raise VideoLoadError("Video reports no FPS; cannot map frames to times")
        frame_size = (loader.metadata.width, loader.metadata.height)
        zone_px = zone.to_pixels(frame_size) if zone else None
        next_time = 0.0
        for frame_index, frame in loader:
            time_s = frame_index / fps
            if time_s + 1e-9 < next_time:
                continue
            next_time += every_seconds
            if zone_px is not None:
                draw_queue_zone(frame, zone_px)
            stamp = f"t={time_s:.1f}s  frame {frame_index}"
            cv2.rectangle(frame, (0, frame.shape[0] - 34), (300, frame.shape[0]), (0, 0, 0), -1)
            cv2.putText(
                frame,
                stamp,
                (8, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.imwrite(str(frames_dir / f"t{time_s:07.1f}_f{frame_index:06d}.png"), frame)
            rows.append((f"{time_s:.3f}", frame_index, ""))

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(rows)
    return frames_dir, csv_path, len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a manual queue-count annotation kit.")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--camera-config", type=Path, help="Draw this camera's queue zone on frames")
    parser.add_argument("--every", type=float, default=5.0, help="Seconds between frames (default 5)")
    parser.add_argument("--output-dir", type=Path, default=ANNOTATIONS_DIR)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing CSV template")
    args = parser.parse_args(argv)
    try:
        frames_dir, csv_path, count = make_kit(
            args.video, args.every, args.camera_config, args.output_dir, args.force
        )
    except (VideoLoadError, CameraConfigError, FileExistsError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {count} frames to {frames_dir} and template {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
