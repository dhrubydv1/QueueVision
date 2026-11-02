"""Pipeline, outputs, live sources, privacy, transcoding and tools.

The pipeline tests drive a MOCKED tracker that returns scripted people, so
they check frame handling and output files without running a model.
"""

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.pipeline import iter_frame_results
from ai_engine.queue.camera_config import CameraConfig, MembershipSettings, load_camera_config
from ai_engine.run_tracking import RunOptions, process_video
from ai_engine.tools.draw_zone import main as draw_zone_main
from ai_engine.tools.make_annotation_kit import make_kit
from ai_engine.video.transcode import reencode_h264
from ai_engine.video.video_loader import VideoLoader
from ai_engine.visualization.privacy import blur_heads
from tests.conftest import SYNTHETIC_FPS, SYNTHETIC_SIZE, write_synthetic_video

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FakeDetector:
    device = "cpu"
    model_path = Path("yolo11n.pt")
    confidence = 0.35
    image_size = 640


class FakeTracker:
    """MOCKED tracker: one person standing still in the frame centre."""

    def __init__(self) -> None:
        self.detector = FakeDetector()
        self.last_inference_seconds = 0.001
        self.updates = 0
        self.configured_fps: float | None = None

    def configure(self, effective_fps: float) -> None:
        self.configured_fps = effective_fps

    def warmup(self, frame_shape: tuple[int, int, int]) -> None:
        pass

    def update(self, frame: np.ndarray) -> list[PersonDetection]:
        self.updates += 1
        return [PersonDetection(bbox=(60, 30, 100, 110), confidence=0.8, track_id=1)]

    def describe(self) -> dict[str, Any]:
        return {"type": "fake"}


CAMERA = CameraConfig(
    camera_id="synthetic",
    queue_zone=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)),
    membership=MembershipSettings(
        min_dwell_seconds=0.5, max_speed_heights_per_second=0.5, exit_grace_seconds=0.3
    ),
)


def test_generator_stride_timestamps_and_max_frames(synthetic_video: Path) -> None:
    tracker = FakeTracker()
    with VideoLoader(synthetic_video) as loader:
        results = list(iter_frame_results(loader, tracker, frame_stride=3))
    assert [r.frame_index for r in results] == [0, 3, 6, 9, 12]
    assert [r.timestamp_s for r in results] == pytest.approx([i / SYNTHETIC_FPS for i in (0, 3, 6, 9, 12)])
    assert all(r.queue is None for r in results)
    with VideoLoader(synthetic_video) as loader:
        assert len(list(iter_frame_results(loader, tracker, max_frames=4))) == 4
    with VideoLoader(synthetic_video) as loader, pytest.raises(ValueError):
        list(iter_frame_results(loader, tracker, frame_stride=0))


def test_process_video_with_queue_writes_all_outputs(synthetic_video: Path, tmp_path: Path) -> None:
    tracker = FakeTracker()
    options = RunOptions(
        output_dir=tmp_path,
        camera=CAMERA,
        frame_stride=2,
        save_mot=True,
        h264=False,
        panel_position="bottom-right",
    )
    report = process_video(synthetic_video, tracker, options)

    assert tracker.configured_fps == pytest.approx(SYNTHETIC_FPS / 2)
    assert report["frames_processed"] == 8  # frames 0,2,...,14
    with VideoLoader(tmp_path / "videos" / "synthetic_tracked.mp4") as loader:
        assert loader.metadata.frame_count == 8
        assert loader.metadata.fps == pytest.approx(SYNTHETIC_FPS / 2)

    queue = report["queue"]
    assert queue["camera_id"] == "synthetic"
    assert queue["zone_enter_events"] == 1 and queue["zone_exit_events"] == 0
    assert queue["members_still_in_queue_at_end"] == 1
    assert queue["maximum_queue_length"] == 1

    with (tmp_path / "timeseries" / "synthetic_queue.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert [float(r["time_s"]) for r in rows] == [0.0, 1.0]  # one row per video second
    assert rows[0]["raw_queue_count"] == "0" and rows[1]["raw_queue_count"] == "1"
    with (tmp_path / "events" / "synthetic_zone_events.csv").open() as handle:
        events = list(csv.DictReader(handle))
    assert events[0]["event_type"] == "zone_enter" and events[0]["zone_entered_s"] == "0.000"

    mot_lines = (tmp_path / "tracks" / "synthetic_mot.txt").read_text().splitlines()
    assert mot_lines[1].startswith("3,1,")  # second processed frame is source frame index 2
    saved = json.loads((tmp_path / "metrics" / "synthetic_metrics.json").read_text())
    assert saved["configuration"]["frame_stride"] == 2
    assert set(saved["outputs"]) >= {"video", "metrics", "tracks", "timeseries", "events"}


def test_process_video_without_camera_has_no_queue_outputs(synthetic_video: Path, tmp_path: Path) -> None:
    report = process_video(synthetic_video, FakeTracker(), RunOptions(output_dir=tmp_path, h264=False))
    assert "queue" not in report
    assert not (tmp_path / "timeseries").exists() and not (tmp_path / "events").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_h264_reencode_replaces_video_and_can_keep_original(synthetic_video: Path, tmp_path: Path) -> None:
    report = process_video(
        synthetic_video, FakeTracker(), RunOptions(output_dir=tmp_path, h264=True, keep_mp4v=True)
    )
    video = tmp_path / "videos" / "synthetic_tracked.mp4"
    assert report["configuration"]["video_codec"] == "h264"
    assert (tmp_path / "videos" / "synthetic_tracked_mp4v.mp4").exists()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "csv=p=0",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "h264"


def test_h264_reencode_without_ffmpeg_keeps_mp4v(synthetic_video: Path, monkeypatch) -> None:
    monkeypatch.setattr("ai_engine.video.transcode.ffmpeg_path", lambda: None)
    assert reencode_h264(synthetic_video) is False
    assert synthetic_video.exists()


# --- live sources (mocked camera) ---------------------------------------------


class FakeCapture:
    """MOCKED cv2.VideoCapture for a webcam that reports no FPS."""

    def __init__(self, target: Any, frames: int = 50) -> None:
        self.target = target
        self.remaining = frames
        self.opened = True

    def isOpened(self) -> bool:
        return self.opened

    def get(self, prop: int) -> float:
        return {cv2.CAP_PROP_FRAME_WIDTH: 64, cv2.CAP_PROP_FRAME_HEIGHT: 48}.get(prop, 0.0)

    def read(self):
        if self.remaining <= 0:
            return False, None
        self.remaining -= 1
        return True, np.full((48, 64, 3), self.remaining, np.uint8)

    def grab(self) -> bool:
        return self.read()[0]

    def release(self) -> None:
        self.opened = False


def test_camera_index_source_measures_fps_and_keeps_sampled_frames() -> None:
    with VideoLoader("0", capture_factory=FakeCapture) as loader:
        metadata = loader.metadata
        assert loader.is_live and metadata.path == "0"
        assert metadata.fps_source == "measured" and metadata.fps > 0
        assert metadata.frame_count == 0
        frames = [frame for _, frame in loader]
    assert len(frames) == 50  # the 30 frames used to measure FPS are not lost


# --- privacy ------------------------------------------------------------------


def test_blur_heads_changes_only_head_region() -> None:
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    original = frame.copy()
    blur_heads(frame, [PersonDetection(bbox=(50, 20, 150, 120), confidence=0.9, track_id=1)], 0.2)
    head = (slice(20, 40), slice(50, 150))
    assert not np.array_equal(frame[head], original[head])
    outside = np.ones(frame.shape[:2], bool)
    outside[head] = False
    assert np.array_equal(frame[outside], original[outside])
    with pytest.raises(ValueError):
        blur_heads(frame, [], 0)


# --- tools --------------------------------------------------------------------


def test_draw_zone_non_interactive_writes_config_and_preview(synthetic_video: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "cams" / "test.yaml"
    preview = tmp_path / "zone.png"
    points = "0.1,0.1 0.9,0.1 0.9,0.9 0.1,0.9"
    assert (
        draw_zone_main(
            [
                "--video",
                str(synthetic_video),
                "--config",
                str(config_path),
                "--points",
                points,
                "--preview",
                str(preview),
            ]
        )
        == 0
    )
    camera = load_camera_config(config_path)
    assert camera.camera_id == "test" and camera.queue_zone[0] == (0.1, 0.1)
    image = cv2.imread(str(preview))
    assert image.shape[:2] == (SYNTHETIC_SIZE[1], SYNTHETIC_SIZE[0])

    # Updating keeps other settings; bad points are rejected.
    assert (
        draw_zone_main(
            ["--video", str(synthetic_video), "--config", str(config_path), "--points", "0,0 1,0 1,1"]
        )
        == 0
    )
    assert len(load_camera_config(config_path).queue_zone) == 3
    assert (
        draw_zone_main(
            ["--video", str(synthetic_video), "--config", str(config_path), "--points", "0,0 2,0 1,1"]
        )
        == 1
    )


def test_annotation_kit_frames_and_template(tmp_path: Path) -> None:
    video = write_synthetic_video(tmp_path / "clip.mp4", frames=25)  # 2.5 s at 10 fps
    camera_path = PROJECT_ROOT / "configs" / "cameras" / "example_queue.yaml"
    frames_dir, csv_path, count = make_kit(video, 1.0, camera_path, tmp_path / "ann")
    assert count == 3
    assert sorted(p.name for p in frames_dir.iterdir()) == [
        "t00000.0_f000000.png",
        "t00001.0_f000010.png",
        "t00002.0_f000020.png",
    ]
    with csv_path.open() as handle:
        rows = list(csv.DictReader(handle))
    assert [r["frame"] for r in rows] == ["0", "10", "20"] and all(r["queue_count"] == "" for r in rows)
    with pytest.raises(FileExistsError):
        make_kit(video, 1.0, None, tmp_path / "ann")


@pytest.mark.parametrize(
    "script",
    [
        "ai_engine/run_tracking.py",
        "ai_engine/evaluate_tracking.py",
        "ai_engine/evaluate_queue.py",
        "ai_engine/tools/draw_zone.py",
        "ai_engine/tools/make_annotation_kit.py",
        "ai_engine/tools/export_model.py",
    ],
)
def test_scripts_run_directly(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, script, "--help"], capture_output=True, text=True, cwd=PROJECT_ROOT, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage" in completed.stdout.lower()


@pytest.mark.integration
def test_direct_run_does_not_shadow_stdlib_queue(tmp_path: Path) -> None:
    """Loading the model imports torch, which imports the stdlib ``queue`` module.

    Running the script directly must not let ai_engine/queue shadow it.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "ai_engine/run_tracking.py",
            "--video",
            str(tmp_path / "missing.mp4"),
            "--device",
            "cpu",
            "--no-h264",
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert "cannot import name" not in completed.stderr
    assert "Video file does not exist" in completed.stderr
    assert completed.returncode == 1
