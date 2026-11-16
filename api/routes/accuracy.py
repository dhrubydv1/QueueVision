"""Accuracy data served from the exported JSON."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.deps import SettingsDep

LOGGER = logging.getLogger("queuevision.api")
router = APIRouter()


@router.get("/api/accuracy")
def accuracy(settings: SettingsDep) -> JSONResponse:
    try:
        text = settings.accuracy_path.read_text()
    except FileNotFoundError:
        raise HTTPException(
            404, "accuracy data not generated; run python scripts/export_accuracy.py"
        ) from None
    try:
        return JSONResponse(json.loads(text))
    except ValueError:
        LOGGER.error("accuracy data at %s is not valid JSON", settings.accuracy_path)
        raise HTTPException(500, "accuracy data unreadable") from None
