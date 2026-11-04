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

from ai_engine import run_tracking
from ai_engine.detection.types import PersonDetection
from ai_engine.pipeline import iter_frame_results
from ai_engine.queue.camera_config import (
    AlertSettings,
    CameraConfig,
    MembershipSettings,
    StitchingSettings,
    load_camera_config,
)
from ai_engine.run_tracking import FrameUpdate, RunOptions, output_paths, parse_arguments, process_video
from ai_engine.tools.draw_zone import main as draw_zone_main
from ai_engine.tools.make_annotation_kit import make_kit
from ai_engine.video.transcode import reencode_h264
from ai_engine.video.video_loader import VideoLoader
from ai_engine.visualization.privacy import blur_heads
from tests.conftest import (
    SYNTHETIC_FPS,
    SYNTHETIC_SIZE,
    FakeTracker,
    WalkerTracker,
    write_synthetic_video,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


# --- waiting-time scenario ----------------------------------------------------


WAIT_CAMERA = CameraConfig(
    camera_id="walker",
    queue_zone=((0.0, 0.0), (0.6, 0.0), (0.6, 1.0), (0.0, 1.0)),
    service_line=((0.8, 0.0), (0.8, 1.0)),
    membership=MembershipSettings(
        min_dwell_seconds=1.0, max_speed_heights_per_second=0.5, exit_grace_seconds=0.3
    ),
    alerts=AlertSettings(max_queue_length=0.5, min_duration_seconds=1.0, cooldown_seconds=5.0),
)


def run_walker(tmp_path: Path, camera: CameraConfig = WAIT_CAMERA, **options: Any) -> dict[str, Any]:
    video = write_synthetic_video(tmp_path / "walker.mp4", frames=60)
    return process_video(
        video, WalkerTracker(), RunOptions(output_dir=tmp_path / "out", camera=camera, h264=False, **options)
    )


def test_waiting_scenario_writes_waits_alerts_events_and_report(tmp_path: Path) -> None:
    report = run_walker(tmp_path)
    out = tmp_path / "out"

    with (out / "events" / "walker_waits.csv").open() as handle:
        waits = list(csv.DictReader(handle))
    assert list(waits[0]) == [
        "person_key",
        "track_ids",
        "join_s",
        "service_s",
        "wait_s",
        "abandoned",
        "left_s",
    ]
    assert len(waits) == 1
    row = waits[0]
    assert row["abandoned"] == "false" and row["track_ids"] == "1" and row["left_s"] == ""
    assert float(row["join_s"]) == pytest.approx(0.5)  # first time in the zone, not confirmation
    assert float(row["service_s"]) == pytest.approx(5.7)
    assert float(row["wait_s"]) == pytest.approx(5.2)

    with (out / "events" / "walker_alerts.csv").open() as handle:
        alerts = list(csv.DictReader(handle))
    assert alerts[0]["event_type"] == "congestion_start" and alerts[0]["reasons"] == "queue_length"
    assert float(alerts[0]["condition_since_s"]) <= float(alerts[0]["timestamp_s"])

    lines = (out / "events" / "walker_events.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines]
    assert {e["event_type"] for e in events} >= {
        "zone_enter",
        "queue_join",
        "service_start",
        "congestion_start",
    }
    assert all(e["schema_version"] == "1.0" for e in events)
    start = next(e for e in events if e["event_type"] == "service_start")
    assert start["wait_s"] == pytest.approx(5.2)

    with (out / "events" / "walker_zone_events.csv").open() as handle:
        zone_events = list(csv.DictReader(handle))
    assert {e["event_type"] for e in zone_events} <= {"zone_enter", "zone_exit"}
    assert "zone_entered_frame" in zone_events[0]

    with (out / "timeseries" / "walker_queue.csv").open() as handle:
        series = list(csv.DictReader(handle))
    assert "predicted_wait_s" in series[0] and series[0]["prediction_status"] == "unknown"

    queue = report["queue"]
    assert queue["service_point"] == "line"
    assert queue["waiting"]["completed"] == 1 and queue["waiting"]["abandoned"] == 0
    assert queue["waiting"]["mean_wait_s"] == pytest.approx(5.2)
    assert queue["stitching"]["enabled"] is True
    assert queue["alerts"]["congestion_starts"] >= 1
    assert queue["event_counts"]["service_start"] == 1
    assert set(report["outputs"]) >= {"waits", "alerts", "event_log"}


def test_on_frame_called_once_per_processed_frame_after_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writers: list[run_tracking.VideoWriter] = []

    class SpyWriter(run_tracking.VideoWriter):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            writers.append(self)

    monkeypatch.setattr(run_tracking, "VideoWriter", SpyWriter)
    video = write_synthetic_video(tmp_path / "clip.mp4", frames=10)
    updates: list[FrameUpdate] = []
    written_at_call: list[int] = []

    def on_frame(update: FrameUpdate) -> None:
        updates.append(update)
        written_at_call.append(writers[0].frames_written)

    process_video(
        video,
        FakeTracker(),
        RunOptions(output_dir=tmp_path / "out", frame_stride=2, h264=False),
        on_frame=on_frame,
    )

    assert [u.frame_index for u in updates] == [0, 2, 4, 6, 8]
    assert all(u.frames_total == 10 for u in updates)
    assert written_at_call == [1, 2, 3, 4, 5]
    assert all(u.people_in_frame == 1 and u.processing_fps > 0 and u.queue is None for u in updates)
    assert updates[1].timestamp_s == pytest.approx(2 / SYNTHETIC_FPS)


def test_should_stop_ends_run_and_still_writes_report(synthetic_video: Path, tmp_path: Path) -> None:
    seen: list[FrameUpdate] = []
    report = process_video(
        synthetic_video,
        FakeTracker(),
        RunOptions(output_dir=tmp_path, h264=False),
        on_frame=seen.append,
        should_stop=lambda: len(seen) >= 3,
    )

    assert report["frames_processed"] == 3
    assert report["stopped_early"] is True
    assert (tmp_path / "metrics" / "synthetic_metrics.json").exists()


def test_callbacks_do_not_change_outputs(tmp_path: Path) -> None:
    video = write_synthetic_video(tmp_path / "walker.mp4", frames=60)
    hooks = {"on_frame": lambda update: None, "should_stop": lambda: False}
    for label, extra in (("plain", {}), ("hooked", hooks)):
        options = RunOptions(output_dir=tmp_path / label, camera=WAIT_CAMERA, h264=False)
        process_video(video, WalkerTracker(), options, **extra)
    for key in ("timeseries", "waits", "event_log"):
        plain = output_paths("walker", tmp_path / "plain")[key].read_bytes()
        hooked = output_paths("walker", tmp_path / "hooked")[key].read_bytes()
        assert plain == hooked


def test_no_stitching_flag_and_option(tmp_path: Path) -> None:
    assert parse_arguments(["--video", "x.mp4"]).no_stitching is False
    assert parse_arguments(["--video", "x.mp4", "--no-stitching"]).no_stitching is True
    camera = CameraConfig(
        camera_id="walker",
        queue_zone=WAIT_CAMERA.queue_zone,
        service_line=WAIT_CAMERA.service_line,
        membership=WAIT_CAMERA.membership,
        stitching=StitchingSettings(enabled=True),
    )
    report = run_walker(tmp_path, camera, stitching=False)
    assert report["queue"]["stitching"]["enabled"] is False
    # Without stitching the abandon timeout is just exit_grace (0.3 s), which expires before the
    # person reaches the service line, so they count as abandoned instead of served.
    assert report["queue"]["waiting"]["completed"] == 0 and report["queue"]["waiting"]["abandoned"] == 1


def test_camera_without_service_point_reports_note(tmp_path: Path) -> None:
    camera = CameraConfig(
        camera_id="walker", queue_zone=WAIT_CAMERA.queue_zone, membership=WAIT_CAMERA.membership
    )
    report = run_walker(tmp_path, camera)
    assert report["queue"]["waiting"] == {"note": "no service point configured"}
    with (tmp_path / "out" / "events" / "walker_waits.csv").open() as handle:
        assert list(csv.DictReader(handle)) == []
    assert output_paths("walker", tmp_path)["waits"].name == "walker_waits.csv"


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


def test_draw_zone_service_targets(synthetic_video: Path, tmp_path: Path) -> None:
    config_path = tmp_path / "cam.yaml"
    base = ["--video", str(synthetic_video), "--config", str(config_path)]
    assert draw_zone_main([*base, "--points", "0.1,0.1 0.9,0.1 0.9,0.9 0.1,0.9"]) == 0
    preview = tmp_path / "line.png"
    assert (
        draw_zone_main(
            [*base, "--target", "service_line", "--points", "0.3,0.2 0.7,0.2", "--preview", str(preview)]
        )
        == 0
    )
    camera = load_camera_config(config_path)
    assert camera.service_line == ((0.3, 0.2), (0.7, 0.2)) and camera.service_zone is None
    assert camera.queue_zone[0] == (0.1, 0.1)
    assert preview.exists()

    zone = "0.5,0.5 0.9,0.5 0.9,0.9"
    assert draw_zone_main([*base, "--target", "service_zone", "--points", zone]) == 0
    camera = load_camera_config(config_path)
    assert camera.service_line is None and len(camera.service_zone) == 3

    assert draw_zone_main([*base, "--target", "service_line", "--points", "0.3,0.2 0.7,0.2 0.5,0.5"]) == 1
    assert draw_zone_main([*base, "--target", "service_zone", "--points", "0.3,0.2 0.7,0.2"]) == 1
    assert draw_zone_main([*base, "--target", "service_line", "--points", "0.3,0.2 1.7,0.2"]) == 1
    missing = ["--video", str(synthetic_video), "--config", str(tmp_path / "new.yaml")]
    assert draw_zone_main([*missing, "--target", "service_line", "--points", "0.3,0.2 0.7,0.2"]) == 1


def test_annotation_kit_writes_waits_template(tmp_path: Path) -> None:
    video = write_synthetic_video(tmp_path / "clip.mp4", frames=15)
    make_kit(video, 1.0, None, tmp_path / "ann")
    waits_path = tmp_path / "ann" / "clip_waits_gt.csv"
    assert waits_path.read_text().splitlines() == ["person,join_s,service_s,abandoned"]
    waits_path.write_text("person,join_s,service_s,abandoned\nann,1,9,false\n")
    make_kit(video, 1.0, None, tmp_path / "ann", force=True)  # --force resets it
    assert waits_path.read_text().splitlines() == ["person,join_s,service_s,abandoned"]


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
        "ai_engine/evaluate_waiting.py",
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
