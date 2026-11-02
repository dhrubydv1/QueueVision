"""Shared pytest fixtures.

Unit tests use synthetic videos generated on the fly. Tests marked
``integration`` run the real YOLO11n model on Ultralytics' bundled sample
images; they are skipped when the weights cannot be loaded.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import torch

from ai_engine.detection.detector import ModelLoadError, PersonDetector

SYNTHETIC_FPS = 10.0
SYNTHETIC_FRAMES = 15
SYNTHETIC_SIZE = (160, 128)  # width, height


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


class _Boxes(SimpleNamespace):
    def __len__(self) -> int:
        return len(self.conf)


def make_result(xyxy, conf, cls, ids=None) -> SimpleNamespace:
    """Build a MOCKED object shaped like ``ultralytics.engine.results.Results``."""
    boxes = _Boxes(
        xyxy=torch.tensor(xyxy, dtype=torch.float32).reshape(-1, 4),
        conf=torch.tensor(conf, dtype=torch.float32),
        cls=torch.tensor(cls, dtype=torch.float32),
        id=None if ids is None else torch.tensor(ids, dtype=torch.float32),
    )
    return SimpleNamespace(boxes=boxes)


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    return write_synthetic_video(tmp_path / "synthetic.mp4")


@pytest.fixture(scope="session")
def real_detector() -> PersonDetector:
    """A real YOLO11n detector on CPU (deterministic and available everywhere)."""
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
    from ai_engine.tracking.tracker import PersonTracker

    try:
        return PersonTracker(PersonDetector(device="cpu"))
    except ModelLoadError as error:
        pytest.skip(f"YOLO11n weights unavailable: {error}")


@pytest.fixture(scope="session")
def bus_image() -> np.ndarray:
    """Ultralytics' bundled bus.jpg, which contains several people."""
    from ultralytics.utils import ASSETS

    image = cv2.imread(str(ASSETS / "bus.jpg"))
    assert image is not None
    return image
