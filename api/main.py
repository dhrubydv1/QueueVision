"""FastAPI app: upload, results, video streaming and live WebSocket for QueueVision."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi import Path as PathParam
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.routing import APIRoute
from starlette.websockets import WebSocketDisconnect

from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from ai_engine.video.video_loader import SUPPORTED_VIDEO_EXTENSIONS
from api import db
from api.jobs import (
    FINAL_STATUSES,
    SCHEMA_VERSION,
    JobManager,
    TrackerFactory,
    default_tracker_factory,
    status_message,
)
from api.models import Alert, CameraInfo, Health, Run, TimeseriesRow, Wait
from api.preview import MJPEG_BOUNDARY, mjpeg_part
from api.settings import ApiSettings
from api.upload_validation import UploadRejected, sanitize_filename, validate_upload

LOGGER = logging.getLogger("queuevision.api")
CHUNK_BYTES = 1024 * 1024
MAX_VIDEO_ID = 2**63 - 1  # SQLite INTEGER is a signed 64-bit value; larger ids would raise OverflowError
VideoId = Annotated[int, PathParam(ge=1, le=MAX_VIDEO_ID)]
PART_SUFFIX = ".part"
WS_NOT_FOUND = 4404
MJPEG_POLL_S = 0.1
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


@contextlib.contextmanager
def _open(settings: ApiSettings) -> Iterator:
    """A connection for the current thread only (routes are plain ``def`` run in the threadpool)."""
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


DASHBOARD_NOT_BUILT_HTML = (
    "<!doctype html><meta charset=utf-8><title>QueueVision</title>"
    "<p>Dashboard not built. Run: cd dashboard &amp;&amp; npm ci &amp;&amp; npm run build "
    '&mdash; then reload. The old test page is at <a href="/legacy">/legacy</a>.</p>'
)


def _safe_file(root: Path, relative: str) -> Path | None:
    """The regular file at ``root/relative``, or None if missing or resolving outside ``root``."""
    try:
        resolved = (root / relative.lstrip("/")).resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            return resolved
    except (OSError, ValueError):
        pass
    return None


def _contained(settings: ApiSettings, name: str | None) -> Path | None:
    """The existing regular file ``name`` if it resolves under the data dir, else None (warns on escape)."""
    if not name:
        return None
    try:
        resolved = Path(name).resolve()
        if not resolved.is_relative_to(settings.data_dir.resolve()):
            LOGGER.warning("report path %s is outside the data directory; not serving it", name)
            return None
        return resolved if resolved.is_file() else None
    except (OSError, ValueError):
        return None


NO_CACHE = {"Cache-Control": "no-cache"}


def _media_type(path: Path) -> str | None:
    return "text/javascript" if path.suffix == ".js" else None


def create_app(settings: ApiSettings | None = None, tracker_factory: TrackerFactory | None = None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    factory = tracker_factory or default_tracker_factory(settings)
    jobs = JobManager(settings, factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        settings.outputs_dir.mkdir(parents=True, exist_ok=True)
        removed = remove_partial_uploads(settings.uploads_dir)
        LOGGER.info("Removed %d leftover partial upload file(s)", removed)
        with _open(settings) as conn:
            db.init_db(conn)
        jobs.start()
        try:
            yield
        finally:
            await run_in_threadpool(jobs.stop, 30.0)

    app = FastAPI(title="QueueVision API", lifespan=lifespan)
    app.state.settings = settings
    app.state.jobs = jobs

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # HTTPException and validation errors have their own handlers and never get here.
        LOGGER.error(
            "Unhandled error on %s %s",
            request.method,
            request.url.path,
            exc_info=(type(error), error, error.__traceback__),
        )
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    class UploadGuardRoute(APIRoute):
        """Refuse an upload on cheap header checks before FastAPI reads and spools the body."""

        def get_route_handler(self):
            handler = super().get_route_handler()

            async def guarded(request: Request):
                if jobs.queue_depth >= settings.max_queued_jobs:
                    return JSONResponse({"detail": "too many queued runs; try again later"}, status_code=429)
                declared = request.headers.get("content-length")
                if (
                    declared
                    and declared.isdigit()
                    and int(declared) > settings.max_upload_bytes + CHUNK_BYTES
                ):
                    return JSONResponse({"detail": "file too large"}, status_code=413)
                return await handler(request)

            return guarded

    upload_router = APIRouter(route_class=UploadGuardRoute)

    def get_record(video_id: int) -> dict:
        with _open(settings) as conn:
            record = db.get_video(conn, video_id)
        if record is None:
            raise HTTPException(404, f"video {video_id} not found")
        return record

    # --- meta ---------------------------------------------------------------

    @app.get("/api/health", response_model=Health)
    def health() -> dict:
        return {
            "status": "ok",
            "device": jobs.loaded_device,
            "configured_device": settings.device,
            "model": settings.model,
            "queue_depth": jobs.queue_depth,
        }

    @app.get("/api/cameras", response_model=list[CameraInfo])
    def cameras() -> list[dict]:
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

    # --- upload and runs ----------------------------------------------------

    @upload_router.post("/api/videos", response_model=Run, status_code=202)
    def upload(
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
        with _open(settings) as conn:
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

    @app.get("/api/videos", response_model=list[Run])
    def list_runs() -> list[dict]:
        with _open(settings) as conn:
            return [run_record(r) for r in db.list_videos(conn)]

    @app.get("/api/videos/{video_id}", response_model=Run)
    def get_run(video_id: VideoId) -> dict:
        return run_record(get_record(video_id))

    @app.get("/api/videos/{video_id}/events")
    def events(
        video_id: VideoId,
        type: str | None = None,
        limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict]:
        get_record(video_id)
        with _open(settings) as conn:
            return db.list_events(conn, video_id, type, limit, offset)

    @app.get("/api/videos/{video_id}/waits", response_model=list[Wait])
    def waits(video_id: VideoId) -> list[dict]:
        get_record(video_id)
        with _open(settings) as conn:
            return db.list_waits(conn, video_id)

    @app.get("/api/videos/{video_id}/alerts", response_model=list[Alert])
    def alerts(video_id: VideoId) -> list[dict]:
        get_record(video_id)
        with _open(settings) as conn:
            return db.list_alerts(conn, video_id)

    @app.get("/api/videos/{video_id}/timeseries", response_model=list[TimeseriesRow])
    def timeseries(video_id: VideoId) -> list[dict]:
        get_record(video_id)
        with _open(settings) as conn:
            return db.list_timeseries(conn, video_id)

    @app.get("/api/videos/{video_id}/report")
    def report(video_id: VideoId) -> dict:
        record = get_record(video_id)
        if record["report"] is None:
            raise HTTPException(404, "no report yet")
        return record["report"]

    @app.get("/api/videos/{video_id}/video")
    def video(video_id: VideoId) -> FileResponse:
        record = get_record(video_id)
        name = ((record["report"] or {}).get("outputs") or {}).get("video")
        path = _contained(settings, name)
        if record["status"] != "completed" or path is None:
            raise HTTPException(404, "annotated video not available")
        return FileResponse(path, media_type="video/mp4")  # Starlette answers Range requests with 206

    @app.get("/api/videos/{video_id}/timeseries.csv")
    def timeseries_csv(video_id: VideoId) -> FileResponse:
        record = get_record(video_id)
        name = ((record["report"] or {}).get("outputs") or {}).get("timeseries")
        path = _contained(settings, name)
        if path is None:
            raise HTTPException(404, "time series not available")
        return FileResponse(path, media_type="text/csv", filename=f"run{video_id}_timeseries.csv")

    def first_preview(video_id: int) -> tuple[int, bytes]:
        record = get_record(video_id)
        if record["status"] != "processing":
            raise HTTPException(404, "no preview: run is not processing")
        latest = jobs.preview.latest(video_id)
        if latest is None:
            raise HTTPException(404, "no preview frame yet")
        return latest

    @app.get("/api/videos/{video_id}/preview.jpg")
    def preview_jpg(video_id: VideoId) -> Response:
        _, jpeg = first_preview(video_id)
        return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/videos/{video_id}/preview.mjpg")
    def preview_mjpg(video_id: VideoId, request: Request) -> StreamingResponse:
        sequence, jpeg = first_preview(video_id)

        async def parts():
            last, current = -1, (sequence, jpeg)
            while True:
                if current is not None and current[0] != last:
                    last = current[0]
                    yield mjpeg_part(current[1])
                await asyncio.sleep(MJPEG_POLL_S)
                if jobs.stopping or await request.is_disconnected():
                    return
                current = jobs.preview.latest(video_id)
                if current is None:  # the run ended: the store is dropped when processing stops
                    return

        return StreamingResponse(
            parts(),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/videos/{video_id}/cancel", response_model=Run)
    def cancel(video_id: VideoId) -> dict:
        get_record(video_id)
        if not jobs.cancel(video_id):
            raise HTTPException(409, "run is not queued or processing")
        return run_record(get_record(video_id))

    @app.delete("/api/videos/{video_id}", status_code=204)
    def delete(video_id: VideoId) -> None:
        record = get_record(video_id)
        if record["status"] == "processing":
            raise HTTPException(409, "run is processing; cancel it first")
        if record["status"] == "queued":
            jobs.cancel(video_id)
            if jobs.is_active(video_id) or get_record(video_id)["status"] == "processing":
                raise HTTPException(409, "run is processing; cancel it first")
        with _open(settings) as conn:
            db.delete_video(conn, video_id)
        for folder in (settings.uploads_dir, settings.outputs_dir):
            shutil.rmtree(folder / str(video_id), ignore_errors=True)

    # --- WebSocket ----------------------------------------------------------

    @app.websocket("/ws/videos/{video_id}")
    async def live(websocket: WebSocket, video_id: VideoId) -> None:
        origin = websocket.headers.get("origin")
        if origin is not None and not settings.origin_allowed(origin):
            await websocket.close(code=1008)  # policy violation: refused before the handshake completes
            return
        await websocket.accept()
        # Subscribe first so no status message slips between the snapshot read and the forwarding.
        queue, unsubscribe = jobs.subscribe(video_id, asyncio.get_running_loop())
        receiver: asyncio.Task | None = None
        try:
            record = await run_in_threadpool(_find, settings, video_id)
            if record is None:
                await websocket.close(code=WS_NOT_FOUND)
                return
            await websocket.send_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "type": "snapshot",
                    "video_id": video_id,
                    "run": run_record(record),
                    "progress": jobs.live_state(video_id),
                }
            )
            if record["status"] in FINAL_STATUSES:
                await websocket.send_json(_status(record))
                await websocket.close()
                return
            receiver = asyncio.ensure_future(websocket.receive())  # notices the client leaving
            while True:
                getter = asyncio.ensure_future(queue.get())
                await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()
                    return  # client disconnected
                message = getter.result()
                if message["type"] == "status":
                    message = {**message, "percent": _status_percent(message)}
                await websocket.send_json(message)
                if message["type"] == "status" and message["status"] in FINAL_STATUSES:
                    await websocket.close()
                    return
        except (WebSocketDisconnect, RuntimeError):
            return
        finally:
            unsubscribe()
            if receiver is not None and not receiver.done():
                receiver.cancel()

    @app.get("/api/accuracy")
    def accuracy() -> JSONResponse:
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

    app.include_router(upload_router)

    from fastapi.staticfiles import StaticFiles

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/legacy", StaticFiles(directory=static_dir, html=True), name="legacy")

    @app.get("/legacy", include_in_schema=False)
    @app.head("/legacy", include_in_schema=False)
    def legacy_redirect(request: Request) -> RedirectResponse:
        query = request.url.query
        return RedirectResponse("/legacy/" + (f"?{query}" if query else ""), status_code=307)

    dashboard_dir = settings.dashboard_dir

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def dashboard(path: str) -> Response:
        if path in ("api", "ws") or path.startswith(("api/", "ws/")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if not dashboard_dir.is_dir():
            return HTMLResponse(DASHBOARD_NOT_BUILT_HTML, headers=NO_CACHE)
        root = dashboard_dir.resolve()
        candidates = (path, f"{path}.html", f"{path}/index.html") if path else ("index.html",)
        for candidate in candidates:
            found = _safe_file(root, candidate)
            if found is not None:
                headers = NO_CACHE if found.suffix == ".html" else None
                return FileResponse(found, media_type=_media_type(found), headers=headers)
        not_found = _safe_file(root, "404.html")
        if not_found is not None:
            return FileResponse(not_found, status_code=404, media_type="text/html", headers=NO_CACHE)
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    return app


def remove_partial_uploads(uploads_dir: Path) -> int:
    """Delete leftover ``*.part`` files (interrupted uploads) under ``uploads_dir``; nothing else."""
    removed = 0
    for path in uploads_dir.glob(f"*/*{PART_SUFFIX}"):
        if path.is_file() and not path.is_symlink():
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


def _find(settings: ApiSettings, video_id: int) -> dict | None:
    with _open(settings) as conn:
        return db.get_video(conn, video_id)


def _status_percent(message: dict) -> float | None:
    total = message.get("frames_total")
    if message["status"] == "completed":
        return 100.0
    if not total:
        return None
    return min(100.0, round(100.0 * (message.get("frames_processed") or 0) / total, 1))


def _status(record: dict) -> dict:
    message = status_message(record["video_id"], record)
    return {**message, "percent": _percent(record)}


app = create_app()
