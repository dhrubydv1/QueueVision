"""Camera YAML ``live:`` section."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_engine.config import PROJECT_ROOT
from ai_engine.queue.camera_config import (
    LIVE_SOURCES,
    CameraConfigError,
    LiveSettings,
    camera_config_from_dict,
    load_camera_config,
)

CAMERAS = PROJECT_ROOT / "configs" / "cameras"
ZONE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


def build(live):
    return camera_config_from_dict({"camera_id": "c", "queue_zone": ZONE, "live": live})


def test_live_section_parsed() -> None:
    assert LIVE_SOURCES == ("webcam", "rtsp", "clip")
    webcam = build({"source": "webcam"}).live
    assert webcam == LiveSettings(source="webcam")
    assert (webcam.webcam_index, webcam.process_fps, webcam.preview_privacy_mode) == (0, 10.0, "frame")
    assert webcam.max_session_s == 7200.0
    rtsp = build({"source": "rtsp", "rtsp_url_env": "RTSP_URL_CAM1", "preview_privacy_mode": "boxes"}).live
    assert rtsp.rtsp_url_env == "RTSP_URL_CAM1" and rtsp.preview_privacy_mode == "boxes"
    clip = build({"source": "clip", "clip": "datasets/raw/x.mp4", "process_fps": 5, "max_session_s": 60}).live
    assert clip.clip_path() == PROJECT_ROOT / "datasets/raw/x.mp4"
    assert LiveSettings(source="clip", clip="/abs/x.mp4").clip_path() == Path("/abs/x.mp4")
    assert build({"source": "webcam"}).to_dict()["live"]["source"] == "webcam"


def test_live_absent_is_none() -> None:
    paths = [p for p in sorted(CAMERAS.glob("cad_*.yaml")) if p.stem != "cad_seq25"]
    assert paths
    for path in paths:
        config = load_camera_config(path)
        assert config.live is None
        assert "live" not in config.to_dict()
    assert load_camera_config(CAMERAS / "cad_seq25.yaml").live.source == "clip"
    assert load_camera_config(CAMERAS / "webcam.yaml").live.source == "webcam"


@pytest.mark.parametrize(
    "live",
    [
        {"source": "usb"},
        {"source": "rtsp"},
        {"source": "rtsp", "rtsp_url_env": "rtsp://user:pw@host/x"},
        {"source": "rtsp", "rtsp_url_env": "lower_case"},
        {"source": "clip"},
        {"source": "webcam", "preview_privacy_mode": "none"},
        {"source": "webcam", "process_fps": 0},
        {"source": "webcam", "process_fps": 31},
        {"source": "webcam", "max_session_s": 0},
        {"source": "webcam", "max_session_s": -5},
        {"source": "webcam", "surprise": 1},
        {"preview_privacy_mode": "frame"},
    ],
)
def test_live_rejects(live) -> None:
    with pytest.raises(CameraConfigError):
        build(live)
