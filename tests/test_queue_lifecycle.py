"""Lifecycle tests: join, service, abandonment, driven through QueueAnalyzer with synthetic tracks."""

from __future__ import annotations

import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.queue import (
    CameraConfig,
    MembershipSettings,
    QueueAbandonEvent,
    QueueAnalyzer,
    QueueJoinEvent,
    ServiceStartEvent,
    StitchingSettings,
)

FPS = 10.0
SIZE = (1000, 1000)
ZONE = ((0.2, 0.4), (0.8, 0.4), (0.8, 0.9), (0.2, 0.9))
LINE = ((0.2, 0.85), (0.8, 0.85))


def person(x: float, y: float, track_id: int = 1) -> PersonDetection:
    return PersonDetection(
        bbox=(int(x - 50), int(y - 200), int(x + 50), int(y)), confidence=0.9, track_id=track_id
    )


def make_analyzer(service: bool = True, stitching: bool | None = None) -> QueueAnalyzer:
    camera = CameraConfig(
        camera_id="cam",
        queue_zone=ZONE,
        service_line=LINE if service else None,
        membership=MembershipSettings(
            min_dwell_seconds=3.0, max_speed_heights_per_second=0.5, exit_grace_seconds=1.5
        ),
        stitching=StitchingSettings(enabled=True, max_gap_seconds=2.0, max_distance_heights=1.0),
    )
    return QueueAnalyzer(camera, stitching_enabled=stitching)


def feed(analyzer: QueueAnalyzer, frames: dict[int, list[PersonDetection]], end: int) -> list:
    """Feed frames 0..end-1 (missing frames are empty); return all events with their frame."""
    events = []
    for index in range(end):
        state = analyzer.update(index / FPS, index, frames.get(index, []), SIZE)
        events += [(index, e) for e in state.events]
    return events


def of_type(events: list, kind: type) -> list:
    return [e for _, e in events if isinstance(e, kind)]


def test_join_timestamp_is_zone_entry_and_detected_at_confirmation() -> None:
    analyzer = make_analyzer(service=False)
    events = feed(analyzer, {i: [person(500, 700)] for i in range(10, 60)}, 60)
    (join,) = of_type(events, QueueJoinEvent)
    assert join.timestamp_s == pytest.approx(1.0) and join.frame_index == 10
    assert join.detected_s == pytest.approx(4.0)
    assert join.person_key == "p0001"


def test_service_start_wait_is_service_minus_join() -> None:
    analyzer = make_analyzer()
    frames = {i: [person(500, 700)] for i in range(0, 60)}
    frames.update(
        {i: [person(500, 700 + (i - 60) * 20)] for i in range(60, 80)}
    )  # walks over the line (y=850)
    events = feed(analyzer, frames, 80)
    (start,) = of_type(events, ServiceStartEvent)
    assert start.join_s == pytest.approx(0.0)
    assert start.timestamp_s == pytest.approx(start.join_s + start.wait_s)
    assert start.timestamp_s > 6.0 and start.track_ids == (1,)
    assert not of_type(events, QueueAbandonEvent)
    assert analyzer.wait_log.completed[0].wait_s == pytest.approx(start.wait_s)
    assert analyzer.lifecycle.waiting_count == 0


def test_walker_who_never_confirms_does_not_join() -> None:
    analyzer = make_analyzer()
    frames = {i: [person(250 + i * 20, 700)] for i in range(0, 25)}  # crosses the zone in 2.5 s
    events = feed(analyzer, frames, 80)
    assert events == []
    assert analyzer.lifecycle.waiting_count == 0


def test_abandon_only_after_threshold_with_last_in_zone_timestamp() -> None:
    analyzer = make_analyzer()  # abandon_after = max(1.5, 2.0) = 2.0
    frames = {i: [person(500, 700)] for i in range(0, 50)}  # last seen in zone at 4.9 s
    events = feed(analyzer, frames, 80)
    (abandon,) = of_type(events, QueueAbandonEvent)
    frame = next(i for i, e in events if isinstance(e, QueueAbandonEvent))
    assert abandon.timestamp_s == pytest.approx(4.9)
    assert abandon.detected_s == pytest.approx(6.9)
    assert frame == 69
    assert abandon.join_s == pytest.approx(0.0)
    assert abandon.time_in_queue_s == pytest.approx(4.9)
    assert analyzer.lifecycle.waiting_count == 0


def test_stitched_person_has_single_join_and_no_abandon() -> None:
    analyzer = make_analyzer()
    frames = {i: [person(500, 700, 1)] for i in range(0, 50)}
    frames.update({i: [person(510, 700, 2)] for i in range(55, 120)})  # 0.6 s gap, new ID
    events = feed(analyzer, frames, 120)
    (join,) = of_type(events, QueueJoinEvent)
    assert join.timestamp_s == pytest.approx(0.0)
    assert not of_type(events, QueueAbandonEvent)
    assert analyzer.stitcher.stitch_count == 1
    assert analyzer.lifecycle.waiting_count == 1


def test_without_stitching_the_id_switch_is_an_abandon_and_a_rejoin() -> None:
    analyzer = make_analyzer(stitching=False)
    frames = {i: [person(500, 700, 1)] for i in range(0, 50)}
    frames.update({i: [person(510, 700, 2)] for i in range(55, 120)})
    events = feed(analyzer, frames, 120)
    assert len(of_type(events, QueueJoinEvent)) == 2
    assert len(of_type(events, QueueAbandonEvent)) == 1


def test_served_person_leaving_is_not_an_abandon() -> None:
    analyzer = make_analyzer()
    frames = {i: [person(500, 700)] for i in range(0, 50)}
    frames.update({i: [person(500, 700 + (i - 50) * 20)] for i in range(50, 60)})  # crosses y=850 at frame 58
    events = feed(analyzer, frames, 150)
    assert len(of_type(events, ServiceStartEvent)) == 1
    assert not of_type(events, QueueAbandonEvent)


def test_nothing_emitted_at_end_and_pending_is_counted() -> None:
    analyzer = make_analyzer()
    frames = {i: [person(500, 700)] for i in range(0, 50)}
    events = feed(analyzer, frames, 66)  # gone 1.6 s: zone_exit seen, abandon not yet final
    assert of_type(events, QueueAbandonEvent) == []
    assert analyzer.lifecycle.waiting_count == 1
    assert analyzer.lifecycle.pending_abandon_count == 1
    assert analyzer.report(6.0)["pending_abandon_at_end"] == 1


def test_unjoined_person_at_service_point_is_counted_not_reported() -> None:
    analyzer = make_analyzer()
    frames = {i: [person(500, 700 + i * 20)] for i in range(0, 10)}  # fast walker crosses y=850
    events = feed(analyzer, frames, 20)
    assert events == []
    assert analyzer.lifecycle.service_visits_without_join == 1


def test_no_service_point_means_no_service_or_abandon_events() -> None:
    analyzer = make_analyzer(service=False)
    events = feed(analyzer, {i: [person(500, 700)] for i in range(0, 50)}, 120)
    assert len(of_type(events, QueueJoinEvent)) == 1
    assert of_type(events, QueueAbandonEvent) == []
    assert analyzer.report(12.0)["waiting"] == {"note": "no service point configured"}
