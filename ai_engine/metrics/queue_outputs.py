"""CSV exports and summary statistics for queue analysis."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from ai_engine.queue.event_schema import (
    EVENT_TYPES,
    CongestionEvent,
    QueueAbandonEvent,
    QueueEventType,
    ServiceStartEvent,
    ZoneEvent,
)
from ai_engine.queue.waiting_time import ServiceRate, WaitPrediction

TIMESERIES_COLUMNS = (
    "time_s",
    "frame",
    "people_in_frame",
    "raw_queue_count",
    "queue_length",
    "service_rate_per_min",
    "service_rate_status",
    "predicted_wait_s",
    "predicted_wait_low_s",
    "predicted_wait_high_s",
    "prediction_status",
)
EVENT_COLUMNS = ("event_type", "track_id", "timestamp_s", "frame", "zone_entered_s", "zone_entered_frame")
WAIT_COLUMNS = ("person_key", "track_ids", "join_s", "service_s", "wait_s", "abandoned", "left_s")
ALERT_COLUMNS = (
    "event_type",
    "timestamp_s",
    "frame",
    "condition_since_s",
    "reasons",
    "queue_length",
    "predicted_wait_s",
)


def _fmt(value: float | None, spec: str = ".3f") -> str:
    return "" if value is None else format(value, spec)


class _CsvWriter:
    def __init__(self, path: str | Path, columns: tuple[str, ...]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(columns)
        self.rows_written = 0

    def _write(self, row: list[Any]) -> None:
        self._writer.writerow(row)
        self.rows_written += 1

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class SecondSampler:
    """Pure once-per-video-second sampling rule used by the time series.

    ``due(t)`` is True for the first frame of each second of video time; call
    it once per frame, in order. Shared by the CSV writer and the replay harness.
    """

    def __init__(self) -> None:
        self._next_second = 0

    def due(self, timestamp_s: float) -> bool:
        if timestamp_s + 1e-9 < self._next_second:
            return False
        self._next_second = math.floor(timestamp_s + 1e-9) + 1
        return True


class QueueTimeSeriesWriter(_CsvWriter):
    """One row per second of *video* time, sampled at the first frame of each second."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path, TIMESERIES_COLUMNS)
        self._sampler = SecondSampler()

    def add(
        self,
        timestamp_s: float,
        frame_index: int,
        people_in_frame: int,
        raw_count: int,
        queue_length: float,
        service_rate: ServiceRate | None = None,
        prediction: WaitPrediction | None = None,
    ) -> None:
        if not self._sampler.due(timestamp_s):
            return
        self._write(
            [
                f"{timestamp_s:.3f}",
                frame_index,
                people_in_frame,
                raw_count,
                f"{queue_length:g}",
                _fmt(service_rate.per_min if service_rate else None, ".3f"),
                service_rate.status if service_rate else "insufficient_data",
                _fmt(prediction.predicted_wait_s if prediction else None, ".1f"),
                _fmt(prediction.low_s if prediction else None, ".1f"),
                _fmt(prediction.high_s if prediction else None, ".1f"),
                prediction.status if prediction else "unknown",
            ]
        )


class ZoneEventWriter(_CsvWriter):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path, EVENT_COLUMNS)

    def add(self, event: QueueEventType) -> None:
        if not isinstance(event, ZoneEvent):
            return
        self._write(
            [
                event.event_type,
                event.track_id,
                f"{event.timestamp_s:.3f}",
                event.frame_index,
                f"{event.zone_entered_s:.3f}",
                event.zone_entered_frame,
            ]
        )


class WaitsWriter(_CsvWriter):
    """One row per completed or abandoned wait, written as the events arrive."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path, WAIT_COLUMNS)

    def add(self, event: QueueEventType) -> None:
        if isinstance(event, ServiceStartEvent):
            self._write(
                [
                    event.person_key,
                    ";".join(str(t) for t in event.track_ids),
                    f"{event.join_s:.3f}",
                    f"{event.timestamp_s:.3f}",
                    f"{event.wait_s:.3f}",
                    "false",
                    "",
                ]
            )
        elif isinstance(event, QueueAbandonEvent):
            self._write(
                [
                    event.person_key,
                    ";".join(str(t) for t in event.track_ids),
                    f"{event.join_s:.3f}",
                    "",
                    "",
                    "true",
                    f"{event.timestamp_s:.3f}",
                ]
            )


class AlertWriter(_CsvWriter):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path, ALERT_COLUMNS)

    def add(self, event: QueueEventType) -> None:
        if not isinstance(event, CongestionEvent):
            return
        self._write(
            [
                event.event_type,
                f"{event.timestamp_s:.3f}",
                event.frame_index,
                f"{event.condition_since_s:.3f}",
                ";".join(event.reasons),
                f"{event.queue_length:g}",
                _fmt(event.predicted_wait_s, ".1f"),
            ]
        )


class EventLogWriter:
    """JSON Lines log of every event, using the versioned event schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", encoding="utf-8")
        self.rows_written = 0

    def add(self, event: QueueEventType) -> None:
        self._file.write(json.dumps(event.to_dict()) + "\n")
        self.rows_written += 1

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


class QueueSummary:
    """Running statistics of the smoothed queue length and zone events."""

    def __init__(self) -> None:
        self.frames = 0
        self.total_queue_length = 0.0
        self.max_queue_length = 0.0
        self.max_raw_count = 0
        self.event_counts = dict.fromkeys(EVENT_TYPES, 0)

    def add(self, raw_count: int, queue_length: float, events: tuple[QueueEventType, ...]) -> None:
        self.frames += 1
        self.total_queue_length += queue_length
        self.max_queue_length = max(self.max_queue_length, queue_length)
        self.max_raw_count = max(self.max_raw_count, raw_count)
        for event in events:
            self.event_counts[event.event_type] = self.event_counts.get(event.event_type, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_queue_length": self.max_queue_length,
            "average_queue_length": round(self.total_queue_length / self.frames, 3) if self.frames else 0.0,
            "maximum_raw_queue_count": self.max_raw_count,
            "zone_enter_events": self.event_counts.get("zone_enter", 0),
            "zone_exit_events": self.event_counts.get("zone_exit", 0),
            "event_counts": dict(self.event_counts),
        }
