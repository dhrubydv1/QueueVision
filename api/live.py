"""Live supervisor: spawns, watches and stops one worker process per live camera.

At most one session runs at a time (one model job at a time). A session holds the ``JobManager``
upload queue from start to end; every end path releases that hold exactly once. The ingest token is
per run, lives only in this process's memory and in the child's environment, and is compared with
``hmac.compare_digest``. No RTSP URL passes through here: the worker reads it from its own environment.
"""

from __future__ import annotations

import contextlib
import hmac
import logging
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ai_engine.config import PROJECT_ROOT
from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from api import db
from api.jobs import JobManager, status_message
from api.settings import ApiSettings

LOGGER = logging.getLogger("queuevision.api")

TOKEN_ENV = "QUEUEVISION_WORKER_TOKEN"
STARTUP_GRACE_S = 60.0  # time allowed from spawn to the first heartbeat (the model loads meanwhile)
RETIRED_KILL_AFTER_S = 5.0  # a worker that sent its final but has not exited yet
_CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class LiveStartError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class _Session:
    video_id: int
    camera_id: str
    source_kind: str
    token: str
    proc: subprocess.Popen
    started_at: str | None
    started: float  # supervisor clock at spawn
    last_beat: float | None = None
    frames: int = 0
    streaming: bool = False  # a heartbeat or batch has reported frames: the startup grace is over
    fps: float = 0.0
    stopping: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    beat: threading.Event = field(default_factory=threading.Event)


class LiveSupervisor:
    def __init__(
        self,
        settings: ApiSettings,
        jobs: JobManager,
        worker_cmd: Sequence[str] = (sys.executable, "-m", "worker"),
        api_url: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_timeout_s: float = 10.0,
        stop_timeout_s: float = 15.0,
        startup_grace_s: float = STARTUP_GRACE_S,
        watchdog_interval_s: float = 1.0,
    ) -> None:
        self.settings = settings
        self.jobs = jobs
        self._cmd = tuple(worker_cmd)
        self._api_url = api_url or f"http://127.0.0.1:{settings.port}"
        self._clock = clock
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._stop_timeout_s = stop_timeout_s
        self._startup_grace_s = startup_grace_s
        self._interval = watchdog_interval_s
        self._lock = threading.RLock()
        self._sessions: dict[int, _Session] = {}
        self._retired: list[tuple[subprocess.Popen, float]] = []
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._app_state = None
        self._closing = False  # shutdown began: the upload hold is never released again
        self.spawned: list[subprocess.Popen] = []  # every worker started (their ``args`` hold no secret)

    # --- start ---------------------------------------------------------------------------

    def start(self, camera_id: str) -> int:
        with self._lock:
            live = self._validate(camera_id)
            if self._sessions:
                raise LiveStartError(409, "already_live")
            if live.source == "rtsp" and not os.environ.get(live.rtsp_url_env or ""):
                raise LiveStartError(409, "rtsp_env_unset")
            if not self.jobs.hold_uploads():
                raise LiveStartError(409, "upload_processing")
            try:
                return self._spawn(camera_id, live)
            except BaseException as error:  # the hold was taken: every failure after it gives it back
                self.jobs.release_uploads()
                if isinstance(error, LiveStartError):
                    raise
                LOGGER.error("Could not start live run for camera %s (%s)", camera_id, type(error).__name__)
                if not isinstance(error, Exception):
                    raise
                raise LiveStartError(500, "spawn_failed") from error

    def _validate(self, camera_id: str):
        if not _CAMERA_ID.match(camera_id):
            raise LiveStartError(404, "unknown_camera")
        path = self.settings.camera_dir / f"{camera_id}.yaml"
        if not path.is_file():
            raise LiveStartError(404, "unknown_camera")
        try:
            camera = load_camera_config(path)
        except (CameraConfigError, OSError, ValueError, TypeError, AttributeError) as error:
            LOGGER.warning("Live start refused, invalid camera config %s: %s", path.name, error)
            raise LiveStartError(409, "invalid_camera") from error
        if camera.live is None:
            raise LiveStartError(409, "no_live_settings")
        return camera.live

    def _spawn(self, camera_id: str, live) -> int:
        options = {
            "realtime": True,
            "frame_stride": 1,
            "blur_heads": False,
            "privacy_mode": None,
            "preview_privacy_mode": live.preview_privacy_mode,
            "max_frames": None,
            "process_fps": live.process_fps,
        }
        conn = db.connect(self.settings.db_path)
        try:
            video_id = db.create_video(
                conn, f"live:{camera_id}", camera_id, options, kind="live", source_kind=live.source
            )
            try:
                db.set_status(conn, video_id, "processing")
                out = self.settings.outputs_dir / str(video_id)
                out.mkdir(parents=True, exist_ok=True)
                token = secrets.token_urlsafe(32)
                args = [
                    *self._cmd,
                    "--video-id",
                    str(video_id),
                    "--camera",
                    camera_id,
                    "--api",
                    self._api_url,
                    "--output-dir",
                    str(out),
                ]
                with open(out / "worker.log", "ab") as log:
                    proc = subprocess.Popen(
                        args,
                        env={**os.environ, TOKEN_ENV: token},
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        cwd=PROJECT_ROOT,
                        start_new_session=True,  # a terminal Ctrl-C reaches the API only
                    )
            except BaseException:
                db.set_status(conn, video_id, "failed", "worker_start_failed")
                self._publish_status(conn, video_id)
                raise
            record = db.get_video(conn, video_id)
        finally:
            conn.close()
        self.spawned.append(proc)
        self._sessions[video_id] = _Session(
            video_id=video_id,
            camera_id=camera_id,
            source_kind=live.source,
            token=token,
            proc=proc,
            started_at=record["started_at"] if record else None,
            started=self._clock(),
        )
        self._ensure_watchdog()
        return video_id

    # --- ingest callbacks ------------------------------------------------------------------

    def verify(self, video_id: int, token: str) -> bool:
        with self._lock:
            session = self._sessions.get(video_id)
            expected = session.token if session else ""
        ok = hmac.compare_digest(token.encode("utf-8", "replace"), expected.encode("utf-8"))
        return ok and session is not None

    def heartbeat(self, video_id: int, frames_processed: int, processing_fps: float) -> None:
        with self._lock:
            session = self._sessions.get(video_id)
            if session is None:
                return
            session.last_beat = self._clock()
            session.frames = frames_processed
            if frames_processed > 0:
                session.streaming = True
            session.fps = processing_fps
            session.beat.set()

    def finished(self, video_id: int) -> None:
        """The worker's final message was stored: the session is over."""
        with self._lock:
            session = self._sessions.pop(video_id, None)
            if session is None:
                return
            self._release_uploads()
            self._retire(session.proc)
        session.finished.set()

    # --- queries ---------------------------------------------------------------------------

    def sessions(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            return [
                {
                    "video_id": s.video_id,
                    "camera_id": s.camera_id,
                    "source_kind": s.source_kind,
                    "started_at": s.started_at,
                    "heartbeat_age_s": None if s.last_beat is None else round(now - s.last_beat, 2),
                    "processing_fps": s.fps if s.last_beat is not None else None,
                }
                for s in self._sessions.values()
            ]

    # --- stop ------------------------------------------------------------------------------

    def stop(self, video_id: int) -> bool:
        """Stop a live run and wait for its final; False when it is not live (or already stopping)."""
        with self._lock:
            session = self._sessions.get(video_id)
            if session is None or session.stopping:
                return False
            session.stopping = True
        self._stop_session(session)
        return True

    def _stop_session(
        self, session: _Session, *, reason: str = "stop_timeout", beat_wait_s: float | None = None
    ) -> None:
        deadline = time.monotonic() + self._stop_timeout_s
        beat_deadline = deadline if beat_wait_s is None else min(deadline, time.monotonic() + beat_wait_s)
        # A worker that has not yet sent a heartbeat may not have its SIGTERM handler installed (it would
        # die without a final); the first heartbeat is sent right after it is. At shutdown the API may no
        # longer hear that heartbeat, so the wait there is short.
        while not session.beat.is_set() and session.proc.poll() is None and time.monotonic() < beat_deadline:
            session.beat.wait(0.05)
        if session.proc.poll() is None and not session.finished.is_set():
            with contextlib.suppress(ProcessLookupError, OSError):
                session.proc.send_signal(signal.SIGTERM)
        while not session.finished.is_set() and session.proc.poll() is None and time.monotonic() < deadline:
            session.finished.wait(0.05)
        if session.finished.is_set():
            return
        if session.proc.poll() is not None:  # exited without a final (a late final is already stored)
            session.finished.wait(0.2)
            if session.finished.is_set():
                return
            self._end(session, "worker_exited")
        else:
            self._end(session, reason)

    # --- supervisor-initiated ends ------------------------------------------------------------

    def _end(self, session: _Session, error: str) -> None:
        """Fail a run the supervisor ends itself, and do all the cleanup the ingest final would do."""
        with self._lock:
            if self._sessions.get(session.video_id) is not session:
                return  # the final arrived first
            del self._sessions[session.video_id]
            try:
                if session.proc.poll() is None:
                    session.proc.kill()
                with contextlib.suppress(Exception):
                    session.proc.wait(timeout=5)
            except OSError:
                pass
            try:
                conn = db.connect(self.settings.db_path)
                try:
                    record = db.get_video(conn, session.video_id)
                    if record is not None and record["status"] == "processing":
                        db.set_status(conn, session.video_id, "failed", error)
                    self._publish_status(conn, session.video_id)
                finally:
                    conn.close()
            except Exception:
                LOGGER.exception("Could not record the end of live run %s", session.video_id)
            ingest_seq = getattr(self._app_state, "ingest_seq", None) if self._app_state else None
            if ingest_seq is not None:
                ingest_seq.pop(session.video_id, None)
            self.jobs.clear_live_state(session.video_id)
            self.jobs.preview.drop(session.video_id)
            self._release_uploads()
        session.finished.set()

    def _release_uploads(self) -> None:
        """Give queued uploads back, except at shutdown: they must not start loading a model then
        (leftover queued uploads are failed as interrupted at the next start)."""
        if not self._closing:
            self.jobs.release_uploads()

    def attach(self, state) -> None:
        """Give the supervisor the app's ``state`` (it pops ``ingest_seq`` entries of runs it ends)."""
        self._app_state = state

    def _publish_status(self, conn, video_id: int) -> None:
        record = db.get_video(conn, video_id)
        if record:
            self.jobs.publish(video_id, status_message(video_id, record))

    # --- watchdog --------------------------------------------------------------------------

    def _ensure_watchdog(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._halt.clear()
            self._thread = threading.Thread(target=self._watch, name="queuevision-live-watchdog", daemon=True)
            self._thread.start()

    def _watch(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                self.check()
            except Exception:
                LOGGER.exception("live watchdog error")

    def check(self) -> None:
        """One watchdog pass (also called directly by tests)."""
        now = self._clock()
        with self._lock:
            sessions = list(self._sessions.values())
            retired, self._retired = self._retired, []
        for proc, since in retired:
            if proc.poll() is not None:
                continue
            if now - since >= RETIRED_KILL_AFTER_S:
                with contextlib.suppress(Exception):
                    proc.kill()
                    proc.wait(timeout=5)
            else:
                with self._lock:
                    self._retired.append((proc, since))
        for session in sessions:
            if session.stopping:
                continue  # stop() owns the end of this session
            if session.proc.poll() is not None:
                self._end(session, "worker_exited")
                continue
            if not session.streaming:  # model load and source open beat nothing: 60 s from start
                silent_for = now - session.started - self._startup_grace_s
            else:
                silent_for = now - session.last_beat - self._heartbeat_timeout_s
            if silent_for >= 0 and not session.stopping:
                self._end(session, "no_heartbeat")

    def _retire(self, proc: subprocess.Popen) -> None:
        self._retired.append((proc, self._clock()))

    # --- shutdown --------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop every session and reap every worker. Safe to call twice."""
        with self._lock:
            self._closing = True
            sessions = list(self._sessions.values())
            for session in sessions:
                session.stopping = True
        threads = [
            threading.Thread(
                target=self._stop_session, args=(s,), kwargs={"reason": "server_shutdown", "beat_wait_s": 2.0}
            )
            for s in sessions
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(self._stop_timeout_s + 10)
        self._halt.set()
        if self._thread is not None:
            self._thread.join(5)
        with self._lock:
            retired, self._retired = self._retired, []
        for proc in [p for p, _ in retired] + [p for p in self.spawned if p.poll() is None]:
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.wait(timeout=2)
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.kill()
                    proc.wait(timeout=5)
