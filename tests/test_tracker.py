"""Tests for PersonTracker (ByteTrack) and its resolved configuration.

``mocked`` tests replace the detector with fake Ultralytics results to check
ID extraction, thresholds and reset logic. ``integration`` tests run real
YOLO11n + ByteTrack.
"""

from typing import Any

import numpy as np
import pytest
import yaml

from ai_engine.tracking.tracker import (
    PersonTracker,
    TrackerSettings,
    resolve_tracker_config,
    write_tracker_config,
)
from tests.conftest import BYTETRACK_DEFAULTS, make_result


class FakeDetector:
    """MOCKED detector returning queued results and recording track() calls."""

    confidence = 0.35

    def __init__(self, results: list[Any]) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.last_inference_seconds = 0.01

    def run_timed(self, method: str, frame: np.ndarray, **kwargs: Any) -> Any:
        self.calls.append({"method": method, **kwargs})
        return self.results.pop(0)


FRAME = np.zeros((64, 64, 3), dtype=np.uint8)


def make_tracker(results: list[Any], **kwargs: Any) -> tuple[PersonTracker, FakeDetector]:
    detector = FakeDetector(results)
    return PersonTracker(detector, base_config=dict(BYTETRACK_DEFAULTS), **kwargs), detector


def test_resolved_config_sets_new_track_threshold_and_buffer() -> None:
    settings = TrackerSettings(new_track_confidence=0.4, input_confidence=0.1, buffer_seconds=1.0)
    resolved = resolve_tracker_config(settings, effective_fps=30.0, base=BYTETRACK_DEFAULTS)
    assert resolved["new_track_thresh"] == 0.4
    assert resolved["track_buffer"] == 30
    # Everything else is Ultralytics' default.
    assert resolved["track_high_thresh"] == 0.25 and resolved["track_low_thresh"] == 0.1
    assert resolve_tracker_config(settings, 10.0, BYTETRACK_DEFAULTS)["track_buffer"] == 10
    assert resolve_tracker_config(settings, 0.4, BYTETRACK_DEFAULTS)["track_buffer"] == 1
    with pytest.raises(ValueError):
        resolve_tracker_config(settings, 0.0, BYTETRACK_DEFAULTS)


def test_written_config_is_valid_yaml_and_reused(tmp_path) -> None:
    resolved = resolve_tracker_config(TrackerSettings(), 30.0, BYTETRACK_DEFAULTS)
    path = write_tracker_config(resolved, tmp_path)
    assert yaml.safe_load(path.read_text()) == resolved
    assert write_tracker_config(resolved, tmp_path) == path


def test_invalid_tracker_settings_rejected() -> None:
    with pytest.raises(ValueError):
        TrackerSettings(input_confidence=0.0)
    with pytest.raises(ValueError):
        TrackerSettings(buffer_seconds=0)


def test_mocked_track_receives_low_confidence_input_and_resolved_yaml() -> None:
    tracker, detector = make_tracker([make_result([], [], [])])
    tracker.update(FRAME)
    call = detector.calls[0]
    assert call["method"] == "track"
    assert call["conf"] == 0.1  # low-score boxes reach ByteTrack's second stage
    assert call["tracker"] == str(tracker.config_path)
    assert yaml.safe_load(tracker.config_path.read_text())["new_track_thresh"] == 0.35


def test_mocked_extracts_track_ids_including_low_score_tracks() -> None:
    tracker, _ = make_tracker(
        [make_result([[0, 0, 10, 20], [5, 5, 15, 25]], [0.9, 0.15], [0, 0], ids=[3, 8])]
    )
    people = tracker.update(FRAME)
    # The 0.15-score box is a track kept alive through occlusion; it is not filtered out.
    assert [p.track_id for p in people] == [3, 8]


def test_mocked_frame_without_tracks_returns_empty() -> None:
    tracker, _ = make_tracker([make_result([], [], []), make_result([[0, 0, 5, 5]], [0.5], [0], ids=None)])
    assert tracker.update(FRAME) == []
    # Boxes with no ByteTrack ID (unconfirmed) are not reported as tracks.
    assert tracker.update(FRAME) == []


def test_mocked_persist_and_reset_sequence() -> None:
    tracker, detector = make_tracker([make_result([], [], [])] * 5)
    for _ in range(3):
        tracker.update(FRAME)
    tracker.reset()
    tracker.update(FRAME)
    tracker.configure(10.0)  # new video at a different processing rate also resets
    tracker.update(FRAME)
    assert [call["persist"] for call in detector.calls] == [False, True, True, False, False]
    assert tracker.describe()["resolved"]["track_buffer"] == 10


@pytest.mark.integration
def test_ids_stay_stable_across_consecutive_frames(
    real_tracker: PersonTracker, bus_image: np.ndarray
) -> None:
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


@pytest.mark.integration
def test_real_tracker_uses_installed_bytetrack_defaults(real_tracker: PersonTracker) -> None:
    resolved = real_tracker.resolved_config
    assert resolved["tracker_type"] == "bytetrack"
    assert resolved["track_high_thresh"] > real_tracker.settings.input_confidence
