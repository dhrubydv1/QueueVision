"""Wait summary, service-rate estimation and wait prediction."""

from __future__ import annotations

import math

import pytest

from ai_engine.queue import (
    QueueAbandonEvent,
    ServiceRate,
    ServiceRateEstimator,
    ServiceRateSettings,
    ServiceStartEvent,
    WaitLog,
    predict_wait,
)


def start(key: str, join_s: float, service_s: float) -> ServiceStartEvent:
    return ServiceStartEvent(key, (1,), service_s, 0, service_s, join_s, service_s - join_s)


def test_wait_summary_statistics() -> None:
    log = WaitLog()
    for index, wait in enumerate([10.0, 20.0, 30.0, 40.0, 100.0]):
        log.add(start(f"p{index}", 0.0, wait))
    log.add(QueueAbandonEvent("px", (9,), 15.0, 0, 17.0, 5.0, 10.0))
    summary = log.summary(duration_s=120.0)
    assert summary["completed"] == 5 and summary["abandoned"] == 1
    assert summary["mean_wait_s"] == 40.0
    assert summary["median_wait_s"] == 30.0
    assert summary["p90_wait_s"] == 76.0  # 40 + 0.6 * (100 - 40)
    assert summary["throughput_per_min"] == 2.5
    assert summary["abandonment_rate"] == pytest.approx(1 / 6, abs=1e-3)
    assert log.abandoned[0].time_in_queue_s == 10.0


def test_wait_summary_empty() -> None:
    summary = WaitLog().summary(0.0)
    assert summary["completed"] == 0
    assert summary["mean_wait_s"] is None and summary["p90_wait_s"] is None
    assert summary["throughput_per_min"] is None and summary["abandonment_rate"] is None


def window_estimator(**overrides) -> ServiceRateEstimator:
    settings = ServiceRateSettings(
        method="window", window_seconds=120.0, min_history_seconds=60.0, **overrides
    )
    return ServiceRateEstimator(settings, start_s=0.0)


def test_rate_insufficient_at_start_and_with_few_services() -> None:
    estimator = window_estimator()
    estimator.add_service(10.0)
    estimator.add_service(20.0)
    early = estimator.estimate(30.0)
    assert early.status == "insufficient_data" and early.per_min is None
    only_one = window_estimator()
    only_one.add_service(10.0)
    assert only_one.estimate(90.0).status == "insufficient_data"


def test_window_rate_value_and_range() -> None:
    estimator = window_estimator()
    for t in (10.0, 30.0, 50.0, 70.0):
        estimator.add_service(t)
    first = estimator.estimate(80.0)  # 4 services over 80 s
    assert first.status == "ok" and first.per_min == pytest.approx(3.0)
    estimator.add_service(90.0)
    second = estimator.estimate(100.0)  # 5 services over 100 s
    assert second.per_min == pytest.approx(3.0)
    estimator.add_service(100.0)
    third = estimator.estimate(150.0)  # window (30, 150]: 50, 70, 90, 100 -> 4 in 120 s = 2/min
    assert third.per_min == pytest.approx(2.0)
    assert third.low_per_min == pytest.approx(2.0) and third.high_per_min == pytest.approx(3.0)


def test_ewma_rate_matches_hand_computation() -> None:
    settings = ServiceRateSettings(method="ewma", ewma_time_constant_seconds=60.0, min_history_seconds=60.0)
    estimator = ServiceRateEstimator(settings, start_s=0.0)
    estimator.add_service(30.0)
    estimator.add_service(60.0)
    rate = estimator.estimate(90.0)
    total = math.exp(-60 / 60) + math.exp(-30 / 60)
    expected = total / 60 * 60 / (1 - math.exp(-90 / 60))
    assert rate.status == "ok" and rate.per_min == pytest.approx(expected)
    assert rate.low_per_min == rate.high_per_min == rate.per_min
    assert estimator.estimate(30.0).status == "insufficient_data"


def test_predict_wait_values_and_unknowns() -> None:
    rate = ServiceRate(2.0, "ok", 1.0, 4.0)
    prediction = predict_wait(6.0, rate)
    assert prediction.status == "ok"
    assert prediction.predicted_wait_s == pytest.approx(180.0)
    assert prediction.low_s == pytest.approx(90.0) and prediction.high_s == pytest.approx(360.0)
    assert predict_wait(0.0, rate).predicted_wait_s == 0.0

    zero_low = predict_wait(6.0, ServiceRate(2.0, "ok", 0.0, 2.0))
    assert zero_low.high_s is None and zero_low.predicted_wait_s == pytest.approx(180.0)

    for bad in (ServiceRate(0.0, "ok", 0.0, 0.0), ServiceRate(None, "insufficient_data")):
        unknown = predict_wait(5.0, bad)
        assert unknown.status == "unknown" and unknown.predicted_wait_s is None
        assert unknown.low_s is None and unknown.high_s is None
