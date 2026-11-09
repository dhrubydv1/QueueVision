"""Synthetic tests for service_complete / queue_exit events and the service-rate basis.

All tracks here are hand-built; nothing is derived from real video.
"""

from __future__ import annotations

import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.queue import (
    CameraConfig,
    CameraConfigError,
    MembershipSettings,
    QueueAnalyzer,
    QueueExitEvent,
    ServiceCompleteEvent,
    ServiceRate,
    ServiceRateEstimator,
    ServiceRateSettings,
    ServiceSettings,
    ServiceStartEvent,
    WaitLog,
    predict_wait,
)
from ai_engine.queue.event_schema import QueueAbandonEvent
from ai_engine.queue.waiting_time import ESTIMATOR_VERSION

FPS = 10.0
SIZE = (1000, 1000)
QUEUE_ZONE = ((0.2, 0.4), (0.8, 0.4), (0.8, 0.9), (0.2, 0.9))
SERVICE_ZONE = ((0.2, 0.05), (0.8, 0.05), (0.8, 0.35), (0.2, 0.35))
LINE = ((0.2, 0.2), (0.8, 0.2))
Y_QUEUE, Y_SERVICE, Y_OUT = 700, 300, 960


def person(y: float, track_id: int = 1, x: float = 500) -> PersonDetection:
    return PersonDetection(
        bbox=(int(x - 50), int(y - 200), int(x + 50), int(y)), confidence=0.9, track_id=track_id
    )


def make(point: str | None = "zone", **kwargs) -> QueueAnalyzer:
    camera = CameraConfig(
        camera_id="cam",
        queue_zone=QUEUE_ZONE,
        service_zone=SERVICE_ZONE if point == "zone" else None,
        service_line=LINE if point == "line" else None,
        membership=MembershipSettings(
            min_dwell_seconds=3.0, max_speed_heights_per_second=0.5, exit_grace_seconds=1.5
        ),
        **kwargs,
    )
    return QueueAnalyzer(camera, stitching_enabled=False)


def run(analyzer: QueueAnalyzer, segments: list[tuple[int, int, float | None]], end: int) -> list:
    """segments: (first_frame, last_frame_exclusive, foot_y or None for invisible)."""
    out = []
    for frame in range(end):
        people = []
        for start, stop, y in segments:
            if start <= frame < stop and y is not None:
                people.append(person(y))
        state = analyzer.update(frame / FPS, frame, people, SIZE)
        out += list(state.events)
    return out


def kinds(events: list, cls: type) -> list:
    return [e for e in events if isinstance(e, cls)]


def test_service_complete_when_observed_leaving_service_zone() -> None:
    analyzer = make()
    segs = [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE), (80, 130, Y_OUT)]
    events = run(analyzer, segs, 130)
    (start,) = kinds(events, ServiceStartEvent)
    (done,) = kinds(events, ServiceCompleteEvent)
    assert start.timestamp_s == pytest.approx(4.0)
    assert done.timestamp_s == pytest.approx(8.0)  # first observed outside
    assert done.detected_s == pytest.approx(9.0)  # confirmed exit_confirm_seconds later
    assert done.service_start_s == pytest.approx(4.0)
    assert done.service_duration_s == pytest.approx(4.0)
    assert done.person_key == start.person_key and done.frame_index == 80
    assert analyzer.wait_log.services[0].service_duration_s == pytest.approx(4.0)
    assert analyzer.wait_log.service_summary()["service_completed"] == 1


def test_no_service_complete_when_person_vanishes_in_service_zone() -> None:
    analyzer = make()
    events = run(analyzer, [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE)], 200)
    assert len(kinds(events, ServiceStartEvent)) == 1
    assert kinds(events, ServiceCompleteEvent) == []
    (exit_event,) = kinds(events, QueueExitEvent)
    assert exit_event.outcome == "served"


def test_no_service_complete_when_reappearing_outside_after_gap() -> None:
    analyzer = make()
    segs = [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE), (110, 160, Y_OUT)]  # 3 s unobserved gap
    events = run(analyzer, segs, 160)
    assert kinds(events, ServiceCompleteEvent) == []


def test_brief_visit_shorter_than_min_service_is_not_complete() -> None:
    analyzer = make()
    segs = [(0, 40, Y_QUEUE), (40, 50, Y_SERVICE), (50, 120, Y_OUT)]  # 1 s inside < 2 s minimum
    events = run(analyzer, segs, 120)
    assert len(kinds(events, ServiceStartEvent)) == 1
    assert kinds(events, ServiceCompleteEvent) == []


def test_returning_to_zone_before_exit_confirm_cancels_exit() -> None:
    analyzer = make()
    segs = [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE), (80, 85, Y_OUT), (85, 120, Y_SERVICE)]
    events = run(analyzer, segs, 120)
    assert kinds(events, ServiceCompleteEvent) == []


def test_service_complete_settings_are_configurable() -> None:
    analyzer = make(service=ServiceSettings(min_service_seconds=6.0, exit_confirm_seconds=0.5))
    events = run(analyzer, [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE), (80, 130, Y_OUT)], 130)
    assert kinds(events, ServiceCompleteEvent) == []  # 4 s in service < 6 s


def test_service_line_never_emits_service_complete() -> None:
    analyzer = make("line")
    events = run(analyzer, [(0, 40, Y_QUEUE), (40, 60, 150), (60, 130, Y_OUT)], 130)
    assert len(kinds(events, ServiceStartEvent)) == 1
    assert kinds(events, ServiceCompleteEvent) == []
    assert analyzer.report(13.0)["service_completions"] == {"note": "needs a service zone"}


def test_queue_exit_served_abandoned_unknown_outcomes() -> None:
    served = run(make(), [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE), (80, 130, Y_OUT)], 300)
    (exit_served,) = kinds(served, QueueExitEvent)
    assert exit_served.outcome == "served"
    assert exit_served.timestamp_s == pytest.approx(12.9)  # last seen
    assert exit_served.detected_s >= exit_served.timestamp_s + 1.5 - 1e-6

    abandoned = run(make(), [(0, 40, Y_QUEUE)], 200)
    (abandon,) = kinds(abandoned, QueueAbandonEvent)
    (exit_abandoned,) = kinds(abandoned, QueueExitEvent)
    assert exit_abandoned.outcome == "abandoned"
    assert abandon.timestamp_s == pytest.approx(3.9)  # semantics unchanged: last time in the zone
    assert exit_abandoned.timestamp_s == pytest.approx(3.9)

    no_service = run(make(point=None), [(0, 40, Y_QUEUE)], 200)
    assert kinds(no_service, QueueAbandonEvent) == []
    (exit_unknown,) = kinds(no_service, QueueExitEvent)
    assert exit_unknown.outcome == "unknown"


def test_queue_exit_not_emitted_for_people_who_never_joined() -> None:
    events = run(make(), [(0, 10, Y_OUT)], 100)
    assert kinds(events, QueueExitEvent) == []


def test_still_visible_abandoner_exits_only_when_unseen() -> None:
    # Joined, then stands outside the queue zone (visible): abandon fires, queue_exit waits until unseen.
    events = run(make(), [(0, 40, Y_QUEUE), (40, 120, Y_OUT)], 200)
    (abandon,) = kinds(events, QueueAbandonEvent)
    (exit_event,) = kinds(events, QueueExitEvent)
    assert exit_event.timestamp_s == pytest.approx(11.9)
    assert exit_event.detected_s > abandon.detected_s


def test_events_serialise_with_documented_fields() -> None:
    done = ServiceCompleteEvent("p0001", (1,), 8.0, 80, 9.0, 4.0, 4.0).to_dict()
    assert list(done) == [
        "schema_version", "event_type", "person_key", "track_ids", "timestamp_s",
        "frame_index", "detected_s", "service_start_s", "service_duration_s",
    ]  # fmt: skip
    leave = QueueExitEvent("p0001", (1,), 8.0, 80, 9.5, "served").to_dict()
    assert leave["outcome"] == "served" and leave["event_type"] == "queue_exit"


def test_wait_log_ignores_nothing_it_should_keep() -> None:
    log = WaitLog()
    log.add(ServiceCompleteEvent("p1", (1,), 8.0, 80, 9.0, 4.0, 4.0))
    log.add(ServiceCompleteEvent("p2", (2,), 20.0, 200, 21.0, 15.0, 5.0))
    log.add(QueueExitEvent("p2", (2,), 22.0, 220, 23.0, "served"))
    assert log.completed == [] and len(log.services) == 2
    assert log.service_summary()["mean_service_duration_s"] == pytest.approx(4.5)
    assert WaitLog().service_summary()["mean_service_duration_s"] is None


# --- service-rate basis ----------------------------------------------------------


def rate_settings(**kw) -> ServiceRateSettings:
    return ServiceRateSettings(
        method="window", window_seconds=60.0, min_history_seconds=10.0, min_services=2, **kw
    )


def test_basis_validation() -> None:
    with pytest.raises(CameraConfigError, match="basis must be one of"):
        CameraConfig("c", QUEUE_ZONE, service_zone=SERVICE_ZONE, service_rate=rate_settings(basis="bogus"))
    ok_rate = rate_settings(basis="service_complete")
    with pytest.raises(CameraConfigError, match="needs a service_zone"):
        CameraConfig("c", QUEUE_ZONE, service_rate=ok_rate)
    with pytest.raises(CameraConfigError, match="needs a service_zone"):
        CameraConfig("c", QUEUE_ZONE, service_line=LINE, service_rate=ok_rate)
    CameraConfig(
        "c", QUEUE_ZONE, service_zone=SERVICE_ZONE, service_rate=rate_settings(basis="service_complete")
    )
    with pytest.raises(CameraConfigError, match="min_service_seconds"):
        CameraConfig("c", QUEUE_ZONE, service=ServiceSettings(min_service_seconds=-1))


def test_default_basis_and_report_fields() -> None:
    analyzer = make()
    run(analyzer, [(0, 40, Y_QUEUE)], 50)
    report = analyzer.report(5.0)["service_rate"]
    assert report["basis"] == report["rate_basis"] == "service_start"
    assert report["estimator"] == ESTIMATOR_VERSION == "queue_length_over_service_rate/v1"


def serve_one(frames_in_service: int) -> list[tuple[int, int, float | None]]:
    return [(0, 40, Y_QUEUE), (40, 40 + frames_in_service, Y_SERVICE), (40 + frames_in_service, 140, Y_OUT)]


def test_service_complete_basis_counts_only_completions() -> None:
    camera_kwargs = {"service_rate": rate_settings(basis="service_complete")}
    complete = make(**camera_kwargs)
    events = run(complete, serve_one(40), 140)
    assert len(kinds(events, ServiceCompleteEvent)) == 1
    assert complete._rate_estimator is not None and complete._rate_estimator._total == 1

    vanished = make(**camera_kwargs)  # served but never observed leaving: nothing counted
    run(vanished, [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE)], 140)
    assert vanished._rate_estimator is not None and vanished._rate_estimator._total == 0

    started = make()  # default basis counts the service start
    run(started, [(0, 40, Y_QUEUE), (40, 80, Y_SERVICE)], 140)
    assert started._rate_estimator is not None and started._rate_estimator._total == 1


# --- prediction is unknown, never zero --------------------------------------------


def test_prediction_unknown_when_no_history() -> None:
    estimator = ServiceRateEstimator(rate_settings(), start_s=0.0)
    for now in (0.0, 5.0):  # before min_history_seconds
        rate = estimator.estimate(now)
        assert rate.status == "insufficient_data" and predict_wait(5, rate).status == "unknown"
        assert predict_wait(5, rate).predicted_wait_s is None


def test_prediction_unknown_when_window_has_too_few_events() -> None:
    estimator = ServiceRateEstimator(rate_settings(), start_s=0.0)
    estimator.add_service(20.0)  # one event, min_services is 2
    rate = estimator.estimate(30.0)
    assert rate.status == "insufficient_data"
    assert predict_wait(5, rate).predicted_wait_s is None


def test_prediction_unknown_with_history_but_zero_completions_in_window() -> None:
    estimator = ServiceRateEstimator(rate_settings(), start_s=0.0)
    estimator.add_service(15.0)
    estimator.add_service(20.0)
    assert estimator.estimate(30.0).status == "ok"
    late = estimator.estimate(200.0)  # both events fell out of the 60 s window
    assert late.status == "insufficient_data" and late.per_min is None
    prediction = predict_wait(5, late)
    assert prediction.status == "unknown" and prediction.predicted_wait_s is None


def test_prediction_unknown_when_rate_is_zero_or_missing() -> None:
    for rate in (ServiceRate(0.0, "ok"), ServiceRate(None, "ok")):
        prediction = predict_wait(3, rate)
        assert prediction.status == "unknown" and prediction.predicted_wait_s is None


def test_prediction_carries_estimator_version() -> None:
    prediction = predict_wait(6, ServiceRate(3.0, "ok", 2.0, 4.0))
    assert prediction.predicted_wait_s == pytest.approx(120.0)
    assert prediction.estimator == ESTIMATOR_VERSION
