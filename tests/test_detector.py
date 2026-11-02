"""Tests for PersonDetector, device selection and result parsing.

Tests whose names contain ``mocked`` use fake Ultralytics results to check
parsing logic only. Tests marked ``integration`` run the real YOLO11n model.
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ai_engine import config
from ai_engine.detection.detector import (
    COREML_DEVICE,
    PersonDetector,
    parse_person_boxes,
    resolve_model_path,
)
from ai_engine.detection.types import PersonDetection
from ai_engine.utils import device as device_utils
from tests.conftest import make_result

# --- device selection -------------------------------------------------------


def test_auto_device_prefers_cuda_then_mps_then_cpu(monkeypatch) -> None:
    monkeypatch.setattr(device_utils, "cuda_available", lambda: False)
    monkeypatch.setattr(device_utils, "mps_available", lambda: False)
    assert device_utils.select_device("auto") == "cpu"
    monkeypatch.setattr(device_utils, "mps_available", lambda: True)
    assert device_utils.select_device("auto") == "mps"
    monkeypatch.setattr(device_utils, "cuda_available", lambda: True)
    assert device_utils.select_device("auto") == "cuda"


def test_unavailable_explicit_device_raises(monkeypatch) -> None:
    monkeypatch.setattr(device_utils, "mps_available", lambda: False)
    monkeypatch.setattr(device_utils, "cuda_available", lambda: False)
    with pytest.raises(device_utils.DeviceUnavailableError):
        device_utils.select_device("mps")
    with pytest.raises(device_utils.DeviceUnavailableError):
        device_utils.select_device("cuda")
    with pytest.raises(ValueError):
        device_utils.select_device("tpu")
    assert device_utils.select_device("CPU") == "cpu"


# --- parsing (mocked results) -------------------------------------------------


def test_mocked_parse_keeps_only_person_class() -> None:
    result = make_result(
        [[10.4, 20.6, 50, 90], [0, 0, 5, 5], [100, 100, 150, 200]],
        [0.9, 0.8, 0.5],
        [0, 2, 0],
    )
    people = parse_person_boxes(result)
    assert [p.bbox for p in people] == [(10, 21, 50, 90), (100, 100, 150, 200)]
    assert people[0].confidence == pytest.approx(0.9)
    assert all(p.track_id is None for p in people)


def test_mocked_parse_empty_result() -> None:
    assert parse_person_boxes(make_result([], [], [])) == []
    assert parse_person_boxes(SimpleNamespace(boxes=None)) == []


def test_detection_to_dict_structure() -> None:
    detection = PersonDetection(bbox=(120, 80, 220, 350), confidence=0.94123)
    assert detection.to_dict() == {"class": "person", "confidence": 0.9412, "bbox": [120, 80, 220, 350]}
    assert detection.center == (170, 215)
    tracked = PersonDetection(bbox=(1, 2, 3, 4), confidence=0.5, track_id=7)
    assert tracked.to_dict()["track_id"] == 7


def test_invalid_detector_settings_rejected_before_loading_model() -> None:
    with pytest.raises(ValueError):
        PersonDetector(confidence=0.0, model=object())
    with pytest.raises(ValueError):
        PersonDetector(image_size=630, model=object())


def test_resolve_model_path_only_caches_bare_names() -> None:
    assert resolve_model_path("yolo11s.pt") == config.MODELS_DIR / "yolo11s.pt"
    # An explicit relative path is used as given, not redirected to the cache.
    assert resolve_model_path("./yolo11n.pt") == Path("./yolo11n.pt")
    assert resolve_model_path("weights/yolo11n.pt") == Path("weights/yolo11n.pt")
    assert resolve_model_path(Path("yolo11n.pt")) == Path("yolo11n.pt")


def test_coreml_model_reports_coreml_device_without_torch_device() -> None:
    detector = PersonDetector(model_path="/models/yolo11n.mlpackage", model=object())
    assert detector.device == COREML_DEVICE
    assert detector.inference_kwargs["device"] == "cpu"


def test_run_timed_overrides_confidence_for_tracking() -> None:
    calls = []

    class FakeModel:
        def track(self, frame, **kwargs):
            calls.append(kwargs)
            return [make_result([], [], [])]

    detector = PersonDetector(device="cpu", model=FakeModel())
    detector.run_timed("track", np.zeros((8, 8, 3), np.uint8), conf=0.1, persist=True)
    assert calls[0]["conf"] == 0.1 and calls[0]["persist"] is True
    assert calls[0]["classes"] == [config.PERSON_CLASS_ID]


# --- real model (integration) -------------------------------------------------


@pytest.mark.integration
def test_model_initializes(real_detector: PersonDetector) -> None:
    assert real_detector.device == "cpu"
    assert real_detector.model_path.name == config.MODEL_NAME
    assert real_detector.model.names[config.PERSON_CLASS_ID] == "person"


@pytest.mark.integration
def test_detects_people_in_sample_image(real_detector: PersonDetector, bus_image: np.ndarray) -> None:
    people = real_detector.detect(bus_image)
    assert len(people) >= 3  # bus.jpg shows 4 pedestrians
    height, width = bus_image.shape[:2]
    for person in people:
        x1, y1, x2, y2 = person.bbox
        assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
        assert person.confidence >= real_detector.confidence
        assert person.to_dict()["class"] == "person"
    assert real_detector.last_inference_seconds > 0


@pytest.mark.integration
def test_blank_frame_gives_empty_detections(real_detector: PersonDetector) -> None:
    assert real_detector.detect(np.zeros((480, 640, 3), dtype=np.uint8)) == []
