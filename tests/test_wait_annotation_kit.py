"""Tests for the waiting-time annotation kit and its sheet checker."""

from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np
import pytest

from ai_engine.evaluation.wait_eval import OBSERVED_COLUMNS
from ai_engine.tools.make_wait_annotation_kit import HINT_COLUMNS, check_sheet, main, make_wait_kit

W, H, FPS, N = 160, 128, 10.0, 15
CAMERA = """camera_id: t
queue_zone: [[0.0, 0.5], [1.0, 0.5], [1.0, 1.0], [0.0, 1.0]]
service_zone: [[0.0, 0.5], [0.2, 0.5], [0.2, 1.0], [0.0, 1.0]]
"""


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "clipA.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for _ in range(N):
        writer.write(np.full((H, W, 3), 90, np.uint8))
    writer.release()
    return path


@pytest.fixture
def camera(tmp_path: Path) -> Path:
    path = tmp_path / "cam.yaml"
    path.write_text(CAMERA)
    return path


@pytest.fixture
def mot(tmp_path: Path) -> Path:
    # id 1 enters the queue zone (foot y=110 > 64) at frame 5 (1-based 6); id 2 stays above it.
    lines = [f"{f},2,10,5,20,40,0.9,-1,-1,-1" for f in range(1, 11)]
    lines += [f"{f},1,60,20,20,{20 if f < 6 else 90},0.9,-1,-1,-1" for f in range(3, 13)]
    path = tmp_path / "clipA_mot.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def build(clip, camera, mot, tmp_path, **kwargs):
    return make_wait_kit(clip, camera, mot, tmp_path / "out", h264=False, **kwargs)


def read_rows(path: Path) -> list[list[str]]:
    with path.open(newline="") as handle:
        return list(csv.reader(handle))


def test_template_header_exact_and_no_data_rows(clip, camera, mot, tmp_path):
    kit = build(clip, camera, mot, tmp_path)["kit"]
    assert read_rows(kit / "clipA_waits_template.csv") == [list(OBSERVED_COLUMNS)]
    assert (kit / "README.md").exists()


def test_rows_option_prefills_only_clip_id(clip, camera, mot, tmp_path):
    kit = build(clip, camera, mot, tmp_path, rows=2)["kit"]
    rows = read_rows(kit / "clipA_waits_template.csv")
    assert rows[1:] == [["clipA", "", "", "", "", "", ""]] * 2


def test_hints_only_zone_entrants_in_separate_file(clip, camera, mot, tmp_path):
    kit = build(clip, camera, mot, tmp_path)["kit"]
    rows = read_rows(kit / "clipA_track_hints.csv")
    assert rows[0] == list(HINT_COLUMNS)
    assert rows[1:] == [["1", "0.20", "0.50", "1.10"]]  # id 2 never enters the zone
    assert "sys" not in (kit / "clipA_waits_template.csv").read_text()


def test_no_tracks_means_no_hints_file(clip, camera, tmp_path):
    kit = make_wait_kit(clip, camera, None, tmp_path / "out", h264=False)["kit"]
    assert not (kit / "clipA_track_hints.csv").exists()


def test_review_video_frame_count_and_overlay(clip, camera, mot, tmp_path):
    summary = build(clip, camera, mot, tmp_path)
    video = cv2.VideoCapture(str(summary["kit"] / "clipA_review.mp4"))
    frames = []
    while True:
        ok, frame = video.read()
        if not ok:
            break
        frames.append(frame)
    assert len(frames) == N == summary["frames"]
    assert video.get(cv2.CAP_PROP_FPS) == pytest.approx(FPS)
    # Burned-in black banner at the top-left versus the flat grey source.
    assert frames[0][:12, :60].mean() < 40
    assert not np.array_equal(frames[3][:20, :100], frames[12][:20, :100])  # counter text changes


def test_refuses_to_overwrite_filled_sheet(clip, camera, mot, tmp_path):
    build(clip, camera, mot, tmp_path)
    with pytest.raises(FileExistsError):
        build(clip, camera, mot, tmp_path)
    build(clip, camera, mot, tmp_path, force=True)


def sheet(tmp_path: Path, rows: list[str], name: str = "s.csv") -> Path:
    path = tmp_path / name
    path.write_text(",".join(OBSERVED_COLUMNS) + "\n" + "\n".join(rows) + "\n")
    return path


def test_checker_accepts_valid_sheet(tmp_path):
    path = sheet(tmp_path, ["c,1,10,30,40,20,high", "c,2,50,,,,low"])
    errors, notes = check_sheet(path)
    assert errors == [] and "1 complete" in notes[0]
    assert main(["--check", str(path)]) == 0


@pytest.mark.parametrize(
    "row",
    [
        "c,1,10,30,40,25,high",  # inconsistent observed wait
        "c,1,30,10,40,-20,high",  # service before join
        "c,1,10,30,40,20,sure",  # bad confidence
        "c,1,abc,30,40,20,high",  # not a number
        "c,1,,30,40,20,high",  # missing join
        "c,1,10,30,20,20,high",  # complete before start
    ],
)
def test_checker_rejects_bad_rows(tmp_path, row):
    path = sheet(tmp_path, [row])
    assert check_sheet(path)[0]
    assert main(["--check", str(path)]) == 1


def test_checker_rejects_duplicate_ids_and_bad_header(tmp_path):
    assert check_sheet(sheet(tmp_path, ["c,1,10,30,,20,high", "c,1,50,60,,10,high"]))[0]
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    assert check_sheet(bad)[0]


def test_compare_two_annotators(tmp_path):
    a = sheet(tmp_path, ["c,1,10,30,,20,high", "c,2,50,80,,30,high"], "a.csv")
    b = sheet(tmp_path, ["c,7,11,32,,21,high"], "b.csv")
    errors, notes = check_sheet(a, b)
    assert errors == []
    assert any("1 people matched" in n and "1 only in first" in n for n in notes)
    assert any("mean abs wait difference 1.00s" in n for n in notes)
