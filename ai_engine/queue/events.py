"""Queue lifecycle: join, service start and abandonment, per anonymous person.

Driven by the membership events (who is confirmed in the queue), the
stitched person keys and an optional service point. All times are video
time in seconds. Nothing is emitted at the end of a video: people still
waiting then are reported as ``waiting_count`` instead of guessed outcomes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.camera_config import ServiceSettings
from ai_engine.queue.event_schema import (
    QueueAbandonEvent,
    QueueEventType,
    QueueExitEvent,
    QueueJoinEvent,
    ServiceCompleteEvent,
    ServiceStartEvent,
    ZoneEvent,
)
from ai_engine.queue.membership import foot_point
from ai_engine.queue.service_point import ServicePoint, ServiceZone

AT_SERVICE_SECONDS = 2.0  # how long a track stays "at the service point" for overlay colouring


@dataclass
class _Person:
    key: str
    track_ids: set[int] = field(default_factory=set)
    joined: bool = False
    join_s: float = 0.0
    join_frame: int = 0
    served: bool = False
    pending_exit: bool = False
    last_seen_s: float = 0.0
    last_in_zone_s: float | None = None
    last_in_zone_frame: int = 0
    stay_start: tuple[float, int] | None = None  # first time in the zone in the current stay
    service_start_s: float | None = None
    service_completed: bool = False
    svc_inside: bool = False  # last visible observation was inside the service zone
    svc_left: tuple[float, int] | None = None  # first visible observation outside, after being inside
    svc_prev_visible_s: float | None = None


@dataclass
class _ExitWatch:
    """A person who joined; emits ``queue_exit`` once they have been unseen for the forget period."""

    key: str
    track_ids: set[int] = field(default_factory=set)
    last_seen: tuple[float, int] = (0.0, 0)
    outcome: str = "unknown"


class QueueLifecycle:
    """Turn zone events, person keys and service-point crossings into queue events."""

    def __init__(
        self,
        service_point: ServicePoint | None,
        abandon_after_seconds: float,
        service_settings: ServiceSettings | None = None,
    ) -> None:
        self.service_point = service_point
        self.service_settings = service_settings or ServiceSettings()
        self.abandon_after_seconds = abandon_after_seconds
        self.service_visits_without_join = 0
        self._persons: dict[str, _Person] = {}
        self._track_key: dict[int, str] = {}
        self._previous_foot: dict[int, tuple[float, float]] = {}
        self._reached_s: dict[int, float] = {}
        self._visit_keys: set[str] = set()
        self._at_service: frozenset[int] = frozenset()
        self._watches: dict[str, _ExitWatch] = {}

    @property
    def waiting_count(self) -> int:
        return sum(1 for p in self._persons.values() if p.joined and not p.served)

    @property
    def pending_abandon_count(self) -> int:
        return sum(1 for p in self._persons.values() if p.joined and not p.served and p.pending_exit)

    @property
    def at_service_track_ids(self) -> frozenset[int]:
        return self._at_service

    def summary_at(self, now: float) -> dict[str, int]:
        return {
            "waiting_count": self.waiting_count,
            "pending_abandon_count": self.pending_abandon_count,
            "service_visits_without_join": self.service_visits_without_join,
        }

    def update(
        self,
        timestamp_s: float,
        frame_index: int,
        people: Sequence[PersonDetection],
        person_keys: dict[int, str],
        zone_events: Sequence[ZoneEvent],
        frame_size: tuple[int, int],
        track_states: dict[int, bool],
    ) -> list[QueueEventType]:
        now = timestamp_s
        events: list[QueueEventType] = []
        visible = [p for p in people if p.track_id is not None and p.track_id in person_keys]

        # 1. Refresh per-person state from the visible tracks.
        for person in visible:
            key = person_keys[person.track_id]
            state = self._persons.get(key)
            if state is None:
                state = self._persons[key] = _Person(key)
            previous_key = self._track_key.get(person.track_id)
            if previous_key is not None and previous_key != key:
                # The stitcher re-keyed this track (deferred link): detach it from the old person.
                old = self._persons.get(previous_key)
                if old is not None:
                    old.track_ids.discard(person.track_id)
            state.track_ids.add(person.track_id)
            self._track_key[person.track_id] = key
            state.last_seen_s = now
            watch = self._watches.get(key)
            if watch is not None:
                watch.track_ids.add(person.track_id)
                watch.last_seen = (now, frame_index)
            if track_states.get(person.track_id, False):
                self._mark_in_zone(state, now, frame_index)

        # 2. Joins (a stitched person who already joined produces no new event).
        for event in zone_events:
            if event.event_type != "zone_enter":
                continue
            state = self._persons.get(self._track_key.get(event.track_id, ""))
            if state is None or state.joined:
                continue
            entry_s, entry_frame = event.zone_entered_s, event.zone_entered_frame
            if state.stay_start is not None and state.stay_start[0] < entry_s:
                entry_s, entry_frame = state.stay_start
            state.joined = True
            state.join_s, state.join_frame = entry_s, entry_frame
            events.append(QueueJoinEvent(state.key, event.track_id, entry_s, entry_frame, now))
            watch = self._watches.setdefault(state.key, _ExitWatch(state.key))
            watch.track_ids |= state.track_ids
            watch.last_seen = (max(now, watch.last_seen[0]), frame_index)
            watch.outcome = "unknown"  # a (re)join starts a fresh outcome

        # 3. Zone exits of joined, unserved people (the person may still be in the zone via a new track).
        for event in zone_events:
            if event.event_type != "zone_exit":
                continue
            state = self._persons.get(self._track_key.get(event.track_id, ""))
            if state and state.joined and not state.served and (state.last_in_zone_s or 0.0) < now:
                state.pending_exit = True

        # 4. Service point.
        events.extend(self._check_service(now, frame_index, visible, person_keys, frame_size))

        # 5. Abandonment and cleanup.
        events.extend(self._expire(now))

        # 6. Queue exits (after abandonment, so the outcome is already known).
        events.extend(self._exits(now))
        self._previous_foot = {p.track_id: foot_point(p) for p in visible}
        return events

    def _mark_in_zone(self, state: _Person, now: float, frame_index: int) -> None:
        last = state.last_in_zone_s
        if state.stay_start is None or last is None or now - last > self.abandon_after_seconds:
            state.stay_start = (now, frame_index)
        state.last_in_zone_s = now
        state.last_in_zone_frame = frame_index
        state.pending_exit = False

    def _check_service(
        self,
        now: float,
        frame_index: int,
        visible: list[PersonDetection],
        person_keys: dict[int, str],
        frame_size: tuple[int, int],
    ) -> list[QueueEventType]:
        events: list[QueueEventType] = []
        at_service: set[int] = set()
        if self.service_point is None:
            self._at_service = frozenset()
            return events
        for person in visible:
            track_id = person.track_id
            state = self._persons[person_keys[track_id]]
            foot = foot_point(person)
            reached_now = self.service_point.reached(self._previous_foot.get(track_id), foot, frame_size)
            if reached_now:
                self._reached_s[track_id] = now
                if state.joined and not state.served:
                    state.served = True
                    state.service_start_s = now
                    watch = self._watches.get(state.key)
                    if watch is not None:
                        watch.outcome = "served"
                    state.pending_exit = False
                    events.append(
                        ServiceStartEvent(
                            state.key,
                            tuple(sorted(state.track_ids)),
                            now,
                            frame_index,
                            now,
                            state.join_s,
                            now - state.join_s,
                        )
                    )
                elif not state.joined and state.key not in self._visit_keys:
                    self._visit_keys.add(state.key)
                    self.service_visits_without_join += 1
            if isinstance(self.service_point, ServiceZone):
                events.extend(self._track_service_exit(state, now, frame_index, reached_now))
            if state.served and now - self._reached_s.get(track_id, -1e9) <= AT_SERVICE_SECONDS:
                at_service.add(track_id)
        self._at_service = frozenset(at_service)
        return events

    def _track_service_exit(
        self, state: _Person, now: float, frame_index: int, inside: bool
    ) -> list[QueueEventType]:
        """Emit ``service_complete`` once a served person is *seen* outside the service zone.

        Visibility gaps longer than ``exit_confirm_seconds`` invalidate a pending exit, so a
        person who vanishes inside the zone and reappears outside is never counted.
        """
        settings = self.service_settings
        previous, state.svc_prev_visible_s = state.svc_prev_visible_s, now
        if not state.served or state.service_completed or state.service_start_s is None:
            return []
        if inside:
            state.svc_inside, state.svc_left = True, None
            return []
        if previous is not None and now - previous > settings.exit_confirm_seconds + 1e-9:
            state.svc_inside, state.svc_left = False, None  # unobserved gap: transition unknown
        if state.svc_left is None:
            if not state.svc_inside:
                return []
            state.svc_left = (now, frame_index)
        left_s, left_frame = state.svc_left
        if now - left_s < settings.exit_confirm_seconds - 1e-9:
            return []
        duration = left_s - state.service_start_s
        if duration < settings.min_service_seconds - 1e-9:
            return []
        state.service_completed = True
        return [
            ServiceCompleteEvent(
                state.key,
                tuple(sorted(state.track_ids)),
                left_s,
                left_frame,
                now,
                state.service_start_s,
                duration,
            )
        ]

    def _exits(self, now: float) -> list[QueueEventType]:
        events: list[QueueEventType] = []
        limit = self.abandon_after_seconds - 1e-9
        for watch in list(self._watches.values()):
            if now - watch.last_seen[0] >= limit:
                events.append(
                    QueueExitEvent(
                        watch.key,
                        tuple(sorted(watch.track_ids)),
                        watch.last_seen[0],
                        watch.last_seen[1],
                        now,
                        watch.outcome,
                    )
                )
                del self._watches[watch.key]
        return events

    def _expire(self, now: float) -> list[QueueEventType]:
        events: list[QueueEventType] = []
        limit = self.abandon_after_seconds - 1e-9
        for state in list(self._persons.values()):
            if state.joined and not state.served:
                last = state.last_in_zone_s if state.last_in_zone_s is not None else state.join_s
                if now - last >= limit:
                    frame = state.last_in_zone_frame if state.last_in_zone_s is not None else state.join_frame
                    # Without a service point nobody is ever served, so "abandoned" would be meaningless.
                    if self.service_point is not None:
                        watch = self._watches.get(state.key)
                        if watch is not None:
                            watch.outcome = "abandoned"
                        events.append(
                            QueueAbandonEvent(
                                state.key,
                                tuple(sorted(state.track_ids)),
                                last,
                                frame,
                                now,
                                state.join_s,
                                last - state.join_s,
                            )
                        )
                    self._forget(state)
            elif now - state.last_seen_s >= limit:
                self._forget(state)
        return events

    def _forget(self, state: _Person) -> None:
        self._persons.pop(state.key, None)
        self._visit_keys.discard(state.key)
        for track_id in state.track_ids:
            if self._track_key.get(track_id) == state.key:
                del self._track_key[track_id]
            self._reached_s.pop(track_id, None)
