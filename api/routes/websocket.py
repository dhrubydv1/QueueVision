"""Live run updates over WebSocket."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket
from fastapi.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from api import db
from api.deps import JobsDep, SettingsDep, open_db, origin_allowed
from api.jobs import FINAL_STATUSES, SCHEMA_VERSION, status_message
from api.routes.videos import VideoId, _percent, run_record
from api.settings import ApiSettings

WS_NOT_FOUND = 4404
router = APIRouter()


def _find(settings: ApiSettings, video_id: int) -> dict | None:
    with open_db(settings) as conn:
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


@router.websocket("/ws/videos/{video_id}")
async def live(websocket: WebSocket, video_id: VideoId, settings: SettingsDep, jobs: JobsDep) -> None:
    if not origin_allowed(settings, websocket.headers.get("origin")):
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
