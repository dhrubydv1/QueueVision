"""Congestion monitor: duration, hysteresis, cooldown, reasons."""

from __future__ import annotations

import pytest

from ai_engine.queue import AlertSettings, CongestionMonitor
from ai_engine.queue.waiting_time import UNKNOWN_PREDICTION, WaitPrediction


def drive(monitor: CongestionMonitor, lengths: dict[int, float], seconds: int, prediction=UNKNOWN_PREDICTION):
    """One update per second; ``lengths`` maps second -> queue length (held until changed)."""
    events, current = [], 0.0
    for second in range(seconds):
        current = lengths.get(second, current)
        events += monitor.update(float(second), second * 10, current, prediction)
    return events


def settings(**overrides) -> AlertSettings:
    values = {"max_queue_length": 5, "min_duration_seconds": 10.0, "cooldown_seconds": 60.0}
    values.update(overrides)
    return AlertSettings(**values)


def test_short_spike_does_not_alert() -> None:
    monitor = CongestionMonitor(settings())
    assert drive(monitor, {5: 9.0, 12: 1.0}, 60) == []  # high for 7 s only
    assert not monitor.active


def test_sustained_condition_starts_and_ends_with_hysteresis() -> None:
    monitor = CongestionMonitor(settings())
    events = drive(monitor, {5: 9.0, 40: 1.0}, 80)
    start, end = events
    assert start.event_type == "congestion_start"
    assert start.condition_since_s == 5.0 and start.timestamp_s == 15.0 and start.detected_s == 15.0
    assert start.reasons == ("queue_length",) and start.queue_length == 9.0
    assert end.event_type == "congestion_end"
    assert end.condition_since_s == 40.0 and end.timestamp_s == 50.0
    assert not monitor.active


def test_cooldown_blocks_restart_until_it_passes() -> None:
    monitor = CongestionMonitor(settings(cooldown_seconds=60.0))
    events = drive(monitor, {0: 9.0, 20: 1.0, 40: 9.0}, 200)
    kinds = [(e.event_type, e.timestamp_s) for e in events]
    # end at 30 s; the condition returns at 40 s but a new alert waits for the cooldown (until 90 s).
    assert kinds == [("congestion_start", 10.0), ("congestion_end", 30.0), ("congestion_start", 90.0)]
    assert events[2].condition_since_s == 40.0


def test_flicker_resets_the_clock() -> None:
    monitor = CongestionMonitor(settings())
    assert drive(monitor, {0: 9.0, 6: 1.0, 7: 9.0}, 16) == []  # 6 s, dip, then 8 s < 10 s
    assert drive(CongestionMonitor(settings()), {0: 9.0, 6: 1.0, 7: 9.0}, 18)[0].timestamp_s == 17.0


def test_predicted_wait_reason() -> None:
    monitor = CongestionMonitor(settings(max_queue_length=None, max_predicted_wait_seconds=100.0))
    prediction = WaitPrediction(150.0, 120.0, 200.0, "ok")
    events = drive(monitor, {}, 20, prediction)
    (start,) = events
    assert start.reasons == ("predicted_wait",) and start.predicted_wait_s == 150.0
    assert monitor.active_reasons == ("predicted_wait",)
    # An unknown prediction never triggers the wait condition.
    quiet = CongestionMonitor(settings(max_queue_length=None, max_predicted_wait_seconds=100.0))
    assert drive(quiet, {}, 30, UNKNOWN_PREDICTION) == []


def test_both_reasons_and_disabled() -> None:
    monitor = CongestionMonitor(settings(max_predicted_wait_seconds=100.0))
    (start,) = drive(monitor, {0: 9.0}, 12, WaitPrediction(150.0, None, None, "ok"))
    assert start.reasons == ("queue_length", "predicted_wait")
    disabled = CongestionMonitor(AlertSettings())
    assert drive(disabled, {0: 99.0}, 60) == []
    assert disabled.active is False and disabled.active_reasons == ()


def test_no_event_for_an_alert_still_active_at_the_end() -> None:
    monitor = CongestionMonitor(settings())
    events = drive(monitor, {0: 9.0}, 30)
    assert [e.event_type for e in events] == ["congestion_start"]
    assert monitor.active
    assert events[0].timestamp_s == pytest.approx(10.0)
