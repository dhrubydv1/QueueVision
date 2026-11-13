"""Completed and abandoned waits, service-rate estimation and wait prediction.

All times are video time in seconds. Unknown values are reported as
``None`` / ``"unknown"`` rather than guessed (no fake zero, no infinity).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from statistics import mean, median

from ai_engine.queue.camera_config import ServiceRateSettings
from ai_engine.queue.event_schema import QueueAbandonEvent, ServiceCompleteEvent, ServiceStartEvent

ESTIMATOR_VERSION = "queue_length_over_service_rate/v1"


@dataclass(frozen=True)
class CompletedWait:
    person_key: str
    track_ids: tuple[int, ...]
    join_s: float
    service_s: float
    wait_s: float


@dataclass(frozen=True)
class AbandonedWait:
    person_key: str
    track_ids: tuple[int, ...]
    join_s: float
    left_s: float
    time_in_queue_s: float


@dataclass(frozen=True)
class CompletedService:
    """A service that was observed to end (person seen leaving the service zone)."""

    person_key: str
    track_ids: tuple[int, ...]
    service_start_s: float
    left_s: float
    service_duration_s: float


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile (``fraction`` in [0, 1]) of a non-empty list."""
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


class WaitLog:
    """Collects waits from service-start and abandon events."""

    def __init__(self) -> None:
        self.completed: list[CompletedWait] = []
        self.abandoned: list[AbandonedWait] = []
        self.services: list[CompletedService] = []

    def add(self, event: object) -> None:
        if isinstance(event, ServiceStartEvent):
            self.completed.append(
                CompletedWait(
                    event.person_key, event.track_ids, event.join_s, event.timestamp_s, event.wait_s
                )
            )
        elif isinstance(event, ServiceCompleteEvent):
            self.services.append(
                CompletedService(
                    event.person_key,
                    event.track_ids,
                    event.service_start_s,
                    event.timestamp_s,
                    event.service_duration_s,
                )
            )
        elif isinstance(event, QueueAbandonEvent):
            self.abandoned.append(
                AbandonedWait(
                    event.person_key, event.track_ids, event.join_s, event.timestamp_s, event.time_in_queue_s
                )
            )

    def summary(self, duration_s: float) -> dict[str, float | int | None]:
        waits = [w.wait_s for w in self.completed]
        total = len(self.completed) + len(self.abandoned)
        return {
            "completed": len(self.completed),
            "abandoned": len(self.abandoned),
            "mean_wait_s": round(mean(waits), 3) if waits else None,
            "median_wait_s": round(median(waits), 3) if waits else None,
            "p90_wait_s": round(percentile(waits, 0.9), 3) if waits else None,
            "throughput_per_min": round(len(self.completed) / (duration_s / 60), 3)
            if duration_s > 0
            else None,
            "abandonment_rate": round(len(self.abandoned) / total, 3) if total else None,
        }

    def service_summary(self) -> dict[str, float | int | None]:
        """Observed service durations (only people seen leaving the service zone)."""
        durations = [s.service_duration_s for s in self.services]
        return {
            "service_completed": len(self.services),
            "mean_service_duration_s": round(mean(durations), 3) if durations else None,
            "median_service_duration_s": round(median(durations), 3) if durations else None,
        }


@dataclass(frozen=True)
class ServiceRate:
    per_min: float | None
    status: str  # "ok" | "insufficient_data"
    low_per_min: float | None = None
    high_per_min: float | None = None


INSUFFICIENT_RATE = ServiceRate(None, "insufficient_data")


class ServiceRateEstimator:
    """Estimate people served per minute with a moving window or an EWMA.

    ``add_service`` counts one event of the configured ``settings.basis`` (a service start or a
    confirmed completion); the caller decides which events to feed in.
    """

    def __init__(self, settings: ServiceRateSettings, start_s: float) -> None:
        self.settings = settings
        self.start_s = start_s
        self._services: deque[float] = deque()  # window method
        self._total = 0
        self._ewma_sum = 0.0  # sum of exp(-(t_last - t_i) / tau)
        self._ewma_time = start_s
        self._history: deque[tuple[float, float]] = deque()  # recent "ok" estimates

    def add_service(self, timestamp_s: float) -> None:
        self._services.append(timestamp_s)
        tau = self.settings.ewma_time_constant_seconds
        self._ewma_sum = self._ewma_sum * math.exp(-(timestamp_s - self._ewma_time) / tau) + 1.0
        self._ewma_time = timestamp_s
        self._total += 1

    def estimate(self, now: float) -> ServiceRate:
        settings = self.settings
        window = settings.window_seconds
        while self._services and self._services[0] <= now - window:
            self._services.popleft()
        elapsed = now - self.start_s
        if elapsed < settings.min_history_seconds - 1e-9 or elapsed <= 0:
            return INSUFFICIENT_RATE
        if settings.method == "window":
            if len(self._services) < settings.min_services:
                return INSUFFICIENT_RATE
            rate = len(self._services) / (min(window, elapsed) / 60)
        else:
            if self._total < settings.min_services:
                return INSUFFICIENT_RATE
            tau = settings.ewma_time_constant_seconds
            decayed = self._ewma_sum * math.exp(-(now - self._ewma_time) / tau)
            rate = decayed / tau * 60 / (1 - math.exp(-elapsed / tau))

        self._history.append((now, rate))
        while self._history[0][0] <= now - window:
            self._history.popleft()
        rates = [r for _, r in self._history]
        return ServiceRate(rate, "ok", min(rates), max(rates))


@dataclass(frozen=True)
class WaitPrediction:
    predicted_wait_s: float | None
    low_s: float | None
    high_s: float | None
    status: str  # "ok" | "unknown"
    estimator: str = ESTIMATOR_VERSION


UNKNOWN_PREDICTION = WaitPrediction(None, None, None, "unknown")


def predict_wait(queue_length: float, rate: ServiceRate) -> WaitPrediction:
    """Predict the wait for someone joining now: queue length / service rate."""
    if rate.status != "ok" or rate.per_min is None or rate.per_min <= 0:
        return UNKNOWN_PREDICTION
    predicted = queue_length / (rate.per_min / 60)
    low = high = None
    if rate.high_per_min is not None and rate.high_per_min > 0:
        low = queue_length / (rate.high_per_min / 60)
    if rate.low_per_min is not None and rate.low_per_min > 0:
        high = queue_length / (rate.low_per_min / 60)
    return WaitPrediction(predicted, low, high, "ok")
