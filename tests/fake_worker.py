"""A model-free stand-in for ``python -m worker``: same CLI, env token and ingest protocol.

Run as ``python -m tests.fake_worker``. ``FAKE_WORKER_MODE`` picks the behaviour:
``normal`` (heartbeat, batches with progress, ``completed`` on SIGTERM), ``slow_start`` (heartbeat,
then a few seconds of pretend model loading that SIGTERM cuts short), ``crash`` (heartbeat, then exit 1
with no final), ``silent`` (one heartbeat, then nothing until killed) and ``deaf`` (like silent, but
ignores SIGTERM). It never prints the environment, and so never an RTSP URL.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time

from worker.client import IngestClient

TOKEN_ENV = "QUEUEVISION_WORKER_TOKEN"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video-id", type=int, required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument("--api", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return 2
    mode = os.environ.get("FAKE_WORKER_MODE", "normal")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    client = IngestClient(args.api, args.video_id, token)
    print(f"fake worker mode={mode} video={args.video_id} camera={args.camera}", flush=True)
    client.heartbeat(0, 0.0)
    if mode == "crash":
        return 1
    if mode == "deaf":  # beats once, then ignores SIGTERM: only SIGKILL ends it
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
        return 0
    if mode == "silent":
        time.sleep(60)
        return 0
    if mode == "slow_start":
        stop.wait(5.0)
        if stop.is_set():
            client.final("completed", None, None)
            return 0
    frames = 0
    seq = 0
    while not stop.is_set():
        frames += 10
        seq += 1
        progress = {
            "schema_version": "1.0",
            "type": "progress",
            "video_id": args.video_id,
            "frames_processed": frames,
            "processing_fps": 10.0,
        }
        client.batch([], [], progress, frames, None, seq)
        stop.wait(0.2)
    client.final("completed", None, {"stopped_by": "operator", "frames_processed": frames})
    return 0


if __name__ == "__main__":
    sys.exit(main())
