"""Reproducible per-stage benchmark of the QueueVision processing pipeline.

Examples::

    python scripts/benchmark.py --video datasets/raw/cad_seq27.mp4 \\
        --camera-config configs/cameras/cad_seq27.yaml --device mps
    python scripts/benchmark.py --tracks outputs/s5_tracks/cad/tracks/cad_seq27_mot.txt \\
        --video datasets/raw/cad_seq27.mp4 --camera-config configs/cameras/cad_seq27.yaml

Measured per processed frame (the first ``--warmup-frames`` are processed but excluded):

* ``decode_ms``: wall time of ``VideoLoader.read``.
* ``detection_ms``: Ultralytics ``preprocess + inference + postprocess`` from ``Results.speed``.
* ``tracking_ms``: wall time of ``PersonTracker.update`` minus ``detection_ms`` (ByteTrack
  association, result parsing and Ultralytics overhead outside its own profiler).
* ``queue_ms``: wall time of ``QueueAnalyzer.update`` (only with ``--camera-config``).
* ``total_ms``: decode + tracker update + queue. ``processing_fps`` = measured frames / sum(total).
  No drawing and no video writing are included.

``--tracks`` benchmarks only ``QueueAnalyzer.update`` on saved MOT tracks; no model is loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

import numpy as np

from ai_engine import config
from ai_engine.detection.types import PersonDetection
from ai_engine.queue.analyzer import QueueAnalyzer
from ai_engine.queue.camera_config import CameraConfig, load_camera_config

try:  # optional: process CPU % sampling
    import psutil
except ImportError:  # pragma: no cover - depends on the environment
    psutil = None  # type: ignore[assignment]

DEFAULT_WARMUP_FRAMES = 10
SAMPLE_INTERVAL_S = 0.5
STAGES = ("decode_ms", "detection_ms", "tracking_ms", "queue_ms", "total_ms")


class TrackerLike(Protocol):
    detector: Any

    def configure(self, effective_fps: float) -> None: ...
    def warmup(self, frame_shape: tuple[int, int, int]) -> None: ...
    def update(self, frame: np.ndarray) -> list[PersonDetection]: ...
    def describe(self) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- statistics


def latency_stats(values: list[float]) -> dict[str, float | int | None]:
    """Mean, median and 95th percentile (linear interpolation) of ``values``, or nulls."""
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None}
    array = np.asarray(values, dtype=float)
    return {
        "count": int(array.size),
        "mean": round(float(array.mean()), 4),
        "median": round(float(np.median(array)), 4),
        "p95": round(float(np.percentile(array, 95)), 4),
    }


# --------------------------------------------------------------------------- resource usage


def peak_rss_mb() -> float:
    """Peak resident set size of this process in MiB (``ru_maxrss`` is bytes on macOS, KiB elsewhere)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


class ResourceMonitor:
    """Sample process CPU % in a thread; also record CPU seconds and wall time of the run."""

    def __init__(self, interval: float = SAMPLE_INTERVAL_S) -> None:
        self.interval = interval
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process = psutil.Process() if psutil is not None else None
        self._times0 = os.times()
        self._wall0 = time.perf_counter()
        self._times1 = self._times0
        self._wall1 = self._wall0

    def __enter__(self) -> ResourceMonitor:
        self._times0 = os.times()
        self._wall0 = time.perf_counter()
        if self._process is not None:
            self._process.cpu_percent(interval=None)  # prime
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        assert self._process is not None
        while not self._stop.wait(self.interval):
            self.samples.append(self._process.cpu_percent(interval=None))

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._times1 = os.times()
        self._wall1 = time.perf_counter()

    def summary(self) -> dict[str, Any]:
        user = self._times1.user - self._times0.user
        system = self._times1.system - self._times0.system
        wall = self._wall1 - self._wall0
        return {
            "cpu_percent_mean": round(float(np.mean(self.samples)), 1) if self.samples else None,
            "cpu_percent_max": round(float(np.max(self.samples)), 1) if self.samples else None,
            "cpu_percent_samples": len(self.samples),
            "cpu_percent_note": (
                "process CPU %, 100 = one core busy; null without psutil or if the run was under one interval"
            ),
            "cpu_user_seconds": round(user, 3),
            "cpu_system_seconds": round(system, 3),
            "cpu_percent_overall": round(100.0 * (user + system) / wall, 1) if wall > 0 else None,
            "wall_seconds": round(wall, 3),
            "peak_rss_mb": peak_rss_mb(),
        }


# --------------------------------------------------------------------------- environment


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _cpu_brand() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _total_ram_gb() -> float | None:
    if psutil is not None:
        return round(psutil.virtual_memory().total / 1024**3, 2)
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 2)
    except (ValueError, OSError, AttributeError):
        return None


def file_sha256_12(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def environment_info() -> dict[str, Any]:
    return {
        "hardware": {
            "machine": platform.machine(),
            "processor": platform.processor() or None,
            "cpu_brand": _cpu_brand(),
            "cpu_count_logical": os.cpu_count(),
            "total_ram_gb": _total_ram_gb(),
        },
        "os": platform.platform(),
        "python": platform.python_version(),
        "torch": _version("torch"),
        "ultralytics": _version("ultralytics"),
        "opencv": _version("opencv-python") or _version("opencv-python-headless"),
        "psutil": _version("psutil"),
    }


def model_info(detector: Any) -> dict[str, Any]:
    path = Path(getattr(detector, "model_path", ""))
    exists = path.is_file()
    return {
        "device_used": getattr(detector, "device", None),
        "weights_path": str(path),
        "weights_sha256_12": file_sha256_12(path),
        "weights_size_bytes": path.stat().st_size if exists else None,
        "detector_confidence": getattr(detector, "confidence", None),
        "imgsz": getattr(detector, "image_size", None),
    }


# --------------------------------------------------------------------------- benchmark runs


@dataclass
class Samples:
    decode_ms: list[float] = field(default_factory=list)
    detection_ms: list[float] = field(default_factory=list)
    tracking_ms: list[float] = field(default_factory=list)
    queue_ms: list[float] = field(default_factory=list)
    total_ms: list[float] = field(default_factory=list)

    def summary(self, include: tuple[str, ...] = STAGES) -> dict[str, dict[str, Any]]:
        return {name: latency_stats(getattr(self, name)) for name in include}


def _fps(total_ms: list[float]) -> float | None:
    seconds = sum(total_ms) / 1000.0
    return round(len(total_ms) / seconds, 2) if seconds > 0 else None


def run_pipeline_benchmark(
    video: Path,
    tracker: TrackerLike,
    camera: CameraConfig | None,
    max_frames: int | None,
    warmup_frames: int,
) -> dict[str, Any]:
    """Benchmark decode + detect/track + queue analysis; returns the report dict."""
    from ai_engine.pipeline import source_fps
    from ai_engine.video.video_loader import VideoLoader

    samples = Samples()
    with VideoLoader(video) as loader:
        meta = loader.metadata
        fps = source_fps(loader)
        frame_size = (meta.width, meta.height)
        tracker.configure(fps)
        tracker.warmup((meta.height, meta.width, 3))  # model setup, not part of the measurements
        analyzer = QueueAnalyzer(camera) if camera else None
        processed = 0
        measured_started = 0.0
        with ResourceMonitor() as monitor:
            while max_frames is None or processed < max_frames:
                t0 = time.perf_counter()
                frame = loader.read()
                decode_ms = (time.perf_counter() - t0) * 1000.0
                if frame is None:
                    break
                t1 = time.perf_counter()
                people = tracker.update(frame)
                update_ms = (time.perf_counter() - t1) * 1000.0
                queue_ms = 0.0
                if analyzer is not None:
                    t2 = time.perf_counter()
                    analyzer.update(processed / fps, processed, people, frame_size)
                    queue_ms = (time.perf_counter() - t2) * 1000.0
                if processed >= warmup_frames:
                    if not samples.total_ms:
                        measured_started = time.perf_counter()
                    speed = getattr(tracker, "last_speed_ms", None)
                    samples.decode_ms.append(decode_ms)
                    if speed:
                        detection_ms = sum(
                            speed.get(k, 0.0) for k in ("preprocess", "inference", "postprocess")
                        )
                        samples.detection_ms.append(detection_ms)
                        samples.tracking_ms.append(max(0.0, update_ms - detection_ms))
                    if analyzer is not None:
                        samples.queue_ms.append(queue_ms)
                    samples.total_ms.append(decode_ms + update_ms + queue_ms)
                processed += 1
            measured_wall = time.perf_counter() - measured_started if samples.total_ms else 0.0

    include = tuple(s for s in STAGES if s != "queue_ms" or camera is not None)
    return {
        "mode": "pipeline",
        "frames_processed": processed,
        "frames_measured": len(samples.total_ms),
        "warmup_frames_excluded": min(warmup_frames, processed),
        "summary": {
            "latency_ms": samples.summary(include),
            "processing_fps": _fps(samples.total_ms),
            "processing_fps_wall": round(len(samples.total_ms) / measured_wall, 2)
            if measured_wall > 0
            else None,
            "resources": monitor.summary(),
        },
        "video": {
            "path": str(video),
            "fps": meta.fps,
            "width": meta.width,
            "height": meta.height,
            "frame_count": meta.frame_count,
        },
    }


def run_replay_benchmark(
    tracks_path: Path,
    video: Path,
    camera: CameraConfig,
    max_frames: int | None,
    warmup_frames: int,
) -> dict[str, Any]:
    """Benchmark ``QueueAnalyzer.update`` alone on saved MOT tracks (no model, no decoding)."""
    from ai_engine.evaluation.replay import read_mot_tracks, video_properties

    tracks = read_mot_tracks(tracks_path)
    fps, frame_size, frame_count = video_properties(video)
    if fps <= 0:
        fps = 30.0
    if frame_count <= 0:
        frame_count = max(tracks, default=-1) + 1
    if max_frames is not None:
        frame_count = min(frame_count, max_frames)
    analyzer = QueueAnalyzer(camera)
    queue_ms: list[float] = []
    people_total = 0
    with ResourceMonitor() as monitor:
        for frame_index in range(frame_count):
            people = list(tracks.get(frame_index, ()))
            t0 = time.perf_counter()
            analyzer.update(frame_index / fps, frame_index, people, frame_size)
            elapsed = (time.perf_counter() - t0) * 1000.0
            if frame_index >= warmup_frames:
                queue_ms.append(elapsed)
                people_total += len(people)
    return {
        "mode": "replay_only",
        "frames_processed": frame_count,
        "frames_measured": len(queue_ms),
        "warmup_frames_excluded": min(warmup_frames, frame_count),
        "summary": {
            "latency_ms": {"queue_ms": latency_stats(queue_ms)},
            "queue_analysis_fps": _fps(queue_ms),
            "mean_people_per_frame": round(people_total / len(queue_ms), 3) if queue_ms else None,
            "resources": monitor.summary(),
        },
        "tracks": str(tracks_path),
        "video": {
            "path": str(video),
            "fps": fps,
            "width": frame_size[0],
            "height": frame_size[1],
            "frame_count": frame_count,
        },
    }


def camera_info(camera: CameraConfig | None, path: Path | None) -> dict[str, Any] | None:
    if camera is None:
        return None
    return {
        "path": str(path),
        "camera_id": camera.camera_id,
        "membership_algorithm": camera.membership.algorithm,
        "stitching_enabled": camera.stitching.enabled,
        "stitching_algorithm": camera.stitching.algorithm,
    }


def default_output(video: Path, device: str, replay: bool) -> Path:
    name = f"benchmark_{video.stem}_{'replay' if replay else device}.json"
    return config.OUTPUTS_DIR / "metrics" / name


def build_tracker(args: argparse.Namespace, fps: float) -> Any:
    from ai_engine.detection.detector import PersonDetector
    from ai_engine.tracking.tracker import PersonTracker, TrackerSettings

    detector = PersonDetector(
        model_path=args.model, confidence=args.confidence, image_size=args.imgsz, device=args.device
    )
    return PersonTracker(detector, TrackerSettings(new_track_confidence=args.confidence), effective_fps=fps)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Benchmark the QueueVision pipeline stages.")
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--camera-config", type=Path, default=None)
    p.add_argument("--tracks", type=Path, default=None, help="MOT file: replay-only QueueAnalyzer benchmark")
    p.add_argument("--device", default=config.DEFAULT_DEVICE)
    p.add_argument("--model", default=str(config.DEFAULT_MODEL_PATH))
    p.add_argument("--imgsz", type=int, default=config.IMAGE_SIZE)
    p.add_argument("--confidence", type=float, default=config.CONFIDENCE)
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--warmup-frames", type=int, default=DEFAULT_WARMUP_FRAMES)
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args(argv)

    camera = load_camera_config(args.camera_config) if args.camera_config else None
    replay = args.tracks is not None
    if replay and camera is None:
        p.error("--tracks requires --camera-config")
    if args.warmup_frames < 0:
        p.error("--warmup-frames must be >= 0")

    started = datetime.now(UTC).isoformat(timespec="seconds")
    if replay:
        assert args.tracks is not None and camera is not None
        report = run_replay_benchmark(args.tracks, args.video, camera, args.max_frames, args.warmup_frames)
        report["model"] = None
        device = "replay"
    else:
        from ai_engine.video.video_loader import VideoLoader

        with VideoLoader(args.video) as loader:
            fps = loader.metadata.fps or 30.0
        tracker = build_tracker(args, fps)
        report = run_pipeline_benchmark(args.video, tracker, camera, args.max_frames, args.warmup_frames)
        report["model"] = model_info(tracker.detector)
        report["tracker"] = tracker.describe()
        device = str(report["model"]["device_used"])
    report["started_at"] = started
    report["camera"] = camera_info(camera, args.camera_config)
    report["environment"] = environment_info()
    report["definitions"] = {
        "detection_ms": "Ultralytics Results.speed preprocess+inference+postprocess",
        "tracking_ms": "wall time of PersonTracker.update minus detection_ms",
        "queue_ms": "wall time of QueueAnalyzer.update",
        "total_ms": "decode + tracker update + queue analysis; no drawing, no video writing",
        "processing_fps": "measured frames / sum(total_ms)",
    }
    output = args.output or default_output(args.video, device, replay)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
