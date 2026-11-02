"""CSV exports and summary statistics for queue analysis."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from ai_engine.queue.membership import ZoneEvent

TIMESERIES_COLUMNS = ("time_s", "frame", "people_in_frame", "raw_queue_count", "queue_length")
EVENT_COLUMNS = ("event_type", "track_id", "timestamp_s", "frame", "zone_entered_s")


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


class QueueTimeSeriesWriter(_CsvWriter):
    """One row per second of *video* time, sampled at the first frame of each second."""

    def __init__(self, path: str | Path) -> None:
        super().__init__(path, TIMESERIES_COLUMNS)
        self._next_second = 0

    def add(
        self, timestamp_s: float, frame_index: int, people_in_frame: int, raw_count: int, queue_length: float
    ) -> None:
        if timestamp_s + 1e-9 < self._next_second:
            return
        self._write([f"{timestamp_s:.3f}", frame_index, people_in_frame, raw_count, f"{queue_length:g}"])
        self._next_second = math.floor(timestamp_s + 1e-9) + 1


class ZoneEventWriter(_CsvWriter):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path, EVENT_COLUMNS)

    def add(self, event: ZoneEvent) -> None:
        self._write(
            [
                event.event_type,
                event.track_id,
                f"{event.timestamp_s:.3f}",
                event.frame_index,
                f"{event.zone_entered_s:.3f}",
            ]
        )


class QueueSummary:
    """Running statistics of the smoothed queue length and zone events."""

    def __init__(self) -> None:
        self.frames = 0
        self.total_queue_length = 0.0
        self.max_queue_length = 0.0
        self.max_raw_count = 0
        self.event_counts = {"zone_enter": 0, "zone_exit": 0}

    def add(self, raw_count: int, queue_length: float, events: tuple[ZoneEvent, ...]) -> None:
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
        }
