"""The live loop: pull the newest frame at ``process_fps``, track it, update the queue, report.

Nothing but the queue time-series CSV and the metrics JSON is written; there is no video or image
file. Frames are drawn once, in the preview privacy mode, and handed to ``on_frame``.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_engine.frame_processor import FrameProcessor
from ai_engine.live.capture import CAMERA_UNAVAILABLE, LiveSource, LiveSourceError
from ai_engine.metrics.performance import save_json
from ai_engine.metrics.queue_outputs import QueueSummary, QueueTimeSeriesWriter
from ai_engine.queue.camera_config import CameraConfig
from ai_engine.run_tracking import PREVIEW_PRIVACY_MODES, FrameUpdate, build_queue_report, model_display_name

READ_SLICE_S = 0.5  # longest single wait for a frame, so a stop request is noticed promptly


@dataclass(frozen=True)
class LiveOptions:
    output_dir: Path
    process_fps: float = 10.0
    preview_privacy_mode: str = "frame"
    max_session_s: float = 7200.0
    first_frame_timeout_s: float = 10.0
    frame_timeout_s: float = 10.0

    def __post_init__(self) -> None:
        if not 0.0 < self.process_fps <= 30.0:
            raise ValueError("process_fps must be in (0, 30]")
        if self.preview_privacy_mode not in PREVIEW_PRIVACY_MODES:
            raise ValueError(f"preview_privacy_mode must be one of {PREVIEW_PRIVACY_MODES}")
        if self.max_session_s <= 0:
            raise ValueError("max_session_s must be > 0")


def run_live(
    source: LiveSource,
    tracker: Any,
    camera: CameraConfig,
    options: LiveOptions,
    on_frame: Callable[[FrameUpdate], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Process a live source until stopped or ``max_session_s``; returns the saved metrics report.

    Raises ``LiveSourceError("camera_unavailable")`` when no frame arrives within
    ``first_frame_timeout_s`` (first) or ``frame_timeout_s`` (later).
    """
    camera_id = camera.camera_id
    paths = {
        "timeseries": options.output_dir / "timeseries" / f"live{camera_id}_queue.csv",
        "metrics": options.output_dir / "metrics" / f"live{camera_id}_metrics.json",
    }
    session_start = clock()
    processor = FrameProcessor(tracker, camera, privacy_mode=options.preview_privacy_mode)
    performance, analyzer = processor.performance, processor.analyzer
    summary = QueueSummary()
    loops: list[dict[str, Any]] = []
    last_loop_count = source.loop_count
    first_capture: float | None = None
    first_queue_ts: float | None = None
    last_queue_ts = 0.0
    pace_start = 0.0
    stopped_by = "operator"
    tracker.configure(options.process_fps)

    def stop_reason() -> str | None:
        if should_stop is not None and should_stop():
            return "operator"
        if clock() - session_start >= options.max_session_s:
            return "max_session"
        return None

    with contextlib.ExitStack() as stack:
        series = stack.enter_context(QueueTimeSeriesWriter(paths["timeseries"]))
        while True:
            reason = stop_reason()
            if reason is None and first_capture is not None:
                due = pace_start + performance.frames_processed / options.process_fps
                if (wait := due - clock()) > 0:
                    sleep(wait)
                    reason = stop_reason()
            if reason is not None:
                stopped_by = reason
                break

            # Wait for the newest frame; give up once nothing has arrived for the allowed time.
            limit = options.first_frame_timeout_s if first_capture is None else options.frame_timeout_s
            waited_from = clock()
            grabbed = None
            while grabbed is None:
                remaining = limit - (clock() - waited_from)
                if remaining <= 0:
                    raise LiveSourceError(CAMERA_UNAVAILABLE)
                grabbed = source.read_latest(min(READ_SLICE_S, remaining))
                if grabbed is None and (reason := stop_reason()) is not None:
                    break
            if grabbed is None:
                stopped_by = reason or "operator"
                break
            frame, capture_time = grabbed

            if first_capture is None:
                first_capture = capture_time
                tracker.warmup(frame.shape)  # keeps one-time model setup out of the pacing and FPS numbers
                pace_start = clock()
            timestamp_s = capture_time - first_capture
            if source.loop_count != last_loop_count:
                last_loop_count = source.loop_count
                loops.append({"loop": last_loop_count, "timestamp_s": round(timestamp_s, 3)})

            frame_index = performance.frames_processed
            result = processor.process(frame, frame_index, timestamp_s)
            processor.record(result)
            people, queue = result.people, result.queue
            if queue is not None:  # same arguments RunWriters.add uses
                summary.add(queue.raw_count, queue.queue_length, queue.events)
                series.add(
                    timestamp_s,
                    frame_index,
                    len(people),
                    queue.raw_count,
                    queue.queue_length,
                    queue.service_rate,
                    queue.prediction,
                )
                if first_queue_ts is None:
                    first_queue_ts = timestamp_s
                last_queue_ts = timestamp_s
            if on_frame:
                on_frame(
                    FrameUpdate(
                        frame_index=frame_index,
                        frames_total=None,
                        timestamp_s=timestamp_s,
                        people_in_frame=len(people),
                        processing_fps=performance.average_processing_fps,
                        queue=queue,
                        image=result.frame,
                        people=tuple(people),
                        image_privacy=options.preview_privacy_mode,
                    )
                )

    duration_s = clock() - session_start
    queue_duration = (
        0.0 if first_queue_ts is None else last_queue_ts - first_queue_ts + 1 / options.process_fps
    )
    detector = tracker.detector
    report = performance.build_report(
        device=detector.device,
        total_runtime_seconds=duration_s,
        model=model_display_name(detector.model_path),
        extra={
            "kind": "live",
            "source_kind": source.source_kind,
            "stopped_by": stopped_by,
            "duration_s": round(duration_s, 3),
            "loops": loops,
            "configuration": {
                "model_path": str(detector.model_path),
                "confidence": detector.confidence,
                "image_size": detector.image_size,
                "process_fps": options.process_fps,
                "tracker": tracker.describe(),
                "preview_privacy_mode": options.preview_privacy_mode,
            },
            "outputs": {"timeseries": str(paths["timeseries"]), "metrics": str(paths["metrics"])},
            "queue": build_queue_report(camera, analyzer, summary, queue_duration),
        },
    )
    save_json(report, paths["metrics"])
    return report
