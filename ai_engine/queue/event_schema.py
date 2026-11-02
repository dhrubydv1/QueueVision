"""Versioned queue event types (the contract consumed by later sprints).

All times are video time in seconds (frame index / FPS), never wall-clock.
``timestamp_s`` is when the thing happened; ``detected_s`` is the video time
at which the system emitted the event, which can be later (a queue join is
only known once the dwell time has passed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"

EVENT_TYPES = (
    "zone_enter",
    "zone_exit",
    "queue_join",
    "service_start",
    "queue_abandon",
    "congestion_start",
    "congestion_end",
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


class _EventBase:
    """Shared ``to_dict``: dataclass fields in declaration order, tuples as lists."""

    def to_dict(self) -> dict[str, Any]:
        names = ("schema_version", "event_type", *(n for n in self.__dataclass_fields__ if n not in _META))
        return {name: _json_safe(getattr(self, name)) for name in names}


_META = ("schema_version", "event_type")


@dataclass(frozen=True)
class ZoneEvent(_EventBase):
    """A confirmed queue member entering or leaving the queue zone.

    ``zone_enter`` fires when membership is confirmed (after the dwell
    time); ``zone_entered_s`` is when the person first stepped into the
    zone, which is the value waiting-time logic should use. ``zone_exit``
    uses the last time the person was seen inside the zone.
    """

    event_type: str  # "zone_enter" | "zone_exit"
    track_id: int
    timestamp_s: float
    frame_index: int
    zone_entered_s: float
    zone_entered_frame: int = 0
    detected_s: float = 0.0
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class QueueJoinEvent(_EventBase):
    """A person joined the queue; ``timestamp_s`` is their first time in the zone."""

    person_key: str
    track_id: int
    timestamp_s: float
    frame_index: int
    detected_s: float
    event_type: str = field(default="queue_join", kw_only=True)
    schema_version: str = field(default=SCHEMA_VERSION, kw_only=True)


@dataclass(frozen=True)
class ServiceStartEvent(_EventBase):
    """A queued person reached the service point after ``wait_s`` seconds."""

    person_key: str
    track_ids: tuple[int, ...]
    timestamp_s: float
    frame_index: int
    detected_s: float
    join_s: float
    wait_s: float
    event_type: str = field(default="service_start", kw_only=True)
    schema_version: str = field(default=SCHEMA_VERSION, kw_only=True)


@dataclass(frozen=True)
class QueueAbandonEvent(_EventBase):
    """A queued person left without being served; ``timestamp_s`` is the last time in the zone."""

    person_key: str
    track_ids: tuple[int, ...]
    timestamp_s: float
    frame_index: int
    detected_s: float
    join_s: float
    time_in_queue_s: float
    event_type: str = field(default="queue_abandon", kw_only=True)
    schema_version: str = field(default=SCHEMA_VERSION, kw_only=True)


@dataclass(frozen=True)
class CongestionEvent(_EventBase):
    """Congestion started or ended (``timestamp_s`` is the time it was detected)."""

    event_type: str  # "congestion_start" | "congestion_end"
    timestamp_s: float
    frame_index: int
    detected_s: float
    condition_since_s: float
    reasons: tuple[str, ...]  # subset of "queue_length", "predicted_wait"
    queue_length: float
    predicted_wait_s: float | None = None
    schema_version: str = SCHEMA_VERSION


QueueEventType = ZoneEvent | QueueJoinEvent | ServiceStartEvent | QueueAbandonEvent | CongestionEvent
