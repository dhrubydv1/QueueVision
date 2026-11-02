"""Draw or set a camera's queue zone, service zone or service line in a camera YAML file.

Interactive (needs a display)::

    python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 \\
        --config configs/cameras/clip.yaml

    left click: add point   right click: undo   Enter: save   Esc: cancel

Non-interactive (works headless, e.g. in tests or over SSH)::

    python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 \\
        --config configs/cameras/clip.yaml \\
        --points "0.2,0.35 0.8,0.35 0.9,0.95 0.1,0.95" --preview zone.png

    # a service line (exactly 2 points) or service zone (3+ points); setting one removes the other
    python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 \\
        --config configs/cameras/clip.yaml --target service_line --points "0.3,0.2 0.7,0.2"
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
    validate_points,
    validate_polygon,
)
from ai_engine.queue.zone import QueueZone
from ai_engine.video.video_loader import VideoLoader, VideoLoadError
from ai_engine.visualization.metrics_overlay import draw_queue_zone, draw_service_point

WINDOW_NAME = "QueueVision zone - L click add, R click undo, Enter save, Esc cancel"
ENTER_KEYS = {10, 13}
ESC_KEY = 27
TARGETS = ("queue_zone", "service_zone", "service_line")


def parse_points(text: str, target: str = "queue_zone") -> tuple[tuple[float, float], ...]:
    """Parse ``"x1,y1 x2,y2 ..."`` into validated normalized points (2 for a line, 3+ for a zone)."""
    try:
        points = [tuple(float(v) for v in pair.split(",")) for pair in text.split()]
    except ValueError as error:
        raise CameraConfigError(f"Could not parse points '{text}': {error}") from error
    if target == "service_line":
        return validate_points(points, "points", exactly=2)
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
    config_path: Path,
    points: tuple[tuple[float, float], ...],
    camera_id: str | None = None,
    target: str = "queue_zone",
) -> CameraConfig:
    """Set ``target`` in an existing config, or create a new one with defaults.

    A service zone and a service line exclude each other, so setting one removes the other.
    """
    if target not in TARGETS:
        raise CameraConfigError(f"target must be one of {TARGETS}")
    changes: dict[str, object] = {target: points}
    if target == "service_zone":
        changes["service_line"] = None
    elif target == "service_line":
        changes["service_zone"] = None
    if config_path.exists():
        camera = dataclasses.replace(load_camera_config(config_path), **changes)
        if camera_id:
            camera = dataclasses.replace(camera, camera_id=camera_id)
    else:
        if target != "queue_zone":
            raise CameraConfigError(f"{config_path} does not exist; draw the queue zone first")
        camera = CameraConfig(camera_id=camera_id or config_path.stem, queue_zone=points)
    save_camera_config(camera, config_path)
    return camera


def render_preview(frame: np.ndarray, camera: CameraConfig | tuple[tuple[float, float], ...]) -> np.ndarray:
    """Draw the queue zone and service point of ``camera`` (or just a polygon of points)."""
    height, width = frame.shape[:2]
    preview = frame.copy()
    if not isinstance(camera, CameraConfig):
        camera = CameraConfig(camera_id="preview", queue_zone=camera)
    draw_queue_zone(preview, QueueZone(camera.queue_zone).to_pixels((width, height)))
    service = camera.service_zone or camera.service_line
    if service is not None:
        service_px = np.array([[round(x * width), round(y * height)] for x, y in service], dtype=np.int32)
        draw_service_point(preview, service_px)
    return preview


def pick_points_interactively(
    frame: np.ndarray, target: str = "queue_zone"
) -> tuple[tuple[float, float], ...] | None:
    """Let the user click a polygon (or two points for a line). Returns None if cancelled."""
    height, width = frame.shape[:2]
    clicked: list[tuple[int, int]] = []
    is_line = target == "service_line"

    def on_mouse(event: int, x: int, y: int, *_: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and not (is_line and len(clicked) >= 2):
            clicked.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN and clicked:
            clicked.pop()

    cv2.namedWindow(WINDOW_NAME)
    cv2.setMouseCallback(WINDOW_NAME, on_mouse)
    try:
        while True:
            canvas = frame.copy()
            if is_line and len(clicked) == 2:
                draw_service_point(canvas, np.array(clicked, dtype=np.int32))
            elif not is_line and len(clicked) >= 3:
                draw_queue_zone(canvas, np.array(clicked, dtype=np.int32))
            for point in clicked:
                cv2.circle(canvas, point, 5, (255, 160, 0), -1)
            cv2.imshow(WINDOW_NAME, canvas)
            key = cv2.waitKey(30) & 0xFF
            if key == ESC_KEY:
                return None
            if key in ENTER_KEYS and len(clicked) >= (2 if is_line else 3):
                return tuple((round(x / width, 4), round(y / height, 4)) for x, y in clicked)
    finally:
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw a queue zone and save it to a camera YAML.")
    parser.add_argument("--video", required=True, help="Video whose frame is used as the background")
    parser.add_argument("--config", type=Path, required=True, help="Camera YAML to create or update")
    parser.add_argument(
        "--target",
        choices=TARGETS,
        default="queue_zone",
        help="What to set: the queue zone (default), a service zone (3+ points) or a service "
        "line (exactly 2 points). Setting a service zone removes the service line and vice versa",
    )
    parser.add_argument("--camera-id", help="camera_id for a new config (default: file name)")
    parser.add_argument("--points", help='Non-interactive: normalized points "x1,y1 x2,y2 ..."')
    parser.add_argument("--preview", type=Path, help="Write a PNG of the frame with the zone drawn")
    parser.add_argument("--frame-index", type=int, default=0, help="Frame to draw on")
    args = parser.parse_args(argv)

    try:
        frame = read_frame(args.video, args.frame_index)
        if args.points:
            points = parse_points(args.points, args.target)
        else:
            points = pick_points_interactively(frame, args.target)
            if points is None:
                print("Cancelled; nothing saved.")
                return 1
        camera = update_config(args.config, points, args.camera_id, args.target)
    except (CameraConfigError, VideoLoadError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Saved {args.target} for '{camera.camera_id}' to {args.config}: {list(points)}")
    if args.preview:
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.preview), render_preview(frame, camera))
        print(f"Preview written to {args.preview}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
