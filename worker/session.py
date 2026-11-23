"""Glue between ``run_live`` and the ingest client: one live camera, from startup to the final message.

Exit codes: 0 ok (``completed`` sent), 1 failed (``failed`` sent), 2 no worker token, 3 the API was
unreachable for more than 15 s (no final message). The only text that leaves this process about a
failure is a fixed reason string; logged text has any ``rtsp://`` URL redacted.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import signal
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import cv2

from ai_engine import config
from ai_engine.live.capture import LiveSourceError, open_source, quiet_opencv_logging
from ai_engine.live.loop import LiveOptions, run_live
from ai_engine.queue.camera_config import load_camera_config
from ai_engine.run_messages import event_message, progress_message, timeseries_row
from ai_engine.run_tracking import FrameUpdate
from worker.client import IngestClient

LOGGER = logging.getLogger("worker")

TOKEN_ENV = "QUEUEVISION_WORKER_TOKEN"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
UNREACHABLE_AFTER_S = 15.0
BATCH_EVERY_S = 1.0
HEARTBEAT_AFTER_S = 2.0
FINAL_ATTEMPTS = 3
FINAL_RETRY_S = 0.5
PREVIEW_EVERY_S = 0.2
PREVIEW_QUALITY = 80
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
ENGINE_ERROR = "engine_error"

_RTSP_URL = re.compile(r"rtsps?://\S+", re.IGNORECASE)


def redact(text: str) -> str:
    """Replace any ``rtsp://...`` (up to whitespace) with ``rtsp://[redacted]``."""
    return _RTSP_URL.sub("rtsp://[redacted]", text)


def default_tracker_factory(env: Mapping[str, str]) -> Callable[[], Any]:
    """Real tracker, built as ``api.jobs.default_tracker_factory`` builds it (no ``api`` import)."""

    def factory() -> Any:
        from ai_engine.detection.detector import PersonDetector
        from ai_engine.tracking.tracker import PersonTracker, TrackerSettings

        model = env.get("QUEUEVISION_MODEL") or str(config.DEFAULT_MODEL_PATH)
        device = env.get("QUEUEVISION_DEVICE") or config.DEFAULT_DEVICE
        detector = PersonDetector(model, device=device)
        return PersonTracker(detector, TrackerSettings(new_track_confidence=config.CONFIDENCE))

    return factory


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m worker",
        description="Run one live camera and post results to the QueueVision API. "
        f"The ingest token is read from the environment variable {TOKEN_ENV}.",
    )
    parser.add_argument("--video-id", type=int, required=True, help="Run id the API created for this session")
    parser.add_argument("--camera", required=True, help="Camera id (configs/cameras/<id>.yaml)")
    parser.add_argument("--api", required=True, help="API base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, required=True, help="Folder for the CSV and metrics JSON")
    return parser.parse_args(argv)


def camera_dir(env: Mapping[str, str]) -> Path:
    return Path(env.get("QUEUEVISION_CAMERA_DIR") or PROJECT_ROOT / "configs" / "cameras")


class _TimedSource:
    """Passes a source through to ``run_live`` and remembers the newest frame's capture time."""

    def __init__(self, source: Any) -> None:
        self._source = source
        self.last_capture: float | None = None
        self.source_kind = source.source_kind

    @property
    def loop_count(self) -> int:
        return self._source.loop_count

    def read_latest(self, timeout_s: float):
        grabbed = self._source.read_latest(timeout_s)
        if grabbed is not None:
            self.last_capture = grabbed[1]
        return grabbed

    def open(self) -> None:
        self._source.open()

    def close(self) -> None:
        self._source.close()


class Session:
    """Buffers one live run's output and sends it to the API; all timing uses the injected clock."""

    def __init__(
        self,
        video_id: int,
        client: IngestClient,
        clock: Callable[[], float],
        wall: Callable[[], float],
        stop: threading.Event,
    ) -> None:
        self.video_id = video_id
        self.client = client
        self.clock = clock
        self.wall = wall
        self.stop = stop
        self.unreachable = False
        self.timed: _TimedSource | None = None
        self.events: list[dict] = []
        self.rows: list[dict] = []
        self.latest: FrameUpdate | None = None
        self.seq = 0  # sequence number of the newest batch built
        self.inflight: dict | None = None  # a batch the API has not accepted yet; resent unchanged
        self.next_second = 0
        now = clock()
        self.last_batch = now  # the last batch attempt (paces the sends)
        self.last_accepted = now  # the last batch the API accepted (paces the heartbeats)
        self.last_heartbeat = now
        self.last_preview = -math.inf

    def _captured_at_unix(self) -> float | None:
        if self.timed is None or self.timed.last_capture is None:
            return None
        return self.wall() - (self.clock() - self.timed.last_capture)

    def _frames(self) -> int:
        return 0 if self.latest is None else self.latest.frame_index + 1

    def pending(self) -> bool:
        return bool(self.events or self.rows or self.inflight)

    def send_batch(self) -> None:
        """Send the unaccepted batch again, unchanged and with its own ``seq``, else build the next one."""
        self.last_batch = self.clock()
        if self.inflight is None:
            update = self.latest
            self.seq += 1
            self.inflight = {
                "events": self.events,
                "rows": self.rows,
                "progress": None if update is None else progress_message(self.video_id, update),
                "frames": self._frames(),
                "captured": self._captured_at_unix(),
                "seq": self.seq,
            }
            self.events, self.rows = [], []
        b = self.inflight
        if self.client.batch(b["events"], b["rows"], b["progress"], b["frames"], b["captured"], b["seq"]):
            self.inflight = None
            self.last_accepted = self.clock()

    def heartbeat(self) -> None:
        self.last_heartbeat = self.clock()
        fps = 0.0 if self.latest is None else self.latest.processing_fps
        self.client.heartbeat(self._frames(), round(fps, 2))

    def on_frame(self, update: FrameUpdate) -> None:
        self.latest = update
        queue = update.queue
        if queue is not None:
            self.events.extend(event_message(self.video_id, e.to_dict())["event"] for e in queue.events)
        if update.timestamp_s + 1e-9 >= self.next_second:  # one row per video second (JobManager rule)
            self.next_second = math.floor(update.timestamp_s + 1e-9) + 1
            if queue is not None:
                self.rows.append(timeseries_row(update))
        now = self.clock()
        if now - self.last_batch >= BATCH_EVERY_S:
            self.send_batch()
        if update.image is not None and now - self.last_preview >= PREVIEW_EVERY_S:
            self.last_preview = now
            ok, jpeg = cv2.imencode(".jpg", update.image, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY])
            if ok and len(jpeg) <= MAX_PREVIEW_BYTES:
                self.client.preview(jpeg.tobytes())

    def should_stop(self) -> bool:
        if self.client.seconds_since_ok() > UNREACHABLE_AFTER_S:
            self.unreachable = True
            return True
        if self.stop.is_set():
            return True
        if self.clock() - max(self.last_accepted, self.last_heartbeat) >= HEARTBEAT_AFTER_S:
            self.heartbeat()
        return False


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] = os.environ,
    *,
    tracker_factory: Callable[[], Any] | None = None,
    source_opener: Callable[..., Any] = open_source,
    run_live_fn: Callable[..., dict] = run_live,
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wall: Callable[[], float] = time.time,
    stop: threading.Event | None = None,
) -> int:
    args = parse_arguments(argv)
    token = env.get(TOKEN_ENV)
    if not token:
        LOGGER.error("%s is not set", TOKEN_ENV)
        return 2
    stop = stop if stop is not None else threading.Event()
    previous_handler = None
    handler_installed = False
    try:
        previous_handler = signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
        handler_installed = True
    except ValueError:  # not the main thread (tests): the stop flag is set directly
        pass
    try:
        client_args: dict[str, Any] = {} if opener is None else {"opener": opener}
        client = IngestClient(args.api, args.video_id, token, clock=clock, **client_args)
        return _run(args, env, client, tracker_factory, source_opener, run_live_fn, clock, sleep, wall, stop)
    finally:
        if handler_installed:
            signal.signal(signal.SIGTERM, previous_handler or signal.SIG_DFL)


def send_final(
    client: IngestClient, sleep: Callable[[float], None], status: str, error: str | None, report: dict | None
) -> bool:
    """The final message, up to ``FINAL_ATTEMPTS`` tries ``FINAL_RETRY_S`` apart; stops at the first success."""
    for attempt in range(FINAL_ATTEMPTS):
        if client.final(status, error, report):
            return True
        if attempt + 1 < FINAL_ATTEMPTS:
            sleep(FINAL_RETRY_S)
    return False


def _fail(client: IngestClient, sleep: Callable[[float], None], reason: str) -> int:
    send_final(client, sleep, "failed", reason, None)
    return 1


def _run(args, env, client, tracker_factory, source_opener, run_live_fn, clock, sleep, wall, stop) -> int:
    session = Session(args.video_id, client, clock, wall, stop)
    source = None
    try:
        quiet_opencv_logging()
        camera = load_camera_config(camera_dir(env) / f"{args.camera}.yaml")
        if camera.live is None:
            raise ValueError("camera has no live settings")
        live = camera.live
        client.heartbeat(0, 0.0)  # tells the API the process is up before the (slow) model load
        tracker = (tracker_factory or default_tracker_factory(env))()
        if stop.is_set():  # stopped while the model was loading: completed, no frames
            send_final(client, sleep, "completed", None, None)
            return 0
        source = source_opener(live, env)
        session.timed = _TimedSource(source)
        session.heartbeat()  # the model load may have been long: confirm the API is still there
        if client.seconds_since_ok() > UNREACHABLE_AFTER_S:
            return 3
        if stop.is_set():  # stopped while the source opened: nothing ran, so no report
            send_final(client, sleep, "completed", None, None)
            return 0
        options = LiveOptions(
            output_dir=args.output_dir,
            process_fps=live.process_fps,
            preview_privacy_mode=live.preview_privacy_mode,
            max_session_s=live.max_session_s,
        )
        report = run_live_fn(
            session.timed,
            tracker,
            camera,
            options,
            on_frame=session.on_frame,
            should_stop=session.should_stop,
            clock=clock,
            sleep=sleep,
        )
        if session.unreachable:
            return 3
        for _ in range(2):  # a batch still unaccepted, then whatever arrived behind it
            if session.pending():
                session.send_batch()
        send_final(client, sleep, "completed", None, report)
        return 0
    except LiveSourceError as error:
        LOGGER.error("live source failed: %s", error.reason)
        return _fail(client, sleep, error.reason)
    except Exception:
        LOGGER.error("worker failed:\n%s", redact(traceback.format_exc()))
        return _fail(client, sleep, ENGINE_ERROR)
    finally:
        if source is not None:
            try:
                source.close()
            except Exception:
                LOGGER.error("closing the source failed:\n%s", redact(traceback.format_exc()))
