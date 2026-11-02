"""YOLO11 person detector restricted to the COCO ``person`` class."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from ultralytics import YOLO

from ai_engine import config
from ai_engine.utils.device import select_device, synchronize


class ModelLoadError(RuntimeError):
    """Raised when YOLO weights cannot be found, downloaded or loaded."""


@dataclass(frozen=True)
class PersonDetection:
    """One detected (and optionally tracked) person in a frame.

    ``track_id`` is a per-video ByteTrack ID, not a permanent identity: the
    same person can receive a new ID after occlusion or leaving the frame.
    """

    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels
    confidence: float
    track_id: int | None = None

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) // 2, (y1 + y2) // 2

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "class": config.PERSON_CLASS_NAME,
            "confidence": round(self.confidence, 4),
            "bbox": list(self.bbox),
        }
        if self.track_id is not None:
            data["track_id"] = self.track_id
        return data


def resolve_model_path(model_path: str | Path) -> Path:
    """Place bare model names (e.g. ``yolo11s.pt``) in the local models cache."""
    path = Path(model_path).expanduser()
    if path.parent == Path("."):
        path = config.MODELS_DIR / path.name
    return path


def load_yolo_model(model_path: str | Path = config.DEFAULT_MODEL_PATH) -> YOLO:
    """Load YOLO weights, downloading official weights into the cache if missing."""
    path = resolve_model_path(model_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return YOLO(str(path))
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

        self.device = select_device(device)
        self.confidence = confidence
        self.image_size = image_size
        self.model_path = resolve_model_path(model_path)
        self.model = model if model is not None else load_yolo_model(self.model_path)
        self.last_inference_seconds = 0.0

    @property
    def inference_kwargs(self) -> dict[str, Any]:
        """Arguments shared by Ultralytics ``predict`` and ``track`` calls."""
        return {
            "classes": [config.PERSON_CLASS_ID],
            "conf": self.confidence,
            "imgsz": self.image_size,
            "device": self.device,
            "verbose": False,
        }

    def run_timed(self, method: str, frame: np.ndarray, **kwargs: Any) -> Any:
        """Call ``model.<method>`` on one frame and record its wall-clock time.

        The device is synchronized before the timer stops so asynchronous
        MPS/CUDA execution is fully counted.
        """
        start = time.perf_counter()
        results = getattr(self.model, method)(frame, **self.inference_kwargs, **kwargs)
        synchronize(self.device)
        self.last_inference_seconds = time.perf_counter() - start
        return results[0]

    def detect(self, frame: np.ndarray) -> list[PersonDetection]:
        """Return the people detected in one BGR frame (possibly empty)."""
        result = self.run_timed("predict", frame)
        return parse_person_boxes(result)
