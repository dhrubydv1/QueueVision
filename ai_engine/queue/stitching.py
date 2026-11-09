"""Re-link a person whose tracker ID changed while inside the queue zone.

ByteTrack gives a new ID after an occlusion. Stitching is deliberately
conservative: a new ID is linked only to a person who vanished inside the
queue zone a moment ago and reappeared close by. Anonymous ``person_key``
values (``p0001``...) are local to one video and carry no identity.
"""

from __future__ import annotations

import json
import logging
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.camera_config import StitchingSettings
from ai_engine.queue.membership import foot_point
from ai_engine.queue.zone import QueueZone

LOGGER = logging.getLogger("queuevision.stitching")

REASON_ACCEPTED = "compatible_motion_and_position"
REJECTION_REASONS = (
    "gap_too_long",
    "distance_too_far",
    "size_mismatch",
    "direction_inconsistent",
    "duplicate_of_visible_person",
    "chain_limit",
    "ambiguous",
    "revived_track",
)
VELOCITY_WINDOW_S = 0.5  # foot samples used to estimate a lost person's velocity
EPS_S = 1e-9


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

    @property
    def decisions(self) -> list[StitchDecision]:
        """The legacy stitcher keeps no decision log."""
        return []

    def summary(self) -> dict[str, Any]:
        return {"merges": self._stitch_count, "rejections_by_reason": {}, "revived_tracks": 0}

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


@dataclass(frozen=True)
class StitchDecision:
    """One accepted or rejected candidate link between a lost track and a new track."""

    previous_track_id: int
    new_track_id: int
    person_key: str
    timestamp_s: float
    time_gap_seconds: float
    distance_pixels: float
    distance_heights: float
    size_ratio: float
    direction_similarity: float | None
    match_confidence: float
    decision: str  # "merged" | "rejected"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_track_id": self.previous_track_id,
            "new_track_id": self.new_track_id,
            "person_key": self.person_key,
            "timestamp_s": round(self.timestamp_s, 4),
            "time_gap_seconds": round(self.time_gap_seconds, 4),
            "distance_pixels": round(self.distance_pixels, 2),
            "distance_heights": round(self.distance_heights, 4),
            "size_ratio": round(self.size_ratio, 4),
            "direction_similarity": (
                None if self.direction_similarity is None else round(self.direction_similarity, 4)
            ),
            "match_confidence": round(self.match_confidence, 4),
            "decision": self.decision,
            "reason": self.reason,
        }


@dataclass
class _GuardedPerson:
    key: str
    active_track: int
    last_foot: tuple[float, float]
    last_height: float
    last_seen_s: float
    in_zone: bool = False
    links: int = 0
    track_ids: list[int] = field(default_factory=list)
    samples: deque[tuple[float, float, float]] = field(default_factory=deque)  # (t, foot_x, foot_y)

    def velocity(self) -> tuple[float, float]:
        """Foot velocity in px/s over the last ``VELOCITY_WINDOW_S`` of samples."""
        if len(self.samples) < 2:
            return 0.0, 0.0
        last = self.samples[-1]
        first = next(s for s in self.samples if s[0] >= last[0] - VELOCITY_WINDOW_S - EPS_S)
        dt = last[0] - first[0]
        if dt <= 0:
            return 0.0, 0.0
        return (last[1] - first[1]) / dt, (last[2] - first[2]) / dt


@dataclass(frozen=True)
class _Candidate:
    person: str
    track: int
    decision: StitchDecision
    reason: str  # "" when the pair passed every hard check


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    inter = max(right - left, 0.0) * max(bottom - top, 0.0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class GuardedTrackStitcher:
    """Conservative stitcher: deferred, scored, ambiguity-checked linking of new tracks.

    Differences from :class:`TrackStitcher`:

    * a lost in-zone person is linked only after being unseen for
      ``min_lost_seconds`` (ByteTrack revives lost tracks with their own ID
      until its buffer runs out, so earlier links would steal revivals);
    * a new track first gets a provisional person key and is re-keyed to the
      lost person's key when a link is accepted later;
    * a retired track ID that reappears is mapped back to its owner when the
      owner's current track is not visible;
    * candidates are scored (predicted foot position, size, direction),
      checked against hard guards and rejected when the runner-up is too close.

    ``match_confidence`` is an uncalibrated heuristic in [0, 1] (product of a
    distance term and a size term), not a probability.
    """

    def __init__(self, zone: QueueZone, settings: StitchingSettings) -> None:
        self.zone = zone
        self.settings = settings
        self._persons: dict[str, _GuardedPerson] = {}
        self._track_key: dict[int, str] = {}
        self._retired: dict[int, str] = {}  # retired track id -> person who owned it
        self._first_seen: dict[int, float] = {}  # tracks eligible for linking
        self._next_number = 1
        self._stitch_count = 0
        self._revived = 0
        self._rejections: dict[str, int] = {}
        self._decisions: list[StitchDecision] = []
        self._last_reason: dict[tuple[int, int], str] = {}

    # ------------------------------------------------------------ public views

    @property
    def stitch_count(self) -> int:
        return self._stitch_count

    @property
    def decisions(self) -> list[StitchDecision]:
        return self._decisions

    def summary(self) -> dict[str, Any]:
        return {
            "merges": self._stitch_count,
            "rejections_by_reason": dict(sorted(self._rejections.items())),
            "revived_tracks": self._revived,
        }

    def track_ids(self, person_key: str) -> tuple[int, ...]:
        person = self._persons.get(person_key)
        return tuple(person.track_ids) if person else ()

    # ----------------------------------------------------------------- update

    def _new_key(self) -> str:
        key = f"p{self._next_number:04d}"
        self._next_number += 1
        return key

    def _create(self, track_id: int, foot: tuple[float, float], height: float, now: float) -> str:
        key = self._new_key()
        person = _GuardedPerson(key, track_id, foot, height, now, track_ids=[track_id])
        self._persons[key] = person
        self._track_key[track_id] = key
        return key

    def update(
        self, timestamp_s: float, people: Sequence[PersonDetection], frame_size: tuple[int, int]
    ) -> dict[int, str]:
        """Return ``track_id -> person_key`` for every tracked person in this frame."""
        now = timestamp_s
        visible = [p for p in people if p.track_id is not None]
        visible_ids = {int(p.track_id) for p in visible}  # type: ignore[arg-type]
        feet = {int(p.track_id): foot_point(p) for p in visible}  # type: ignore[arg-type]
        heights = {int(p.track_id): float(p.bbox[3] - p.bbox[1]) for p in visible}  # type: ignore[arg-type]
        boxes = {int(p.track_id): tuple(float(v) for v in p.bbox) for p in visible}  # type: ignore[arg-type]

        for track_id in (int(p.track_id) for p in visible):  # type: ignore[arg-type]
            if track_id in self._track_key:
                continue
            if track_id in self._retired:
                self._handle_revival(track_id, now, feet, heights, visible_ids)
            else:
                self._create(track_id, feet[track_id], heights[track_id], now)
                self._first_seen[track_id] = now

        if self.settings.enabled:
            for track_id in [
                t for t, first in self._first_seen.items() if now - first > self.settings.max_gap_seconds
            ]:
                del self._first_seen[track_id]
            self._link(now, visible_ids, feet, heights, boxes)

        keys: dict[int, str] = {}
        for track_id in sorted(visible_ids):
            key = self._track_key[track_id]
            keys[track_id] = key
            person = self._persons[key]
            person.last_foot, person.last_height, person.last_seen_s = feet[track_id], heights[track_id], now
            person.in_zone = self.zone.contains(feet[track_id], frame_size)
            person.samples.append((now, feet[track_id][0], feet[track_id][1]))
            while len(person.samples) > 2 and person.samples[1][0] < now - VELOCITY_WINDOW_S - EPS_S:
                person.samples.popleft()

        self._forget_lost(now, visible_ids)
        return keys

    # ---------------------------------------------------------------- revival

    def _handle_revival(
        self,
        track_id: int,
        now: float,
        feet: dict[int, tuple[float, float]],
        heights: dict[int, float],
        visible_ids: set[int],
    ) -> None:
        owner_key = self._retired.pop(track_id)
        owner = self._persons.get(owner_key)
        self._revived += 1
        if owner is not None and owner.active_track not in visible_ids:
            previous = owner.active_track
            self._retired[previous] = owner.key
            self._track_key.pop(previous, None)
            owner.active_track = track_id
            owner.track_ids.append(track_id)
            self._track_key[track_id] = owner.key
            self._record(self._measure(owner, track_id, now, feet, heights, "merged", "revived_track"))
            return
        key = self._create(track_id, feet[track_id], heights[track_id], now)
        reference = owner or self._persons[key]
        self._record(
            self._measure(
                reference, track_id, now, feet, heights, "rejected", "revived_track", person_key=key
            )
        )

    # ---------------------------------------------------------------- linking

    def _measure(
        self,
        person: _GuardedPerson,
        track_id: int,
        now: float,
        feet: dict[int, tuple[float, float]],
        heights: dict[int, float],
        decision: str,
        reason: str,
        person_key: str | None = None,
    ) -> StitchDecision:
        """Build a decision with the motion, size and direction measurements for one pair."""
        gap = now - person.last_seen_s
        height = max(person.last_height, 1.0)
        vx, vy = person.velocity()
        move_x, move_y = vx * gap, vy * gap
        cap = self.settings.max_distance_heights * height
        move = math.hypot(move_x, move_y)
        if move > cap > 0:
            move_x, move_y = move_x * cap / move, move_y * cap / move
        foot = feet[track_id]
        predicted = (person.last_foot[0] + move_x, person.last_foot[1] + move_y)
        distance = math.hypot(foot[0] - predicted[0], foot[1] - predicted[1])
        new_height = max(heights[track_id], 1.0)
        ratio = max(height, new_height) / min(height, new_height)
        direction: float | None = None
        displacement = (foot[0] - person.last_foot[0], foot[1] - person.last_foot[1])
        speed = math.hypot(vx, vy)
        norm = speed * math.hypot(*displacement)
        if speed / height >= self.settings.direction_min_speed_heights and norm > 0:
            direction = (vx * displacement[0] + vy * displacement[1]) / norm
        s = self.settings
        confidence = _clamp01(1.0 - distance / height / s.max_distance_heights) * _clamp01(
            1.0 - (ratio - 1.0) / (s.max_size_ratio - 1.0)
        )
        return StitchDecision(
            person.active_track,
            track_id,
            person_key or person.key,
            now,
            gap,
            distance,
            distance / height,
            ratio,
            direction,
            confidence,
            decision,
            reason,
        )

    def _hard_reason(
        self,
        person: _GuardedPerson,
        d: StitchDecision,
        track_id: int,
        boxes: dict[int, tuple[float, float, float, float]],
        known: list[int],
    ) -> str:
        s = self.settings
        if d.time_gap_seconds > s.max_gap_seconds + EPS_S:
            return "gap_too_long"
        if d.distance_heights > s.max_distance_heights:
            return "distance_too_far"
        if d.size_ratio > s.max_size_ratio:
            return "size_mismatch"
        if d.direction_similarity is not None and d.direction_similarity < s.min_direction_similarity:
            return "direction_inconsistent"
        if any(_iou(boxes[track_id], boxes[other]) > s.max_duplicate_iou for other in known):
            return "duplicate_of_visible_person"
        if person.links >= s.max_links_per_person:
            return "chain_limit"
        return ""

    def _link(
        self,
        now: float,
        visible_ids: set[int],
        feet: dict[int, tuple[float, float]],
        heights: dict[int, float],
        boxes: dict[int, tuple[float, float, float, float]],
    ) -> None:
        s = self.settings
        lost = [
            p
            for p in self._persons.values()
            if p.active_track not in visible_ids
            and p.in_zone
            and now - p.last_seen_s >= s.min_lost_seconds - EPS_S
        ]
        tracks = sorted(t for t in self._first_seen if t in visible_ids)
        if not lost or not tracks:
            self._last_reason = {k: v for k, v in self._last_reason.items() if k[1] in self._first_seen}
            return
        candidates: list[_Candidate] = []
        for person in lost:
            for track_id in tracks:
                if self._first_seen[track_id] <= person.last_seen_s:
                    continue
                d = self._measure(person, track_id, now, feet, heights, "rejected", "")
                known = [
                    t
                    for t in visible_ids
                    if t != track_id and self._first_seen.get(t, -math.inf) <= person.last_seen_s
                ]
                reason = self._hard_reason(person, d, track_id, boxes, known)
                candidates.append(_Candidate(person.key, track_id, d, reason))

        passing = [c for c in candidates if not c.reason]
        accepted: list[_Candidate] = []
        used_persons: set[str] = set()
        used_tracks: set[int] = set()
        ambiguous: set[int] = set()
        for index, c in enumerate(passing):
            for other in passing:
                if other is c or (other.person != c.person and other.track != c.track):
                    continue
                if abs(other.decision.match_confidence - c.decision.match_confidence) < s.ambiguity_margin:
                    ambiguous.add(index)
        for index, c in sorted(
            enumerate(passing), key=lambda item: (-item[1].decision.match_confidence, item[1].track)
        ):
            if index in ambiguous or c.person in used_persons or c.track in used_tracks:
                continue
            accepted.append(c)
            used_persons.add(c.person)
            used_tracks.add(c.track)

        accepted_ids = {id(c) for c in accepted}
        ambiguous_ids = {id(passing[i]) for i in ambiguous}
        for c in candidates:
            d = c.decision
            if id(c) in accepted_ids:
                self._record_pair(_with(d, "merged", REASON_ACCEPTED))
            elif c.reason:
                self._record_pair(_with(d, "rejected", c.reason))
            elif id(c) in ambiguous_ids:
                self._record_pair(_with(d, "rejected", "ambiguous"))
        for c in accepted:
            self._apply(c)
        self._last_reason = {k: v for k, v in self._last_reason.items() if k[1] in self._first_seen}

    def _apply(self, c: _Candidate) -> None:
        person = self._persons[c.person]
        provisional = self._track_key.get(c.track)
        if provisional is not None and provisional != person.key:
            self._persons.pop(provisional, None)
        self._retired[person.active_track] = person.key
        self._track_key.pop(person.active_track, None)
        person.active_track = c.track
        person.track_ids.append(c.track)
        person.links += 1
        self._track_key[c.track] = person.key
        self._first_seen.pop(c.track, None)
        self._stitch_count += 1

    def _record_pair(self, decision: StitchDecision) -> None:
        pair = (decision.previous_track_id, decision.new_track_id)
        if decision.decision == "rejected" and self._last_reason.get(pair) == decision.reason:
            return
        self._last_reason[pair] = decision.reason
        self._record(decision)

    def _record(self, decision: StitchDecision) -> None:
        if decision.decision == "rejected":
            self._rejections[decision.reason] = self._rejections.get(decision.reason, 0) + 1
        if self.settings.diagnostics:
            self._decisions.append(decision)
        LOGGER.debug("stitch %s", decision.to_dict())

    # ----------------------------------------------------------------- expiry

    def _forget_lost(self, now: float, visible_ids: set[int]) -> None:
        for key in [
            k
            for k, p in self._persons.items()
            if p.active_track not in visible_ids and now - p.last_seen_s > self.settings.max_gap_seconds
        ]:
            for track_id in self._persons.pop(key).track_ids:
                if self._track_key.get(track_id) == key:
                    del self._track_key[track_id]
                if self._retired.get(track_id) == key:
                    del self._retired[track_id]
                self._first_seen.pop(track_id, None)
        for track_id in [t for t, k in self._retired.items() if k not in self._persons]:
            del self._retired[track_id]


def _with(decision: StitchDecision, outcome: str, reason: str) -> StitchDecision:
    return StitchDecision(**{**decision.__dict__, "decision": outcome, "reason": reason})


def write_decisions_jsonl(path: str | Path, decisions: Sequence[StitchDecision]) -> Path:
    """Write one stitch decision per line (empty file when there are none)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(json.dumps(decision.to_dict()) + "\n")
    return path
