"""Decide which tracked people are queuing, and emit zone enter/exit events."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.camera_config import MembershipSettings
from ai_engine.queue.zone import QueueZone


class MemberStatus(str, Enum):
    CANDIDATE = "candidate"  # inside the zone, dwell/speed rules not yet met
    IN_QUEUE = "in_queue"
    LEFT = "left"  # was in the queue, now gone for longer than the grace period


@dataclass(frozen=True)
class ZoneEvent:
    """A confirmed queue member entering or leaving the queue zone.

    ``zone_enter`` fires when membership is confirmed (after the dwell
    time); ``zone_entered_s`` is when the person first stepped into the
    zone, which is the value waiting-time logic should use. ``zone_exit``
    uses the last time the person was seen inside the zone. All times are
    video time (frame index / FPS), never wall-clock time.
    """

    event_type: str  # "zone_enter" | "zone_exit"
    track_id: int
    timestamp_s: float
    frame_index: int
    zone_entered_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _TrackState:
    track_id: int
    last_seen_s: float
    zone_entered_s: float | None = None
    zone_entered_frame: int = 0
    last_in_zone_s: float | None = None
    last_in_zone_frame: int = 0
    status: MemberStatus | None = None
    # (time, foot_x, foot_y, box_height) samples, oldest first
    positions: deque[tuple[float, float, float, float]] = field(default_factory=deque)


def foot_point(person: PersonDetection) -> tuple[float, float]:
    """Bottom-centre of the box: where the person stands on the floor."""
    x1, _, x2, y2 = person.bbox
    return (x1 + x2) / 2, float(y2)


class QueueMembershipTracker:
    """Track per-person queue status from tracked detections.

    A person becomes ``in_queue`` once their foot point has been inside the
    zone for ``min_dwell_seconds`` and their average speed over that period
    (foot displacement / box height / time) is at most
    ``max_speed_heights_per_second``. Gaps up to ``exit_grace_seconds``
    (occlusion, brief steps outside) do not end membership. State for
    tracks unseen for longer than the grace period is discarded.
    """

    def __init__(self, zone: QueueZone, settings: MembershipSettings) -> None:
        self.zone = zone
        self.settings = settings
        self._states: dict[int, _TrackState] = {}

    @property
    def statuses(self) -> dict[int, MemberStatus]:
        """Current status of every remembered track (outside tracks are omitted)."""
        return {tid: s.status for tid, s in self._states.items() if s.status is not None}

    @property
    def queue_count(self) -> int:
        """Confirmed members, including ones briefly occluded within the grace period."""
        return sum(1 for s in self._states.values() if s.status is MemberStatus.IN_QUEUE)

    @property
    def tracked_state_count(self) -> int:
        return len(self._states)

    def _average_speed(self, state: _TrackState, now: float) -> float:
        """Net foot displacement over the dwell window, in box heights per second."""
        window_start = now - self.settings.min_dwell_seconds
        samples = [p for p in state.positions if p[0] >= window_start] or list(state.positions)
        if len(samples) < 2:
            return 0.0
        (t0, x0, y0, _), (t1, x1, y1, _) = samples[0], samples[-1]
        if t1 <= t0:
            return 0.0
        mean_height = sum(p[3] for p in samples) / len(samples)
        distance = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        return distance / max(mean_height, 1.0) / (t1 - t0)

    def update(
        self,
        timestamp_s: float,
        frame_index: int,
        people: Sequence[PersonDetection],
        frame_size: tuple[int, int],
    ) -> list[ZoneEvent]:
        """Process one frame and return the zone events it produced."""
        grace = self.settings.exit_grace_seconds
        events: list[ZoneEvent] = []

        for person in people:
            if person.track_id is None:
                continue
            state = self._states.get(person.track_id)
            if state is None:
                state = self._states[person.track_id] = _TrackState(person.track_id, timestamp_s)
            state.last_seen_s = timestamp_s

            foot = foot_point(person)
            height = float(person.bbox[3] - person.bbox[1])
            state.positions.append((timestamp_s, foot[0], foot[1], height))
            history = max(self.settings.min_dwell_seconds, grace)
            while state.positions and state.positions[0][0] < timestamp_s - history:
                state.positions.popleft()

            if self.zone.contains(foot, frame_size):
                self._handle_inside(state, timestamp_s, frame_index, events)

        for state in self._states.values():
            self._handle_absence(state, timestamp_s, grace, events)

        # Forget tracks that have been gone for longer than the grace period.
        for track_id in [tid for tid, s in self._states.items() if timestamp_s - s.last_seen_s > grace]:
            del self._states[track_id]
        return events

    def _handle_inside(
        self, state: _TrackState, now: float, frame_index: int, events: list[ZoneEvent]
    ) -> None:
        if state.status in (None, MemberStatus.LEFT):
            state.status = MemberStatus.CANDIDATE
            state.zone_entered_s = now
            state.zone_entered_frame = frame_index
        state.last_in_zone_s = now
        state.last_in_zone_frame = frame_index

        if state.status is MemberStatus.CANDIDATE:
            dwell = now - state.zone_entered_s
            if (
                dwell >= self.settings.min_dwell_seconds
                and self._average_speed(state, now) <= self.settings.max_speed_heights_per_second
            ):
                state.status = MemberStatus.IN_QUEUE
                events.append(ZoneEvent("zone_enter", state.track_id, now, frame_index, state.zone_entered_s))

    def _handle_absence(self, state: _TrackState, now: float, grace: float, events: list[ZoneEvent]) -> None:
        if state.last_in_zone_s is None or now - state.last_in_zone_s <= grace:
            return
        if state.status is MemberStatus.IN_QUEUE:
            events.append(
                ZoneEvent(
                    "zone_exit",
                    state.track_id,
                    state.last_in_zone_s,
                    state.last_in_zone_frame,
                    state.zone_entered_s,
                )
            )
            state.status = MemberStatus.LEFT
        elif state.status is MemberStatus.CANDIDATE:
            state.status = None  # walked through or stepped away before qualifying
            state.zone_entered_s = None
