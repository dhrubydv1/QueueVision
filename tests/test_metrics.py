"""Tests for performance metrics, JSON export, overlay drawing and the pipeline."""

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.metrics.performance import PerformanceTracker, save_json
from ai_engine.metrics.queue_outputs import (
    AlertWriter,
    EventLogWriter,
    QueueSummary,
    QueueTimeSeriesWriter,
    WaitsWriter,
    ZoneEventWriter,
)
from ai_engine.queue.event_schema import (
    CongestionEvent,
    QueueAbandonEvent,
    QueueJoinEvent,
    ServiceStartEvent,
    ZoneEvent,
)
from ai_engine.queue.waiting_time import ServiceRate, WaitPrediction
from ai_engine.run_tracking import RunOptions, main, model_display_name, process_video
from ai_engine.tracking.tracker import PersonTracker
from ai_engine.video.video_loader import VideoLoader
from ai_engine.visualization.metrics_overlay import (
    OverlayStats,
    draw_alert_banner,
    draw_metrics_panel,
    draw_people,
    draw_service_point,
    format_duration,
    predicted_wait_text,
    service_rate_text,
)
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


# --- waiting-time outputs and overlay -------------------------------------------


def test_format_duration() -> None:
    assert [format_duration(s) for s in (0, 45, 59.6, 130, 3600, 3725)] == [
        "0s",
        "45s",
        "1m 00s",
        "2m 10s",
        "1h 00m",
        "1h 02m",
    ]
    assert format_duration(-5) == "0s"


def test_overlay_texts() -> None:
    assert service_rate_text(ServiceRate(1.84, "ok")) == "Service rate: 1.8/min"
    assert service_rate_text(ServiceRate(None, "insufficient_data")) == "Service rate: insufficient data"
    assert (
        predicted_wait_text(WaitPrediction(130, 100, 185, "ok")) == "Predicted wait: 2m 10s (1m 40s-3m 05s)"
    )
    assert predicted_wait_text(WaitPrediction(45, None, None, "ok")) == "Predicted wait: 45s"
    assert predicted_wait_text(WaitPrediction(None, None, None, "unknown")) == "Predicted wait: unknown"


def test_service_point_alert_and_service_colour_drawing() -> None:
    frame = np.zeros((100, 200, 3), np.uint8)
    draw_service_point(frame, np.array([[20, 50], [180, 50]], dtype=np.int32))
    assert tuple(frame[50, 100]) == (255, 0, 255)
    polygon = np.zeros((100, 200, 3), np.uint8)
    draw_service_point(polygon, np.array([[20, 40], [80, 40], [80, 90]], dtype=np.int32))
    assert polygon.any()
    banner = np.zeros((100, 200, 3), np.uint8)
    draw_alert_banner(banner, ("queue_length", "predicted_wait"))
    assert banner[:30].any() and not banner[30:].any()
    bottom = np.zeros((100, 200, 3), np.uint8)
    draw_alert_banner(bottom, ("queue_length",), position="bottom")
    assert bottom[70:].any() and not bottom[:70].any()
    with pytest.raises(ValueError):
        draw_alert_banner(bottom, (), position="left")
    person = PersonDetection(bbox=(50, 20, 100, 90), confidence=0.9, track_id=7)
    served = np.zeros((100, 200, 3), np.uint8)
    draw_people(served, [person], at_service={7})
    assert tuple(served[20, 60]) == (255, 0, 255)
    panel = np.zeros((200, 400, 3), np.uint8)
    stats = OverlayStats(1, 10, 1, 10.0, 9.0, "cpu", 2.0, "Service rate: 1.8/min", "Predicted wait: unknown")
    draw_metrics_panel(panel, stats)
    assert panel.any()


def test_queue_writers(tmp_path: Path) -> None:
    zone = ZoneEvent("zone_enter", 3, 4.0, 40, 3.5, 35, 4.0)
    join = QueueJoinEvent("t3", 3, 3.5, 35, 4.0)
    served = ServiceStartEvent("t3", (3, 5), 9.0, 90, 9.0, 3.5, 5.5)
    abandon = QueueAbandonEvent("t8", (8,), 12.0, 120, 14.0, 6.0, 6.0)
    start = CongestionEvent(
        "congestion_start", 20.0, 200, 20.0, 10.0, ("queue_length", "predicted_wait"), 6.0, 90.0
    )
    events = [zone, join, served, abandon, start]

    with (
        WaitsWriter(tmp_path / "w.csv") as waits,
        AlertWriter(tmp_path / "a.csv") as alerts,
        EventLogWriter(tmp_path / "e.jsonl") as log,
        ZoneEventWriter(tmp_path / "z.csv") as zones,
    ):
        for event in events:
            for writer in (waits, alerts, log, zones):
                writer.add(event)
        assert (waits.rows_written, alerts.rows_written, log.rows_written, zones.rows_written) == (2, 1, 5, 1)

    with (tmp_path / "w.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0] == {
        "person_key": "t3",
        "track_ids": "3;5",
        "join_s": "3.500",
        "service_s": "9.000",
        "wait_s": "5.500",
        "abandoned": "false",
        "left_s": "",
    }
    assert rows[1]["abandoned"] == "true" and rows[1]["left_s"] == "12.000" and rows[1]["service_s"] == ""
    with (tmp_path / "a.csv").open() as handle:
        alert = next(csv.DictReader(handle))
    assert alert["reasons"] == "queue_length;predicted_wait" and alert["predicted_wait_s"] == "90.0"
    assert alert["condition_since_s"] == "10.000"
    parsed = [json.loads(line) for line in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert [e["event_type"] for e in parsed] == [
        "zone_enter",
        "queue_join",
        "service_start",
        "queue_abandon",
        "congestion_start",
    ]
    with (tmp_path / "z.csv").open() as handle:
        zone_row = next(csv.DictReader(handle))
    assert zone_row["zone_entered_frame"] == "35"

    summary = QueueSummary()
    summary.add(1, 1.0, tuple(events))
    result = summary.to_dict()
    assert result["zone_enter_events"] == 1
    assert result["event_counts"]["queue_abandon"] == 1 and result["event_counts"]["zone_exit"] == 0


def test_timeseries_writer_new_columns(tmp_path: Path) -> None:
    with QueueTimeSeriesWriter(tmp_path / "t.csv") as writer:
        writer.add(0.0, 0, 1, 1, 1.0)
        writer.add(
            1.0, 10, 1, 1, 1.0, ServiceRate(2.0, "ok", 1.5, 2.5), WaitPrediction(30.0, 24.0, 40.0, "ok")
        )
    with (tmp_path / "t.csv").open() as handle:
        first, second = list(csv.DictReader(handle))
    assert first["service_rate_per_min"] == "" and first["prediction_status"] == "unknown"
    assert second["service_rate_per_min"] == "2.000" and second["service_rate_status"] == "ok"
    assert (second["predicted_wait_s"], second["predicted_wait_low_s"], second["predicted_wait_high_s"]) == (
        "30.0",
        "24.0",
        "40.0",
    )
