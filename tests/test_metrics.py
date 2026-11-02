"""Tests for performance metrics, JSON export, overlay drawing and the pipeline."""

import json
from pathlib import Path

import numpy as np
import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.metrics.performance import PerformanceTracker, save_json
from ai_engine.run_tracking import RunOptions, main, model_display_name, process_video
from ai_engine.tracking.tracker import PersonTracker
from ai_engine.video.video_loader import VideoLoader
from ai_engine.visualization.metrics_overlay import OverlayStats, draw_metrics_panel, draw_people
from tests.conftest import SYNTHETIC_FRAMES, SYNTHETIC_SIZE


def test_average_fps_uses_totals_not_last_frame() -> None:
    performance = PerformanceTracker(window=2)
    performance.record_frame(0.10, 0.20, people_count=2, track_ids=[1, 2])
    performance.record_frame(0.05, 0.10, people_count=5, track_ids=[2, 3])
    performance.record_frame(0.05, 0.10, people_count=0)
    assert performance.frames_processed == 3
    assert performance.total_inference_seconds == pytest.approx(0.20)
    assert performance.average_inference_fps == pytest.approx(3 / 0.20)
    assert performance.average_processing_fps == pytest.approx(3 / 0.40)
    assert performance.rolling_inference_fps == pytest.approx(2 / 0.10)
    assert performance.max_people_in_frame == 5
    assert performance.average_people_per_frame == pytest.approx(7 / 3)
    assert performance.unique_track_ids == {1, 2, 3}


def test_no_frames_gives_zero_fps() -> None:
    performance = PerformanceTracker()
    assert performance.average_inference_fps == 0.0
    assert performance.average_processing_fps == 0.0
    assert performance.average_people_per_frame == 0.0


def test_negative_timing_rejected() -> None:
    with pytest.raises(ValueError):
        PerformanceTracker().record_frame(-1.0, 0.1, 0)


def test_report_structure_and_json_round_trip(tmp_path: Path) -> None:
    performance = PerformanceTracker()
    performance.record_frame(0.02, 0.04, people_count=3, track_ids=[1, 2, 3])
    report = performance.build_report(device="cpu", total_runtime_seconds=1.5)
    assert report["project"] == "QueueVision"
    assert report["model"] == "YOLO11n"
    assert report["tracker"] == "ByteTrack"
    assert report["frames_processed"] == 1
    assert report["performance"]["average_inference_fps"] == 50.0
    assert report["performance"]["average_processing_fps"] == 25.0
    assert report["statistics"]["maximum_people_detected_in_a_frame"] == 3

    path = save_json(report, tmp_path / "metrics" / "run.json")
    assert json.loads(path.read_text()) == report


def test_overlay_draws_on_frame() -> None:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    draw_people(frame, [PersonDetection(bbox=(300, 100, 380, 300), confidence=0.9, track_id=4)])
    draw_metrics_panel(frame, OverlayStats(1, 10, 1, 30.0, 25.0, "cpu"))
    assert frame[200, 300].tolist() == [0, 200, 0]  # green box edge
    assert frame[20:200, 10:270].any()  # panel/text drawn
    # Panels stay inside frames smaller than the panel itself.
    draw_metrics_panel(np.zeros((50, 80, 3), dtype=np.uint8), OverlayStats(1, 0, 0, 0.0, 0.0, "mps"))


def test_model_display_name() -> None:
    assert model_display_name(Path("yolo11n.pt")) == "YOLO11n"
    assert model_display_name(Path("models/yolo11s.pt")) == "YOLO11s"


@pytest.mark.integration
def test_pipeline_writes_annotated_video_and_metrics(
    real_tracker: PersonTracker, synthetic_video: Path, tmp_path: Path
) -> None:
    report = process_video(synthetic_video, real_tracker, RunOptions(output_dir=tmp_path, h264=False))

    video_path = tmp_path / "videos" / "synthetic_tracked.mp4"
    metrics_path = tmp_path / "metrics" / "synthetic_metrics.json"
    assert report["outputs"]["video"] == str(video_path)
    with VideoLoader(video_path) as loader:
        assert loader.metadata.frame_count == SYNTHETIC_FRAMES
        assert (loader.metadata.width, loader.metadata.height) == SYNTHETIC_SIZE

    saved = json.loads(metrics_path.read_text())
    assert saved["frames_processed"] == SYNTHETIC_FRAMES
    assert saved["device"] == "cpu"
    assert saved["performance"]["average_inference_fps"] > 0
    assert saved["performance"]["average_processing_fps"] > 0
    assert saved["performance"]["total_processing_seconds"] >= saved["performance"]["total_inference_seconds"]


@pytest.mark.integration
def test_cli_respects_max_frames_and_reports_errors(synthetic_video: Path, tmp_path: Path) -> None:
    assert (
        main(
            [
                "--video",
                str(synthetic_video),
                "--output",
                str(tmp_path),
                "--device",
                "cpu",
                "--max-frames",
                "4",
                "--no-h264",
            ]
        )
        == 0
    )
    saved = json.loads((tmp_path / "metrics" / "synthetic_metrics.json").read_text())
    assert saved["frames_processed"] == 4
    assert saved["configuration"]["tracker"]["input_confidence"] == 0.1
    assert saved["configuration"]["tracker"]["resolved"]["new_track_thresh"] == 0.35
    assert saved["configuration"]["video_codec"] == "mp4v"
    assert (tmp_path / "logs" / "synthetic_tracking.log").read_text()

    assert main(["--video", str(tmp_path / "missing.mp4"), "--output", str(tmp_path), "--device", "cpu"]) == 1
