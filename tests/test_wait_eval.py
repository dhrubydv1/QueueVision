"""Waiting-time evaluation against hand-built ground truth."""

import json
from pathlib import Path

import pytest

from ai_engine.evaluate_waiting import main as evaluate_waiting_main
from ai_engine.evaluation.wait_eval import (
    TimeseriesPrediction,
    WaitRecord,
    evaluate_predictions,
    evaluate_throughput,
    evaluate_waits,
    match_by_service_time,
    read_ground_truth_waits,
    read_predicted_waits,
    read_timeseries_predictions,
)


def record(person: str, join_s: float, service_s: float | None, abandoned: bool = False) -> WaitRecord:
    return WaitRecord(person, join_s, service_s, abandoned)


def test_matching_is_one_to_one_within_tolerance() -> None:
    gt = [record("a", 0, 10), record("b", 5, 20), record("c", 8, 40)]
    pred = [record("p1", 1, 11), record("p2", 4, 12), record("p3", 9, 60)]
    matches, missed, extra = match_by_service_time(gt, pred, tolerance_s=3.0)
    # p1 (|11-10|=1) wins a; p2 would also fit a but a is taken, and b (20) is too far from p2 (12)
    assert [(g.person, p.person) for g, p in matches] == [("a", "p1")]
    assert [g.person for g in missed] == ["b", "c"]
    assert [p.person for p in extra] == ["p2", "p3"]


def test_wait_errors_missed_extra_and_abandoned() -> None:
    gt = [record("a", 0, 10), record("b", 5, 20), record("c", 8, None, abandoned=True), record("d", 9, 50)]
    pred = [
        record("p1", 1, 11),  # wait 10 vs true 10 -> error 0
        record("p2", 4, 21),  # wait 17 vs true 15 -> error +2
        record("p3", 0, None, abandoned=True),
        record("p4", 70, 80),  # extra
    ]
    result = evaluate_waits(gt, pred, tolerance_s=3.0)
    assert result["gt_completed"] == 3 and result["predicted_completed"] == 3
    assert (result["matched"], result["missed"], result["extra"]) == (2, 1, 1)
    assert result["gt_abandoned"] == 1 and result["predicted_abandoned"] == 1
    assert result["wait_mae_s"] == pytest.approx(1.0)
    assert result["wait_median_abs_error_s"] == pytest.approx(1.0)
    assert result["wait_bias_s"] == pytest.approx(1.0)


def test_no_matches_gives_none_errors() -> None:
    result = evaluate_waits([record("a", 0, 10)], [], 3.0)
    assert result["matched"] == 0 and result["wait_mae_s"] is None and result["wait_bias_s"] is None


def test_prediction_mae_mape_and_unknowns() -> None:
    gt = [record("a", 10, 30), record("b", 20, 20), record("c", 40, 100), record("d", 200, 260)]
    series = [
        TimeseriesPrediction(10.0, 25.0, "ok"),  # true 20 -> abs 5, 25 %
        TimeseriesPrediction(20.4, 7.0, "ok"),  # true wait 0 -> counts in MAE, skipped in MAPE
        TimeseriesPrediction(40.0, None, "unknown"),  # unknown
    ]  # "d" has no row near 200 -> unknown
    result = evaluate_predictions(gt, series)
    assert result["evaluated"] == 2 and result["unknown_predictions"] == 2
    assert result["prediction_mae_s"] == pytest.approx(6.0)
    assert result["prediction_mape_percent"] == pytest.approx(25.0)


def test_throughput() -> None:
    gt = [record(str(i), 0, 10 + i) for i in range(6)]
    pred = [record(str(i), 0, 10 + i) for i in range(3)]
    result = evaluate_throughput(gt, pred, duration_s=120)
    assert result == {"gt_per_min": 3.0, "predicted_per_min": 1.5, "abs_error_per_min": 1.5}
    assert evaluate_throughput(gt, pred, 0)["gt_per_min"] is None


def test_read_ground_truth_validation(tmp_path: Path) -> None:
    path = tmp_path / "gt.csv"
    path.write_text("person,join_s,service_s,abandoned\n\nann,1,11,false\nbob,2,,yes\n,,,\ncy,3,9,\n")
    rows = read_ground_truth_waits(path)
    assert [(r.person, r.wait_s, r.abandoned) for r in rows] == [
        ("ann", 10.0, False),
        ("bob", None, True),
        ("cy", 6.0, False),
    ]
    bad = tmp_path / "bad.csv"
    bad.write_text("person,join_s,service_s,abandoned\nann,1,,false\n")
    with pytest.raises(ValueError, match="row 2"):
        read_ground_truth_waits(bad)
    bad.write_text("person,join_s,service_s,abandoned\nann,1,5,maybe\n")
    with pytest.raises(ValueError, match="row 2"):
        read_ground_truth_waits(bad)
    bad.write_text("person,join_s,service_s,abandoned\nann,x,5,false\n")
    with pytest.raises(ValueError, match="join_s"):
        read_ground_truth_waits(bad)


def write_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    waits = tmp_path / "clip_waits.csv"
    waits.write_text(
        "person_key,track_ids,join_s,service_s,wait_s,abandoned,left_s\n"
        "t1,1,0.500,20.000,19.500,false,\n"
        "t2,2;3,5.000,,,true,12.000\n"
    )
    truth = tmp_path / "clip_waits_gt.csv"
    truth.write_text("person,join_s,service_s,abandoned\nann,0.0,21.0,false\nbob,5,,true\n")
    series = tmp_path / "clip_queue.csv"
    series.write_text(
        "time_s,frame,people_in_frame,raw_queue_count,queue_length,service_rate_per_min,"
        "service_rate_status,predicted_wait_s,predicted_wait_low_s,predicted_wait_high_s,prediction_status\n"
        "0.000,0,1,1,1,,insufficient_data,,,,unknown\n"
        "1.000,10,1,1,1,3.0,ok,25.0,20.0,30.0,ok\n"
    )
    return waits, truth, series


def test_read_predicted_and_timeseries(tmp_path: Path) -> None:
    waits, _, series = write_inputs(tmp_path)
    rows = read_predicted_waits(waits)
    assert rows[0].wait_s == pytest.approx(19.5) and rows[1].abandoned and rows[1].service_s is None
    parsed = read_timeseries_predictions(series)
    assert parsed[0].predicted_wait_s is None and parsed[1].predicted_wait_s == 25.0


def test_cli_writes_report(tmp_path: Path) -> None:
    waits, truth, series = write_inputs(tmp_path)
    output = tmp_path / "eval.json"
    code = evaluate_waiting_main(
        [
            "--predicted-waits",
            str(waits),
            "--ground-truth",
            str(truth),
            "--timeseries",
            str(series),
            "--duration",
            "60",
            "--output",
            str(output),
        ]
    )
    assert code == 0
    metrics = json.loads(output.read_text())["metrics"]
    assert metrics["waits"]["matched"] == 1 and metrics["waits"]["wait_bias_s"] == pytest.approx(-1.5)
    assert metrics["waits"]["gt_abandoned"] == 1 and metrics["waits"]["predicted_abandoned"] == 1
    assert metrics["throughput"]["gt_per_min"] == 1.0
    assert metrics["predictions"]["evaluated"] == 0 and metrics["predictions"]["unknown_predictions"] == 1


def test_cli_missing_or_invalid_files_exit_1(tmp_path: Path) -> None:
    waits, truth, _ = write_inputs(tmp_path)
    missing = tmp_path / "nope.csv"
    assert evaluate_waiting_main(["--predicted-waits", str(missing), "--ground-truth", str(truth)]) == 1
    truth.write_text("person,join_s,service_s,abandoned\nann,1,,false\n")
    assert evaluate_waiting_main(["--predicted-waits", str(waits), "--ground-truth", str(truth)]) == 1
    truth.write_text("who,when\n")
    assert evaluate_waiting_main(["--predicted-waits", str(waits), "--ground-truth", str(truth)]) == 1
