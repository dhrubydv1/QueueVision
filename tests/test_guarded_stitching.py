"""Tests for the guarded stitcher, stitch evaluation and the SERVICE-counting rule (synthetic data)."""

from __future__ import annotations

import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.mot_eval import FrameBoxes, GroundTruth
from ai_engine.evaluation.stitch_eval import (
    Link,
    dominant_identities,
    final_keys,
    judge_links,
    rewrite_ids,
)
from ai_engine.queue import (
    CameraConfig,
    CameraConfigError,
    GuardedTrackStitcher,
    MembershipSettings,
    QueueAnalyzer,
    QueueLifecycle,
    QueueZone,
    StitchingSettings,
    TemporalMembershipClassifier,
)

FPS = 10.0
SIZE = (1000, 1000)
ZONE = ((0.05, 0.1), (0.95, 0.1), (0.95, 0.95), (0.05, 0.95))
DICT_KEYS = {
    "previous_track_id",
    "new_track_id",
    "person_key",
    "timestamp_s",
    "time_gap_seconds",
    "distance_pixels",
    "distance_heights",
    "size_ratio",
    "direction_similarity",
    "match_confidence",
    "decision",
    "reason",
}


def at(x: float, track_id: int, y: float = 700, height: int = 200, width: int = 100) -> PersonDetection:
    return PersonDetection(
        bbox=(int(x - width / 2), int(y - height), int(x + width / 2), int(y)),
        confidence=0.9,
        track_id=track_id,
    )


def settings(**kw) -> StitchingSettings:
    return StitchingSettings(algorithm="guarded", diagnostics=True, **kw)


def run(stitcher: GuardedTrackStitcher, spec: dict[int, list[PersonDetection]], end: int):
    keys = {}
    for frame in range(end):
        keys[frame] = stitcher.update(frame / FPS, spec.get(frame, []), SIZE)
    return keys


def stand(spec, track_id, x, start, stop, **kw):
    for frame in range(start, stop):
        spec.setdefault(frame, []).append(at(x, track_id, **kw))


def make(**kw) -> GuardedTrackStitcher:
    return GuardedTrackStitcher(QueueZone(ZONE), settings(**kw))


def reasons(stitcher: GuardedTrackStitcher) -> set[str]:
    return {d.reason for d in stitcher.decisions}


def test_defaults_keep_legacy_algorithm() -> None:
    assert StitchingSettings().algorithm == "legacy"
    assert StitchingSettings().diagnostics is False


@pytest.mark.parametrize(
    "bad",
    [
        {"algorithm": "x"},
        {"max_size_ratio": 1.0},
        {"min_direction_similarity": -2},
        {"max_duplicate_iou": 0},
        {"max_links_per_person": 0},
        {"ambiguity_margin": -0.1},
    ],
)
def test_invalid_stitching_settings_rejected(bad) -> None:
    with pytest.raises(CameraConfigError):
        CameraConfig("c", ZONE, stitching=StitchingSettings(**bad))


def test_deferred_linking_waits_for_min_lost_seconds() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)  # last seen t=0.9
    stand(spec, 2, 520, 10, 40)  # new track from t=1.0
    keys = run(s, spec, 40)
    assert keys[12][2] != keys[5][1]  # not linked yet (gap 0.3 s)
    assert keys[18][2] != keys[5][1]
    assert keys[19][2] == keys[5][1]  # gap reaches 1.0 s
    assert keys[30][2] == keys[5][1]  # stays linked
    assert s.stitch_count == 1
    assert s.summary()["merges"] == 1
    merged = [d for d in s.decisions if d.decision == "merged"]
    assert [d.reason for d in merged] == ["compatible_motion_and_position"]
    assert (merged[0].previous_track_id, merged[0].new_track_id) == (1, 2)


def test_revived_track_maps_back_to_its_owner() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 520, 20, 30)  # linked at once (gap 1.1 s)
    stand(spec, 1, 520, 32, 40)  # ByteTrack revives ID 1 while track 2 is gone
    keys = run(s, spec, 40)
    owner = keys[0][1]
    assert keys[25][2] == owner
    assert keys[35][1] == owner
    assert s.summary()["revived_tracks"] == 1
    assert any(d.reason == "revived_track" and d.decision == "merged" for d in s.decisions)


def test_revived_track_gets_new_key_when_owner_is_visible_elsewhere() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 520, 20, 40)
    stand(spec, 1, 800, 32, 40)  # owner's current track (2) is visible: ID 1 is somebody else
    keys = run(s, spec, 40)
    assert keys[35][1] != keys[35][2]
    assert any(d.reason == "revived_track" and d.decision == "rejected" for d in s.decisions)


def test_expired_person_is_never_reused() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 520, 20, 30)  # 1 -> 2 linked, retiring ID 1
    stand(spec, 1, 520, 60, 70)  # owner unseen for 3 s (> max_gap): expired
    stand(spec, 3, 520, 80, 90)
    keys = run(s, spec, 90)
    owner = keys[0][1]
    assert keys[65][1] != owner
    assert keys[85][3] != owner
    assert keys[85][3] == keys[65][1]  # the new person (not the expired owner) continues
    assert s.stitch_count == 2


def test_gap_too_long_is_not_linked() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 510, 40, 50)  # 3.1 s later
    keys = run(s, spec, 50)
    assert keys[45][2] != keys[0][1]
    assert s.stitch_count == 0


def test_ambiguity_rejects_two_lost_persons_for_one_track() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 400, 0, 10)
    stand(spec, 2, 420, 0, 10)
    stand(spec, 3, 410, 20, 30)
    keys = run(s, spec, 30)
    assert keys[25][3] not in (keys[0][1], keys[0][2])
    assert s.stitch_count == 0
    assert "ambiguous" in reasons(s)


def test_ambiguity_rejects_two_new_tracks_for_one_lost_person() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 480, 20, 30)
    stand(spec, 3, 520, 20, 30)
    run(s, spec, 30)
    assert s.stitch_count == 0
    assert "ambiguous" in reasons(s)


def test_clear_winner_is_linked_despite_competitor() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 505, 20, 30)
    stand(spec, 3, 640, 20, 30)  # 0.7 heights away: much worse score
    keys = run(s, spec, 30)
    assert keys[25][2] == keys[0][1]
    assert keys[25][3] != keys[0][1]


def test_size_mismatch_rejected() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 505, 20, 30, height=400)
    run(s, spec, 30)
    assert s.stitch_count == 0
    assert "size_mismatch" in reasons(s)


def test_direction_inconsistency_rejected_and_control_accepted() -> None:
    def scenario(new_x: float) -> GuardedTrackStitcher:
        s = make()
        spec: dict = {}
        for frame in range(10):  # 50 px/s = 0.25 heights/s to the right
            spec.setdefault(frame, []).append(at(300 + 5 * frame, 1))
        stand(spec, 2, new_x, 20, 30)
        run(s, spec, 30)
        return s

    behind = scenario(330)
    assert behind.stitch_count == 0
    assert "direction_inconsistent" in reasons(behind)
    assert scenario(400).stitch_count == 1


def test_duplicate_of_visible_person_rejected() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 9, 540, 0, 30)  # known, visible throughout
    stand(spec, 2, 520, 20, 30)  # overlaps person 9 with IoU > 0.5
    run(s, spec, 30)
    assert s.stitch_count == 0
    assert "duplicate_of_visible_person" in reasons(s)


def test_chain_limit_rejects_further_links() -> None:
    s = make(max_links_per_person=1)
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 510, 20, 30)
    stand(spec, 3, 520, 40, 50)
    keys = run(s, spec, 50)
    assert s.stitch_count == 1
    assert keys[45][3] != keys[0][1]
    assert "chain_limit" in reasons(s)


def test_distance_too_far_rejected() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 200, 0, 10)
    stand(spec, 2, 700, 20, 30)
    run(s, spec, 30)
    assert s.stitch_count == 0
    assert "distance_too_far" in reasons(s)


def test_disabled_stitching_never_links() -> None:
    s = make(enabled=False)
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 505, 20, 30)
    run(s, spec, 30)
    assert s.stitch_count == 0


def test_decision_dict_has_exactly_the_specified_keys() -> None:
    s = make()
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 505, 20, 30)
    run(s, spec, 30)
    assert s.decisions
    for d in s.decisions:
        assert set(d.to_dict()) == DICT_KEYS
        assert d.decision in ("merged", "rejected")


def test_decisions_kept_only_with_diagnostics_but_summary_always_counts() -> None:
    s = GuardedTrackStitcher(QueueZone(ZONE), StitchingSettings(algorithm="guarded"))
    spec: dict = {}
    stand(spec, 1, 500, 0, 10)
    stand(spec, 2, 505, 20, 30, height=400)
    run(s, spec, 30)
    assert s.decisions == []
    assert s.summary()["rejections_by_reason"]["size_mismatch"] >= 1


def test_analyzer_picks_algorithm_and_reports_summary() -> None:
    camera = CameraConfig("c", ZONE, stitching=StitchingSettings(algorithm="guarded"))
    analyzer = QueueAnalyzer(camera)
    assert isinstance(analyzer.stitcher, GuardedTrackStitcher)
    report = analyzer.report(1.0)["stitching"]
    assert report["algorithm"] == "guarded"
    assert {"merges", "rejections_by_reason", "revived_tracks"} <= set(report)
    assert QueueAnalyzer(CameraConfig("c", ZONE)).report(1.0)["stitching"]["algorithm"] == "legacy"


def test_lifecycle_detaches_track_whose_person_key_changes() -> None:
    lifecycle = QueueLifecycle(None, 2.0)
    person = [at(500, 5)]
    lifecycle.update(0.0, 0, person, {5: "p0002"}, [], SIZE, {5: True})
    lifecycle.update(0.1, 1, person, {5: "p0001"}, [], SIZE, {5: True})
    assert 5 not in lifecycle._persons["p0002"].track_ids
    assert 5 in lifecycle._persons["p0001"].track_ids
    for frame in range(2, 40):  # the provisional person simply expires
        lifecycle.update(frame / FPS, frame, person, {5: "p0001"}, [], SIZE, {5: True})
    assert "p0002" not in lifecycle._persons


def test_provisional_key_change_end_to_end_keeps_one_person() -> None:
    camera = CameraConfig(
        "c",
        ZONE,
        membership=MembershipSettings(algorithm="temporal", min_dwell_seconds=2.0),
        stitching=StitchingSettings(algorithm="guarded"),
    )
    analyzer = QueueAnalyzer(camera)
    counts = []
    for frame in range(120):
        people = []
        if frame < 40:
            people = [at(500, 1)]
        elif frame >= 41:
            people = [at(505, 2)]  # new track while track 1 is lost: provisional key until frame 50
        state = analyzer.update(frame / FPS, frame, people, SIZE)
        counts.append(state.raw_count)
    assert max(counts) == 1
    assert counts[-1] == 1
    assert analyzer.stitcher.stitch_count == 1
    assert analyzer.membership.state_counts["candidate"] == 0


# ------------------------------------------------------------- SERVICE counting

SERVICE_ZONE = ((0.6, 0.1), (0.95, 0.1), (0.95, 0.95), (0.6, 0.95))


def classifier(count_service: bool) -> TemporalMembershipClassifier:
    return TemporalMembershipClassifier(
        QueueZone(ZONE),
        MembershipSettings(
            algorithm="temporal",
            min_dwell_seconds=3.0,
            exit_grace_seconds=1.5,
            count_service_as_queue=count_service,
        ),
        QueueZone(SERVICE_ZONE),
    )


def test_unqualified_candidate_in_service_zone_is_never_counted() -> None:
    c = classifier(True)
    for frame in range(10):
        c.update(frame / FPS, frame, [at(800, 1)], SIZE)
    assert c.states[1].value == "service"
    assert c.queue_count == 0


@pytest.mark.parametrize("count_service,expected", [(True, 1), (False, 0)])
def test_joined_person_in_service_zone_counts_only_if_configured(count_service, expected) -> None:
    c = classifier(count_service)
    for frame in range(40):
        c.update(frame / FPS, frame, [at(300, 1)], SIZE)
    assert c.queue_count == 1
    for frame in range(40, 50):
        c.update(frame / FPS, frame, [at(800, 1)], SIZE)
    assert c.states[1].value == "service"
    assert c.queue_count == expected


# ------------------------------------------------------------ stitch evaluation


def boxes(entries):
    fb = FrameBoxes()
    for track_id, box in entries:
        fb.ids.append(track_id)
        fb.boxes.append(box)
    return fb


def test_dominant_identity_and_merge_correctness_on_tiny_gt() -> None:
    a = (100.0, 100.0, 40.0, 100.0)
    b = (400.0, 100.0, 40.0, 100.0)
    frames = list(range(1, 21))
    gt = GroundTruth({f: boxes([(7, a), (8, b)]) for f in frames}, {})
    tracks = {}
    for f in frames:
        entries = [(1, a) if f <= 10 else (2, a), (3, b)]  # GT 7: track 1 then 2; GT 8: track 3
        if f > 15:
            entries.append((4, (700.0, 100.0, 40.0, 100.0)))  # matches nobody
        tracks[f] = boxes(entries)
    identities = dominant_identities(gt, tracks, frames)
    assert identities == {1: 7, 2: 7, 3: 8, 4: None}
    links = [Link(1, 2, "p1", 10), Link(2, 3, "p1", 11), Link(3, 4, "p1", 16)]
    verdict = judge_links(links, identities)
    assert (verdict["correct"], verdict["incorrect"], verdict["unverifiable"]) == (1, 1, 1)
    assert verdict["merge_precision"] == 0.5
    assert judge_links([], identities)["merge_precision"] is None


def test_rewrite_ids_uses_frame_keys_and_final_keys() -> None:
    box = (0.0, 0.0, 10.0, 10.0)
    tracks = {1: boxes([(5, box)]), 2: boxes([(5, box)])}
    frame_keys = {0: {5: "p0003"}, 1: {5: "p0001"}}  # provisional key, then linked
    assert [rewrite_ids(tracks, frame_keys)[f].ids for f in (1, 2)] == [[3], [1]]
    assert [rewrite_ids(tracks, final_keys(frame_keys))[f].ids for f in (1, 2)] == [[1], [1]]
    assert rewrite_ids(tracks, None)[1].ids == [5]
