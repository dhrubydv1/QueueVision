"""QueueVision Sprint 1: detect and track people in a recorded video.

Example::

    python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --device auto

Writes ``outputs/videos/<name>_tracked.mp4``, ``outputs/metrics/<name>_metrics.json``
and ``outputs/logs/<name>_tracking.log``.
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Allow ``python ai_engine/run_tracking.py`` as well as ``python -m``.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from ai_engine import config
from ai_engine.detection.detector import ModelLoadError, PersonDetector
from ai_engine.metrics.performance import PerformanceTracker, save_json
from ai_engine.tracking.tracker import PersonTracker
from ai_engine.utils.device import DeviceUnavailableError
from ai_engine.video.video_loader import VideoLoader, VideoLoadError, VideoWriter
from ai_engine.visualization.metrics_overlay import OverlayStats, draw_metrics_panel, draw_people

LOGGER = logging.getLogger("queuevision")
WINDOW_NAME = "QueueVision - press q to stop"


def model_display_name(model_path: Path) -> str:
    """Turn ``yolo11n.pt`` into ``YOLO11n`` for reports."""
    stem = model_path.stem
    return "YOLO" + stem[4:] if stem.lower().startswith("yolo") else stem


def output_paths(video_path: Path, output_dir: Path) -> dict[str, Path]:
    name = video_path.stem
    return {
        "video": output_dir / "videos" / f"{name}_tracked.mp4",
        "metrics": output_dir / "metrics" / f"{name}_metrics.json",
        "log": output_dir / "logs" / f"{name}_tracking.log",
    }


def process_video(
    video_path: Path,
    tracker: PersonTracker,
    output_dir: Path = config.OUTPUTS_DIR,
    show: bool = False,
    max_frames: int | None = None,
    run_started_at: float | None = None,
    extra_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Track people through one video, write the annotated MP4 and metrics JSON.

    Returns the metrics report that was saved to disk.
    """
    run_started_at = run_started_at if run_started_at is not None else time.perf_counter()
    paths = output_paths(video_path, output_dir)
    performance = PerformanceTracker()
    detector = tracker.detector
    stopped_early = False

    with VideoLoader(video_path) as loader:
        metadata = loader.metadata
        LOGGER.info(
            "Video %s: %dx%d, %.2f FPS, %d frames, %.1fs",
            video_path.name, metadata.width, metadata.height,
            metadata.fps, metadata.frame_count, metadata.duration_seconds,
        )
        # Warm-up keeps one-time model setup out of the FPS measurements.
        tracker.warmup((metadata.height, metadata.width, 3))

        with VideoWriter(paths["video"], metadata.fps, (metadata.width, metadata.height)) as writer:
            try:
                while max_frames is None or performance.frames_processed < max_frames:
                    frame_started_at = time.perf_counter()
                    frame = loader.read()
                    if frame is None:
                        break

                    people = tracker.update(frame)
                    inference_seconds = tracker.last_inference_seconds

                    draw_people(frame, people)
                    draw_metrics_panel(
                        frame,
                        OverlayStats(
                            frame_number=performance.frames_processed + 1,
                            total_frames=metadata.frame_count,
                            people_in_frame=len(people),
                            # Rolling averages over recent frames, for display only.
                            inference_fps=performance.rolling_inference_fps
                            or (1 / inference_seconds if inference_seconds > 0 else 0.0),
                            processing_fps=performance.rolling_processing_fps,
                            device=detector.device,
                        ),
                    )
                    writer.write(frame)

                    performance.record_frame(
                        inference_seconds=inference_seconds,
                        processing_seconds=time.perf_counter() - frame_started_at,
                        people_count=len(people),
                        track_ids=[person.track_id for person in people],
                    )

                    if performance.frames_processed % 100 == 0:
                        LOGGER.info(
                            "Frame %d: %d people, inference %.1f FPS, processing %.1f FPS",
                            performance.frames_processed, len(people),
                            performance.average_inference_fps,
                            performance.average_processing_fps,
                        )

                    # Display time is deliberately excluded from processing FPS.
                    if show:
                        cv2.imshow(WINDOW_NAME, frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            stopped_early = True
                            break
            finally:
                if show:
                    cv2.destroyAllWindows()

    if performance.frames_processed == 0:
        raise VideoLoadError(f"No frames could be read from {video_path}")

    report = performance.build_report(
        device=detector.device,
        total_runtime_seconds=time.perf_counter() - run_started_at,
        model=model_display_name(detector.model_path),
        extra={
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "video": metadata.to_dict(),
            "configuration": {
                "model_path": str(detector.model_path),
                "confidence": detector.confidence,
                "image_size": detector.image_size,
                "tracker_config": tracker.tracker_config,
                "max_frames": max_frames,
                "display_enabled": show,
            },
            "stopped_early": stopped_early,
            "outputs": {key: str(path) for key, path in paths.items()},
            "notes": [
                "average_inference_fps covers YOLO detection plus ByteTrack update, "
                "with device synchronization.",
                "average_processing_fps covers decoding, inference, drawing and video "
                "writing; live display is excluded.",
                "total_runtime_seconds includes model loading and warm-up.",
                "unique_track_ids_assigned is not a count of unique people: IDs can "
                "change after occlusion.",
            ],
            **(extra_report or {}),
        },
    )
    save_json(report, paths["metrics"])
    LOGGER.info("Saved annotated video: %s", paths["video"])
    LOGGER.info("Saved metrics: %s", paths["metrics"])
    return report


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    for handler in (logging.StreamHandler(), logging.FileHandler(log_path, mode="w")):
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and track people in a recorded video (QueueVision Sprint 1)."
    )
    parser.add_argument("--video", type=Path, required=True, help="Input video path")
    parser.add_argument(
        "--model", type=Path, default=config.DEFAULT_MODEL_PATH,
        help="YOLO weights path or name; downloaded if missing (default: %(default)s)",
    )
    parser.add_argument(
        "--output", type=Path, default=config.OUTPUTS_DIR,
        help="Output root containing videos/, metrics/ and logs/ (default: %(default)s)",
    )
    parser.add_argument(
        "--device", default=config.DEFAULT_DEVICE, choices=["auto", "cpu", "mps", "cuda"],
        help="Execution device (default: %(default)s)",
    )
    parser.add_argument("--confidence", type=float, default=config.CONFIDENCE)
    parser.add_argument("--imgsz", type=int, default=config.IMAGE_SIZE)
    parser.add_argument("--show", action="store_true", help="Display frames while processing")
    parser.add_argument(
        "--max-frames", type=int, default=None, help="Stop after this many frames"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    run_started_at = time.perf_counter()
    args = parse_arguments(argv)
    video_path = args.video.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    configure_logging(output_paths(video_path, output_dir)["log"])

    try:
        model_load_started_at = time.perf_counter()
        detector = PersonDetector(
            model_path=args.model,
            confidence=args.confidence,
            image_size=args.imgsz,
            device=args.device,
        )
        model_load_seconds = time.perf_counter() - model_load_started_at
        LOGGER.info(
            "Loaded %s on device '%s' in %.2fs", detector.model_path.name,
            detector.device, model_load_seconds,
        )
        report = process_video(
            video_path,
            PersonTracker(detector),
            output_dir=output_dir,
            show=args.show,
            max_frames=args.max_frames,
            run_started_at=run_started_at,
            extra_report={
                "environment": {
                    "python": platform.python_version(),
                    "platform": platform.platform(),
                    "machine": platform.machine(),
                },
                "model_load_seconds": round(model_load_seconds, 4),
            },
        )
    except (VideoLoadError, ModelLoadError, DeviceUnavailableError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1

    performance = report["performance"]
    LOGGER.info(
        "Done: %d frames, inference %.2f FPS, processing %.2f FPS, max %d people/frame",
        report["frames_processed"], performance["average_inference_fps"],
        performance["average_processing_fps"],
        report["statistics"]["maximum_people_detected_in_a_frame"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
