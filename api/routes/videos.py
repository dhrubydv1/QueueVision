"""Upload and run endpoints."""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute

from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from ai_engine.video.video_loader import SUPPORTED_VIDEO_EXTENSIONS
from api import db
from api.deps import JobsDep, SettingsDep, contained_path, get_record, open_db
from api.models import Alert, Run, TimeseriesRow, Wait
from api.upload_validation import UploadRejected, sanitize_filename, validate_upload

LOGGER = logging.getLogger("queuevision.api")
CHUNK_BYTES = 1024 * 1024
MAX_VIDEO_ID = 2**63 - 1  # SQLite INTEGER is a signed 64-bit value; larger ids would raise OverflowError
VideoId = Annotated[int, PathParam(ge=1, le=MAX_VIDEO_ID)]
PART_SUFFIX = ".part"
_CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _percent(record: dict) -> float | None:
    """Progress percent; a completed run is always 100 (frame_stride > 1 ends short of the total)."""
    if record["status"] == "completed":
        return 100.0
    total = record.get("frames_total")
    if not total:
        return None
    return min(100.0, round(100.0 * record["frames_processed"] / total, 1))


def run_record(record: dict) -> dict[str, Any]:
    """Public run record built from a ``videos`` row."""
    report = record.get("report") or {}
    return {
        **{k: record[k] for k in Run.model_fields if k in record and k not in ("percent", "summary")},
        "percent": _percent(record),
        "summary": report.get("queue"),
    }


class UploadGuardRoute(APIRoute):
    """Refuse an upload on cheap header checks before FastAPI reads and spools the body."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            jobs = request.app.state.jobs
            settings = request.app.state.settings
            if jobs.queue_depth >= settings.max_queued_jobs:
                return JSONResponse({"detail": "too many queued runs; try again later"}, status_code=429)
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > settings.max_upload_bytes + CHUNK_BYTES:
                return JSONResponse({"detail": "file too large"}, status_code=413)
            return await handler(request)

        return guarded


# Several routers instead of one: this only keeps the OpenAPI path order (and the include order in
# api/main.py) identical to the old single-function layout; it has nothing to do with route matching.
router = APIRouter()  # reads and listing
mutation_router = APIRouter()  # cancel and delete
upload_router = APIRouter(route_class=UploadGuardRoute)  # POST /api/videos only


@upload_router.post("/api/videos", response_model=Run, status_code=202)
def upload(
    settings: SettingsDep,
    jobs: JobsDep,
    file: Annotated[UploadFile, File()],
    camera_id: Annotated[str, Form()],
    frame_stride: Annotated[int, Form(ge=1, le=10)] = 1,
    blur_heads: Annotated[bool, Form()] = True,
    max_frames: Annotated[int | None, Form(ge=1)] = None,
    realtime: Annotated[bool, Form()] = False,
) -> dict:
    name = sanitize_filename(file.filename or "")
    ext = Path(name).suffix.lower()
    if ext not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(
            400, f"unsupported file type {ext!r}; use one of {sorted(SUPPORTED_VIDEO_EXTENSIONS)}"
        )
    if not _CAMERA_ID.match(camera_id):
        raise HTTPException(400, "invalid camera_id")
    try:
        load_camera_config(settings.camera_dir / f"{camera_id}.yaml")
    except (CameraConfigError, OSError, ValueError, TypeError, AttributeError) as error:
        LOGGER.warning("Upload rejected, camera %r: %s", camera_id, error)
        raise HTTPException(400, f"unknown or invalid camera {camera_id!r}") from error

    options = {
        "frame_stride": frame_stride,
        "blur_heads": blur_heads,
        "max_frames": max_frames,
        "realtime": realtime,
    }
    with open_db(settings) as conn:
        video_id = db.create_video(conn, name, camera_id, options)
        folder = settings.uploads_dir / str(video_id)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            written = 0
            final = folder / f"video{video_id}{ext}"
            partial = final.with_name(final.name + PART_SUFFIX)  # never looks like a complete upload
            with open(partial, "wb") as out:
                while chunk := file.file.read(CHUNK_BYTES):
                    written += len(chunk)
                    if written > settings.max_upload_bytes:
                        raise HTTPException(413, "file too large")
                    out.write(chunk)
            if written == 0:
                raise HTTPException(400, "empty file")
            try:
                validate_upload(
                    partial,
                    ext,
                    max_seconds=settings.max_video_seconds,
                    max_pixels=settings.max_frame_pixels,
                )
            except UploadRejected as rejected:
                raise HTTPException(400, str(rejected)) from rejected
            os.replace(partial, final)
        except BaseException:
            db.delete_video(conn, video_id)
            shutil.rmtree(folder, ignore_errors=True)
            raise
        record = db.get_video(conn, video_id)
    jobs.submit(video_id)
    return run_record(record)


@router.get("/api/videos", response_model=list[Run])
def list_runs(settings: SettingsDep) -> list[dict]:
    with open_db(settings) as conn:
        return [run_record(r) for r in db.list_videos(conn)]


@router.get("/api/videos/{video_id}", response_model=Run)
def get_run(video_id: VideoId, settings: SettingsDep) -> dict:
    return run_record(get_record(settings, video_id))


@router.get("/api/videos/{video_id}/events")
def events(
    video_id: VideoId,
    settings: SettingsDep,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    get_record(settings, video_id)
    with open_db(settings) as conn:
        return db.list_events(conn, video_id, type, limit, offset)


@router.get("/api/videos/{video_id}/waits", response_model=list[Wait])
def waits(video_id: VideoId, settings: SettingsDep) -> list[dict]:
    get_record(settings, video_id)
    with open_db(settings) as conn:
        return db.list_waits(conn, video_id)


@router.get("/api/videos/{video_id}/alerts", response_model=list[Alert])
def alerts(video_id: VideoId, settings: SettingsDep) -> list[dict]:
    get_record(settings, video_id)
    with open_db(settings) as conn:
        return db.list_alerts(conn, video_id)


@router.post("/api/videos/{video_id}/alerts/{alert_id}/ack", response_model=Alert)
def acknowledge_alert(
    video_id: VideoId, alert_id: Annotated[int, PathParam(ge=1, le=MAX_VIDEO_ID)], settings: SettingsDep
) -> dict:
    get_record(settings, video_id)
    with open_db(settings) as conn:
        alert = db.acknowledge_alert(conn, video_id, alert_id)
    if alert is None:
        raise HTTPException(404, f"alert {alert_id} not found for video {video_id}")
    return alert


@router.get("/api/videos/{video_id}/timeseries", response_model=list[TimeseriesRow])
def timeseries(video_id: VideoId, settings: SettingsDep) -> list[dict]:
    get_record(settings, video_id)
    with open_db(settings) as conn:
        return db.list_timeseries(conn, video_id)


@router.get("/api/videos/{video_id}/report")
def report(video_id: VideoId, settings: SettingsDep) -> dict:
    record = get_record(settings, video_id)
    if record["report"] is None:
        raise HTTPException(404, "no report yet")
    return record["report"]


@router.get("/api/videos/{video_id}/video")
def video(video_id: VideoId, settings: SettingsDep) -> FileResponse:
    record = get_record(settings, video_id)
    name = ((record["report"] or {}).get("outputs") or {}).get("video")
    path = contained_path(settings, name)
    if record["status"] != "completed" or path is None:
        raise HTTPException(404, "annotated video not available")
    return FileResponse(path, media_type="video/mp4")  # Starlette answers Range requests with 206


@router.get("/api/videos/{video_id}/timeseries.csv")
def timeseries_csv(video_id: VideoId, settings: SettingsDep) -> FileResponse:
    record = get_record(settings, video_id)
    name = ((record["report"] or {}).get("outputs") or {}).get("timeseries")
    path = contained_path(settings, name)
    if path is None:
        raise HTTPException(404, "time series not available")
    return FileResponse(path, media_type="text/csv", filename=f"run{video_id}_timeseries.csv")


@mutation_router.post("/api/videos/{video_id}/cancel", response_model=Run)
def cancel(video_id: VideoId, settings: SettingsDep, jobs: JobsDep) -> dict:
    get_record(settings, video_id)
    if not jobs.cancel(video_id):
        raise HTTPException(409, "run is not queued or processing")
    return run_record(get_record(settings, video_id))


@mutation_router.delete("/api/videos/{video_id}", status_code=204)
def delete(video_id: VideoId, settings: SettingsDep, jobs: JobsDep) -> None:
    record = get_record(settings, video_id)
    if record["status"] == "processing":
        raise HTTPException(409, "run is processing; cancel it first")
    if record["status"] == "queued":
        jobs.cancel(video_id)
        if jobs.is_active(video_id) or get_record(settings, video_id)["status"] == "processing":
            raise HTTPException(409, "run is processing; cancel it first")
    with open_db(settings) as conn:
        db.delete_video(conn, video_id)
    for folder in (settings.uploads_dir, settings.outputs_dir):
        shutil.rmtree(folder / str(video_id), ignore_errors=True)
