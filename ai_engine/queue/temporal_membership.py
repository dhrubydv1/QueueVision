"""Temporal queue-membership classifier: explicit per-person states.

Unlike the legacy :class:`~ai_engine.queue.membership.QueueMembershipTracker`
(one state per tracker ID), this classifier keeps one state per *person key*
(the stitcher's anonymous key), so a person whose ByteTrack ID changed keeps
their WAITING state instead of repeating the dwell time.

States and transitions (every threshold comes from ``MembershipSettings``)::

    OUTSIDE   --visible, passes entry test-------------------> CANDIDATE
    CANDIDATE --dwell, speed, observation rules pass----------> WAITING        (emits zone_enter)
    CANDIDATE --fails stay test / unseen > exit_grace---------> (forgotten; passers-by never count)
    CANDIDATE --foot in service zone, rules not met-----------> SERVICE        (no join, never counted)
    WAITING   --not detected this frame-----------------------> TEMPORARILY_OCCLUDED
    OCCLUDED  --detected again and passes stay test-----------> WAITING
    WAITING/OCCLUDED --foot in service zone-------------------> SERVICE
    WAITING/OCCLUDED --outside stay zone / unseen > grace-----> ABANDONED      (emits zone_exit)
    SERVICE   --outside both zones / unseen > grace-----------> EXITED         (emits zone_exit)
    ABANDONED/EXITED are terminal and forgotten after ``terminal_retention_seconds``.

A SERVICE person counts toward the queue only if they joined (reached WAITING
first) and ``count_service_as_queue`` is true.

``zone_exit`` events are emitted only for people who were confirmed (a
``zone_enter`` was emitted), exactly like the legacy tracker; a candidate that
reaches the service zone without qualifying leaves silently (the lifecycle
counts it as a service visit without a join).

Service *lines* give no SERVICE state from membership (a line is a crossing,
not an area); only a configured service *zone* does.

``confidences`` is an uncalibrated heuristic in [0, 1], not a probability:
``min(1, dwell / min_dwell) * min(1, max_speed / speed) * observed_fraction``.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

import cv2
import numpy as np

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.camera_config import MembershipSettings
from ai_engine.queue.event_schema import ZoneEvent
from ai_engine.queue.membership import MemberStatus, foot_point
from ai_engine.queue.zone import QueueZone

DEPTH_SAMPLES = 3  # samples averaged at each end of the window for the box-height trend
EPS_S = 1e-9


class QueueState(str, Enum):
    OUTSIDE = "outside"
    CANDIDATE = "candidate"
    WAITING = "waiting"
    TEMPORARILY_OCCLUDED = "temporarily_occluded"
    SERVICE = "service"
    EXITED = "exited"
    ABANDONED = "abandoned"


_COUNTED = (QueueState.WAITING, QueueState.TEMPORARILY_OCCLUDED)
_TERMINAL = (QueueState.EXITED, QueueState.ABANDONED)
_PRESENT = (QueueState.CANDIDATE, QueueState.WAITING, QueueState.TEMPORARILY_OCCLUDED, QueueState.SERVICE)

_STATUS_OF: dict[QueueState, MemberStatus] = {
    QueueState.CANDIDATE: MemberStatus.CANDIDATE,
    QueueState.WAITING: MemberStatus.IN_QUEUE,
    QueueState.TEMPORARILY_OCCLUDED: MemberStatus.IN_QUEUE,
    QueueState.SERVICE: MemberStatus.IN_QUEUE,
    QueueState.ABANDONED: MemberStatus.LEFT,
    QueueState.EXITED: MemberStatus.LEFT,
}


@dataclass
class _Person:
    key: str
    track_id: int
    state: QueueState
    entry_s: float
    entry_frame: int
    last_seen_s: float
    last_present_s: float  # last time inside the stay zone (or the service zone)
    last_present_frame: int
    joined: bool = False
    terminal_s: float | None = None
    visible: bool = False
    # (time, foot_x, foot_y, box_height, confidence) detections, oldest first
    samples: deque[tuple[float, float, float, float, float]] = field(default_factory=deque)


class TemporalMembershipClassifier:
    """Per-person state machine deciding who is in the queue (see module docstring)."""

    uses_person_keys = True

    def __init__(
        self,
        zone: QueueZone,
        settings: MembershipSettings,
        service_zone: QueueZone | None = None,
    ) -> None:
        self.zone = zone
        self.settings = settings
        self.service_zone = service_zone
        self._persons: dict[str, _Person] = {}
        self._frame_times: deque[float] = deque()
        self._in_zone_flags: dict[int, bool] = {}
        self._states: dict[int, QueueState] = {}
        self._confidences: dict[int, float] = {}
        self._polygon_px: dict[tuple[int, int], np.ndarray] = {}

    # ------------------------------------------------------------------ views

    @property
    def statuses(self) -> dict[int, MemberStatus]:
        """Overlay status of every remembered person, under their current track ID."""
        return {p.track_id: _STATUS_OF[p.state] for p in self._persons.values() if p.state in _STATUS_OF}

    @property
    def states(self) -> dict[int, QueueState]:
        """State of every track visible in the last update (``OUTSIDE`` if not remembered)."""
        return dict(self._states)

    @property
    def state_counts(self) -> dict[str, int]:
        """Number of remembered persons per state (every state present, zeros included)."""
        counts = {state.value: 0 for state in QueueState}
        for person in self._persons.values():
            counts[person.state.value] += 1
        return counts

    @property
    def confidences(self) -> dict[int, float]:
        """Heuristic membership score in [0, 1] per current track ID (not a probability)."""
        return dict(self._confidences)

    @property
    def last_in_zone_flags(self) -> dict[int, bool]:
        """For the last update: whether each visible track's foot point was inside the zone."""
        return dict(self._in_zone_flags)

    @property
    def queue_count(self) -> int:
        service = self.settings.count_service_as_queue
        return sum(
            1
            for p in self._persons.values()
            if p.state in _COUNTED or (service and p.state is QueueState.SERVICE and p.joined)
        )

    @property
    def tracked_state_count(self) -> int:
        return len(self._persons)

    # ------------------------------------------------------------- geometry

    def _signed_distance(self, foot: tuple[float, float], frame_size: tuple[int, int]) -> float:
        """Signed pixel distance to the zone boundary (positive inside)."""
        polygon = self._polygon_px.get(frame_size)
        if polygon is None:
            width, height = frame_size
            polygon = (self.zone.points * [width, height]).astype(np.float32).reshape(-1, 1, 2)
            self._polygon_px[frame_size] = polygon
        return float(cv2.pointPolygonTest(polygon, (float(foot[0]), float(foot[1])), True))

    def _passes(
        self, foot: tuple[float, float], height: float, frame_size: tuple[int, int], entering: bool
    ) -> bool:
        """Entry (strictly inside by the margin) or stay (not beyond the margin outside) test."""
        margin = self.settings.boundary_margin_heights
        if margin <= 0:
            return self.zone.contains(foot, frame_size)
        limit = margin * max(height, 1.0)
        distance = self._signed_distance(foot, frame_size)
        return distance >= limit if entering else distance >= -limit

    # ------------------------------------------------------------- measures

    def _window(self, person: _Person, now: float) -> list[tuple[float, float, float, float, float]]:
        start = now - self.settings.min_dwell_seconds
        return [s for s in person.samples if s[0] >= start] or list(person.samples)

    def _speed(self, person: _Person, now: float, frame_width: int) -> float:
        """Total speed in box heights per second over the dwell window (lateral, plus depth if weighted)."""
        samples = self._window(person, now)
        if len(samples) < 2:
            return 0.0
        first, last = samples[0], samples[-1]
        dt = last[0] - first[0]
        if dt <= 0:
            return 0.0
        mean_height = max(sum(s[3] for s in samples) / len(samples), 1.0)
        lateral = math.hypot(last[1] - first[1], last[2] - first[2]) / mean_height / dt
        weight = self.settings.scale_motion_weight
        if weight <= 0:
            return lateral
        k = min(DEPTH_SAMPLES, len(samples))
        h_start = max(sum(s[3] for s in samples[:k]) / k, 1.0)
        h_end = max(sum(s[3] for s in samples[-k:]) / k, 1.0)
        depth = weight * (frame_width / mean_height) * abs(math.log(h_end / h_start)) / dt
        return math.hypot(lateral, depth)

    def _observation(self, person: _Person, now: float) -> tuple[int, float, float]:
        """Detections, observed fraction and mean confidence over the dwell window since entry."""
        start = max(person.entry_s, now - self.settings.min_dwell_seconds) - EPS_S
        seen = [s for s in person.samples if s[0] >= start]
        frames = sum(1 for t in self._frame_times if t >= start)
        fraction = min(1.0, len(seen) / frames) if frames else 0.0
        mean_conf = sum(s[4] for s in seen) / len(seen) if seen else 0.0
        return len(seen), fraction, mean_conf

    def _qualifies(self, person: _Person, now: float, frame_width: int) -> bool:
        s = self.settings
        if now - person.entry_s < s.min_dwell_seconds:
            return False
        if self._speed(person, now, frame_width) > s.max_speed_heights_per_second:
            return False
        count, fraction, mean_conf = self._observation(person, now)
        return (
            count >= s.min_track_observations
            and fraction >= s.min_observed_fraction
            and mean_conf >= s.min_mean_detection_confidence
        )

    def _confidence(self, person: _Person, now: float, frame_width: int) -> float:
        s = self.settings
        dwell = min(1.0, max(now - person.entry_s, 0.0) / s.min_dwell_seconds)
        speed = self._speed(person, now, frame_width)
        slowness = 1.0 if speed <= 0 else min(1.0, s.max_speed_heights_per_second / speed)
        _, fraction, _ = self._observation(person, now)
        return max(0.0, min(1.0, dwell * slowness * fraction))

    # --------------------------------------------------------------- update

    def update(
        self,
        timestamp_s: float,
        frame_index: int,
        people: Sequence[PersonDetection],
        frame_size: tuple[int, int],
        person_keys: Mapping[int, str] | None = None,
    ) -> list[ZoneEvent]:
        """Process one frame and return the zone events it produced."""
        s = self.settings
        now = timestamp_s
        events: list[ZoneEvent] = []
        history = max(s.min_dwell_seconds, s.exit_grace_seconds)
        self._in_zone_flags = {}
        self._states = {}
        self._confidences = {}
        self._frame_times.append(now)
        while self._frame_times and self._frame_times[0] < now - history - EPS_S:
            self._frame_times.popleft()

        # One detection per person this frame (highest confidence wins if two tracks share a key).
        visible: dict[str, PersonDetection] = {}
        keys: dict[int, str] = {}
        for person in people:
            if person.track_id is None:
                continue
            key = (person_keys or {}).get(person.track_id) or f"t{person.track_id}"
            keys[person.track_id] = key
            best = visible.get(key)
            if best is None or person.confidence > best.confidence:
                visible[key] = person

        for person in self._persons.values():
            person.visible = False
        for key, detection in visible.items():
            self._observe(key, detection, now, frame_index, frame_size, events)

        for person in list(self._persons.values()):
            self._step_absence(person, now, events)
            expired = person.terminal_s is not None and now - person.terminal_s > s.terminal_retention_seconds
            if person.state in _TERMINAL and expired:
                del self._persons[person.key]

        for track_id, key in keys.items():
            person = self._persons.get(key)
            self._states[track_id] = person.state if person else QueueState.OUTSIDE
        for person in self._persons.values():
            if person.state in _PRESENT:
                self._confidences[person.track_id] = self._confidence(person, now, frame_size[0])
        return events

    def _observe(
        self,
        key: str,
        detection: PersonDetection,
        now: float,
        frame_index: int,
        frame_size: tuple[int, int],
        events: list[ZoneEvent],
    ) -> None:
        s = self.settings
        foot = foot_point(detection)
        height = float(detection.bbox[3] - detection.bbox[1])
        track_id = int(detection.track_id)  # type: ignore[arg-type]
        self._in_zone_flags[track_id] = self.zone.contains(foot, frame_size)
        in_service = self.service_zone is not None and self.service_zone.contains(foot, frame_size)

        person = self._persons.get(key)
        if person is not None and person.state in _TERMINAL:
            if self._passes(foot, height, frame_size, entering=True):
                del self._persons[key]  # same person comes back: start over, like the legacy tracker
                person = None
            else:
                person.visible = True
                person.track_id = track_id
                return
        stay = self._passes(foot, height, frame_size, entering=False)
        if person is None:
            if not self._passes(foot, height, frame_size, entering=True):
                return
            person = self._persons[key] = _Person(
                key, track_id, QueueState.CANDIDATE, now, frame_index, now, now, frame_index
            )
            stay = True
        person.visible = True
        person.track_id = track_id
        person.last_seen_s = now
        person.samples.append((now, foot[0], foot[1], height, detection.confidence))
        history = max(s.min_dwell_seconds, s.exit_grace_seconds)
        while person.samples and person.samples[0][0] < now - history - EPS_S:
            person.samples.popleft()

        if stay or in_service:
            person.last_present_s, person.last_present_frame = now, frame_index

        if person.state is QueueState.TEMPORARILY_OCCLUDED and stay:
            person.state = QueueState.WAITING
        if person.state is QueueState.CANDIDATE and stay and self._qualifies(person, now, frame_size[0]):
            person.state = QueueState.WAITING
            person.joined = True
            events.append(
                ZoneEvent("zone_enter", track_id, now, frame_index, person.entry_s, person.entry_frame, now)
            )
        if in_service and person.state in (*_COUNTED, QueueState.CANDIDATE):
            person.state = QueueState.SERVICE

    def _step_absence(self, person: _Person, now: float, events: list[ZoneEvent]) -> None:
        """Handle occlusion and exit: not detected, or detected but outside the stay zone."""
        grace = self.settings.exit_grace_seconds
        if person.state in _TERMINAL:
            return
        if person.state is QueueState.WAITING and not person.visible:
            person.state = QueueState.TEMPORARILY_OCCLUDED
        if now - person.last_present_s <= grace:
            return
        if person.state is QueueState.CANDIDATE:
            del self._persons[person.key]  # walked through or stepped away before qualifying
            return
        self._finish(person, now, events)

    def _finish(self, person: _Person, now: float, events: list[ZoneEvent]) -> None:
        was_service = person.state is QueueState.SERVICE
        person.state = QueueState.EXITED if was_service else QueueState.ABANDONED
        person.terminal_s = now
        if person.joined:
            events.append(
                ZoneEvent(
                    "zone_exit",
                    person.track_id,
                    person.last_present_s,
                    person.last_present_frame,
                    person.entry_s,
                    person.entry_frame,
                    now,
                )
            )
