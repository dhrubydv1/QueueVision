"""Tests for PersonTracker (ByteTrack).

``mocked`` tests replace the detector with fake Ultralytics results to check
ID extraction and reset logic. ``integration`` tests run real YOLO11n + ByteTrack.
"""

from typing import Any

import numpy as np
import pytest

from ai_engine.tracking.tracker import PersonTracker
from tests.conftest import make_result


class FakeDetector:
    """MOCKED detector returning queued results and recording track() calls."""

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.last_inference_seconds = 0.01

    def run_timed(self, method: str, frame: np.ndarray, **kwargs: Any) -> Any:
        self.calls.append({"method": method, **kwargs})
        return self.results.pop(0)


FRAME = np.zeros((64, 64, 3), dtype=np.uint8)


def test_mocked_extracts_track_ids() -> None:
    detector = FakeDetector([make_result([[0, 0, 10, 20], [5, 5, 15, 25]], [0.9, 0.7], [0, 0], ids=[3, 8])])
    people = PersonTracker(detector).update(FRAME)
    assert [p.track_id for p in people] == [3, 8]
    assert detector.calls[0]["method"] == "track"
    assert detector.calls[0]["tracker"] == "bytetrack.yaml"


def test_mocked_frame_without_tracks_returns_empty() -> None:
    detector = FakeDetector([make_result([], [], []), make_result([[0, 0, 5, 5]], [0.5], [0], ids=None)])
    tracker = PersonTracker(detector)
    assert tracker.update(FRAME) == []
    # Boxes with no ByteTrack ID (unconfirmed) are not reported as tracks.
    assert tracker.update(FRAME) == []


def test_mocked_persist_and_reset_sequence() -> None:
    detector = FakeDetector([make_result([], [], [])] * 4)
    tracker = PersonTracker(detector)
    for _ in range(3):
        tracker.update(FRAME)
    tracker.reset()
    tracker.update(FRAME)
    assert [call["persist"] for call in detector.calls] == [False, True, True, False]


@pytest.mark.integration
def test_ids_stay_stable_across_consecutive_frames(real_tracker: PersonTracker, bus_image: np.ndarray) -> None:
    frames = [real_tracker.update(bus_image) for _ in range(5)]
    assert len(frames[-1]) >= 3
    first_ids = sorted(p.track_id for p in frames[1])
    assert first_ids, "ByteTrack should confirm tracks by the second frame"
    assert sorted(p.track_id for p in frames[-1]) == first_ids
    assert real_tracker.last_inference_seconds > 0


@pytest.mark.integration
def test_reset_restarts_ids_for_new_video(real_tracker: PersonTracker, bus_image: np.ndarray) -> None:
    for _ in range(3):
        before = real_tracker.update(bus_image)
    real_tracker.update(np.zeros_like(bus_image))  # everyone disappears
    real_tracker.reset()
    for _ in range(3):
        after = real_tracker.update(bus_image)
    assert min(p.track_id for p in after) == 1
    assert sorted(p.track_id for p in after) == sorted(p.track_id for p in before)


@pytest.mark.integration
def test_empty_frame_has_no_tracks(real_tracker: PersonTracker) -> None:
    assert real_tracker.update(np.zeros((480, 640, 3), dtype=np.uint8)) == []
