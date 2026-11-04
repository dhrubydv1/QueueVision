"""Congestion alerts with a minimum duration and a cooldown between alerts."""

from __future__ import annotations

from ai_engine.queue.camera_config import AlertSettings
from ai_engine.queue.event_schema import CongestionEvent
from ai_engine.queue.waiting_time import WaitPrediction

_EPS = 1e-9


class CongestionMonitor:
    """Emit ``congestion_start`` / ``congestion_end`` once a condition has held long enough.

    Timestamps are the detection time; ``condition_since_s`` is when the
    condition began (or stopped) holding. Nothing is emitted at end of video.
    """

    def __init__(self, settings: AlertSettings) -> None:
        self.settings = settings
        self._active = False
        self._reasons: tuple[str, ...] = ()
        self._condition = False
        self._since: float | None = None
        self._last_end_s: float | None = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def active_reasons(self) -> tuple[str, ...]:
        return self._reasons if self._active else ()

    def _reasons_now(self, queue_length: float, prediction: WaitPrediction) -> tuple[str, ...]:
        reasons = []
        limit = self.settings.max_queue_length
        if limit is not None and queue_length > limit:
            reasons.append("queue_length")
        wait_limit = self.settings.max_predicted_wait_seconds
        if (
            wait_limit is not None
            and prediction.status == "ok"
            and prediction.predicted_wait_s is not None
            and prediction.predicted_wait_s > wait_limit
        ):
            reasons.append("predicted_wait")
        return tuple(reasons)

    def update(
        self, timestamp_s: float, frame_index: int, queue_length: float, prediction: WaitPrediction
    ) -> list[CongestionEvent]:
        if not self.settings.enabled:
            return []
        reasons = self._reasons_now(queue_length, prediction)
        condition = bool(reasons)
        if self._since is None or condition != self._condition:
            self._condition = condition
            self._since = timestamp_s
        held = timestamp_s - self._since
        duration = self.settings.min_duration_seconds

        if condition:
            if self._active:
                self._reasons = reasons
            elif held >= duration - _EPS and not self._in_cooldown(timestamp_s):
                self._active = True
                self._reasons = reasons
                return [self._event("congestion_start", timestamp_s, frame_index, queue_length, prediction)]
        elif self._active and held >= duration - _EPS:
            event = self._event("congestion_end", timestamp_s, frame_index, queue_length, prediction)
            self._active = False
            self._last_end_s = timestamp_s
            return [event]
        return []

    def _in_cooldown(self, now: float) -> bool:
        return self._last_end_s is not None and now - self._last_end_s < self.settings.cooldown_seconds - _EPS

    def _event(
        self, event_type: str, now: float, frame_index: int, queue_length: float, prediction: WaitPrediction
    ) -> CongestionEvent:
        return CongestionEvent(
            event_type,
            now,
            frame_index,
            now,
            self._since if self._since is not None else now,
            self._reasons,
            queue_length,
            prediction.predicted_wait_s if prediction.status == "ok" else None,
        )
