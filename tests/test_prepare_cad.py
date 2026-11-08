"""Tests for the Collective Activity Dataset preparation tool."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from ai_engine.evaluation.queue_eval import read_annotations as read_queue_csv
from ai_engine.tools import prepare_cad

ANNOTATIONS = (
    "001\t10\t10\t20\t40\t4\t1\n"
    "001\t50\t10\t20\t40\t4\t2\n"
    "001\t90\t10\t20\t40\t5\t3\n"
    "003\t10\t10\t20\t40\t4\t1\n"
)
ROOT = Path(__file__).resolve().parents[1]


def _jpeg(value: int) -> bytes:
    frame = np.full((48, 64, 3), value, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", frame)
    assert ok
    return buffer.tobytes()


@pytest.fixture
def cad_zip(tmp_path: Path) -> Path:
    path = tmp_path / "cad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("seq05/annotations.txt", ANNOTATIONS)
        for number, value in enumerate((40, 120, 200), start=1):
            archive.writestr(f"seq05/frame{number:04d}.jpg", _jpeg(value))
        archive.writestr("seq05/../evil.jpg", _jpeg(0))
        archive.writestr("seq06/frame0001.jpg", _jpeg(0))
    return path


def test_read_annotations() -> None:
    assert prepare_cad.read_annotations(ANNOTATIONS) == {1: [4, 4, 5], 3: [4]}


def test_queue_counts() -> None:
    counts = prepare_cad.queue_counts({1: [4, 4, 5], 3: [4]}, fps=10)
    assert counts == [(0.0, 0, 2), (0.2, 2, 1)]


def test_prepare_sequence_writes_outputs(cad_zip: Path, tmp_path: Path) -> None:
    video, annotations = prepare_cad.prepare_sequence(cad_zip, 5, 10.0, tmp_path / "video", tmp_path / "ann")
    assert video == tmp_path / "video" / "cad_seq05.mp4"
    assert annotations == tmp_path / "ann" / "cad_seq05_queue_counts.csv"
    capture = cv2.VideoCapture(str(video))
    assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 3
    capture.release()
    labelled, blank = read_queue_csv(annotations)
    assert labelled == [(0.0, 2), (0.2, 1)] and blank == 0
    meta = json.loads((tmp_path / "ann" / "cad_seq05.json").read_text())
    assert meta["fps_assumed"] is True and meta["frames"] == 3 and meta["sequence"] == 5
    assert meta["fps"] == 10.0


def test_prepare_sequence_refuses_overwrite(cad_zip: Path, tmp_path: Path) -> None:
    args = (cad_zip, 5, 10.0, tmp_path / "video", tmp_path / "ann")
    prepare_cad.prepare_sequence(*args)
    with pytest.raises(FileExistsError):
        prepare_cad.prepare_sequence(*args)
    prepare_cad.prepare_sequence(*args, force=True)


def test_prepare_sequence_from_folder(cad_zip: Path, tmp_path: Path) -> None:
    folder = tmp_path / "extracted"
    with zipfile.ZipFile(cad_zip) as archive:
        (folder / "seq05").mkdir(parents=True)
        for name in ("annotations.txt", "frame0001.jpg", "frame0002.jpg", "frame0003.jpg"):
            (folder / "seq05" / name).write_bytes(archive.read(f"seq05/{name}"))
    _, annotations = prepare_cad.prepare_sequence(folder, 5, 10.0, tmp_path / "v", tmp_path / "a")
    assert read_queue_csv(annotations)[0] == [(0.0, 2), (0.2, 1)]


def test_missing_sequence_raises(cad_zip: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        prepare_cad.prepare_sequence(cad_zip, 7, 10.0, tmp_path / "v", tmp_path / "a")


def test_help_runs_directly() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "ai_engine" / "tools" / "prepare_cad.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "--source" in result.stdout
