"""Tests for MOT export/evaluation and queue-count evaluation (synthetic data only)."""

import json
from pathlib import Path

import numpy as np
import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.evaluate_queue import main as evaluate_queue_main
from ai_engine.evaluation.mot_eval import (
    FrameBoxes,
    MotWriter,
    drop_distractor_matches,
    evaluated_frames,
    iou_distance,
    iou_matrix,
    load_ground_truth,
    load_tracks,
    read_seqinfo,
)
from ai_engine.evaluation.queue_eval import compare_counts, read_annotations, read_predictions

# frame,id,x,y,w,h,consider,class,visibility in 1920x1080 coordinates
GT_ROWS = """\
1,1,100,200,40,80,1,1,1.0
1,2,600,200,40,80,0,1,1.0
1,3,800,400,40,80,1,7,1.0
1,4,1000,400,40,80,1,3,1.0
2,1,104,200,40,80,1,1,0.8
"""


def test_mot_writer_format_and_one_based_frames(tmp_path: Path) -> None:
    path = tmp_path / "tracks" / "x_mot.txt"
    with MotWriter(path) as writer:
        writer.write(1, [PersonDetection((10, 20, 50, 100), 0.91234, 7), PersonDetection((0, 0, 1, 1), 0.5)])
        writer.write(2, [])
        with pytest.raises(ValueError):
            writer.write(0, [])
    assert path.read_text() == "1,7,10.00,20.00,40.00,80.00,0.9123,-1,-1,-1\n"
    assert writer.rows_written == 1
    tracks = load_tracks(path)
    assert tracks[1].ids == [7] and tracks[1].boxes == [(10, 20, 40, 80)]


def test_ground_truth_filtering_and_scaling(tmp_path: Path) -> None:
    gt = tmp_path / "gt.txt"
    gt.write_text(GT_ROWS)
    truth = load_ground_truth(gt, scale=(0.5, 0.5))
    # Only class 1 with consider=1 is scored; id 2 (consider=0) and class 3 are dropped.
    assert truth.pedestrians[1].ids == [1]
    assert truth.pedestrians[1].boxes == [(50, 100, 20, 40)]
    assert truth.pedestrians[2].boxes == [(52, 100, 20, 40)]
    # Class 7 (static person) is kept as a distractor.
    assert truth.distractors[1].ids == [3]
    assert truth.distractors[1].boxes == [(400, 200, 20, 40)]


def test_read_seqinfo(tmp_path: Path) -> None:
    path = tmp_path / "seqinfo.ini"
    path.write_text("[Sequence]\nname=S\nframeRate=30\nseqLength=525\nimWidth=1920\nimHeight=1080\n")
    assert read_seqinfo(path) == {
        "name": "S",
        "frame_rate": 30.0,
        "seq_length": 525,
        "width": 1920,
        "height": 1080,
    }
    with pytest.raises(FileNotFoundError):
        read_seqinfo(tmp_path / "missing.ini")


def test_iou_matrix_and_distance() -> None:
    a = np.array([[0, 0, 10, 10]], float)
    b = np.array([[0, 0, 10, 10], [5, 0, 10, 10], [50, 50, 5, 5]], float)
    assert iou_matrix(a, b) == pytest.approx(np.array([[1.0, 50 / 150, 0.0]]))
    distance = iou_distance(a, b, threshold=0.5)
    assert distance[0, 0] == pytest.approx(0.0)
    assert np.isnan(distance[0, 1]) and np.isnan(distance[0, 2])
    assert iou_matrix(np.zeros((0, 4)), b).shape == (0, 3)


def test_hypotheses_on_distractors_are_removed() -> None:
    pedestrians = FrameBoxes([1], [(0, 0, 10, 20)])
    distractors = FrameBoxes([9], [(100, 0, 10, 20)])
    hyp = FrameBoxes([5, 6, 7], [(0, 0, 10, 20), (101, 0, 10, 20), (300, 0, 10, 20)])
    kept = drop_distractor_matches(hyp, pedestrians, distractors)
    assert kept.ids == [5, 7]  # 6 sits on the distractor; 7 is a real false positive


def test_evaluate_tracks_counts_switches(tmp_path: Path) -> None:
    pytest.importorskip("motmetrics")
    from ai_engine.evaluation.mot_eval import GroundTruth, evaluate_tracks

    box = (0.0, 0.0, 10.0, 20.0)
    truth = GroundTruth({f: FrameBoxes([1], [box]) for f in (1, 2, 3, 4)}, {})
    tracks = {1: FrameBoxes([5], [box]), 2: FrameBoxes([5], [box]), 3: FrameBoxes([6], [box])}
    metrics = evaluate_tracks(truth, tracks, evaluated_frames(4))
    assert metrics["num_switches"] == 1
    assert metrics["num_misses"] == 1  # frame 4 has no hypothesis
    assert metrics["num_false_positives"] == 0
    assert metrics["mota"] == pytest.approx(1 - (1 + 0 + 1) / 4)
    assert metrics["recall"] == pytest.approx(0.75)
    assert evaluated_frames(10, 3) == [1, 4, 7, 10]


# --- queue count evaluation ---------------------------------------------------


def write_csv(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_queue_count_comparison(tmp_path: Path) -> None:
    predicted = write_csv(
        tmp_path / "clip_queue.csv",
        "time_s,frame,people_in_frame,raw_queue_count,queue_length\n"
        "0.000,0,3,2,2\n5.033,151,4,3,3.5\n10.000,300,4,4,4\n15.000,450,1,1,1\n",
    )
    annotations = write_csv(
        tmp_path / "clip_queue_counts.csv",
        "time_s,frame,queue_count\n0.000,0,2\n5.000,150,3\n10.000,300,6\n15.000,450,\n30.000,900,2\n",
    )
    labelled, blank = read_annotations(annotations)
    assert blank == 1 and len(labelled) == 4
    metrics = compare_counts(read_predictions(predicted), labelled, tolerance_s=0.6)
    # errors: 0, +0.5, -2 ; t=30 has no prediction within tolerance
    assert metrics["matched"] == 3 and metrics["unmatched"] == 1
    assert metrics["mae"] == pytest.approx(2.5 / 3, abs=1e-4)
    assert metrics["rmse"] == pytest.approx(np.sqrt((0 + 0.25 + 4) / 3), abs=1e-4)
    assert metrics["exact_match_rate"] == pytest.approx(1 / 3, abs=1e-4)  # round(3.5) == 4 != 3
    assert metrics["within_one_rate"] == pytest.approx(2 / 3, abs=1e-4)

    raw = compare_counts(read_predictions(predicted, "raw_queue_count"), labelled)
    assert raw["exact_match_rate"] == pytest.approx(2 / 3, abs=1e-4)
    with pytest.raises(ValueError):
        read_predictions(predicted, "missing_column")


def test_evaluate_queue_cli_writes_report(tmp_path: Path) -> None:
    predicted = write_csv(
        tmp_path / "clip_queue.csv",
        "time_s,frame,people_in_frame,raw_queue_count,queue_length\n0.000,0,1,1,1\n",
    )
    annotations = write_csv(tmp_path / "counts.csv", "time_s,frame,queue_count\n0.0,0,1\n")
    output = tmp_path / "eval.json"
    assert (
        evaluate_queue_main(
            ["--predicted", str(predicted), "--annotations", str(annotations), "--output", str(output)]
        )
        == 0
    )
    report = json.loads(output.read_text())
    assert report["metrics"]["mae"] == 0 and report["metrics"]["exact_match_rate"] == 1

    empty = write_csv(tmp_path / "empty.csv", "time_s,frame,queue_count\n0.0,0,\n")
    assert evaluate_queue_main(["--predicted", str(predicted), "--annotations", str(empty)]) == 1
