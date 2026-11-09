"""Tests for the dataset manifest validator."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from ai_engine.data.validation import (
    REQUIRED_LICENSE_COLUMNS,
    REQUIRED_MANIFEST_COLUMNS,
    ManifestError,
    validate,
)
from scripts.validate_dataset import main

REPO = Path(__file__).resolve().parent.parent
MANIFEST_COLS = REQUIRED_MANIFEST_COLUMNS
LICENSE_COLS = REQUIRED_LICENSE_COLUMNS


def make_video(path: Path, frames: int = 20, fps: float = 10.0) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 48))
    for i in range(frames):
        writer.write(np.full((48, 64, 3), i * 10 % 255, dtype=np.uint8))
    writer.release()


def row(**kw: str) -> dict[str, str]:
    base = dict.fromkeys(MANIFEST_COLS, "")
    base.update(acquisition_status="acquired", dataset="d", license_id="lic", split="test")
    base.update(kw)
    return base


def write_csv(path: Path, cols: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Path]:
    make_video(tmp_path / "ok.mp4")
    (tmp_path / "ann.csv").write_text("time_s,frame,queue_count\n")
    (tmp_path / "bad.mp4").write_bytes(os.urandom(2048))
    make_video(tmp_path / "other.mp4")
    rows = [
        row(clip_id="ok", video_path="ok.mp4", annotation_paths="ann.csv", expected_fps="10"),
        row(clip_id="plan", acquisition_status="planned", split=""),
        row(clip_id="nov", video_path="nope.mp4", annotation_paths="ann.csv"),
        row(clip_id="corrupt", video_path="bad.mp4", annotation_paths="ann.csv"),
        row(clip_id="dup", video_path="ok.mp4", annotation_paths="ann.csv"),
        row(clip_id="dup", video_path="ok.mp4", annotation_paths="ann.csv"),
        row(clip_id="nolic", video_path="ok.mp4", annotation_paths="ann.csv", license_id="zzz"),
        row(clip_id="badsplit", video_path="ok.mp4", annotation_paths="ann.csv", split="holdout"),
        row(clip_id="mismatch", video_path="ok.mp4", annotation_paths="ann.csv", split="val"),
        row(clip_id="noann", video_path="ok.mp4", annotation_paths="missing.csv"),
        row(clip_id="fpsoff", video_path="other.mp4", annotation_paths="ann.csv", expected_fps="30"),
    ]
    write_csv(tmp_path / "manifest.csv", MANIFEST_COLS, rows)
    lic = dict.fromkeys(LICENSE_COLS, "")
    lic.update(license_id="lic", dataset="d", license_name="x", terms_verified="no")
    write_csv(tmp_path / "lic.csv", LICENSE_COLS, [lic])
    splits = tmp_path / "splits"
    splits.mkdir()
    (splits / "train.txt").write_text("# c\n")
    (splits / "val.txt").write_text("ghost\n")
    (splits / "test.txt").write_text("ok\nnov\ncorrupt\n")
    return {"root": tmp_path, "splits": splits}


def run(env: dict[str, Path]):
    root = env["root"]
    return validate(root / "manifest.csv", root / "lic.csv", env["splits"], root)


def test_statuses_and_summary(env: dict[str, Path]) -> None:
    rep = run(env)
    status = {c.clip_id: c.status for c in rep.clips}
    assert status["ok"] == "ok"
    assert status["plan"] == "planned_not_acquired"
    assert status["nov"] == "missing_video"
    assert status["corrupt"] == "unreadable_video"
    assert status["noann"] == "missing_annotations"
    plan = next(c for c in rep.clips if c.clip_id == "plan")
    assert not any(i.severity == "error" for i in plan.issues if i.code != "license_unverified")
    ok = next(c for c in rep.clips if c.clip_id == "ok")
    assert ok.video is not None and ok.video.frame_count == 20 and ok.video.width == 64
    codes = {i.code for c in rep.clips for i in c.issues} | {i.code for i in rep.global_issues}
    assert {
        "duplicate_clip_id",
        "license_not_registered",
        "invalid_split",
        "split_mismatch",
        "split_clip_not_in_manifest",
        "fps_mismatch",
        "license_unverified",
    } <= codes
    s = rep.summary
    assert s["clips_total"] == 11
    assert s["planned_not_acquired"] == 1
    assert s["acquired"] == 10
    assert s["video_found"] == 9
    assert s["video_readable"] == 8
    assert s["annotated"] == 9
    assert s["license_verified"] == 0
    assert s["errors"] > 0 and s["warnings"] > 0
    assert rep.exit_code == 1


def test_cli_writes_json_and_exit_code(env: dict[str, Path], capsys: pytest.CaptureFixture) -> None:
    root = env["root"]
    out = root / "out" / "report.json"
    argv = [
        "--manifest",
        "manifest.csv",
        "--licenses",
        "lic.csv",
        "--splits-dir",
        "splits",
        "--root",
        str(root),
        "--output",
        str(out),
    ]
    assert main(argv) == 1
    data = json.loads(out.read_text())
    assert data["summary"]["clips_total"] == 11
    assert data["clips"][0]["clip_id"] == "ok"
    assert "Dataset validation: 11 clips" in capsys.readouterr().out


def test_clean_manifest_exits_zero(tmp_path: Path) -> None:
    make_video(tmp_path / "a.mp4")
    (tmp_path / "a.csv").write_text("x\n")
    write_csv(
        tmp_path / "m.csv",
        MANIFEST_COLS,
        [
            row(clip_id="a", video_path="a.mp4", annotation_paths="a.csv"),
            row(clip_id="p", acquisition_status="planned", split=""),
        ],
    )
    lic = dict.fromkeys(LICENSE_COLS, "")
    lic.update(license_id="lic", terms_verified="yes")
    write_csv(tmp_path / "l.csv", LICENSE_COLS, [lic])
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "test.txt").write_text("a\n")
    (tmp_path / "s" / "train.txt").write_text("")
    (tmp_path / "s" / "val.txt").write_text("")
    rep = validate(tmp_path / "m.csv", tmp_path / "l.csv", tmp_path / "s", tmp_path)
    assert rep.exit_code == 0
    assert rep.summary["errors"] == 0 and rep.summary["license_verified"] == 1


def test_missing_columns(tmp_path: Path) -> None:
    (tmp_path / "m.csv").write_text("clip_id,dataset\na,b\n")
    with pytest.raises(ManifestError, match="missing required columns"):
        validate(tmp_path / "m.csv", tmp_path / "l.csv", tmp_path, tmp_path)
    out = tmp_path / "o.json"
    assert main(["--manifest", "m.csv", "--root", str(tmp_path), "--output", str(out)]) == 1


def test_real_committed_files_are_structurally_valid(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    code = main(
        [
            "--manifest",
            "datasets/metadata/dataset_manifest.csv",
            "--output",
            str(out),
        ]
    )
    data = json.loads(out.read_text())
    assert data["summary"]["clips_total"] >= 25
    assert not [i for i in data["global_issues"] if i["severity"] == "error"]
    codes = {i["code"] for c in data["clips"] for i in c["issues"]}
    assert not codes & {"license_not_registered", "split_mismatch", "invalid_split"}
    for c in data["clips"]:
        if c["acquisition_status"] == "planned":
            assert c["status"] == "planned_not_acquired"
        elif not (REPO / "datasets" / "raw").exists() or c["status"] == "missing_video":
            assert c["status"] in {"missing_video", "ok", "missing_annotations"}
    assert code in (0, 1)
