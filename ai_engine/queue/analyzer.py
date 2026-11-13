"""Combine zone, membership, stitching, lifecycle, waiting time and alerts for one video."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.alerts import CongestionMonitor
from ai_engine.queue.camera_config import CameraConfig
from ai_engine.queue.event_schema import QueueEventType, ServiceCompleteEvent, ServiceStartEvent
from ai_engine.queue.events import QueueLifecycle
from ai_engine.queue.membership import MemberStatus, QueueMembershipTracker
from ai_engine.queue.queue_length import QueueLengthEstimator
from ai_engine.queue.service_point import ServiceZone, service_point_from_config
from ai_engine.queue.stitching import GuardedTrackStitcher, TrackStitcher
from ai_engine.queue.temporal_membership import TemporalMembershipClassifier
from ai_engine.queue.waiting_time import (
    ESTIMATOR_VERSION,
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
    membership_states: dict[int, str] = field(default_factory=dict)  # track id -> QueueState value
    state_counts: dict[str, int] = field(default_factory=dict)  # persons per state (temporal only)
    membership_confidence: dict[int, float] = field(default_factory=dict)  # heuristic, not a probability


class QueueAnalyzer:
    """Per-video queue logic driven by tracked people; no model or drawing code.

    ``stitching_enabled`` overrides ``camera.stitching.enabled`` when not
    ``None`` (``False`` forces stitching off for comparison runs);
    ``stitch_diagnostics`` likewise overrides ``camera.stitching.diagnostics``.
    """

    def __init__(
        self,
        camera: CameraConfig,
        stitching_enabled: bool | None = None,
        stitch_diagnostics: bool | None = None,
    ) -> None:
        self.camera = camera
        stitching = camera.stitching
        if stitching_enabled is not None:
            stitching = replace(stitching, enabled=stitching_enabled)
        if stitch_diagnostics is not None:
            stitching = replace(stitching, diagnostics=stitch_diagnostics)
        self.stitching_settings = stitching
        self.zone = QueueZone(camera.queue_zone)
        self.membership: QueueMembershipTracker | TemporalMembershipClassifier
        if camera.membership.algorithm == "temporal":
            service_zone = QueueZone(camera.service_zone) if camera.service_zone is not None else None
            self.membership = TemporalMembershipClassifier(self.zone, camera.membership, service_zone)
        else:
            self.membership = QueueMembershipTracker(self.zone, camera.membership)
        self.length = QueueLengthEstimator(camera.smoothing.window_seconds)
        self.stitcher: TrackStitcher | GuardedTrackStitcher = (
            GuardedTrackStitcher(self.zone, stitching)
            if stitching.algorithm == "guarded"
            else TrackStitcher(self.zone, stitching)
        )
        self.service_point = service_point_from_config(camera)
        abandon_after = camera.membership.exit_grace_seconds
        if stitching.enabled:
            abandon_after = max(abandon_after, stitching.max_gap_seconds)
        self.lifecycle = QueueLifecycle(self.service_point, abandon_after, camera.service)
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
        if isinstance(self.membership, TemporalMembershipClassifier):
            zone_events = self.membership.update(
                timestamp_s, frame_index, people, frame_size, person_keys=person_keys
            )
        else:
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
        basis = self.camera.service_rate.basis
        for event in lifecycle_events:
            self.wait_log.add(event)
            if basis == "service_start" and isinstance(event, ServiceStartEvent):
                self._rate_estimator.add_service(event.timestamp_s)
            elif basis == "service_complete" and isinstance(event, ServiceCompleteEvent):
                # Counted when confirmed (detected_s) so the estimator sees events in order.
                self._rate_estimator.add_service(event.detected_s)

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
            **self._temporal_fields(),
        )

    def _temporal_fields(self) -> dict[str, Any]:
        """Per-frame temporal-membership views (empty for the legacy algorithm)."""
        if not isinstance(self.membership, TemporalMembershipClassifier):
            return {}
        return {
            "membership_states": {tid: state.value for tid, state in self.membership.states.items()},
            "state_counts": self.membership.state_counts,
            "membership_confidence": self.membership.confidences,
        }

    def report(self, duration_s: float) -> dict[str, Any]:
        """Queue-analysis section of the metrics JSON."""
        point = self.service_point
        rate = self._last_rate
        return {
            "stitching": {
                "enabled": self.stitching_settings.enabled,
                "stitches": self.stitcher.stitch_count,
                "algorithm": self.stitching_settings.algorithm,
                **self.stitcher.summary(),
            },
            "service_point": point.kind if point else None,
            "waiting": (
                self.wait_log.summary(duration_s) if point else {"note": "no service point configured"}
            ),
            "service_completions": (
                self.wait_log.service_summary()
                if isinstance(self.service_point, ServiceZone)
                else {"note": "needs a service zone"}
            ),
            "service_rate": {
                "estimator": ESTIMATOR_VERSION,
                "basis": self.camera.service_rate.basis,
                "rate_basis": self.camera.service_rate.basis,
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
            "membership": {
                "algorithm": self.camera.membership.algorithm,
                "settings": asdict(self.camera.membership),
                "state_counts_at_end": (
                    self.membership.state_counts
                    if isinstance(self.membership, TemporalMembershipClassifier)
                    else None
                ),
            },
            "waiting_at_end": self.lifecycle.waiting_count,
            "pending_abandon_at_end": self.lifecycle.pending_abandon_count,
            "service_visits_without_join": self.lifecycle.service_visits_without_join,
        }
