"""Person detection with Ultralytics YOLO."""

from ai_engine.detection.detector import (
    ModelLoadError,
    PersonDetector,
    is_coreml_model,
    load_yolo_model,
    parse_person_boxes,
    resolve_model_path,
)
from ai_engine.detection.types import PersonDetection

__all__ = [
    "ModelLoadError",
    "PersonDetection",
    "PersonDetector",
    "is_coreml_model",
    "load_yolo_model",
    "parse_person_boxes",
    "resolve_model_path",
]
