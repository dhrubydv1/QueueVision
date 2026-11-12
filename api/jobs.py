"""Background job manager: one worker thread runs the engine on uploaded videos.

The worker owns its SQLite connection, persists results as it goes and fans live
messages out to asyncio subscribers (WebSocket handlers) via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import cv2

from ai_engine import config
from ai_engine.queue.camera_config import load_camera_config
from ai_engine.run_tracking import FrameUpdate, RunOptions, process_video
from api import db
from api.preview import PreviewStore
from api.settings import ApiSettings

LOGGER = logging.getLogger("queuevision.api")

SCHEMA_VERSION = "1.0"
SUBSCRIBER_QUEUE_SIZE = 256  # progress backlog bound; event/status messages are never dropped
FINAL_STATUSES = ("completed", "failed", "cancelled")

TrackerFactory = Callable[[], Any]


def default_tracker_factory(settings: ApiSettings) -> TrackerFactory:
    """Factory of real trackers; the detector is loaded once, on the first call, and reused."""
    detector = None
    lock = threading.Lock()

    def factory() -> Any:
        nonlocal detector
        from ai_engine.detection.detector import PersonDetector
        from ai_engine.tracking.tracker import PersonTracker, TrackerSettings

        with lock:
            if detector is None:
                detector = PersonDetector(settings.model, device=settings.device)
        return PersonTracker(detector, TrackerSettings(new_track_confidence=config.CONFIDENCE))

    def loaded_device() -> str | None:
        """Device the detector actually runs on; None until the first job loaded it."""
        return None if detector is None else str(detector.device)

    factory.loaded_device = loaded_device  # type: ignore[attr-defined]
    return factory


# --- messages ----------------------------------------------------------------


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def progress_message(video_id: int, update: FrameUpdate) -> dict[str, Any]:
    """Live numbers for one frame (sent about once per video second)."""
    queue = update.queue
    total = update.frames_total
    frames_done = update.frame_index + 1  # source frames, matching the overlay's frame number
    percent = min(100.0, round(100.0 * frames_done / total, 1)) if total else None
    message: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "type": "progress",
        "video_id": video_id,
        "frames_processed": frames_done,
        "frames_total": total,
        "percent": percent,
        "video_time_s": round(update.timestamp_s, 3),
        "people_in_frame": update.people_in_frame,
        "processing_fps": round(update.processing_fps, 2),
        "queue_length": None,
        "raw_queue_count": None,
        "service_rate_per_min": None,
        "service_rate_status": None,
        "predicted_wait_s": None,
        "predicted_wait_low_s": None,
        "predicted_wait_high_s": None,
        "prediction_status": None,
        "alert_active": False,
        "alert_reasons": [],
    }
    if queue is not None:
        message.update(
            queue_length=queue.queue_length,
            raw_queue_count=queue.raw_count,
            service_rate_per_min=_round(queue.service_rate.per_min, 3),
            service_rate_status=queue.service_rate.status,
            predicted_wait_s=_round(queue.prediction.predicted_wait_s, 1),
            predicted_wait_low_s=_round(queue.prediction.low_s, 1),
            predicted_wait_high_s=_round(queue.prediction.high_s, 1),
            prediction_status=queue.prediction.status,
            alert_active=queue.alert_active,
            alert_reasons=list(queue.alert_reasons),
        )
    return message


def event_message(video_id: int, event: dict) -> dict[str, Any]:
    """One queue event; ``event`` is the event's ``to_dict()`` payload, unchanged."""
    return {"schema_version": SCHEMA_VERSION, "type": "event", "video_id": video_id, "event": event}


def status_message(video_id: int, record: dict) -> dict[str, Any]:
    """Status change built from a ``videos`` record."""
    return {
        "schema_version": SCHEMA_VERSION,
        "type": "status",
        "video_id": video_id,
        "status": record["status"],
        "error": record.get("error"),
        "frames_processed": record.get("frames_processed"),
        "frames_total": record.get("frames_total"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
    }


def _timeseries_row(update: FrameUpdate) -> dict[str, Any]:
    """One time-series row; missing values are None (same fields and rounding as the CSV)."""
    queue = update.queue
    assert queue is not None
    rate, prediction = queue.service_rate, queue.prediction
    return {
        "time_s": round(update.timestamp_s, 3),
        "frame": update.frame_index,
        "people_in_frame": update.people_in_frame,
        "raw_queue_count": queue.raw_count,
        "queue_length": queue.queue_length,
        "service_rate_per_min": _round(rate.per_min, 3),
        "service_rate_status": rate.status,
        "predicted_wait_s": _round(prediction.predicted_wait_s, 1),
        "predicted_wait_low_s": _round(prediction.low_s, 1),
        "predicted_wait_high_s": _round(prediction.high_s, 1),
        "prediction_status": prediction.status,
    }


def _put(queue: asyncio.Queue, message: dict) -> None:
    """Runs on the subscriber's loop. Only ``progress`` is dropped, once the backlog is full."""
    if message["type"] == "progress" and queue.qsize() >= SUBSCRIBER_QUEUE_SIZE:
        return
    queue.put_nowait(message)


# --- manager -----------------------------------------------------------------


class JobManager:
    """Runs queued videos one at a time on a single worker thread."""

    def __init__(
        self,
        settings: ApiSettings,
        tracker_factory: TrackerFactory,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], object] | None = None,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._interrupt = threading.Event()  # wakes a real-time pacing wait on cancel/stop
        self._wait = wait if wait is not None else self._interrupt.wait
        self._factory = tracker_factory
        self.preview = PreviewStore()
        self._cond = threading.Condition()
        self._pending: deque[int] = deque()
        self._current: int | None = None
        self._cancel_current = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._thread: threading.Thread | None = None
        self._live: dict[int, dict] = {}
        self._subs_lock = threading.Lock()
        self._subs: dict[int, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}

    @property
    def loaded_device(self) -> str | None:
        """Device of the loaded detector, or None when the factory has not loaded one yet."""
        getter = getattr(self._factory, "loaded_device", None)
        return getter() if getter else None

    # lifecycle

    def start(self) -> None:
        """Start the worker; leftover queued/processing rows are failed first."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="queuevision-worker", daemon=True)
        self._thread.start()
        if not self._ready.wait(30.0):
            raise RuntimeError("job worker did not start")
        if self._start_error is not None:
            self._thread = None
            raise self._start_error

    def stop(self, timeout: float = 30.0) -> None:
        """Ask the worker to stop; the current job ends ``cancelled``."""
        self._stop.set()
        self._interrupt.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)

    # queue control

    def submit(self, video_id: int) -> None:
        with self._cond:
            self._pending.append(video_id)
            self._cond.notify_all()

    def cancel(self, video_id: int) -> bool:
        """Remove a queued job (``cancelled``) or stop the running one; False when not active."""
        with self._cond:
            if video_id in self._pending:
                self._pending.remove(video_id)
                self._cond.notify_all()
                queued = True
            elif self._current == video_id:
                self._cancel_current.set()
                self._interrupt.set()
                return True
            else:
                return False
        if queued:
            conn = db.connect(self.settings.db_path)
            try:
                db.set_status(conn, video_id, "cancelled")
                record = db.get_video(conn, video_id)
            finally:
                conn.close()
            if record:
                self._publish(video_id, status_message(video_id, record))
        return True

    @property
    def stopping(self) -> bool:
        """True once ``stop()`` was called."""
        return self._stop.is_set()

    def is_active(self, video_id: int) -> bool:
        with self._cond:
            return self._current == video_id or video_id in self._pending

    @property
    def queue_depth(self) -> int:
        with self._cond:
            return len(self._pending)

    def live_state(self, video_id: int) -> dict | None:
        """The latest progress message body for a video, if any."""
        return self._live.get(video_id)

    def wait_idle(self, timeout: float) -> bool:
        """True once nothing is queued and nothing is processing (for tests)."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._pending and self._current is None, timeout)

    # subscriptions

    def subscribe(
        self, video_id: int, loop: asyncio.AbstractEventLoop
    ) -> tuple[asyncio.Queue, Callable[[], None]]:
        queue: asyncio.Queue = asyncio.Queue()
        entry = (loop, queue)
        with self._subs_lock:
            self._subs.setdefault(video_id, []).append(entry)

        def unsubscribe() -> None:
            with self._subs_lock:
                entries = self._subs.get(video_id, [])
                if entry in entries:
                    entries.remove(entry)
                if not entries:
                    self._subs.pop(video_id, None)

        return queue, unsubscribe

    def _publish(self, video_id: int, message: dict) -> None:
        with self._subs_lock:
            entries = list(self._subs.get(video_id, ()))
        for loop, queue in entries:
            with contextlib.suppress(RuntimeError):  # loop already closed
                loop.call_soon_threadsafe(_put, queue, message)

    # worker

    def _run(self) -> None:
        conn = None
        try:
            conn = db.connect(self.settings.db_path)
            db.init_db(conn)
            db.mark_interrupted(conn)
            self._ready.set()
            while True:
                with self._cond:
                    self._cond.wait_for(lambda: self._pending or self._stop.is_set())
                    if self._stop.is_set():
                        return
                    self._current = self._pending.popleft()
                    self._cancel_current.clear()
                    self._interrupt.clear()
                video_id = self._current
                try:
                    self._process(conn, video_id)
                except Exception as error:
                    LOGGER.exception("Job %s failed", video_id)
                    try:
                        self._finish(
                            conn, video_id, "failed", f"processing failed ({type(error).__name__})"
                        )  # details stay in the log
                    except Exception:
                        LOGGER.exception("Could not record failure of job %s", video_id)
                finally:
                    with self._cond:
                        self._current = None
                        self._cond.notify_all()
        except BaseException as error:
            if not self._ready.is_set():
                self._start_error = error
            else:
                LOGGER.exception("Job worker stopped unexpectedly")
        finally:
            self._ready.set()
            if conn is not None:
                conn.close()

    def _finish(
        self, conn, video_id: int, status: str, error: str | None = None, report: dict | None = None
    ) -> None:
        with conn:  # the report and the final status are stored together or not at all
            if report is not None:
                db.set_report(conn, video_id, report, commit=False)
            db.set_status(conn, video_id, status, error, commit=False)
        record = db.get_video(conn, video_id)
        if record:
            self._publish(video_id, status_message(video_id, record))

    def _should_stop(self) -> bool:
        return self._stop.is_set() or self._cancel_current.is_set()

    def _process(self, conn, video_id: int) -> None:
        record = db.get_video(conn, video_id)
        if record is None or record["status"] != "queued":
            return  # deleted or already cancelled
        self._finish(conn, video_id, "processing")
        options = record["options"]
        stride = int(options.get("frame_stride", 1))
        source = next(self.settings.uploads_dir.joinpath(str(video_id)).glob(f"video{video_id}.*"), None)
        if source is None:
            raise FileNotFoundError(f"upload file for video {video_id} not found")
        camera = load_camera_config(self.settings.camera_dir / f"{record['camera_id']}.yaml")
        output_dir = self.settings.outputs_dir / str(video_id)
        capture = cv2.VideoCapture(str(source))
        try:
            if not capture.isOpened():
                raise ValueError("could not open video file")
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finally:
            capture.release()
        db.set_media(conn, video_id, fps=fps, frames_total=count or None, output_dir=str(output_dir))
        tracker = self._factory()

        events: list[dict] = []
        rows: list[dict] = []
        state = {"frames": 0, "next_second": 0}  # frames: source frames done
        realtime = bool(options.get("realtime", False))
        pacing: dict[str, float] = {}  # "start": clock reading at video time 0

        def flush() -> None:
            with conn:  # one transaction for the whole batch
                if events:
                    db.add_events(conn, video_id, events, commit=False)
                if rows:
                    db.add_timeseries(conn, video_id, rows, commit=False)
                db.set_progress(conn, video_id, state["frames"], commit=False)
            events.clear()
            rows.clear()

        def on_frame(update: FrameUpdate) -> None:
            state["frames"] = update.frame_index + 1
            if update.image is not None:
                self.preview.offer(video_id, update.image, update.people, self._clock())
            queue = update.queue
            if queue is not None:
                for event in queue.events:
                    payload = event.to_dict()
                    events.append(payload)
                    self._publish(video_id, event_message(video_id, payload))
            if update.timestamp_s + 1e-9 >= state["next_second"]:
                state["next_second"] = math.floor(update.timestamp_s + 1e-9) + 1
                if queue is not None:
                    rows.append(_timeseries_row(update))
                flush()
                message = progress_message(video_id, update)
                self._live[video_id] = message
                self._publish(video_id, message)
            if realtime:  # pace to video time; a late run never waits, so lag does not build up
                if "start" not in pacing:
                    pacing["start"] = self._clock() - update.timestamp_s
                delay = pacing["start"] + update.timestamp_s - self._clock()
                if delay > 0:
                    self._wait(delay)

        try:
            report = process_video(
                source,
                tracker,
                RunOptions(
                    output_dir=output_dir,
                    camera=camera,
                    frame_stride=stride,
                    max_frames=options.get("max_frames"),
                    blur_heads=bool(options.get("blur_heads", True)),
                ),
                on_frame=on_frame,
                should_stop=self._should_stop,
                frame_callback_wants_image=True,
            )
        finally:
            self.preview.drop(video_id)
        flush()
        stopped = bool(report.get("stopped_early"))  # a cancel after the last frame leaves the run completed
        self._finish(conn, video_id, "cancelled" if stopped else "completed", report=report)
