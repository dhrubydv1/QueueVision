"""Combine zone, membership, stitching, lifecycle, waiting time and alerts for one video."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.alerts import CongestionMonitor
from ai_engine.queue.camera_config import CameraConfig
from ai_engine.queue.event_schema import QueueEventType, ServiceStartEvent
from ai_engine.queue.events import QueueLifecycle
from ai_engine.queue.membership import MemberStatus, QueueMembershipTracker
from ai_engine.queue.queue_length import QueueLengthEstimator
from ai_engine.queue.service_point import service_point_from_config
from ai_engine.queue.stitching import TrackStitcher
from ai_engine.queue.waiting_time import (
    INSUFFICIENT_RATE,
    UNKNOWN_PREDICTION,
    ServiceRate,
    ServiceRateEstimator,
    WaitLog,
    WaitPrediction,
    predict_wait,
)
from ai_engine.queue.zone import QueueZone


@dataclass(frozen=True)
class QueueFrameState:
    """Queue state after processing one frame."""

    raw_count: int
    queue_length: float  # median-smoothed
    statuses: dict[int, MemberStatus]
    events: tuple[QueueEventType, ...]  # every event produced this frame, in order
    person_keys: dict[int, str] = field(default_factory=dict)
    at_service: frozenset[int] = frozenset()
    service_rate: ServiceRate = INSUFFICIENT_RATE
    prediction: WaitPrediction = UNKNOWN_PREDICTION
    alert_active: bool = False
    alert_reasons: tuple[str, ...] = ()


class QueueAnalyzer:
    """Per-video queue logic driven by tracked people; no model or drawing code.

    ``stitching_enabled`` overrides ``camera.stitching.enabled`` when not
    ``None`` (``False`` forces stitching off for comparison runs).
    """

    def __init__(self, camera: CameraConfig, stitching_enabled: bool | None = None) -> None:
        self.camera = camera
        stitching = camera.stitching
        if stitching_enabled is not None:
            stitching = replace(stitching, enabled=stitching_enabled)
        self.stitching_settings = stitching
        self.zone = QueueZone(camera.queue_zone)
        self.membership = QueueMembershipTracker(self.zone, camera.membership)
        self.length = QueueLengthEstimator(camera.smoothing.window_seconds)
        self.stitcher = TrackStitcher(self.zone, stitching)
        self.service_point = service_point_from_config(camera)
        abandon_after = camera.membership.exit_grace_seconds
        if stitching.enabled:
            abandon_after = max(abandon_after, stitching.max_gap_seconds)
        self.lifecycle = QueueLifecycle(self.service_point, abandon_after)
        self.wait_log = WaitLog()
        self.alerts = CongestionMonitor(camera.alerts)
        self._rate_estimator: ServiceRateEstimator | None = None
        self._last_rate = INSUFFICIENT_RATE
        self._last_prediction = UNKNOWN_PREDICTION
        self._congestion_starts = 0

    @property
    def has_service_point(self) -> bool:
        return self.service_point is not None

    def update(
        self,
        timestamp_s: float,
        frame_index: int,
        people: Sequence[PersonDetection],
        frame_size: tuple[int, int],
    ) -> QueueFrameState:
        person_keys = self.stitcher.update(timestamp_s, people, frame_size)
        zone_events = self.membership.update(timestamp_s, frame_index, people, frame_size)
        raw_count = self.membership.queue_count
        lifecycle_events = self.lifecycle.update(
            timestamp_s,
            frame_index,
            people,
            person_keys,
            zone_events,
            frame_size,
            self.membership.last_in_zone_flags,
        )
        if self._rate_estimator is None:
            self._rate_estimator = ServiceRateEstimator(self.camera.service_rate, timestamp_s)
        for event in lifecycle_events:
            self.wait_log.add(event)
            if isinstance(event, ServiceStartEvent):
                self._rate_estimator.add_service(event.timestamp_s)

        queue_length = self.length.update(timestamp_s, raw_count)
        rate = self._rate_estimator.estimate(timestamp_s) if self.has_service_point else INSUFFICIENT_RATE
        prediction = predict_wait(queue_length, rate)
        congestion_events = self.alerts.update(timestamp_s, frame_index, queue_length, prediction)
        self._congestion_starts += sum(1 for e in congestion_events if e.event_type == "congestion_start")
        self._last_rate, self._last_prediction = rate, prediction

        return QueueFrameState(
            raw_count=raw_count,
            queue_length=queue_length,
            statuses=self.membership.statuses,
            events=(*zone_events, *lifecycle_events, *congestion_events),
            person_keys=person_keys,
            at_service=self.lifecycle.at_service_track_ids,
            service_rate=rate,
            prediction=prediction,
            alert_active=self.alerts.active,
            alert_reasons=self.alerts.active_reasons,
        )

    def report(self, duration_s: float) -> dict[str, Any]:
        """Queue-analysis section of the metrics JSON."""
        point = self.service_point
        rate = self._last_rate
        return {
            "stitching": {"enabled": self.stitching_settings.enabled, "stitches": self.stitcher.stitch_count},
            "service_point": point.kind if point else None,
            "waiting": (
                self.wait_log.summary(duration_s) if point else {"note": "no service point configured"}
            ),
            "service_rate": {
                "settings": asdict(self.camera.service_rate),
                "final_per_min": rate.per_min,
                "final_status": rate.status,
                "final_low_per_min": rate.low_per_min,
                "final_high_per_min": rate.high_per_min,
            },
            "alerts": {
                "enabled": self.camera.alerts.enabled,
                "congestion_starts": self._congestion_starts,
                "active_at_end": self.alerts.active,
            },
            "waiting_at_end": self.lifecycle.waiting_count,
            "pending_abandon_at_end": self.lifecycle.pending_abandon_count,
            "service_visits_without_join": self.lifecycle.service_visits_without_join,
        }
