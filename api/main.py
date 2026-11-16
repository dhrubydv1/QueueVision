"""FastAPI app: upload, results, video streaming and live WebSocket for QueueVision."""

from __future__ import annotations

import contextlib
import logging
import os  # noqa: F401  (tests patch ``api.main.os.replace``)
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from api import db
from api.deps import open_db
from api.jobs import JobManager, TrackerFactory, default_tracker_factory
from api.routes import accuracy, cameras, health, preview, static, videos, websocket
from api.routes.videos import MAX_VIDEO_ID, PART_SUFFIX, run_record  # noqa: F401  (re-exported)
from api.settings import ApiSettings

LOGGER = logging.getLogger("queuevision.api")


def remove_partial_uploads(uploads_dir: Path) -> int:
    """Delete leftover ``*.part`` files (interrupted uploads) under ``uploads_dir``; nothing else."""
    removed = 0
    for path in uploads_dir.glob(f"*/*{PART_SUFFIX}"):
        if path.is_file() and not path.is_symlink():
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


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
        with open_db(settings) as conn:
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

    # Order matters: specific /api and /ws routes first, then the /legacy mount and
    # redirect, and the dashboard catch-all last.
    app.include_router(health.router)
    app.include_router(cameras.router)
    app.include_router(videos.router)
    app.include_router(preview.router)
    app.include_router(videos.mutation_router)
    app.include_router(websocket.router)
    app.include_router(accuracy.router)
    app.include_router(videos.upload_router)
    static.mount_legacy(app)
    app.include_router(static.router)
    return app


app = create_app()
