"""The per-frame step: track, analyse the queue, blur and draw one frame.

:class:`FrameProcessor` knows nothing about where frames come from or where they go: no
``VideoLoader``, no frame count, no ``VideoWriter``. ``process_video`` feeds it decoded file frames
and writes the results to disk; a live camera worker can feed it frames from any source.
"""

from __future__ import annotations

import time

import numpy as np

from ai_engine.metrics.performance import PerformanceTracker
from ai_engine.pipeline import FrameResult, track_frame
from ai_engine.queue.analyzer import QueueAnalyzer
from ai_engine.queue.camera_config import CameraConfig
from ai_engine.tracking.tracker import PersonTracker
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


def service_point_pixels(camera: CameraConfig, frame_size: tuple[int, int]) -> np.ndarray | None:
    """Pixel coordinates of the configured service zone or line, if any."""
    points = camera.service_zone or camera.service_line
    if points is None:
        return None
    width, height = frame_size
    return np.array([[round(x * width), round(y * height)] for x, y in points], dtype=np.int32)


class FrameProcessor:
    """Turn one raw BGR frame into an annotated :class:`FrameResult`.

    The tracker should already be configured (``tracker.configure(effective_fps)``) and, ideally,
    warmed up. With a ``camera`` a :class:`QueueAnalyzer` is built (``stitching`` and
    ``stitch_diagnostics`` override the camera config as in :class:`QueueAnalyzer`).
    ``frame_size`` (width, height) is taken from the first frame when not given.

    Call :meth:`process` for each frame, hand the drawn frame to any sinks (writer, stream), then
    call :meth:`record` so the processing time, which then includes the sinks, feeds
    :attr:`performance` and the FPS shown on later frames.
    """

    def __init__(
        self,
        tracker: PersonTracker,
        camera: CameraConfig | None = None,
        *,
        blur_heads: bool = False,
        head_fraction: float = DEFAULT_HEAD_FRACTION,
        panel_position: str = "top-left",
        stitching: bool | None = None,
        stitch_diagnostics: bool | None = None,
        frame_size: tuple[int, int] | None = None,
    ) -> None:
        if panel_position not in PANEL_POSITIONS:
            raise ValueError(f"panel_position must be one of {PANEL_POSITIONS}")
        self.tracker = tracker
        self.camera = camera
        self.blur_heads = blur_heads
        self.head_fraction = head_fraction
        self.panel_position = panel_position
        self.analyzer = (
            QueueAnalyzer(camera, stitching_enabled=stitching, stitch_diagnostics=stitch_diagnostics)
            if camera
            else None
        )
        self.performance = PerformanceTracker()
        self._frame_size: tuple[int, int] | None = None
        self._zone_px: np.ndarray | None = None
        self._service_px: np.ndarray | None = None
        if frame_size is not None:
            self._set_frame_size(frame_size)

    @property
    def frame_size(self) -> tuple[int, int] | None:
        return self._frame_size

    def _set_frame_size(self, frame_size: tuple[int, int]) -> None:
        self._frame_size = frame_size
        self._zone_px = self.analyzer.zone.to_pixels(frame_size) if self.analyzer else None
        self._service_px = service_point_pixels(self.camera, frame_size) if self.camera else None

    def process(
        self,
        frame: np.ndarray,
        frame_index: int,
        timestamp_s: float,
        *,
        total_frames: int | None = None,
        started_at: float | None = None,
    ) -> FrameResult:
        """Track, analyse and draw ``frame`` in place; the result's ``frame`` is the drawn image.

        ``total_frames`` only feeds the "Frame N/total" overlay line (None for live sources).
        ``started_at`` is when work on this frame began (default: now), for processing FPS.
        """
        started_at = time.perf_counter() if started_at is None else started_at
        if self._frame_size is None:
            height, width = frame.shape[:2]
            self._set_frame_size((width, height))
        result = track_frame(
            self.tracker, self.analyzer, frame, frame_index, timestamp_s, self._frame_size, started_at
        )
        self.draw(result, total_frames)
        return result

    def draw(self, result: FrameResult, total_frames: int | None = None) -> None:
        """Blur heads (if enabled) and draw zone, service point, people, alert banner and panel."""
        frame, people, queue = result.frame, result.people, result.queue
        if self.blur_heads:
            blur_heads(frame, people, self.head_fraction)
        if self._zone_px is not None:
            draw_queue_zone(frame, self._zone_px)
        if self._service_px is not None:
            draw_service_point(frame, self._service_px)
        draw_people(frame, people, queue.statuses if queue else None, queue.at_service if queue else None)
        if queue and queue.alert_active:
            # Keep the banner away from the metrics panel.
            banner_position = "bottom" if self.panel_position.startswith("top") else "top"
            draw_alert_banner(frame, queue.alert_reasons, banner_position)
        performance = self.performance
        draw_metrics_panel(
            frame,
            OverlayStats(
                frame_number=result.frame_index + 1,
                total_frames=total_frames if total_frames is not None else 0,
                people_in_frame=len(people),
                # Rolling averages over recent frames, for display only.
                inference_fps=performance.rolling_inference_fps
                or (1 / result.inference_seconds if result.inference_seconds > 0 else 0.0),
                processing_fps=performance.rolling_processing_fps,
                device=self.tracker.detector.device,
                queue_length=queue.queue_length if queue else None,
                service_rate_text=service_rate_text(queue.service_rate) if queue else None,
                predicted_wait_text=predicted_wait_text(queue.prediction) if queue else None,
            ),
            position=self.panel_position,
        )

    def record(self, result: FrameResult) -> None:
        """Record the frame's timing and people once its sinks are done (see the class docs)."""
        self.performance.record_frame(
            inference_seconds=result.inference_seconds,
            processing_seconds=time.perf_counter() - result.started_at,
            people_count=len(result.people),
            track_ids=[person.track_id for person in result.people],
        )
