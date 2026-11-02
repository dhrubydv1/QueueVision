"""Shared pytest fixtures.

Unit tests use synthetic videos and fake Ultralytics results, and import
neither torch nor ultralytics, so ``pytest -m "not integration"`` runs
without the model stack. Tests marked ``integration`` run the real YOLO11n
model on Ultralytics' bundled sample images; they are skipped when the
weights cannot be loaded.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

SYNTHETIC_FPS = 10.0
SYNTHETIC_FRAMES = 15
SYNTHETIC_SIZE = (160, 128)  # width, height
BYTETRACK_DEFAULTS = {
    "tracker_type": "bytetrack",
    "track_high_thresh": 0.25,
    "track_low_thresh": 0.1,
    "new_track_thresh": 0.25,
    "track_buffer": 30,
    "match_thresh": 0.8,
    "fuse_score": True,
}


def write_synthetic_video(path: Path, frames: int = SYNTHETIC_FRAMES) -> Path:
    """Write a small MP4 of a white square moving across a dark background."""
    width, height = SYNTHETIC_SIZE
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), SYNTHETIC_FPS, (width, height))
    for index in range(frames):
        frame = np.full((height, width, 3), 40, dtype=np.uint8)
        x = 5 + index * 5
        cv2.rectangle(frame, (x, 40), (x + 20, 80), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()
    return path


class FakeTensor:
    """Minimal stand-in for a torch tensor: only ``.cpu()`` and array conversion."""

    def __init__(self, values) -> None:
        self.values = np.asarray(values, dtype=np.float32)

    def cpu(self) -> np.ndarray:
        return self.values

    def __len__(self) -> int:
        return len(self.values)


class _Boxes(SimpleNamespace):
    def __len__(self) -> int:
        return len(self.conf)


def make_result(xyxy, conf, cls, ids=None) -> SimpleNamespace:
    """Build a MOCKED object shaped like ``ultralytics.engine.results.Results``."""
    boxes = _Boxes(
        xyxy=FakeTensor(np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)),
        conf=FakeTensor(conf),
        cls=FakeTensor(cls),
        id=None if ids is None else FakeTensor(ids),
    )
    return SimpleNamespace(boxes=boxes)


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    return write_synthetic_video(tmp_path / "synthetic.mp4")


@pytest.fixture(scope="session")
def real_detector():
    """A real YOLO11n detector on CPU (deterministic and available everywhere)."""
    from ai_engine.detection.detector import ModelLoadError, PersonDetector

    pytest.importorskip("ultralytics")
    try:
        return PersonDetector(device="cpu")
    except ModelLoadError as error:
        pytest.skip(f"YOLO11n weights unavailable: {error}")


@pytest.fixture
def real_tracker():
    """A real ByteTrack tracker on its own CPU YOLO11n instance.

    Tracking registers callbacks on the YOLO object, so it gets a fresh model
    instead of sharing the detect-only ``real_detector``.
    """
    from ai_engine.detection.detector import ModelLoadError, PersonDetector
    from ai_engine.tracking.tracker import PersonTracker

    pytest.importorskip("ultralytics")
    try:
        return PersonTracker(PersonDetector(device="cpu"))
    except ModelLoadError as error:
        pytest.skip(f"YOLO11n weights unavailable: {error}")


@pytest.fixture(scope="session")
def bus_image() -> np.ndarray:
    """Ultralytics' bundled bus.jpg, which contains several people."""
    ultralytics_utils = pytest.importorskip("ultralytics.utils")
    image = cv2.imread(str(ultralytics_utils.ASSETS / "bus.jpg"))
    assert image is not None
    return image
