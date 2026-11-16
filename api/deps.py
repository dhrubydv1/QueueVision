"""Request-scoped accessors for app state; nothing here holds module-level app state."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from api import db
from api.jobs import JobManager
from api.settings import ApiSettings

LOGGER = logging.getLogger("queuevision.api")


async def get_settings(conn: HTTPConnection) -> ApiSettings:
    return conn.app.state.settings


async def get_jobs(conn: HTTPConnection) -> JobManager:
    return conn.app.state.jobs


async def get_preview(conn: HTTPConnection):
    return conn.app.state.jobs.preview


SettingsDep = Annotated[ApiSettings, Depends(get_settings)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]


@contextlib.contextmanager
def open_db(settings: ApiSettings) -> Iterator:
    """A connection for the current thread only (routes are plain ``def`` run in the threadpool)."""
    conn = db.connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def origin_allowed(settings: ApiSettings, origin: str | None) -> bool:
    """True when the request has no Origin header or the origin is allowed."""
    return origin is None or settings.origin_allowed(origin)


def contained_path(settings: ApiSettings, name: str | None) -> Path | None:
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


def get_record(settings: ApiSettings, video_id: int) -> dict:
    with open_db(settings) as conn:
        record = db.get_video(conn, video_id)
    if record is None:
        raise HTTPException(404, f"video {video_id} not found")
    return record
