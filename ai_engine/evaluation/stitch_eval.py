"""Tracking-identity evaluation of track stitching on sequences with ground-truth IDs.

The saved tracks of a sequence are replayed through the stitcher only (no
membership logic). Every track ID is rewritten to the person key the
stitcher assigned at that frame, and the rewritten tracks are scored with
:func:`ai_engine.evaluation.mot_eval.evaluate_tracks` (MOTA, IDF1, ID
switches, ...). HOTA is not computed: ``motmetrics`` does not implement it
and TrackEval is not a dependency.

Merge correctness: every link ``previous_track_id -> new_track_id`` is checked
against each track's *dominant ground-truth identity* (see
:func:`dominant_identities`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.mot_eval import (
    IOU_THRESHOLD,
    FrameBoxes,
    GroundTruth,
    drop_distractor_matches,
    evaluate_tracks,
    iou_matrix,
)
from ai_engine.queue.camera_config import CameraConfig, StitchingSettings
from ai_engine.queue.stitching import GuardedTrackStitcher, StitchDecision, TrackStitcher
from ai_engine.queue.zone import QueueZone

MIN_DOMINANT_VOTES = 3  # matched frames a track needs before it has a dominant identity
MIN_DOMINANT_SHARE = 0.5  # share of its matched frames the dominant identity must hold
CONFIG_NAMES = ("no_stitching", "legacy", "guarded")
CORRECT, INCORRECT, UNVERIFIABLE = "correct", "incorrect", "unverifiable"


@dataclass(frozen=True)
class Link:
    """One identity link made by a stitcher: ``previous_track_id`` continues as ``new_track_id``."""

    previous_track_id: int
    new_track_id: int
    person_key: str
    first_frame: int  # 0-based frame at which the link took effect


@dataclass
class StitchReplay:
    """Result of replaying a stitcher over saved tracks."""

    frame_keys: dict[int, dict[int, str]] = field(default_factory=dict)  # 0-based frame -> track -> key
    links: list[Link] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    decisions: list[StitchDecision] = field(default_factory=list)


def stitching_variant(camera: CameraConfig, name: str) -> StitchingSettings:
    """Stitching settings for one named configuration, derived from the camera's own settings."""
    base = camera.stitching
    if name == "no_stitching":
        return StitchingSettings(**{**base.__dict__, "enabled": False, "algorithm": "legacy"})
    if name in ("legacy", "guarded"):
        return StitchingSettings(**{**base.__dict__, "enabled": True, "algorithm": name})
    raise ValueError(f"unknown stitching configuration {name!r}")


def replay_stitcher(
    tracks: Mapping[int, Sequence[PersonDetection]],
    camera: CameraConfig,
    settings: StitchingSettings,
    fps: float,
    frame_size: tuple[int, int],
    frame_count: int,
) -> StitchReplay:
    """Run only the stitcher over frames ``0..frame_count-1`` and record keys and links."""
    zone = QueueZone(camera.queue_zone)
    stitcher: TrackStitcher | GuardedTrackStitcher = (
        GuardedTrackStitcher(zone, settings)
        if settings.algorithm == "guarded"
        else TrackStitcher(zone, settings)
    )
    result = StitchReplay()
    last_track: dict[str, int] = {}
    seen: set[tuple[int, str]] = set()
    for frame in range(frame_count):
        keys = stitcher.update(frame / fps, list(tracks.get(frame, ())), frame_size)
        result.frame_keys[frame] = keys
        for track_id, key in keys.items():
            previous = last_track.get(key)
            if previous is not None and previous != track_id and (track_id, key) not in seen:
                result.links.append(Link(previous, track_id, key, frame))
            seen.add((track_id, key))
            last_track[key] = track_id
    result.summary = stitcher.summary()
    result.decisions = list(stitcher.decisions)
    return result


def key_number(key: str) -> int:
    """``"p0012"`` -> 12 (the numeric ID written into the rewritten MOT tracks)."""
    return int(key.removeprefix("p"))


def final_keys(frame_keys: Mapping[int, Mapping[int, str]]) -> dict[int, dict[int, str]]:
    """Per frame, map every track to the key it has at the *last* frame it appears in.

    This is what a post-hoc consumer sees after deferred links are applied; it
    removes the extra ID change a provisional key causes before its link.
    """
    last: dict[int, str] = {}
    for frame in sorted(frame_keys):
        last.update(frame_keys[frame])
    return {frame: {t: last[t] for t in keys} for frame, keys in frame_keys.items()}


def rewrite_ids(
    tracks: Mapping[int, FrameBoxes], frame_keys: Mapping[int, Mapping[int, str]] | None
) -> dict[int, FrameBoxes]:
    """Replace track IDs by person-key numbers. ``tracks`` is 1-based, ``frame_keys`` 0-based.

    ``frame_keys=None`` keeps the original IDs. If two tracks of one frame end up with the
    same person key, only the first is kept (a person cannot be in two places).
    """
    if frame_keys is None:
        return dict(tracks)
    rewritten: dict[int, FrameBoxes] = {}
    for frame, entry in tracks.items():
        keys = frame_keys.get(frame - 1, {})
        out = FrameBoxes()
        for track_id, box in zip(entry.ids, entry.boxes, strict=True):
            new_id = key_number(keys[track_id]) if track_id in keys else track_id + 10_000_000
            if new_id in out.ids:
                continue
            out.ids.append(new_id)
            out.boxes.append(box)
        rewritten[frame] = out
    return rewritten


def dominant_identities(
    ground_truth: GroundTruth,
    tracks: Mapping[int, FrameBoxes],
    frames: Sequence[int],
    threshold: float = IOU_THRESHOLD,
    min_votes: int = MIN_DOMINANT_VOTES,
    min_share: float = MIN_DOMINANT_SHARE,
) -> dict[int, int | None]:
    """Dominant ground-truth pedestrian ID of every track (``None`` when there is none).

    Per frame, hypotheses matching a distractor are dropped (as in scoring), then
    hypotheses and ground-truth pedestrians are matched one-to-one by IoU
    (Hungarian) and pairs with IoU >= ``threshold`` vote. A track's dominant
    identity is the ID with most votes, provided the track has at least
    ``min_votes`` matched frames and the ID holds at least ``min_share`` of them.
    """
    from scipy.optimize import linear_sum_assignment

    empty = FrameBoxes()
    votes: dict[int, Counter[int]] = {}
    seen_tracks: set[int] = set()
    for frame in frames:
        pedestrians = ground_truth.pedestrians.get(frame, empty)
        hyp = tracks.get(frame, empty)
        seen_tracks.update(hyp.ids)
        hyp = drop_distractor_matches(hyp, pedestrians, ground_truth.distractors.get(frame, empty), threshold)
        if not hyp.ids or not pedestrians.ids:
            continue
        iou = iou_matrix(pedestrians.array(), hyp.array())
        rows, cols = linear_sum_assignment(-iou)
        for row, col in zip(rows, cols, strict=True):
            if iou[row, col] >= threshold:
                votes.setdefault(hyp.ids[col], Counter())[pedestrians.ids[row]] += 1
    result: dict[int, int | None] = {track: None for track in seen_tracks}
    for track, counter in votes.items():
        total = sum(counter.values())
        gt_id, count = counter.most_common(1)[0]
        if total >= min_votes and count / total >= min_share:
            result[track] = gt_id
    return result


def judge_links(links: Sequence[Link], identities: Mapping[int, int | None]) -> dict[str, Any]:
    """Count correct / incorrect / unverifiable links and the merge precision."""
    verdicts = Counter()
    for link in links:
        a, b = identities.get(link.previous_track_id), identities.get(link.new_track_id)
        if a is None or b is None:
            verdicts[UNVERIFIABLE] += 1
        elif a == b:
            verdicts[CORRECT] += 1
        else:
            verdicts[INCORRECT] += 1
    judged = verdicts[CORRECT] + verdicts[INCORRECT]
    return {
        "merges": len(links),
        CORRECT: verdicts[CORRECT],
        INCORRECT: verdicts[INCORRECT],
        UNVERIFIABLE: verdicts[UNVERIFIABLE],
        "merge_precision": round(verdicts[CORRECT] / judged, 4) if judged else None,
    }


def restrict_to_zone(
    ground_truth: GroundTruth,
    tracks: Mapping[int, FrameBoxes],
    zone: QueueZone,
    frame_size: tuple[int, int],
) -> tuple[GroundTruth, dict[int, FrameBoxes]]:
    """Keep only boxes whose foot point (bottom centre) lies inside the zone."""

    def keep(entries: Mapping[int, FrameBoxes]) -> dict[int, FrameBoxes]:
        filtered: dict[int, FrameBoxes] = {}
        for frame, entry in entries.items():
            out = FrameBoxes()
            for track_id, (x, y, w, h) in zip(entry.ids, entry.boxes, strict=True):
                if zone.contains((x + w / 2, y + h), frame_size):
                    out.ids.append(track_id)
                    out.boxes.append((x, y, w, h))
            filtered[frame] = out
        return filtered

    return (
        GroundTruth(keep(ground_truth.pedestrians), keep(ground_truth.distractors)),
        keep(tracks),
    )


def _subset(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {k: metrics[k] for k in ("mota", "idf1", "idp", "idr", "num_switches", "num_fragmentations")}


def evaluate_configuration(
    name: str,
    camera: CameraConfig,
    detections: Mapping[int, Sequence[PersonDetection]],
    tracks: Mapping[int, FrameBoxes],
    ground_truth: GroundTruth,
    fps: float,
    frame_size: tuple[int, int],
    frames: Sequence[int],
) -> dict[str, Any]:
    """Replay one stitching configuration and score identity quality (whole frame and zone only)."""
    settings = stitching_variant(camera, name)
    replay = replay_stitcher(detections, camera, settings, fps, frame_size, len(frames))
    rewritten = rewrite_ids(tracks, replay.frame_keys if settings.enabled else None)
    zone_truth, zone_tracks = restrict_to_zone(
        ground_truth, rewritten, QueueZone(camera.queue_zone), frame_size
    )
    zone_metrics = evaluate_tracks(zone_truth, zone_tracks, frames)
    identities = dominant_identities(ground_truth, tracks, frames)
    final_metrics = (
        evaluate_tracks(ground_truth, rewrite_ids(tracks, final_keys(replay.frame_keys)), frames)
        if settings.enabled
        else None
    )
    entry: dict[str, Any] = {
        "metrics": evaluate_tracks(ground_truth, rewritten, frames),
        "zone_only": {
            key: zone_metrics[key] for key in ("num_switches", "num_fragmentations", "idf1", "mota")
        },
        "stitching": replay.summary,
        "metrics_with_final_keys": _subset(final_metrics) if final_metrics else None,
        "merge_correctness": judge_links(replay.links if settings.enabled else [], identities),
        "persons_after_stitching": len({k for keys in replay.frame_keys.values() for k in keys.values()})
        if settings.enabled
        else len({t for e in tracks.values() for t in e.ids}),
        "hota": "not available (motmetrics has no HOTA; TrackEval not installed)",
    }
    return entry


def stitch_statistics(
    detections: Mapping[int, Sequence[PersonDetection]],
    camera: CameraConfig,
    fps: float,
    frame_size: tuple[int, int],
    frame_count: int,
) -> dict[str, Any]:
    """Stitch counts for legacy and guarded stitching on a clip without ground-truth IDs."""
    stats: dict[str, Any] = {}
    for name in ("legacy", "guarded"):
        replay = replay_stitcher(
            detections, camera, stitching_variant(camera, name), fps, frame_size, frame_count
        )
        stats[name] = {"links": len(replay.links), **replay.summary}
    return stats


def count_unique_tracks(tracks: Mapping[int, FrameBoxes]) -> int:
    return len({t for e in tracks.values() for t in e.ids})
