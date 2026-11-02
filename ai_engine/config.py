"""Default configuration values for the QueueVision AI engine.

Values can be overridden with environment variables (see ``.env.example``)
or with command-line arguments to ``run_tracking.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "ai_engine" / "models"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

MODEL_NAME = "yolo11n.pt"
PERSON_CLASS_ID = 0
PERSON_CLASS_NAME = "person"
TRACKER_CONFIG = "bytetrack.yaml"


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


DEFAULT_MODEL_PATH = Path(os.environ.get("QUEUEVISION_MODEL", str(MODELS_DIR / MODEL_NAME)))
DEFAULT_DEVICE = os.environ.get("QUEUEVISION_DEVICE", "auto")
# Detection threshold for plain detection, and the minimum score needed to
# start a new ByteTrack track when tracking.
CONFIDENCE = _env_float("QUEUEVISION_CONFIDENCE", 0.35)
IMAGE_SIZE = _env_int("QUEUEVISION_IMGSZ", 640)

# Detections passed into ByteTrack. Must stay below bytetrack.yaml's
# track_high_thresh (0.25) so the low-score association stage receives boxes.
TRACKER_INPUT_CONFIDENCE = 0.1
# How long a lost track is kept before it is dropped.
TRACK_BUFFER_SECONDS = 1.0
