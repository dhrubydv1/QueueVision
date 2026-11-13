"""Camera config listing."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from api.deps import SettingsDep
from api.models import CameraInfo

LOGGER = logging.getLogger("queuevision.api")
router = APIRouter()


@router.get("/api/cameras", response_model=list[CameraInfo])
def cameras(settings: SettingsDep) -> list[dict]:
    out = []
    for path in sorted(settings.camera_dir.glob("*.yaml")):
        try:
            cam = load_camera_config(path)
        except (CameraConfigError, OSError, ValueError, TypeError, AttributeError) as error:
            LOGGER.warning("Invalid camera config %s: %s", path.name, error)
            out.append({"id": path.stem, "error": "invalid camera config"})
            continue
        out.append(
            {
                "id": path.stem,
                "description": cam.description,
                "has_service_point": cam.service_line is not None or cam.service_zone is not None,
                "alerts_enabled": cam.alerts.enabled,
                "max_queue_length": cam.alerts.max_queue_length,
                "smoothing_window_s": cam.smoothing.window_seconds,
            }
        )
    return out
