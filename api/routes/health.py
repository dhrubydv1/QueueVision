"""Service health."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import JobsDep, SettingsDep
from api.models import Health

router = APIRouter()


@router.get("/api/health", response_model=Health)
def health(settings: SettingsDep, jobs: JobsDep) -> dict:
    return {
        "status": "ok",
        "device": jobs.loaded_device,
        "configured_device": settings.device,
        "model": settings.model,
        "queue_depth": jobs.queue_depth,
    }
