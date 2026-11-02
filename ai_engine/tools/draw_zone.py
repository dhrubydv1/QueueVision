"""Draw or set a camera's queue zone and save it to a camera YAML file.

Interactive (needs a display)::

    python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 \\
        --config configs/cameras/clip.yaml

    left click: add point   right click: undo   Enter: save   Esc: cancel

Non-interactive (works headless, e.g. in tests or over SSH)::

    python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 \\
        --config configs/cameras/clip.yaml \\
        --points "0.2,0.35 0.8,0.35 0.9,0.95 0.1,0.95" --preview zone.png
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[2])

import cv2
import numpy as np

from ai_engine.queue.camera_config import (
    CameraConfig,
    CameraConfigError,
    load_camera_config,
    save_camera_config,
    validate_polygon,
)
from ai_engine.queue.zone import QueueZone
from ai_engine.video.video_loader import VideoLoader, VideoLoadError
from ai_engine.visualization.metrics_overlay import draw_queue_zone

WINDOW_NAME = "QueueVision zone - L click add, R click undo, Enter save, Esc cancel"
ENTER_KEYS = {10, 13}
ESC_KEY = 27


def parse_points(text: str) -> tuple[tuple[float, float], ...]:
    """Parse ``"x1,y1 x2,y2 ..."`` into validated normalized points."""
    try:
        points = [tuple(float(v) for v in pair.split(",")) for pair in text.split()]
    except ValueError as error:
        raise CameraConfigError(f"Could not parse points '{text}': {error}") from error
    return validate_polygon(points, "points")


def read_frame(video: str | Path, frame_index: int = 0) -> np.ndarray:
    with VideoLoader(video) as loader:
        for _ in range(frame_index):
            if not loader.skip():
                raise VideoLoadError(f"Video has fewer than {frame_index + 1} frames")
        frame = loader.read()
    if frame is None:
        raise VideoLoadError(f"Could not read a frame from {video}")
    return frame


def update_config(
    config_path: Path, points: tuple[tuple[float, float], ...], camera_id: str | None = None
) -> CameraConfig:
    """Replace the zone in an existing config, or create a new one with defaults."""
    if config_path.exists():
        camera = dataclasses.replace(load_camera_config(config_path), queue_zone=points)
        if camera_id:
            camera = dataclasses.replace(camera, camera_id=camera_id)
    else:
        camera = CameraConfig(camera_id=camera_id or config_path.stem, queue_zone=points)
    save_camera_config(camera, config_path)
    return camera


def render_preview(frame: np.ndarray, points: tuple[tuple[float, float], ...]) -> np.ndarray:
    height, width = frame.shape[:2]
    preview = frame.copy()
    draw_queue_zone(preview, QueueZone(points).to_pixels((width, height)))
    return preview


def pick_points_interactively(frame: np.ndarray) -> tuple[tuple[float, float], ...] | None:
    """Let the user click a polygon. Returns None if cancelled."""
    height, width = frame.shape[:2]
    clicked: list[tuple[int, int]] = []

    def on_mouse(event: int, x: int, y: int, *_: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and clicked:
            clicked.pop()

    cv2.namedWindow(WINDOW_NAME)
    cv2.setMouseCallback(WINDOW_NAME, on_mouse)
    try:
        while True:
            canvas = frame.copy()
            if len(clicked) >= 3:
                draw_queue_zone(canvas, np.array(clicked, dtype=np.int32))
            for point in clicked:
                cv2.circle(canvas, point, 5, (255, 160, 0), -1)
            cv2.imshow(WINDOW_NAME, canvas)
            key = cv2.waitKey(30) & 0xFF
            if key == ESC_KEY:
                return None
            if key in ENTER_KEYS and len(clicked) >= 3:
                return tuple((round(x / width, 4), round(y / height, 4)) for x, y in clicked)
    finally:
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw a queue zone and save it to a camera YAML.")
    parser.add_argument("--video", required=True, help="Video whose frame is used as the background")
    parser.add_argument("--config", type=Path, required=True, help="Camera YAML to create or update")
    parser.add_argument("--camera-id", help="camera_id for a new config (default: file name)")
    parser.add_argument("--points", help='Non-interactive: normalized points "x1,y1 x2,y2 ..."')
    parser.add_argument("--preview", type=Path, help="Write a PNG of the frame with the zone drawn")
    parser.add_argument("--frame-index", type=int, default=0, help="Frame to draw on")
    args = parser.parse_args(argv)

    try:
        frame = read_frame(args.video, args.frame_index)
        if args.points:
            points = parse_points(args.points)
        else:
            points = pick_points_interactively(frame)
            if points is None:
                print("Cancelled; nothing saved.")
                return 1
        camera = update_config(args.config, points, args.camera_id)
    except (CameraConfigError, VideoLoadError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Saved zone for '{camera.camera_id}' to {args.config}: {list(points)}")
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.preview), render_preview(frame, points))
        print(f"Preview written to {args.preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
