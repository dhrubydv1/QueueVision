"""Measure a live camera session end to end through the API and write ``outputs/s11_live/<name>.json``.

    python scripts/measure_live.py --camera cad_seq25 --seconds 120
    python scripts/measure_live.py --camera cad_seq25 --seconds 1800 --name soak_cad_seq25
    python scripts/measure_live.py --camera webcam --seconds 120      # needs the macOS camera permission

The script starts one API server (a child process, loopback, free port, temporary data folder), starts a
live session on the camera through ``POST /api/cameras/<id>/live``, samples every second for
``--seconds``, stops the session through ``POST /api/videos/<id>/stop`` and stops the server. Nothing in
``api/`` or ``worker/`` is changed for the measurement; two small wrappers around them (inside this
file) record what the product does not report:

* the **worker wrapper** (``--internal-worker``, started by the API in place of ``python -m worker``)
  times every ``PersonTracker.update`` call (detector plus tracker) and, at exit, writes those timings
  and its own ``getrusage(RUSAGE_SELF)`` (CPU seconds, peak RSS) to a file;
* the **API wrapper** (``--internal-api``) wraps the ingest route's batch store to record, per batch,
  the time from the newest frame's capture to the API's receipt (``time.time() - captured_at_unix``,
  same machine clock), the number of events and the number of time-series rows; at exit it writes those
  and its own ``getrusage``.

Peak RSS comes from ``ru_maxrss`` self-reported by each process at exit (macOS: bytes). The RSS-over-
time samples come from ``ps -o rss=`` once per second, taken by this script for the API and the worker.

Limits: ``captured_at_unix`` belongs to the newest frame when a batch is built (about once per second),
so the latency is capture-to-receipt of that frame, not the age of the oldest event in the batch. Queue
numbers from a looped clip are not accuracy figures: loop restarts cause false joins and leaves. The
webcam is never opened unless you run this script with ``--camera webcam`` yourself."""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "outputs" / "s11_live"
WORKER_FILE_ENV = "QV_MEASURE_WORKER_FILE"
API_FILE_ENV = "QV_MEASURE_API_FILE"
RSS_UNIT = 1 if sys.platform == "darwin" else 1024  # ru_maxrss is bytes on macOS, KiB on Linux


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def stats_ms(seconds: list[float]) -> dict:
    return {
        "n": len(seconds),
        "median_ms": None if not seconds else round(statistics.median(seconds) * 1000, 3),
        "p90_ms": None if not seconds else round(percentile(seconds, 0.9) * 1000, 3),
        "mean_ms": None if not seconds else round(statistics.fmean(seconds) * 1000, 3),
    }


def rusage_self() -> dict:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "cpu_user_s": round(usage.ru_utime, 3),
        "cpu_system_s": round(usage.ru_stime, 3),
        "peak_rss_mb": round(usage.ru_maxrss * RSS_UNIT / 1024 / 1024, 1),
    }


def machine_state() -> dict:
    def run(*cmd: str) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    batt = run("pmset", "-g", "batt")
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "power": "AC" if "AC Power" in batt else ("battery" if "Battery Power" in batt else "unknown"),
        "pmset_batt": batt.replace("\n", " | "),
        "uptime": run("uptime"),
        "load_average": os.getloadavg(),
    }


# --- wrappers run inside the server and worker processes ---------------------------------------------


def internal_worker(argv: list[str]) -> int:
    """Run ``python -m worker`` with the tracker update timed; dump timings and rusage at exit."""
    from ai_engine.tracking.tracker import PersonTracker
    from worker.session import main

    started = time.monotonic()
    samples: list[list[float]] = []  # [seconds since this wrapper started, update seconds]
    original = PersonTracker.update

    def timed(self, frame):
        began = time.perf_counter()
        try:
            return original(self, frame)
        finally:
            samples.append([round(time.monotonic() - started, 3), time.perf_counter() - began])

    PersonTracker.update = timed
    try:
        code = main(argv)
    finally:
        out = os.environ.get(WORKER_FILE_ENV)
        if out:
            Path(out).write_text(
                json.dumps({"updates": samples, "rusage": rusage_self(), "pid": os.getpid()})
            )
    return code


def internal_api(port: int) -> int:
    """Serve the API with the worker command swapped for the timing wrapper and batches recorded."""
    import uvicorn

    from api.main import create_app
    from api.routes import ingest

    batches: list[dict] = []
    original = ingest._store_batch

    def recording(request, video_id, batch):
        received = time.time()
        original(request, video_id, batch)
        batches.append(
            {
                "latency_s": None if batch.captured_at_unix is None else received - batch.captured_at_unix,
                "events": len(batch.events),
                "rows": len(batch.timeseries),
                "seq": batch.seq,
            }
        )

    ingest._store_batch = recording
    worker_cmd = [sys.executable, str(Path(__file__).resolve()), "--internal-worker"]
    app = create_app(live_options={"worker_cmd": worker_cmd})

    def dump() -> None:
        out = os.environ.get(API_FILE_ENV)
        if out:
            Path(out).write_text(json.dumps({"batches": batches, "rusage": rusage_self()}))

    def on_sigterm(_signum, _frame) -> None:
        # uvicorn restores this handler and re-raises SIGTERM after its own shutdown (which stops the
        # workers), so the file is written once the server has finished.
        dump()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_sigterm)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=5)
    dump()
    return 0


# --- the measurement driver --------------------------------------------------------------------------


def free_port(preferred: int) -> int:
    for port in (preferred, *range(8791, 8800)):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("no free port between 8790 and 8799")


def http(method: str, url: str, timeout: float = 30.0):
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else None


def ps_rss_mb(pid: int) -> float | None:
    result = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    text = result.stdout.strip()
    return round(int(text) / 1024, 1) if text.isdigit() else None


def child_pids(pid: int) -> list[int]:
    result = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(p) for p in result.stdout.split()]


def window(samples: list[list[float]], start: float, end: float) -> list[float]:
    return [duration for at, duration in samples if start <= at < end]


def summarise_updates(samples: list[list[float]], seconds_window: float = 60.0) -> dict:
    """All updates, and the first and last ``seconds_window`` of updates (by wrapper time)."""
    out = {"all": stats_ms([d for _, d in samples])}
    if samples:
        first_at, last_at = samples[0][0], samples[-1][0]
        out["first_window"] = stats_ms(window(samples, first_at, first_at + seconds_window))
        out["last_window"] = stats_ms(window(samples, last_at - seconds_window, last_at + 1))
        out["window_s"] = seconds_window
    return out


def summarise_rss(series: list[dict], key: str, seconds_window: float = 60.0) -> dict:
    values = [(s["t"], s[key]) for s in series if s.get(key) is not None]
    if not values:
        return {"samples": 0}
    first = [v for t, v in values if t < values[0][0] + seconds_window]
    last = [v for t, v in values if t >= values[-1][0] - seconds_window]
    return {
        "samples": len(values),
        "first_window_mean_mb": round(statistics.fmean(first), 1),
        "last_window_mean_mb": round(statistics.fmean(last), 1),
        "max_mb": max(v for _, v in values),
        "window_s": seconds_window,
    }


def measure(args: argparse.Namespace) -> dict:
    port = free_port(args.port)
    workdir = Path(tempfile.mkdtemp(prefix="qv_measure_"))
    data_dir = workdir / "data"
    worker_file, api_file = workdir / "worker.json", workdir / "api.json"
    env = {
        **os.environ,
        "QUEUEVISION_DATA_DIR": str(data_dir),
        "QUEUEVISION_HOST": "127.0.0.1",
        "QUEUEVISION_PORT": str(port),
        WORKER_FILE_ENV: str(worker_file),
        API_FILE_ENV: str(api_file),
    }
    base = f"http://127.0.0.1:{port}"
    result: dict = {
        "name": args.name,
        "camera": args.camera,
        "seconds_requested": args.seconds,
        "port": port,
        "command": " ".join(sys.argv),
        "machine_start": machine_state(),
    }
    api = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--internal-api", "--port", str(port)],
        env=env,
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    rss_series: list[dict] = []
    seen_workers: set[int] = set()
    video_id = None
    try:
        for _ in range(120):
            try:
                result["health"] = http("GET", f"{base}/api/health", timeout=2)
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
        else:
            raise SystemExit("the API did not start")
        run = http("POST", f"{base}/api/cameras/{args.camera}/live")
        video_id = run["video_id"]
        result["video_id"] = video_id
        began = time.monotonic()
        while (elapsed := time.monotonic() - began) < args.seconds:
            sample = {"t": round(elapsed, 1), "api_rss_mb": ps_rss_mb(api.pid)}
            workers = child_pids(api.pid)
            seen_workers.update(workers)
            sample["worker_rss_mb"] = ps_rss_mb(workers[0]) if workers else None
            rss_series.append(sample)
            time.sleep(max(0.0, 1.0 - ((time.monotonic() - began) - elapsed)))
        result["live_listing_before_stop"] = http("GET", f"{base}/api/live")
        http("POST", f"{base}/api/videos/{video_id}/stop", timeout=60)
        result["seconds_measured"] = round(time.monotonic() - began, 1)
        run = http("GET", f"{base}/api/videos/{video_id}")
        result["run"] = {
            k: run.get(k) for k in ("status", "error", "kind", "source_kind", "frames_processed")
        }
        result["queue_summary_not_accuracy"] = {
            "note": "Looped clip: restarts cause false joins and leaves. These are not accuracy figures.",
            "summary": {
                k: (run.get("summary") or {}).get(k)
                for k in ("maximum_queue_length", "average_queue_length", "event_counts")
            },
        }
    finally:
        if api.poll() is None:
            api.send_signal(signal.SIGTERM)
        try:
            api.wait(timeout=30)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait()
    leftover = [pid for pid in seen_workers if ps_rss_mb(pid) is not None]
    for pid in leftover:
        os.kill(pid, signal.SIGKILL)
    result["worker_processes_left_after_api_exit"] = len(leftover)
    result["machine_end"] = machine_state()

    worker = json.loads(worker_file.read_text()) if worker_file.exists() else None
    api_side = json.loads(api_file.read_text()) if api_file.exists() else None
    if worker:
        updates = worker["updates"]
        result["worker"] = {
            "update": summarise_updates(updates),
            "cpu_user_s": worker["rusage"]["cpu_user_s"],
            "cpu_system_s": worker["rusage"]["cpu_system_s"],
            "peak_rss_mb": worker["rusage"]["peak_rss_mb"],
            "cpu_source": "worker getrusage(RUSAGE_SELF) at exit, written to a file by the wrapper",
            "peak_rss_source": "worker ru_maxrss at exit",
            "updates_per_s": round(len(updates) / result["seconds_measured"], 2),
        }
    if api_side:
        batches = api_side["batches"]
        latencies = [b["latency_s"] for b in batches if b["latency_s"] is not None]
        result["api"] = {
            "peak_rss_mb": api_side["rusage"]["peak_rss_mb"],
            "peak_rss_source": "API ru_maxrss at exit",
            "cpu_user_s": api_side["rusage"]["cpu_user_s"],
            "cpu_system_s": api_side["rusage"]["cpu_system_s"],
        }
        result["capture_to_ingest"] = {
            "source": "time.time() at receipt minus captured_at_unix per batch, via a wrapper around "
            "api.routes.ingest._store_batch (no log line, no payload logged)",
            "n": len(latencies),
            "median_ms": None if not latencies else round(statistics.median(latencies) * 1000, 2),
            "p90_ms": None if not latencies else round(percentile(latencies, 0.9) * 1000, 2),
            "max_ms": None if not latencies else round(max(latencies) * 1000, 2),
        }
        result["batches"] = {
            "count": len(batches),
            "rate_per_s": round(len(batches) / result["seconds_measured"], 3),
            "mean_events_per_batch": round(statistics.fmean([b["events"] for b in batches]), 3)
            if batches
            else None,
            "mean_rows_per_batch": round(statistics.fmean([b["rows"] for b in batches]), 3)
            if batches
            else None,
            "total_events": sum(b["events"] for b in batches),
            "total_rows": sum(b["rows"] for b in batches),
        }
    result["rss_over_time"] = {
        "source": "ps -o rss= once per second, taken by this script",
        "api": summarise_rss(rss_series, "api_rss_mb"),
        "worker": summarise_rss(rss_series, "worker_rss_mb"),
    }
    result["rss_samples_every_10s"] = [s for s in rss_series if int(s["t"]) % 10 == 0]
    shutil.rmtree(workdir, ignore_errors=True)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--internal-worker":
        return internal_worker(argv[1:])
    if argv and argv[0] == "--internal-api":
        return internal_api(int(argv[argv.index("--port") + 1]))
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--camera", required=True, help="camera id in configs/cameras (cad_seq25, webcam, ...)"
    )
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--name", help="output name (default: <camera>_<seconds>s_<timestamp>)")
    parser.add_argument("--port", type=int, default=8790, help="first port to try (never 8000, 8001 or 8770)")
    args = parser.parse_args(argv)
    if args.port in (8000, 8001, 8770):
        parser.error("port is reserved")
    args.name = args.name or f"{args.camera}_{int(args.seconds)}s_{time.strftime('%Y%m%d_%H%M%S')}"
    result = measure(args)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{args.name}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
