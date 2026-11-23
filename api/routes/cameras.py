"""Camera config listing."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException

from ai_engine.queue.camera_config import CameraConfig, CameraConfigError, load_camera_config
from api import db
from api.deps import LiveDep, SettingsDep, open_db
from api.live import LiveStartError
from api.models import CameraInfo, LiveSession, Run
from api.routes.videos import mutation_router, run_record

LOGGER = logging.getLogger("queuevision.api")
router = APIRouter()


def _live_ready(cam: CameraConfig) -> bool:
    """Whether a live session could start now; reveals no URL, path or variable value."""
    live = cam.live
    if live is None:
        return False
    if live.source == "webcam":
        return True
    if live.source == "clip":
        return live.clip_path().is_file()
    return bool(live.rtsp_url_env and os.environ.get(live.rtsp_url_env))


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
                "live_source": cam.live.source if cam.live else None,
                "live_ready": _live_ready(cam),
            }
        )
    return out


@mutation_router.post("/api/cameras/{camera_id}/live", response_model=Run, status_code=201)
def start_live(camera_id: str, settings: SettingsDep, live: LiveDep) -> dict:
    try:
        video_id = live.start(camera_id)
    except LiveStartError as error:
        raise HTTPException(error.status, error.reason) from error
    with open_db(settings) as conn:
        return run_record(db.get_video(conn, video_id))


@router.get("/api/live", response_model=list[LiveSession])
def live_sessions(live: LiveDep) -> list[dict]:
    return live.sessions()
