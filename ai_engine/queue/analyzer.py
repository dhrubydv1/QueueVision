"""Combine zone, membership and queue-length estimation for one video."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.camera_config import CameraConfig
from ai_engine.queue.membership import MemberStatus, QueueMembershipTracker, ZoneEvent
from ai_engine.queue.queue_length import QueueLengthEstimator
from ai_engine.queue.zone import QueueZone


@dataclass(frozen=True)
class QueueFrameState:
    """Queue state after processing one frame."""

    raw_count: int
    queue_length: float  # median-smoothed
    statuses: dict[int, MemberStatus]
    events: tuple[ZoneEvent, ...]


class QueueAnalyzer:
    """Per-video queue logic driven by tracked people; no model or drawing code."""

    def __init__(self, camera: CameraConfig) -> None:
        self.camera = camera
        self.zone = QueueZone(camera.queue_zone)
        self.membership = QueueMembershipTracker(self.zone, camera.membership)
        self.length = QueueLengthEstimator(camera.smoothing.window_seconds)

    def update(
        self,
        timestamp_s: float,
        frame_index: int,
        people: Sequence[PersonDetection],
        frame_size: tuple[int, int],
    ) -> QueueFrameState:
        events = self.membership.update(timestamp_s, frame_index, people, frame_size)
        raw_count = self.membership.queue_count
        return QueueFrameState(
            raw_count=raw_count,
            queue_length=self.length.update(timestamp_s, raw_count),
            statuses=self.membership.statuses,
            events=tuple(events),
        )
