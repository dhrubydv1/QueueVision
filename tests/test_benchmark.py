"""Benchmark script: JSON structure, warmup exclusion, replay-only mode (no model)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from ai_engine.queue.camera_config import load_camera_config
from tests.conftest import FakeTracker, write_synthetic_video

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py"
spec = importlib.util.spec_from_file_location("benchmark_script", SCRIPT)
assert spec and spec.loader
benchmark = importlib.util.module_from_spec(spec)
sys.modules["benchmark_script"] = benchmark
spec.loader.exec_module(benchmark)

CAMERA_YAML = """\
camera_id: bench
queue_zone: [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
"""


class SpeedTracker(FakeTracker):
    """MOCKED tracker that also exposes Ultralytics-style per-stage timings."""

    last_speed_ms: ClassVar[dict[str, float]] = {"preprocess": 1.0, "inference": 2.0, "postprocess": 3.0}


@pytest.fixture
def camera_file(tmp_path: Path) -> Path:
    path = tmp_path / "cam.yaml"
    path.write_text(CAMERA_YAML, encoding="utf-8")
    return path


def test_latency_stats() -> None:
    stats = benchmark.latency_stats([1.0, 2.0, 3.0, 4.0])
    assert stats["count"] == 4 and stats["mean"] == 2.5 and stats["median"] == 2.5
    assert benchmark.latency_stats([])["mean"] is None


def test_pipeline_excludes_warmup(tmp_path: Path, camera_file: Path) -> None:
    video = write_synthetic_video(tmp_path / "v.mp4", frames=15)
    tracker = SpeedTracker()
    report = benchmark.run_pipeline_benchmark(video, tracker, load_camera_config(camera_file), None, 5)
    assert report["frames_processed"] == 15
    assert report["frames_measured"] == 10
    assert report["warmup_frames_excluded"] == 5
    latency = report["summary"]["latency_ms"]
    assert set(latency) == {"decode_ms", "detection_ms", "tracking_ms", "queue_ms", "total_ms"}
    for stage in latency.values():
        assert stage["count"] == 10
        assert {"mean", "median", "p95"} <= set(stage)
    assert latency["detection_ms"]["mean"] == 6.0
    assert report["summary"]["processing_fps"] > 0
    assert report["summary"]["resources"]["peak_rss_mb"] > 0
    assert tracker.updates == 15
    json.dumps(report)


def test_pipeline_without_camera_or_speed(tmp_path: Path) -> None:
    video = write_synthetic_video(tmp_path / "v.mp4", frames=8)
    report = benchmark.run_pipeline_benchmark(video, FakeTracker(), None, 6, 2)
    latency = report["summary"]["latency_ms"]
    assert "queue_ms" not in latency
    assert latency["detection_ms"]["mean"] is None  # tracker reports no timings: never invented
    assert latency["decode_ms"]["count"] == 4


def test_replay_only(tmp_path: Path, camera_file: Path) -> None:
    video = write_synthetic_video(tmp_path / "v.mp4", frames=15)
    mot = tmp_path / "t.txt"
    mot.write_text(
        "\n".join(f"{i},1,20,20,30,60,0.9,-1,-1,-1" for i in range(1, 16)) + "\n", encoding="utf-8"
    )
    report = benchmark.run_replay_benchmark(mot, video, load_camera_config(camera_file), None, 3)
    assert report["mode"] == "replay_only"
    assert report["frames_measured"] == 12
    assert report["summary"]["latency_ms"]["queue_ms"]["count"] == 12
    assert report["summary"]["mean_people_per_frame"] == 1.0


def test_main_replay_writes_json(tmp_path: Path, camera_file: Path) -> None:
    video = write_synthetic_video(tmp_path / "v.mp4", frames=12)
    mot = tmp_path / "t.txt"
    mot.write_text("1,1,20,20,30,60,0.9,-1,-1,-1\n", encoding="utf-8")
    out = tmp_path / "out" / "b.json"
    argv = [
        "--video",
        str(video),
        "--tracks",
        str(mot),
        "--camera-config",
        str(camera_file),
        "--output",
        str(out),
    ]
    assert benchmark.main(argv) == 0
    data = json.loads(out.read_text())
    assert data["model"] is None
    assert {"hardware", "os", "python", "torch", "ultralytics", "opencv"} <= set(data["environment"])
    assert data["camera"]["membership_algorithm"] == "legacy"
