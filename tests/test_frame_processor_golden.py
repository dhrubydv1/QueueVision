"""Golden test: ``process_video`` output is unchanged by the per-frame refactor.

``_reference_process_video`` below is a FROZEN verbatim copy of ``process_video`` (and the
``iter_frame_results`` / ``service_point_pixels`` helpers it used) from ``ai_engine/run_tracking.py``
and ``ai_engine/pipeline.py`` at commit 68001b8, before ``FrameProcessor`` existed. Do not edit it
to follow later changes: its job is to pin the old behaviour. The tests run it and the live
``process_video`` on the same synthetic clip and MOCKED tracker and compare every output file:
byte for byte, except the MP4 (decoded and compared frame by frame) and the metrics JSON (compared
after dropping run-varying fields). ``time.perf_counter`` is frozen during both runs so the FPS
values drawn into the overlay are deterministic.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.mot_eval import MotWriter
from ai_engine.frame_processor import FrameProcessor
from ai_engine.metrics.performance import PerformanceTracker, save_json
from ai_engine.metrics.queue_outputs import (
    AlertWriter,
    EventLogWriter,
    QueueSummary,
    QueueTimeSeriesWriter,
    WaitsWriter,
    ZoneEventWriter,
)
from ai_engine.pipeline import FrameResult, source_fps
from ai_engine.queue.analyzer import QueueAnalyzer
from ai_engine.queue.camera_config import CameraConfig, load_camera_config
from ai_engine.queue.stitching import write_decisions_jsonl
from ai_engine.run_tracking import FrameUpdate, RunOptions, model_display_name, output_paths, process_video
from ai_engine.tracking.tracker import PersonTracker
from ai_engine.video.transcode import original_copy_path, reencode_h264
from ai_engine.video.video_loader import VideoLoader, VideoLoadError, VideoWriter, source_name
from ai_engine.visualization.metrics_overlay import (
    OverlayStats,
    draw_alert_banner,
    draw_metrics_panel,
    draw_people,
    draw_queue_zone,
    draw_service_point,
    predicted_wait_text,
    service_rate_text,
)
from ai_engine.visualization.privacy import blur_heads
from tests.conftest import SYNTHETIC_FPS, FakeTracker, WalkerTracker, write_synthetic_video
from tests.test_api_jobs import CAMERA_YAML

# --- frozen reference (verbatim copy, do not edit) ---------------------------------------------

LOGGER = logging.getLogger("queuevision")
WINDOW_NAME = "QueueVision - press q to stop"


def _reference_iter_frame_results(
    loader: VideoLoader,
    tracker: PersonTracker,
    queue_analyzer: QueueAnalyzer | None = None,
    frame_stride: int = 1,
    max_frames: int | None = None,
) -> Iterator[FrameResult]:
    if frame_stride < 1:
        raise ValueError("frame_stride must be >= 1")
    fps = source_fps(loader)
    frame_size = (loader.metadata.width, loader.metadata.height)
    processed = 0
    while max_frames is None or processed < max_frames:
        started_at = time.perf_counter()
        if processed > 0 and not all(loader.skip() for _ in range(frame_stride - 1)):
            return
        frame = loader.read()
        if frame is None:
            return
        frame_index = processed * frame_stride
        timestamp_s = frame_index / fps

        people = tracker.update(frame)
        inference_seconds = tracker.last_inference_seconds
        queue_state = (
            queue_analyzer.update(timestamp_s, frame_index, people, frame_size)
            if queue_analyzer is not None
            else None
        )
        processed += 1
        yield FrameResult(
            frame_index=frame_index,
            timestamp_s=timestamp_s,
            frame=frame,
            people=people,
            queue=queue_state,
            inference_seconds=inference_seconds,
            started_at=started_at,
        )


def _reference_service_point_pixels(camera: CameraConfig, frame_size: tuple[int, int]) -> np.ndarray | None:
    points = camera.service_zone or camera.service_line
    if points is None:
        return None
    width, height = frame_size
    return np.array([[round(x * width), round(y * height)] for x, y in points], dtype=np.int32)


def _reference_process_video(
    source: str | Path | int,
    tracker: PersonTracker,
    options: RunOptions | None = None,
    run_started_at: float | None = None,
    extra_report: dict[str, Any] | None = None,
    on_frame: Callable[[FrameUpdate], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    frame_callback_wants_image: bool = False,
) -> dict[str, Any]:
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

        analyzer = (
            QueueAnalyzer(
                camera, stitching_enabled=options.stitching, stitch_diagnostics=options.stitch_diagnostics
            )
            if camera
            else None
        )
        zone_px = analyzer.zone.to_pixels(frame_size) if analyzer else None
        service_px = _reference_service_point_pixels(camera, frame_size) if camera else None
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

        for result in _reference_iter_frame_results(
            loader, tracker, analyzer, options.frame_stride, options.max_frames
        ):
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
                        image=frame if frame_callback_wants_image else None,
                        people=tuple(people) if frame_callback_wants_image else None,
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
        if analyzer is not None and analyzer.stitching_settings.diagnostics:
            write_decisions_jsonl(paths["stitch_diagnostics"], analyzer.stitcher.decisions)
            outputs["stitch_diagnostics"] = str(paths["stitch_diagnostics"])

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


# --- comparison helpers -------------------------------------------------------------------------

RUN_VARYING = ("total_runtime_seconds", "total_processing_seconds", "total_inference_seconds")


def stable(report: Any) -> Any:
    """Drop run-varying fields (as tests/test_pipeline.py does); ``outputs`` differ only by root dir."""
    if isinstance(report, dict):
        return {
            k: stable(v)
            for k, v in report.items()
            if k not in ("generated_at", "outputs") and k not in RUN_VARYING and "fps" not in k.lower()
        }
    if isinstance(report, list):
        return [stable(v) for v in report]
    return report


def frame_hashes(video: Path) -> list[str]:
    capture = cv2.VideoCapture(str(video))
    hashes = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        hashes.append(hashlib.sha256(frame.tobytes()).hexdigest())
    capture.release()
    return hashes


def write_large_video(path: Path, frames: int = 12, size: tuple[int, int] = (640, 480)) -> Path:
    """A clip big enough that every metrics-panel line (FPS, Frame N/total) is drawn on screen."""
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), SYNTHETIC_FPS, (width, height))
    for index in range(frames):
        frame = np.full((height, width, 3), 40, dtype=np.uint8)
        x = 20 + index * 20
        cv2.rectangle(frame, (x, 150), (x + 80, 320), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return path


def run_both(
    video: Path,
    tmp_path: Path,
    make_tracker: Callable[[], Any],
    monkeypatch: pytest.MonkeyPatch,
    **options: Any,
) -> dict[str, tuple[dict[str, Any], list[tuple[Any, ...]]]]:
    # Frozen clock: drawn FPS values and metrics FPS become deterministic (0.0 for processing).
    monkeypatch.setattr(time, "perf_counter", lambda: 1000.0)
    runs = {}
    for label, function in (("reference", _reference_process_video), ("new", process_video)):
        updates: list[tuple[Any, ...]] = []

        def on_frame(update: FrameUpdate, updates: list[tuple[Any, ...]] = updates) -> None:
            updates.append(
                (
                    update.frame_index,
                    update.frames_total,
                    update.timestamp_s,
                    update.people_in_frame,
                    update.processing_fps,
                    update.queue,
                    hashlib.sha256(update.image.tobytes()).hexdigest(),
                    update.people,
                )
            )

        report = function(
            video,
            make_tracker(),
            RunOptions(output_dir=tmp_path / label, h264=False, **options),
            run_started_at=1000.0,
            on_frame=on_frame,
            frame_callback_wants_image=True,
        )
        runs[label] = (report, updates)
    return runs


def assert_outputs_identical(tmp_path: Path, name: str, runs: dict[str, Any]) -> list[str]:
    ref_root, new_root = tmp_path / "reference", tmp_path / "new"
    ref_files = sorted(p.relative_to(ref_root).as_posix() for p in ref_root.rglob("*") if p.is_file())
    new_files = sorted(p.relative_to(new_root).as_posix() for p in new_root.rglob("*") if p.is_file())
    assert ref_files == new_files
    paths = {label: output_paths(name, tmp_path / label) for label in ("reference", "new")}
    for relative in ref_files:
        ref_path, new_path = ref_root / relative, new_root / relative
        if ref_path == paths["reference"]["video"]:
            ref_hashes, new_hashes = frame_hashes(ref_path), frame_hashes(new_path)
            assert ref_hashes and ref_hashes == new_hashes, relative
        elif ref_path == paths["reference"]["metrics"]:
            saved = [json.loads(p.read_text()) for p in (ref_path, new_path)]
            assert stable(saved[0]) == stable(saved[1]), relative
            assert set(saved[0]["outputs"]) == set(saved[1]["outputs"])
        else:
            assert ref_path.read_bytes() == new_path.read_bytes(), relative
    (ref_report, ref_updates), (new_report, new_updates) = runs["reference"], runs["new"]
    assert stable(ref_report) == stable(new_report)
    assert ref_report["performance"] == new_report["performance"]  # frozen clock: exact
    assert ref_updates and ref_updates == new_updates
    return ref_files


# --- tests --------------------------------------------------------------------------------------


def test_golden_camera_run_matches_frozen_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = write_synthetic_video(tmp_path / "walker.mp4", frames=60)
    camera_path = tmp_path / "walker.yaml"
    camera_path.write_text(CAMERA_YAML)
    camera = load_camera_config(camera_path)
    runs = run_both(
        video,
        tmp_path,
        WalkerTracker,
        monkeypatch,
        camera=camera,
        save_mot=True,
        blur_heads=True,
        stitch_diagnostics=True,
    )
    files = assert_outputs_identical(tmp_path, "walker", runs)
    assert files == [
        "events/walker_alerts.csv",
        "events/walker_events.jsonl",
        "events/walker_stitch_diagnostics.jsonl",
        "events/walker_waits.csv",
        "events/walker_zone_events.csv",
        "metrics/walker_metrics.json",
        "timeseries/walker_queue.csv",
        "tracks/walker_mot.txt",
        "videos/walker_tracked.mp4",
    ]
    # The scenario really exercises the queue outputs, not just empty headers.
    report = runs["new"][0]
    assert report["queue"]["waiting"]["completed"] == 1
    assert report["queue"]["alerts"]["congestion_starts"] >= 1
    assert len((tmp_path / "new" / "tracks" / "walker_mot.txt").read_text().splitlines()) == 60


def test_golden_plain_run_with_stride_matches_frozen_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = write_large_video(tmp_path / "large.mp4")
    runs = run_both(
        video,
        tmp_path,
        FakeTracker,
        monkeypatch,
        frame_stride=2,
        save_mot=True,
        blur_heads=True,
        panel_position="bottom-right",
    )
    files = assert_outputs_identical(tmp_path, "large", runs)
    assert files == ["metrics/large_metrics.json", "tracks/large_mot.txt", "videos/large_tracked.mp4"]
    assert [u[0] for u in runs["new"][1]] == [0, 2, 4, 6, 8, 10]


def test_frame_processor_runs_on_plain_frame_list() -> None:
    """No VideoLoader, no frame count, no writer: the shape a live camera worker uses."""
    from ai_engine.queue.camera_config import CameraConfig, MembershipSettings

    camera = CameraConfig(
        camera_id="live",
        queue_zone=((0.0, 0.0), (0.6, 0.0), (0.6, 1.0), (0.0, 1.0)),
        service_line=((0.8, 0.0), (0.8, 1.0)),
        membership=MembershipSettings(
            min_dwell_seconds=1.0, max_speed_heights_per_second=0.5, exit_grace_seconds=0.3
        ),
    )
    frames = [np.full((128, 160, 3), 40, dtype=np.uint8) for _ in range(12)]
    originals = [frame.copy() for frame in frames]
    processor = FrameProcessor(WalkerTracker(), camera=camera, blur_heads=True)
    results = []
    for index, frame in enumerate(frames):
        result = processor.process(frame, index, index / SYNTHETIC_FPS, total_frames=None)
        processor.record(result)
        results.append(result)

    assert [r.frame_index for r in results] == list(range(12))
    assert all(r.queue is not None for r in results)
    assert all(isinstance(r.people[0], PersonDetection) for r in results)
    assert results[-1].queue.queue_length >= 0
    assert all(r.frame is frames[i] for i, r in enumerate(results))  # drawn in place
    assert all(not np.array_equal(r.frame, originals[i]) for i, r in enumerate(results))  # annotated
    assert processor.performance.frames_processed == 12
    assert processor.analyzer is not None


def test_frame_processor_without_camera_has_no_queue() -> None:
    processor = FrameProcessor(FakeTracker())
    result = processor.process(np.zeros((128, 160, 3), dtype=np.uint8), 0, 0.0)
    assert result.queue is None and processor.analyzer is None
    assert len(result.people) == 1
