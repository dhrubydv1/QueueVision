"""Replay harness tests: synthetic unit tests plus a data-dependent correctness gate."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.queue_eval import compare_counts
from ai_engine.evaluation.replay import (
    FrameError,
    SeriesRow,
    apply_overrides,
    metrics_from_errors,
    pool_scores,
    read_mot_tracks,
    read_split_file,
    replay_queue,
    score_series,
    video_properties,
)
from ai_engine.queue import CameraConfig, CameraConfigError, MembershipSettings, load_camera_config
from ai_engine.queue.event_schema import ZoneEvent

ROOT = Path(__file__).resolve().parent.parent
CAD_CLIPS = [f"cad_seq{n}" for n in (10, 25, 26, 27, 39, 40, 41)]
ZONE = ((0.2, 0.4), (0.8, 0.4), (0.8, 0.9), (0.2, 0.9))


def test_read_mot_tracks_converts_to_zero_based_int_boxes(tmp_path: Path) -> None:
    path = tmp_path / "t_mot.txt"
    path.write_text(
        "1,7,10.00,20.00,30.00,40.00,0.9000,-1,-1,-1\n"
        "1,8,0.00,0.00,5.00,5.00,0.5000,-1,-1,-1\n"
        "3,7,12.00,22.00,30.00,40.00,0.8000,-1,-1,-1\n"
    )
    tracks = read_mot_tracks(path)
    assert sorted(tracks) == [0, 2]
    assert tracks[0][0] == PersonDetection((10, 20, 40, 60), 0.9, 7)
    assert [p.track_id for p in tracks[0]] == [7, 8]
    assert tracks[2][0].bbox == (12, 22, 42, 62)


def test_read_mot_tracks_rejects_zero_based_frames(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_text("0,1,1,1,1,1,0.9,-1,-1,-1\n")
    with pytest.raises(ValueError):
        read_mot_tracks(path)


def test_apply_overrides_replaces_and_validates() -> None:
    camera = CameraConfig(camera_id="c", queue_zone=ZONE)
    changed = apply_overrides(
        camera, {"membership": {"algorithm": "temporal"}, "stitching": {"enabled": False}}
    )
    assert changed.membership.algorithm == "temporal"
    assert changed.membership.min_dwell_seconds == camera.membership.min_dwell_seconds
    assert changed.stitching.enabled is False
    assert camera.membership.algorithm == "legacy"  # original untouched
    assert apply_overrides(camera, None) is camera
    with pytest.raises(CameraConfigError):
        apply_overrides(camera, {"membership": {"algorithm": "bogus"}})
    with pytest.raises(CameraConfigError):
        apply_overrides(camera, {"membership": {"nope": 1}})
    with pytest.raises(CameraConfigError):
        apply_overrides(camera, {"unknown": {}})


def _rows(values: list[tuple[float, float, int]]) -> list[SeriesRow]:
    return [SeriesRow(t, int(t * 30), 0, raw, smooth, None, None) for t, smooth, raw in values]


def test_score_series_windows_and_matches_compare_counts() -> None:
    rows = _rows([(0.0, 0.0, 0), (1.0, 1.0, 2), (5.0, 3.0, 4), (6.0, 3.0, 3)])
    annotations = [(0.0, 0), (1.0, 2), (5.0, 3), (6.0, 5)]
    scores = score_series(rows, annotations)
    assert scores.metrics()["smoothed_all"] == compare_counts(
        [(r.time_s, r.queue_length) for r in rows], annotations
    )
    assert scores.metrics()["raw_all"] == compare_counts(
        [(r.time_s, float(r.raw_queue_count)) for r in rows], annotations
    )
    from5 = scores.metrics()["smoothed_from5s"]
    assert from5["matched"] == 2  # annotations at 5 s and 6 s only
    assert from5["mae"] == 1.0  # |3-3| and |3-5| -> 1.0


def test_pooled_metrics_pool_frames_not_clip_means() -> None:
    clip_a = score_series(_rows([(0.0, 0.0, 0)]), [(0.0, 1)], from_s=0.0)
    clip_b = score_series(_rows([(float(t), 0.0, 0) for t in range(4)]), [(float(t), 0) for t in range(4)])
    pooled = pool_scores([clip_a, clip_b])["smoothed_all"]
    assert pooled["matched"] == 5
    assert pooled["mae"] == 0.2  # 1 error over 5 frames; a mean of clip means would give 0.5
    assert pooled["bias"] == -0.2


def test_metrics_from_errors_empty_and_unmatched() -> None:
    assert metrics_from_errors([], 3) == {"matched": 0, "unmatched": 3}
    m = metrics_from_errors([FrameError(0.0, 1.5, 2), FrameError(1.0, -0.5, 0)], 1)
    assert m["mae"] == 1.0 and m["within_one_rate"] == 0.5 and m["exact_match_rate"] == 0.5


def test_read_split_file_handles_comments_and_absence(tmp_path: Path) -> None:
    path = tmp_path / "val.txt"
    path.write_text("# comment\ncad_seq10\n\ncad_seq25  # trailing\n")
    assert read_split_file(path) == ["cad_seq10", "cad_seq25"]
    assert read_split_file(tmp_path / "missing.txt") == []


def test_replay_feeds_every_frame_and_samples_once_per_second() -> None:
    camera = CameraConfig(
        camera_id="c", queue_zone=ZONE, membership=MembershipSettings(min_dwell_seconds=1.0)
    )
    person = PersonDetection((450, 500, 550, 700), 0.9, 1)
    tracks = {i: [person] for i in range(0, 60)}
    result = replay_queue(tracks, camera, 10.0, (1000, 1000), 60)
    assert [row.time_s for row in result.series] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert result.frames_replayed == 60
    assert result.series[0].raw_queue_count == 0 and result.series[-1].raw_queue_count == 1
    assert any(isinstance(e, ZoneEvent) and e.event_type == "zone_enter" for e in result.events)


# --------------------------------------------------------------- correctness gate (needs local data)

TRACKS_DIR = ROOT / "outputs/s5_tracks/cad/tracks"
REFERENCE_DIR = ROOT / "outputs/s5_tracks/cad/timeseries"
EVENTS_DIR = ROOT / "outputs/s5_tracks/cad/events"


@pytest.mark.parametrize("clip", CAD_CLIPS)
def test_replay_reproduces_run_tracking_time_series(clip: str) -> None:
    paths = [
        TRACKS_DIR / f"{clip}_mot.txt",
        REFERENCE_DIR / f"{clip}_queue.csv",
        EVENTS_DIR / f"{clip}_zone_events.csv",
        ROOT / f"datasets/raw/{clip}.mp4",
        ROOT / f"configs/cameras/{clip}.yaml",
    ]
    if not all(p.is_file() for p in paths):
        pytest.skip("saved tracks / videos not available")
    fps, size, frame_count = video_properties(paths[3])
    camera = load_camera_config(paths[4])
    result = replay_queue(read_mot_tracks(paths[0]), camera, fps, size, frame_count)

    with paths[1].open(newline="") as handle:
        reference = list(csv.DictReader(handle))
    assert len(reference) == len(result.series)
    for ref, row in zip(reference, result.series, strict=True):
        assert float(ref["time_s"]) == pytest.approx(row.time_s)
        assert int(ref["frame"]) == row.frame
        assert int(ref["people_in_frame"]) == row.people_in_frame
        assert int(ref["raw_queue_count"]) == row.raw_queue_count
        assert float(ref["queue_length"]) == pytest.approx(row.queue_length)

    with paths[2].open(newline="") as handle:
        ref_events = [(r["event_type"], int(r["track_id"]), int(r["frame"])) for r in csv.DictReader(handle)]
    events = [(e.event_type, e.track_id, e.frame_index) for e in result.events if isinstance(e, ZoneEvent)]
    assert events == ref_events


@pytest.mark.parametrize("clip", ["cad_seq10", "cad_seq27"])
def test_temporal_without_service_zone_or_stitching_matches_legacy(clip: str) -> None:
    track_path = TRACKS_DIR / f"{clip}_mot.txt"
    video_path = ROOT / f"datasets/raw/{clip}.mp4"
    camera_path = ROOT / f"configs/cameras/{clip}.yaml"
    if not all(p.is_file() for p in (track_path, video_path, camera_path)):
        pytest.skip("saved tracks / videos not available")
    from dataclasses import replace

    fps, size, frame_count = video_properties(video_path)
    tracks = read_mot_tracks(track_path)
    camera = replace(load_camera_config(camera_path), service_zone=None)
    legacy = replay_queue(tracks, camera, fps, size, frame_count, stitching_enabled=False)
    temporal_camera = apply_overrides(camera, {"membership": {"algorithm": "temporal"}})
    temporal = replay_queue(tracks, temporal_camera, fps, size, frame_count, stitching_enabled=False)
    assert [r.raw_queue_count for r in legacy.series] == [r.raw_queue_count for r in temporal.series]
