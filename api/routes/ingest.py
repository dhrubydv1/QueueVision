"""Loopback-only, token-protected routes a live worker process posts its results to.

Not part of the public API: excluded from the OpenAPI schema. Checks run in this order: unknown run
(404), non-loopback client (403), missing or wrong token (401), run not a processing live run (409).
"""

from __future__ import annotations

import threading
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, ValidationError

from ai_engine.run_messages import event_message
from api import db
from api.deps import open_db
from api.jobs import status_message
from api.routes.videos import VideoId
from api.settings import is_loopback

TOKEN_HEADER = "X-Worker-Token"
MAX_PREVIEW_BYTES = 2 * 1024 * 1024

router = APIRouter(prefix="/api/ingest", include_in_schema=False)
_seq_lock = threading.Lock()


class Batch(BaseModel):
    events: list[dict[str, Any]] = []
    timeseries: list[dict[str, Any]] = []
    progress: dict[str, Any] | None = None
    frames_processed: int = 0
    captured_at_unix: float | None = None
    seq: int | None = None


class Heartbeat(BaseModel):
    frames_processed: int = 0
    processing_fps: float = 0.0


class Final(BaseModel):
    status: Literal["completed", "failed"]
    error: str | None = None
    report: dict[str, Any] | None = None


def check_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if not is_loopback(host):
        raise HTTPException(403, "ingest is loopback only")


def authorize(request: Request, video_id: int) -> dict:
    """Return the run record once the client, token and run state all check out."""
    with open_db(request.app.state.settings) as conn:
        record = db.get_video(conn, video_id)
    if record is None:
        raise HTTPException(404, f"video {video_id} not found")
    check_loopback(request)
    live = request.app.state.live
    token = request.headers.get(TOKEN_HEADER)
    if live is None or not token or not live.verify(video_id, token):
        raise HTTPException(401, "invalid worker token")
    if record["kind"] != "live" or record["status"] != "processing":
        raise HTTPException(409, "run is not a processing live run")
    return record


async def parse(request: Request, model: type[BaseModel]):
    """Validate the body after authorization, so an unauthenticated caller learns nothing from a 422."""
    try:
        return model.model_validate_json(await request.body())
    except ValidationError as error:
        raise HTTPException(422, "invalid body") from error


def _store_batch(request: Request, video_id: int, batch: Batch) -> None:
    jobs, live = request.app.state.jobs, request.app.state.live
    last_seq: dict[int, int] = request.app.state.ingest_seq
    with _seq_lock:  # a duplicate or older seq is a retry of something already stored: ignore it
        if batch.seq is not None and batch.seq <= last_seq.get(video_id, 0):
            return
        with open_db(request.app.state.settings) as conn, conn:
            if batch.events:
                db.add_events(conn, video_id, batch.events, commit=False)
            if batch.timeseries:
                db.add_timeseries(conn, video_id, batch.timeseries, commit=False)
            db.set_progress(conn, video_id, batch.frames_processed, commit=False)
        if batch.seq is not None:
            last_seq[video_id] = batch.seq
    for event in batch.events:
        jobs.publish(video_id, event_message(video_id, event))
    fps = 0.0
    if batch.progress is not None:
        jobs.set_live_state(video_id, batch.progress)
        jobs.publish(video_id, batch.progress)
        fps = float(batch.progress.get("processing_fps") or 0.0)
    live.heartbeat(video_id, batch.frames_processed, fps)


@router.post("/{video_id}/batch", status_code=204)
async def batch(video_id: VideoId, request: Request) -> Response:
    await run_in_threadpool(authorize, request, video_id)
    body = await parse(request, Batch)
    await run_in_threadpool(_store_batch, request, video_id, body)
    return Response(status_code=204)


@router.post("/{video_id}/preview", status_code=204)
async def preview(video_id: VideoId, request: Request) -> Response:
    await run_in_threadpool(authorize, request, video_id)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_PREVIEW_BYTES:
        raise HTTPException(413, "preview too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_PREVIEW_BYTES:
            raise HTTPException(413, "preview too large")
        chunks.append(chunk)
    request.app.state.jobs.preview.offer_jpeg(video_id, b"".join(chunks))
    return Response(status_code=204)


@router.post("/{video_id}/heartbeat", status_code=204)
async def heartbeat(video_id: VideoId, request: Request) -> Response:
    await run_in_threadpool(authorize, request, video_id)
    body = await parse(request, Heartbeat)
    request.app.state.live.heartbeat(video_id, body.frames_processed, body.processing_fps)
    return Response(status_code=204)


def _store_final(request: Request, video_id: int, body: Final) -> None:
    jobs = request.app.state.jobs
    with open_db(request.app.state.settings) as conn:
        with conn:  # the report and the final status are stored together or not at all
            if body.report is not None:
                db.set_report(conn, video_id, body.report, commit=False)
            db.set_status(conn, video_id, body.status, body.error, commit=False)
        record = db.get_video(conn, video_id)
    if record:
        jobs.publish(video_id, status_message(video_id, record))
    jobs.clear_live_state(video_id)
    jobs.preview.drop(video_id)
    with _seq_lock:
        request.app.state.ingest_seq.pop(video_id, None)
    request.app.state.live.finished(video_id)


@router.post("/{video_id}/final", status_code=204)
async def final(video_id: VideoId, request: Request) -> Response:
    await run_in_threadpool(authorize, request, video_id)
    body = await parse(request, Final)
    await run_in_threadpool(_store_final, request, video_id, body)
    return Response(status_code=204)
