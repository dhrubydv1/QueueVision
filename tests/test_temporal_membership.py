"""Tests for the temporal membership classifier using synthetic detections."""

from __future__ import annotations

from dataclasses import replace

import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.queue import (
    CameraConfig,
    CameraConfigError,
    MembershipSettings,
    MemberStatus,
    QueueAnalyzer,
    QueueMembershipTracker,
    QueueState,
    QueueZone,
    StitchingSettings,
    TemporalMembershipClassifier,
)
from ai_engine.queue.event_schema import QueueJoinEvent, ServiceStartEvent, ZoneEvent

FPS = 10.0
SIZE = (1000, 1000)
ZONE = ((0.2, 0.4), (0.8, 0.4), (0.8, 0.9), (0.2, 0.9))
SERVICE_ZONE = ((0.6, 0.4), (0.8, 0.4), (0.8, 0.9), (0.6, 0.9))
BASE = MembershipSettings(
    algorithm="temporal", min_dwell_seconds=3.0, max_speed_heights_per_second=0.5, exit_grace_seconds=1.5
)


def at(x: float, y: float = 700, track_id: int = 1, height: int = 200, conf: float = 0.9) -> PersonDetection:
    return PersonDetection(
        bbox=(int(x - 50), int(y - height), int(x + 50), int(y)), confidence=conf, track_id=track_id
    )


def classifier(settings: MembershipSettings = BASE, service: bool = False) -> TemporalMembershipClassifier:
    return TemporalMembershipClassifier(
        QueueZone(ZONE), settings, QueueZone(SERVICE_ZONE) if service else None
    )


def run(c, frames, keys=None):
    """Feed frames at FPS; return (events with frame, per-frame (states, count))."""
    events, trace = [], []
    for i, people in enumerate(frames):
        person_keys = keys(i) if keys else None
        for event in c.update(i / FPS, i, people, SIZE, person_keys=person_keys):
            events.append((i, event))
        trace.append((c.states, c.queue_count))
    return events, trace


def stand(x: float, start: int, stop: int, **kw) -> dict[int, list[PersonDetection]]:
    return {i: [at(x, **kw)] for i in range(start, stop)}


def frames_of(spec: dict[int, list[PersonDetection]], end: int) -> list[list[PersonDetection]]:
    return [spec.get(i, []) for i in range(end)]


def test_entering_roi_is_candidate_and_not_counted_before_dwell() -> None:
    c = classifier()
    _, trace = run(c, frames_of(stand(400, 0, 60), 60))
    assert trace[0][0][1] is QueueState.CANDIDATE
    assert all(count == 0 for _, count in trace[:30])
    assert trace[29][0][1] is QueueState.CANDIDATE
    assert trace[30][0][1] is QueueState.WAITING and trace[30][1] == 1


def test_passer_by_is_never_waiting_or_counted() -> None:
    walk = {i: [at(100 + i * 15)] for i in range(0, 54)}  # 150 px/s = 0.75 box heights/s
    events, trace = run(classifier(), frames_of(walk, 70))
    assert events == []
    assert all(count == 0 for _, count in trace)
    assert all(QueueState.WAITING not in states.values() for states, _ in trace)


def test_waiting_person_is_counted_and_enter_event_uses_entry_time() -> None:
    events, trace = run(classifier(), frames_of(stand(400, 0, 60), 60))
    enters = [e for _, e in events if e.event_type == "zone_enter"]
    assert len(enters) == 1
    assert enters[0].zone_entered_s == 0.0 and enters[0].timestamp_s == pytest.approx(3.0)
    assert trace[-1][1] == 1


def test_brief_occlusion_keeps_counting_and_recovers() -> None:
    spec = {**stand(400, 0, 40), **stand(400, 50, 80)}
    events, trace = run(classifier(), frames_of(spec, 80))
    assert trace[45][0] == {}  # nobody visible
    c = classifier()
    run(c, frames_of(spec, 45))
    assert c.state_counts["temporarily_occluded"] == 1 and c.queue_count == 1
    assert trace[50][0][1] is QueueState.WAITING and trace[50][1] == 1
    assert all(e.event_type != "zone_exit" for _, e in events)


def test_long_occlusion_abandons_and_emits_exit_with_last_seen_time() -> None:
    spec = stand(400, 0, 41)  # last detection at t = 4.0
    c = classifier()
    events, trace = run(c, frames_of(spec, 70))
    exits = [(i, e) for i, e in events if e.event_type == "zone_exit"]
    assert len(exits) == 1
    frame, exit_event = exits[0]
    assert exit_event.timestamp_s == pytest.approx(4.0)
    assert frame == 56  # first frame where the gap exceeds the 1.5 s grace
    assert trace[55][1] == 1 and trace[56][1] == 0
    assert c.state_counts["abandoned"] == 1


def test_leaving_queue_is_abandoned_only_after_grace() -> None:
    # Stands 4 s, then walks right out of the zone (x > 800) at 400 px/s.
    spec = {**stand(400, 0, 40), **{i: [at(400 + (i - 39) * 40)] for i in range(40, 80)}}
    c = classifier()
    _, trace = run(c, frames_of(spec, 80))
    left_zone = next(i for i in range(40, 80) if 400 + (i - 39) * 40 > 800)  # first frame outside
    assert trace[left_zone][1] == 1 and trace[left_zone][0][1] is QueueState.WAITING
    abandoned_at = next(i for i, (s, _) in enumerate(trace) if s.get(1) is QueueState.ABANDONED)
    assert abandoned_at >= left_zone + 10  # not immediately: the 1.5 s grace must pass first
    assert trace[abandoned_at][1] == 0
    assert c.statuses[1] is MemberStatus.LEFT


def test_service_state_counted_only_when_configured() -> None:
    spec = {**stand(400, 0, 40), **stand(700, 40, 60)}
    for counted in (True, False):
        c = classifier(replace(BASE, count_service_as_queue=counted), service=True)
        _, trace = run(c, frames_of(spec, 60))
        assert trace[-1][0][1] is QueueState.SERVICE
        assert trace[-1][1] == (1 if counted else 0)
        assert c.statuses[1] is MemberStatus.IN_QUEUE


def test_service_to_exited_emits_exit() -> None:
    spec = {**stand(400, 0, 40), **stand(700, 40, 60)}
    c = classifier(service=True)
    events, _ = run(c, frames_of(spec, 90))
    assert c.state_counts["exited"] == 1 and c.state_counts["abandoned"] == 0
    assert [e.event_type for _, e in events] == ["zone_enter", "zone_exit"]


def test_abandoning_never_passes_through_service() -> None:
    spec = {**stand(400, 0, 40), **{i: [at(400 - (i - 39) * 20)] for i in range(40, 70)}}  # walks left, out
    c = classifier(service=True)
    events, trace = run(c, frames_of(spec, 100))
    assert all(QueueState.SERVICE not in s.values() for s, _ in trace)
    assert c.state_counts["abandoned"] == 1 and c.queue_count == 0
    assert [e.event_type for _, e in events] == ["zone_enter", "zone_exit"]


def test_candidate_reaching_service_without_qualifying_is_service_without_join() -> None:
    c = classifier(service=True)
    events, trace = run(c, frames_of(stand(700, 0, 10), 10))
    assert trace[-1][0][1] is QueueState.SERVICE
    assert events == []  # no join; the lifecycle counts it as a service visit without join


def test_duplicate_track_ids_for_one_person_count_once_without_new_dwell() -> None:
    spec = {i: [at(400, track_id=1)] for i in range(0, 45)}
    spec.update({i: [at(400, track_id=2)] for i in range(40, 80)})  # overlap frames 40-44
    keys = lambda i: {1: "p1", 2: "p1"}  # noqa: E731
    c = classifier()
    events, trace = run(c, frames_of(spec, 80), keys)
    assert all(count <= 1 for _, count in trace)
    assert sum(1 for _, e in events if e.event_type == "zone_enter") == 1
    assert trace[50][0][2] is QueueState.WAITING and trace[50][1] == 1  # no re-dwell for track 2
    assert c.tracked_state_count == 1
    # Without person keys the new ID is a separate person that must dwell again.
    c2 = classifier()
    _, trace2 = run(c2, frames_of(spec, 80))
    assert trace2[50][0][2] is QueueState.CANDIDATE


def test_terminal_entries_expire_and_new_track_never_inherits() -> None:
    c = classifier()
    spec = {**stand(400, 0, 41), **stand(410, 100, 110, track_id=2)}
    _, trace = run(c, frames_of(spec, 110))
    assert c.state_counts["abandoned"] == 0  # old person forgotten after retention (5 s)
    assert trace[99][0] == {} and c.tracked_state_count == 1
    assert trace[100][0][2] is QueueState.CANDIDATE and trace[100][1] == 0
    assert trace[109][0][2] is QueueState.CANDIDATE


def test_boundary_margin_requires_depth_to_enter_and_hysteresis_to_leave() -> None:
    settings = replace(BASE, boundary_margin_heights=0.25)  # 50 px at box height 200
    # 20 px inside the left edge (x = 200): not deep enough to become a candidate.
    c = classifier(settings)
    _, trace = run(c, frames_of(stand(220, 0, 60), 60))
    assert all(s.get(1) is QueueState.OUTSIDE for s, _ in trace) and trace[-1][1] == 0
    # Waits at x=300, drifts 30 px outside the edge (within margin): still WAITING; 80 px outside: leaves.
    spec = {**stand(300, 0, 40), **stand(170, 40, 100), **stand(120, 100, 130)}
    c = classifier(settings)
    _, trace = run(c, frames_of(spec, 140))
    assert trace[99][0][1] is QueueState.WAITING and trace[99][1] == 1
    assert trace[-1][0].get(1) is not QueueState.WAITING
    assert c.state_counts["abandoned"] == 1
    # Without margin the same drift ends membership after grace.
    c = classifier()
    _, trace = run(c, frames_of(spec, 100))
    assert trace[99][1] == 0


def test_min_observed_fraction_rejects_flickering_track() -> None:
    flicker = {i: [at(400)] for i in range(0, 60, 2)}  # detected in half the frames
    for fraction, expected in ((0.6, False), (0.4, True)):
        c = classifier(replace(BASE, min_observed_fraction=fraction))
        _, trace = run(c, frames_of(flicker, 60))
        assert (trace[-1][1] == 1) is expected


def test_min_track_observations_counts_detections_in_window() -> None:
    flicker = {i: [at(400)] for i in range(0, 60, 2)}  # about 16 detections per 3 s window
    c = classifier(replace(BASE, min_track_observations=20))
    _, trace = run(c, frames_of(flicker, 60))
    assert trace[-1][1] == 0
    c = classifier(replace(BASE, min_track_observations=10))
    _, trace = run(c, frames_of(flicker, 60))
    assert trace[-1][1] == 1


def test_min_mean_detection_confidence() -> None:
    for conf, expected in ((0.2, False), (0.5, True)):
        c = classifier(replace(BASE, min_mean_detection_confidence=0.35))
        _, trace = run(c, frames_of(stand(400, 0, 60, conf=conf), 60))
        assert (trace[-1][1] == 1) is expected


def test_scale_motion_weight_rejects_person_walking_toward_camera() -> None:
    # Foot point fixed, box grows from 100 px to 400 px in 4.5 s.
    growing = {i: [at(500, height=100 + round(i * 300 / 45))] for i in range(0, 46)}
    c = classifier()
    _, trace = run(c, frames_of(growing, 46))
    assert trace[-1][1] == 1  # lateral-only speed is 0, so the legacy rule accepts them
    c = classifier(replace(BASE, scale_motion_weight=1.0))
    _, trace = run(c, frames_of(growing, 46))
    assert trace[-1][1] == 0 and trace[-1][0][1] is QueueState.CANDIDATE


def test_confidence_is_a_bounded_heuristic() -> None:
    c = classifier()
    run(c, frames_of(stand(400, 0, 15), 15))
    assert 0.0 < c.confidences[1] < 1.0  # candidate: dwell 1.4 / 3 s
    c = classifier()
    run(c, frames_of(stand(400, 0, 60), 60))
    assert c.confidences[1] == pytest.approx(1.0)


def test_unkeyed_tracks_use_track_id_and_untracked_people_are_ignored() -> None:
    c = classifier()
    untracked = PersonDetection((350, 500, 450, 700), 0.9, None)
    c.update(0.0, 0, [at(400), untracked], SIZE)
    assert c.tracked_state_count == 1 and c.states == {1: QueueState.CANDIDATE}


# ------------------------------------------------------------------ configuration


@pytest.mark.parametrize(
    "field, value",
    [
        ("algorithm", "magic"),
        ("min_observed_fraction", 1.5),
        ("min_observed_fraction", -0.1),
        ("min_track_observations", 0),
        ("min_track_observations", 2.5),
        ("boundary_margin_heights", -1),
        ("min_mean_detection_confidence", 2),
        ("scale_motion_weight", -0.5),
        ("count_service_as_queue", "yes"),
        ("terminal_retention_seconds", 0),
    ],
)
def test_membership_validation_errors(field: str, value) -> None:
    with pytest.raises(CameraConfigError, match=field):
        CameraConfig(camera_id="c", queue_zone=ZONE, membership=MembershipSettings(**{field: value}))


def test_legacy_is_default_and_selected_by_analyzer() -> None:
    assert MembershipSettings().algorithm == "legacy"
    analyzer = QueueAnalyzer(CameraConfig(camera_id="c", queue_zone=ZONE))
    assert isinstance(analyzer.membership, QueueMembershipTracker)
    state = analyzer.update(0.0, 0, [at(400)], SIZE)
    assert state.membership_states == {} and state.state_counts == {}
    assert analyzer.report(1.0)["membership"]["algorithm"] == "legacy"


def test_analyzer_with_temporal_produces_lifecycle_events_and_state_fields() -> None:
    camera = CameraConfig(
        camera_id="c",
        queue_zone=ZONE,
        service_zone=SERVICE_ZONE,
        membership=BASE,
        stitching=StitchingSettings(enabled=True, max_gap_seconds=2.0, max_distance_heights=1.0),
    )
    analyzer = QueueAnalyzer(camera)
    assert isinstance(analyzer.membership, TemporalMembershipClassifier)
    # Waits at x=400 (track 1 -> track 2 ID switch after a 0.5 s gap), then steps into the service zone.
    spec = {
        **stand(400, 0, 40),
        **{i: [at(400, track_id=2)] for i in range(45, 60)},
        **{i: [at(700, track_id=2)] for i in range(60, 80)},
    }
    events, states = [], []
    for i in range(80):
        state = analyzer.update(i / FPS, i, spec.get(i, []), SIZE)
        events += list(state.events)
        states.append(state)
    assert sum(isinstance(e, QueueJoinEvent) for e in events) == 1
    assert sum(isinstance(e, ServiceStartEvent) for e in events) == 1
    assert sum(isinstance(e, ZoneEvent) and e.event_type == "zone_enter" for e in events) == 1
    assert states[50].membership_states == {2: "waiting"}
    assert states[50].state_counts["waiting"] == 1 and states[50].raw_count == 1
    assert states[79].membership_states == {2: "service"}
    assert 0.0 <= states[79].membership_confidence[2] <= 1.0
    membership = analyzer.report(8.0)["membership"]
    assert membership["algorithm"] == "temporal"
    assert membership["state_counts_at_end"]["service"] == 1
    assert membership["settings"]["min_dwell_seconds"] == 3.0
