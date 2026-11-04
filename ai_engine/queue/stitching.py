"""Re-link a person whose tracker ID changed while inside the queue zone.

ByteTrack gives a new ID after an occlusion. Stitching is deliberately
conservative: a new ID is linked only to a person who vanished inside the
queue zone a moment ago and reappeared close by. Anonymous ``person_key``
values (``p0001``...) are local to one video and carry no identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.camera_config import StitchingSettings
from ai_engine.queue.membership import foot_point
from ai_engine.queue.zone import QueueZone


@dataclass
class _Person:
    key: str
    active_track: int
    last_foot: tuple[float, float]
    last_height: float
    last_seen_s: float
    in_zone: bool
    track_ids: list[int] = field(default_factory=list)
    stitched_from: list[int] = field(default_factory=list)


class TrackStitcher:
    """Map tracker IDs to anonymous per-video person keys, bridging ID switches."""

    def __init__(self, zone: QueueZone, settings: StitchingSettings) -> None:
        self.zone = zone
        self.settings = settings
        self._persons: dict[str, _Person] = {}
        self._track_key: dict[int, str] = {}
        self._retired: set[int] = set()
        self._next_number = 1
        self._stitch_count = 0

    @property
    def stitch_count(self) -> int:
        return self._stitch_count

    def track_ids(self, person_key: str) -> tuple[int, ...]:
        person = self._persons.get(person_key)
        return tuple(person.track_ids) if person else ()

    def _new_key(self) -> str:
        key = f"p{self._next_number:04d}"
        self._next_number += 1
        return key

    def update(
        self, timestamp_s: float, people: Sequence[PersonDetection], frame_size: tuple[int, int]
    ) -> dict[int, str]:
        """Return ``track_id -> person_key`` for every tracked person in this frame."""
        visible = [p for p in people if p.track_id is not None]
        visible_ids = {p.track_id for p in visible}
        feet = {p.track_id: foot_point(p) for p in visible}
        heights = {p.track_id: float(p.bbox[3] - p.bbox[1]) for p in visible}

        new_tracks = [p.track_id for p in visible if self._is_new(p.track_id)]
        links = self._find_links(timestamp_s, new_tracks, feet) if self.settings.enabled else {}

        keys: dict[int, str] = {}
        for track_id in (p.track_id for p in visible):
            if track_id in links:
                person = self._persons[links[track_id]]
                self._retired.add(person.active_track)
                person.active_track = track_id
                person.track_ids.append(track_id)
                person.stitched_from.append(track_id)
                self._stitch_count += 1
                self._track_key[track_id] = person.key
            elif track_id in new_tracks or track_id in self._retired:
                self._retired.discard(track_id)
                key = self._new_key()
                self._persons[key] = _Person(
                    key, track_id, feet[track_id], heights[track_id], timestamp_s, False
                )
                self._persons[key].track_ids.append(track_id)
                self._track_key[track_id] = key
            keys[track_id] = self._track_key[track_id]

        for track_id, key in keys.items():
            person = self._persons[key]
            person.last_foot = feet[track_id]
            person.last_height = heights[track_id]
            person.last_seen_s = timestamp_s
            person.in_zone = self.zone.contains(feet[track_id], frame_size)

        self._forget_lost(timestamp_s, visible_ids)
        return keys

    def _is_new(self, track_id: int) -> bool:
        return track_id not in self._track_key

    def _find_links(
        self, now: float, new_tracks: list[int], feet: dict[int, tuple[float, float]]
    ) -> dict[int, str]:
        """Greedy nearest-first matching of new tracks to recently lost in-queue persons."""
        active = set(feet)
        candidates = []
        for person in self._persons.values():
            gap = now - person.last_seen_s
            if person.active_track in active or not person.in_zone:
                continue
            if not 0 < gap <= self.settings.max_gap_seconds:
                continue
            limit = self.settings.max_distance_heights * max(person.last_height, 1.0)
            for track_id in new_tracks:
                foot = feet[track_id]
                distance = (
                    (foot[0] - person.last_foot[0]) ** 2 + (foot[1] - person.last_foot[1]) ** 2
                ) ** 0.5
                if distance <= limit:
                    candidates.append((distance, track_id, person.key))
        links: dict[int, str] = {}
        used_persons: set[str] = set()
        for _, track_id, key in sorted(candidates):
            if track_id in links or key in used_persons:
                continue
            links[track_id] = key
            used_persons.add(key)
        return links

    def _forget_lost(self, now: float, visible_ids: set[int]) -> None:
        for key in [
            k
            for k, p in self._persons.items()
            if p.active_track not in visible_ids and now - p.last_seen_s > self.settings.max_gap_seconds
        ]:
            for track_id in self._persons.pop(key).track_ids:
                if self._track_key.get(track_id) == key:
                    del self._track_key[track_id]
                self._retired.discard(track_id)
