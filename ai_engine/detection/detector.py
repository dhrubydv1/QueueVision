"""YOLO11 person detector restricted to the COCO ``person`` class."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from ai_engine import config
from ai_engine.detection.types import PersonDetection
from ai_engine.utils.device import select_device, synchronize

if TYPE_CHECKING:  # ultralytics is imported lazily so light modules stay torch-free
    from ultralytics import YOLO

COREML_SUFFIX = ".mlpackage"
COREML_DEVICE = "coreml"


class ModelLoadError(RuntimeError):
    """Raised when YOLO weights cannot be found, downloaded or loaded."""


def resolve_model_path(model_path: str | Path) -> Path:
    """Return where the model lives.

    Only a bare file name given as a string (``"yolo11s.pt"``) is placed in
    the local models cache; any path with a directory part, including
    ``"./yolo11n.pt"``, and any :class:`~pathlib.Path` object is used as given.
    """
    if isinstance(model_path, str):
        text = model_path.strip()
        if text and os.path.basename(text) == text and not text.startswith("."):
            return config.MODELS_DIR / text
    return Path(model_path).expanduser()


def is_coreml_model(model_path: str | Path) -> bool:
    return Path(model_path).suffix.lower() == COREML_SUFFIX


def load_yolo_model(model_path: str | Path = config.DEFAULT_MODEL_PATH) -> YOLO:
    """Load YOLO weights, downloading official weights into the cache if missing."""
    from ultralytics import YOLO

    path = resolve_model_path(model_path)
    if is_coreml_model(path) and not path.exists():
        raise ModelLoadError(
            f"CoreML model not found: {path}. Create it with ai_engine/tools/export_model.py"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return YOLO(str(path), task="detect")
    except Exception as error:  # Ultralytics raises several error types here.
        raise ModelLoadError(f"Could not load YOLO model '{path}': {error}") from error


def parse_person_boxes(result: Any, with_track_ids: bool = False) -> list[PersonDetection]:
    """Convert an Ultralytics ``Results`` object into person detections.

    Boxes of any class other than ``person`` are dropped, even though the
    model is already asked for class 0 only.
    """
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []

    xyxy = np.asarray(boxes.xyxy.cpu()).round().astype(int)
    confidences = np.asarray(boxes.conf.cpu(), dtype=float)
    classes = np.asarray(boxes.cls.cpu()).astype(int)
    track_ids = None
    if with_track_ids and boxes.id is not None:
        track_ids = np.asarray(boxes.id.cpu()).astype(int)

    people = []
    for index, class_id in enumerate(classes):
        if class_id != config.PERSON_CLASS_ID:
            continue
        x1, y1, x2, y2 = (int(value) for value in xyxy[index])
        people.append(
            PersonDetection(
                bbox=(x1, y1, x2, y2),
                confidence=float(confidences[index]),
                track_id=int(track_ids[index]) if track_ids is not None else None,
            )
        )
    return people


class PersonDetector:
    """Load YOLO once and detect people frame by frame.

    The loaded model is also shared with :class:`PersonTracker`, so a video
    is never processed with a model that is re-created per frame.

    For a CoreML ``.mlpackage`` the PyTorch device does not apply: inference
    runs through Core ML (which picks CPU/GPU/Neural Engine itself) and
    ``device`` is reported as ``"coreml"``.
    """

    def __init__(
        self,
        model_path: str | Path = config.DEFAULT_MODEL_PATH,
        confidence: float = config.CONFIDENCE,
        image_size: int = config.IMAGE_SIZE,
        device: str = config.DEFAULT_DEVICE,
        model: YOLO | None = None,
    ) -> None:
        if not 0.0 < confidence <= 1.0:
            raise ValueError(f"Confidence must be in (0, 1], got {confidence}")
        if image_size <= 0 or image_size % 32 != 0:
            raise ValueError(f"Image size must be a positive multiple of 32, got {image_size}")

        self.model_path = resolve_model_path(model_path)
        if is_coreml_model(self.model_path):
            self.device = COREML_DEVICE
            self._torch_device = "cpu"  # where Ultralytics keeps pre/post-processing tensors
        else:
            self.device = select_device(device)
            self._torch_device = self.device
        self.confidence = confidence
        self.image_size = image_size
        self.model = model if model is not None else load_yolo_model(self.model_path)
        self.last_inference_seconds = 0.0
        self.last_speed_ms: dict[str, float] | None = None  # Ultralytics per-stage timings, read-only

    @property
    def inference_kwargs(self) -> dict[str, Any]:
        """Arguments shared by Ultralytics ``predict`` and ``track`` calls."""
        return {
            "classes": [config.PERSON_CLASS_ID],
            "conf": self.confidence,
            "imgsz": self.image_size,
            "device": self._torch_device,
            "verbose": False,
        }

    def run_timed(self, method: str, frame: np.ndarray, **overrides: Any) -> Any:
        """Call ``model.<method>`` on one frame and record its wall-clock time.

        ``overrides`` replace or extend :attr:`inference_kwargs`. The device
        is synchronized before the timer stops so asynchronous MPS/CUDA
        execution is fully counted.
        """
        start = time.perf_counter()
        results = getattr(self.model, method)(frame, **{**self.inference_kwargs, **overrides})
        synchronize(self._torch_device)
        self.last_inference_seconds = time.perf_counter() - start
        speed = getattr(results[0], "speed", None)
        self.last_speed_ms = (
            {k: float(v) for k, v in speed.items() if isinstance(v, int | float)}
            if isinstance(speed, dict)
            else None
        )
        return results[0]

    def detect(self, frame: np.ndarray) -> list[PersonDetection]:
        """Return the people detected in one BGR frame (possibly empty)."""
        result = self.run_timed("predict", frame)
        return parse_person_boxes(result)
