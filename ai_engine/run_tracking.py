"""QueueVision: detect and track people in a video, optionally measuring a queue.

Examples::

    python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4
    python ai_engine/run_tracking.py --video datasets/raw/clip.mp4 \\
        --camera-config configs/cameras/example_queue.yaml --frame-stride 3
    python ai_engine/run_tracking.py --video 0 --max-frames 300   # webcam

Outputs (``<name>`` is the video stem or ``camera<N>``):

* ``outputs/videos/<name>_tracked.mp4`` and ``outputs/metrics/<name>_metrics.json``
* ``outputs/logs/<name>_tracking.log``
* with ``--save-mot``: ``outputs/tracks/<name>_mot.txt``
* with ``--camera-config``: ``outputs/timeseries/<name>_queue.csv`` (queue length,
  service rate, predicted wait), ``outputs/events/<name>_zone_events.csv``,
  ``outputs/events/<name>_waits.csv`` (one row per completed or abandoned wait),
  ``outputs/events/<name>_alerts.csv`` (congestion alerts) and
  ``outputs/events/<name>_events.jsonl`` (every event, versioned schema); the
  metrics JSON gains waiting-time, service-rate and alert sections under ``queue``.
  ``--no-stitching`` turns ID-switch stitching off.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import platform
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    # Running the file directly puts ai_engine/ on sys.path, where ai_engine/queue
    # would shadow the standard-library ``queue`` module. Use the project root instead.
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

import cv2
import numpy as np

from ai_engine import config
from ai_engine.detection.detector import ModelLoadError, PersonDetector
from ai_engine.evaluation.mot_eval import MotWriter
from ai_engine.metrics.performance import PerformanceTracker, save_json
from ai_engine.metrics.queue_outputs import (
    AlertWriter,
    EventLogWriter,
    QueueSummary,
    QueueTimeSeriesWriter,
    WaitsWriter,
    ZoneEventWriter,
)
from ai_engine.pipeline import iter_frame_results, source_fps
from ai_engine.queue.analyzer import QueueAnalyzer, QueueFrameState
from ai_engine.queue.camera_config import CameraConfig, CameraConfigError, load_camera_config
from ai_engine.tracking.tracker import PersonTracker, TrackerSettings
from ai_engine.utils.device import DeviceUnavailableError
from ai_engine.video.transcode import original_copy_path, reencode_h264
from ai_engine.video.video_loader import VideoLoader, VideoLoadError, VideoWriter, source_name
from ai_engine.visualization.metrics_overlay import (
    PANEL_POSITIONS,
    OverlayStats,
    draw_alert_banner,
    draw_metrics_panel,
    draw_people,
    draw_queue_zone,
    draw_service_point,
    predicted_wait_text,
    service_rate_text,
)
from ai_engine.visualization.privacy import DEFAULT_HEAD_FRACTION, blur_heads

LOGGER = logging.getLogger("queuevision")
WINDOW_NAME = "QueueVision - press q to stop"


@dataclass(frozen=True)
class RunOptions:
    """Per-run output and processing options for :func:`process_video`."""

    output_dir: Path = config.OUTPUTS_DIR
    camera: CameraConfig | None = None
    frame_stride: int = 1
    max_frames: int | None = None
    show: bool = False
    save_mot: bool = False
    blur_heads: bool = False
    head_fraction: float = DEFAULT_HEAD_FRACTION
    panel_position: str = "top-left"
    h264: bool = True
    keep_mp4v: bool = False
    stitching: bool | None = None  # None: follow the camera config; False forces it off

    def __post_init__(self) -> None:
        if self.frame_stride < 1:
            raise ValueError("frame_stride must be >= 1")
        if self.panel_position not in PANEL_POSITIONS:
            raise ValueError(f"panel_position must be one of {PANEL_POSITIONS}")
        if not 0.0 < self.head_fraction <= 1.0:
            raise ValueError("head_fraction must be in (0, 1]")


@dataclass(frozen=True)
class FrameUpdate:
    """Progress report passed to the ``on_frame`` callback of :func:`process_video`."""

    frame_index: int  # source frame index
    frames_total: int | None
    timestamp_s: float
    people_in_frame: int
    processing_fps: float  # running average
    queue: QueueFrameState | None


def model_display_name(model_path: Path) -> str:
    """Turn ``yolo11n.pt`` into ``YOLO11n`` for reports."""
    stem = model_path.stem
    return "YOLO" + stem[4:] if stem.lower().startswith("yolo") else stem


def output_paths(name: str, output_dir: Path) -> dict[str, Path]:
    return {
        "video": output_dir / "videos" / f"{name}_tracked.mp4",
        "metrics": output_dir / "metrics" / f"{name}_metrics.json",
        "log": output_dir / "logs" / f"{name}_tracking.log",
        "tracks": output_dir / "tracks" / f"{name}_mot.txt",
        "timeseries": output_dir / "timeseries" / f"{name}_queue.csv",
        "events": output_dir / "events" / f"{name}_zone_events.csv",
        "waits": output_dir / "events" / f"{name}_waits.csv",
        "alerts": output_dir / "events" / f"{name}_alerts.csv",
        "event_log": output_dir / "events" / f"{name}_events.jsonl",
    }


def service_point_pixels(camera: CameraConfig, frame_size: tuple[int, int]) -> np.ndarray | None:
    """Pixel coordinates of the configured service zone or line, if any."""
    points = camera.service_zone or camera.service_line
    if points is None:
        return None
    width, height = frame_size
    return np.array([[round(x * width), round(y * height)] for x, y in points], dtype=np.int32)


def process_video(
    source: str | Path | int,
    tracker: PersonTracker,
    options: RunOptions | None = None,
    run_started_at: float | None = None,
    extra_report: dict[str, Any] | None = None,
    on_frame: Callable[[FrameUpdate], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Track people through one video and write the annotated MP4, metrics and CSVs.

    ``on_frame`` is called after each processed frame is drawn and written. ``should_stop`` is
    checked after ``on_frame``; returning True ends the run like pressing ``q`` (outputs are
    still written). Returns the metrics report that was saved to disk.
    """
    options = options or RunOptions()
    run_started_at = run_started_at if run_started_at is not None else time.perf_counter()
    name = source_name(source)
    paths = output_paths(name, options.output_dir)
    performance = PerformanceTracker()
    queue_summary = QueueSummary()
    detector = tracker.detector
    stopped_early = False
    camera = options.camera

    with contextlib.ExitStack() as stack:
        loader = stack.enter_context(VideoLoader(source))
        metadata = loader.metadata
        fps = source_fps(loader)
        effective_fps = fps / options.frame_stride
        frame_size = (metadata.width, metadata.height)
        LOGGER.info(
            "Source %s: %dx%d, %.2f FPS (%s), %s frames, stride %d -> %.2f processed FPS",
            name,
            metadata.width,
            metadata.height,
            metadata.fps,
            metadata.fps_source,
            metadata.frame_count or "unknown",
            options.frame_stride,
            effective_fps,
        )
        tracker.configure(effective_fps)
        # Warm-up keeps one-time model setup out of the FPS measurements.
        tracker.warmup((metadata.height, metadata.width, 3))

        analyzer = QueueAnalyzer(camera, stitching_enabled=options.stitching) if camera else None
        zone_px = analyzer.zone.to_pixels(frame_size) if analyzer else None
        service_px = service_point_pixels(camera, frame_size) if camera else None
        writer = stack.enter_context(VideoWriter(paths["video"], effective_fps, frame_size))
        mot_writer = stack.enter_context(MotWriter(paths["tracks"])) if options.save_mot else None
        series_writer = stack.enter_context(QueueTimeSeriesWriter(paths["timeseries"])) if camera else None
        event_writer = stack.enter_context(ZoneEventWriter(paths["events"])) if camera else None
        queue_writers = (
            [
                stack.enter_context(WaitsWriter(paths["waits"])),
                stack.enter_context(AlertWriter(paths["alerts"])),
                stack.enter_context(EventLogWriter(paths["event_log"])),
            ]
            if camera
            else []
        )
        first_timestamp: float | None = None
        last_timestamp = 0.0
        if options.show:
            stack.callback(cv2.destroyAllWindows)

        for result in iter_frame_results(loader, tracker, analyzer, options.frame_stride, options.max_frames):
            frame, people, queue = result.frame, result.people, result.queue
            if options.blur_heads:
                blur_heads(frame, people, options.head_fraction)
            if zone_px is not None:
                draw_queue_zone(frame, zone_px)
            if service_px is not None:
                draw_service_point(frame, service_px)
            draw_people(frame, people, queue.statuses if queue else None, queue.at_service if queue else None)
            if queue and queue.alert_active:
                # Keep the banner away from the metrics panel.
                banner_position = "bottom" if options.panel_position.startswith("top") else "top"
                draw_alert_banner(frame, queue.alert_reasons, banner_position)
            draw_metrics_panel(
                frame,
                OverlayStats(
                    frame_number=result.frame_index + 1,
                    total_frames=metadata.frame_count,
                    people_in_frame=len(people),
                    # Rolling averages over recent frames, for display only.
                    inference_fps=performance.rolling_inference_fps
                    or (1 / result.inference_seconds if result.inference_seconds > 0 else 0.0),
                    processing_fps=performance.rolling_processing_fps,
                    device=detector.device,
                    queue_length=queue.queue_length if queue else None,
                    service_rate_text=service_rate_text(queue.service_rate) if queue else None,
                    predicted_wait_text=predicted_wait_text(queue.prediction) if queue else None,
                ),
                position=options.panel_position,
            )
            writer.write(frame)

            performance.record_frame(
                inference_seconds=result.inference_seconds,
                processing_seconds=time.perf_counter() - result.started_at,
                people_count=len(people),
                track_ids=[person.track_id for person in people],
            )
            if mot_writer:
                mot_writer.write(result.frame_index + 1, people)
            if queue:
                queue_summary.add(queue.raw_count, queue.queue_length, queue.events)
                series_writer.add(
                    result.timestamp_s,
                    result.frame_index,
                    len(people),
                    queue.raw_count,
                    queue.queue_length,
                    queue.service_rate,
                    queue.prediction,
                )
                first_timestamp = result.timestamp_s if first_timestamp is None else first_timestamp
                last_timestamp = result.timestamp_s
                for event in queue.events:
                    event_writer.add(event)  # ignores everything but zone events
                    for queue_writer in queue_writers:
                        queue_writer.add(event)

            if performance.frames_processed % 100 == 0:
                LOGGER.info(
                    "Frame %d: %d people%s, inference %.1f FPS, processing %.1f FPS",
                    result.frame_index + 1,
                    len(people),
                    f", queue {queue.queue_length:g}" if queue else "",
                    performance.average_inference_fps,
                    performance.average_processing_fps,
                )

            if on_frame:
                on_frame(
                    FrameUpdate(
                        frame_index=result.frame_index,
                        frames_total=metadata.frame_count or None,
                        timestamp_s=result.timestamp_s,
                        people_in_frame=len(people),
                        processing_fps=performance.average_processing_fps,
                        queue=queue,
                    )
                )
            if should_stop and should_stop():
                stopped_early = True
                break

            # Display time is deliberately excluded from processing FPS.
            if options.show:
                cv2.imshow(WINDOW_NAME, frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    stopped_early = True
                    break

    if performance.frames_processed == 0:
        raise VideoLoadError(f"No frames could be read from {source}")

    video_codec = "mp4v"
    if options.h264 and reencode_h264(paths["video"], keep_original=options.keep_mp4v):
        video_codec = "h264"
        if options.keep_mp4v:
            LOGGER.info("Kept mp4v original: %s", original_copy_path(paths["video"]))

    outputs = {key: str(paths[key]) for key in ("video", "metrics", "log")}
    if options.save_mot:
        outputs["tracks"] = str(paths["tracks"])
    if camera:
        outputs.update(
            timeseries=str(paths["timeseries"]),
            events=str(paths["events"]),
            waits=str(paths["waits"]),
            alerts=str(paths["alerts"]),
            event_log=str(paths["event_log"]),
        )

    report_extra: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "video": metadata.to_dict(),
        "configuration": {
            "model_path": str(detector.model_path),
            "confidence": detector.confidence,
            "image_size": detector.image_size,
            "frame_stride": options.frame_stride,
            "effective_fps": round(effective_fps, 3),
            "tracker": tracker.describe(),
            "max_frames": options.max_frames,
            "display_enabled": options.show,
            "blur_heads": options.blur_heads,
            "head_fraction": options.head_fraction if options.blur_heads else None,
            "video_codec": video_codec,
        },
        "stopped_early": stopped_early,
        "outputs": outputs,
        "notes": [
            "average_inference_fps covers YOLO detection plus ByteTrack update, "
            "with device synchronization (none needed for CoreML/CPU).",
            "average_processing_fps covers frame skipping, decoding, inference, queue "
            "logic, drawing and video writing; live display and H.264 re-encoding are excluded.",
            "total_runtime_seconds includes model loading, warm-up and H.264 re-encoding.",
            "unique_track_ids_assigned is not a count of unique people: IDs can change after occlusion.",
        ],
    }
    if camera:
        report_extra["queue"] = {
            "camera_id": camera.camera_id,
            "zone": [list(point) for point in camera.queue_zone],
            "membership": camera.to_dict()["membership"],
            "smoothing": camera.to_dict()["smoothing"],
            "members_still_in_queue_at_end": analyzer.membership.queue_count,
            **queue_summary.to_dict(),
        }
        duration_s = (
            last_timestamp - first_timestamp + 1 / effective_fps if first_timestamp is not None else 0.0
        )
        report_extra["queue"].update(analyzer.report(duration_s))
    report = performance.build_report(
        device=detector.device,
        total_runtime_seconds=time.perf_counter() - run_started_at,
        model=model_display_name(detector.model_path),
        extra={**report_extra, **(extra_report or {})},
    )
    save_json(report, paths["metrics"])
    LOGGER.info("Saved annotated video: %s", paths["video"])
    LOGGER.info("Saved metrics: %s", paths["metrics"])
    return report


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    LOGGER.setLevel(logging.INFO)
    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    for handler in (logging.StreamHandler(), logging.FileHandler(log_path, mode="w")):
        handler.setFormatter(formatter)
        LOGGER.addHandler(handler)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and track people in a video and optionally measure a queue."
    )
    parser.add_argument("--video", required=True, help="Video file path, or a camera index such as 0")
    parser.add_argument(
        "--model",
        default=str(config.DEFAULT_MODEL_PATH),
        help="YOLO weights (.pt) or CoreML (.mlpackage). A bare name like yolo11s.pt is "
        "cached in ai_engine/models/ (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=config.OUTPUTS_DIR,
        help="Output root (default: %(default)s)",
    )
    parser.add_argument(
        "--device",
        default=config.DEFAULT_DEVICE,
        choices=["auto", "cpu", "mps", "cuda"],
        help="PyTorch device; ignored for CoreML models (default: %(default)s)",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=config.CONFIDENCE,
        help="Minimum score to start a new track (default: %(default)s)",
    )
    parser.add_argument(
        "--tracker-input-confidence",
        type=float,
        default=config.TRACKER_INPUT_CONFIDENCE,
        help="Minimum score of detections passed into ByteTrack (default: %(default)s)",
    )
    parser.add_argument(
        "--track-buffer-seconds",
        type=float,
        default=config.TRACK_BUFFER_SECONDS,
        help="How long lost tracks are kept (default: %(default)s)",
    )
    parser.add_argument("--imgsz", type=int, default=config.IMAGE_SIZE)
    parser.add_argument("--frame-stride", type=int, default=1, help="Process every Nth frame")
    parser.add_argument("--camera-config", type=Path, help="Camera YAML with the queue zone")
    parser.add_argument(
        "--no-stitching",
        action="store_true",
        help="Disable ID-switch stitching inside the queue zone (overrides the camera config)",
    )
    parser.add_argument("--save-mot", action="store_true", help="Write MOTChallenge track file")
    parser.add_argument("--blur-heads", action="store_true", help="Blur head regions in the saved video")
    parser.add_argument("--head-fraction", type=float, default=DEFAULT_HEAD_FRACTION)
    parser.add_argument("--panel-position", default="top-left", choices=PANEL_POSITIONS)
    parser.add_argument("--no-h264", action="store_true", help="Skip ffmpeg H.264 re-encoding")
    parser.add_argument("--keep-mp4v", action="store_true", help="Keep the mp4v original as well")
    parser.add_argument("--show", action="store_true", help="Display frames while processing")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop after this many processed frames")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    run_started_at = time.perf_counter()
    args = parse_arguments(argv)
    source: str | Path = args.video
    if not args.video.strip().isdigit():
        source = Path(args.video).expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    configure_logging(output_paths(source_name(source), output_dir)["log"])

    try:
        camera = load_camera_config(args.camera_config) if args.camera_config else None
        options = RunOptions(
            output_dir=output_dir,
            camera=camera,
            frame_stride=args.frame_stride,
            max_frames=args.max_frames,
            show=args.show,
            save_mot=args.save_mot,
            blur_heads=args.blur_heads,
            head_fraction=args.head_fraction,
            panel_position=args.panel_position,
            h264=not args.no_h264,
            keep_mp4v=args.keep_mp4v,
            stitching=False if args.no_stitching else None,
        )
        model_load_started_at = time.perf_counter()
        detector = PersonDetector(
            model_path=args.model,
            confidence=args.confidence,
            image_size=args.imgsz,
            device=args.device,
        )
        model_load_seconds = time.perf_counter() - model_load_started_at
        LOGGER.info(
            "Loaded %s on device '%s' in %.2fs",
            detector.model_path.name,
            detector.device,
            model_load_seconds,
        )
        tracker = PersonTracker(
            detector,
            TrackerSettings(
                new_track_confidence=args.confidence,
                input_confidence=args.tracker_input_confidence,
                buffer_seconds=args.track_buffer_seconds,
            ),
        )
        report = process_video(
            source,
            tracker,
            options,
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
    except (VideoLoadError, ModelLoadError, DeviceUnavailableError, CameraConfigError, ValueError) as error:
        LOGGER.error("%s", error)
        return 1

    performance = report["performance"]
    queue = report.get("queue")
    LOGGER.info(
        "Done: %d frames, inference %.2f FPS, processing %.2f FPS, max %d people/frame%s",
        report["frames_processed"],
        performance["average_inference_fps"],
        performance["average_processing_fps"],
        report["statistics"]["maximum_people_detected_in_a_frame"],
        f", max queue length {queue['maximum_queue_length']:g}" if queue else "",
    )
    if queue and queue.get("service_point"):
        LOGGER.info("%d completed waits", queue["waiting"]["completed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
