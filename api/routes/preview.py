"""Live preview frames for a processing run."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from api.deps import JobsDep, SettingsDep, get_preview, get_record
from api.preview import MJPEG_BOUNDARY, mjpeg_part
from api.routes.videos import VideoId
from api.settings import ApiSettings

MJPEG_POLL_S = 0.1
router = APIRouter()
PreviewDep = Annotated[object, Depends(get_preview)]


def first_preview(settings: ApiSettings, preview, video_id: int) -> tuple[int, bytes]:
    record = get_record(settings, video_id)
    if record["status"] != "processing":
        raise HTTPException(404, "no preview: run is not processing")
    latest = preview.latest(video_id)
    if latest is None:
        raise HTTPException(404, "no preview frame yet")
    return latest


@router.get("/api/videos/{video_id}/preview.jpg")
def preview_jpg(video_id: VideoId, settings: SettingsDep, preview: PreviewDep) -> Response:
    _, jpeg = first_preview(settings, preview, video_id)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/videos/{video_id}/preview.mjpg")
def preview_mjpg(
    video_id: VideoId, request: Request, settings: SettingsDep, jobs: JobsDep, preview: PreviewDep
) -> StreamingResponse:
    sequence, jpeg = first_preview(settings, preview, video_id)

    async def parts():
        last, current = -1, (sequence, jpeg)
        while True:
            if current is not None and current[0] != last:
                last = current[0]
                yield mjpeg_part(current[1])
            await asyncio.sleep(MJPEG_POLL_S)
            if jobs.stopping or await request.is_disconnected():
                return
            current = preview.latest(video_id)
            if current is None:  # the run ended: the store is dropped when processing stops
                return

    return StreamingResponse(
        parts(),
        media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        headers={"Cache-Control": "no-store"},
    )
