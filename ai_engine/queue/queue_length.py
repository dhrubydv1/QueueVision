"""Raw and median-smoothed queue length over time."""

from __future__ import annotations

from collections import deque
from statistics import median


class QueueLengthEstimator:
    """Median-filter the per-frame queue count over a sliding time window.

    The median removes one-frame spikes and drops (a missed detection, a
    passer-by briefly counted) that a mean would smear across the window.
    """

    def __init__(self, window_seconds: float = 2.0) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = window_seconds
        self._samples: deque[tuple[float, int]] = deque()

    def update(self, timestamp_s: float, raw_count: int) -> float:
        """Add the count for one frame and return the smoothed queue length."""
        self._samples.append((timestamp_s, raw_count))
        while self._samples[0][0] <= timestamp_s - self.window_seconds:
            self._samples.popleft()
        return float(median(count for _, count in self._samples))
