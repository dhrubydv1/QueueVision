"""SYNTHETIC tests for the observed-wait ground-truth format.

Every CSV below is written by the test into tmp_path with invented numbers. These tests check the
evaluation code only. No real ground truth with person ids exists, so real-video waiting-time
accuracy is not measured (blocked on annotation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_engine.evaluate_waiting import main as evaluate_waiting_main
from ai_engine.evaluation.wait_eval import (
    ClipPredictions,
    ObservedWait,
    TimeseriesPrediction,
    WaitRecord,
    error_statistics,
    evaluate_observed_waits,
    is_observed_format,
    match_by_join_time,
    read_observed_ground_truth,
)

HEADER = (
    "clip_id,track_id,queue_join_seconds,service_start_seconds,service_complete_seconds,"
    "observed_wait_seconds,annotation_confidence\n"
)


def write_gt(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "gt_synthetic.csv"
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_format_detection(tmp_path: Path) -> None:
    assert is_observed_format(write_gt(tmp_path, []))
    legacy = tmp_path / "legacy.csv"
    legacy.write_text("person,join_s,service_s,abandoned\na,0,10,false\n", encoding="utf-8")
    assert not is_observed_format(legacy)


def test_exclusion_reasons_and_valid_rows(tmp_path: Path) -> None:
    path = write_gt(
        tmp_path,
        [
            "c1,1,10,30,40,20,high",  # valid
            "c1,2,12,,,,high",  # abandoned / not served
            "c1,3,,30,40,18,high",  # missing join
            "c1,4,10,30,40,25,high",  # inconsistent: 30-10=20 vs 25
            "c1,5,10,30,40,20.4,medium",  # within 0.5 s: valid
            "c1,6,10,30,40,20,bogus",  # invalid confidence
            "c1,7,10,30,40,20,low",  # valid unless --min-confidence
            "c1,8,10,abc,40,20,high",  # unparseable
            "c1,9,10,5,40,-5,high",  # service before join
            "c1,10,10,30,40,,high",  # observed wait blank
            "",  # blank line ignored
        ],
    )
    truth = read_observed_ground_truth(path)
    assert truth.rows_read == 10
    assert [o.track_id for o in truth.valid] == ["1", "5", "7"]
    assert truth.excluded["not_served"] == 1
    assert truth.excluded["missing_join"] == 1
    assert truth.excluded["inconsistent_observed_wait"] == 1
    assert truth.excluded["invalid_confidence"] == 1
    assert truth.excluded["unparseable_number"] == 1
    assert truth.excluded["service_before_join"] == 1
    assert truth.excluded["missing_observed_wait"] == 1
    assert truth.inconsistent[0]["track_id"] == "4"

    strict = read_observed_ground_truth(path, min_confidence="medium")
    assert [o.track_id for o in strict.valid] == ["1", "5"]
    assert strict.excluded["below_min_confidence"] == 1
    with pytest.raises(ValueError, match="min_confidence"):
        read_observed_ground_truth(path, min_confidence="max")


def test_missing_column_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("clip_id,queue_join_seconds\nc,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        read_observed_ground_truth(path)


def test_error_statistics_values() -> None:
    stats = error_statistics([2.0, -4.0, 0.0])
    assert stats["samples"] == 3 and stats["mae_s"] == 2.0
    assert stats["rmse_s"] == pytest.approx((20 / 3) ** 0.5, abs=1e-3)
    assert stats["median_abs_error_s"] == 2.0 and stats["bias_s"] == pytest.approx(-0.667, abs=1e-3)
    assert error_statistics([])["mae_s"] is None


def obs(track: str, join: float, start: float, clip: str = "c1") -> ObservedWait:
    return ObservedWait(clip, track, join, start, start - join, None, "high")


def test_matching_by_join_time_is_one_to_one() -> None:
    observations = [obs("a", 10, 30), obs("b", 12, 40)]
    predicted = [WaitRecord("p1", 11, 31, False), WaitRecord("p2", 40, 60, False)]
    matches, missed, extra = match_by_join_time(observations, predicted, tolerance_s=3.0)
    assert [(o.track_id, p.person) for o, p in matches] == [("a", "p1")]  # a is closer than b
    assert [o.track_id for o in missed] == ["b"] and [p.person for p in extra] == ["p2"]


def test_end_to_end_measured_and_predicted_are_separate(tmp_path: Path) -> None:
    truth = read_observed_ground_truth(
        write_gt(tmp_path, ["c1,1,10,30,40,20,high", "c1,2,50,90,100,40,high", "c2,1,5,25,30,20,high"])
    )
    series = [
        TimeseriesPrediction(10.0, 15.0, "ok"),  # joins at 10: predicted 15 vs observed 20 -> -5
        TimeseriesPrediction(50.0, None, "unknown"),  # unknown, must not count as 0
    ]
    clip1 = ClipPredictions(
        [WaitRecord("p1", 10.5, 32.0, False), WaitRecord("p2", 50.0, 88.0, False)], series
    )
    result = evaluate_observed_waits(truth, {"c1": clip1}, estimator="queue_length_over_service_rate/v1")

    assert result["valid_observations"] == 3
    assert result["clips_without_predictions"] == ["c2"]
    assert result["matching"]["unmatched_observations"] == 1  # c2 counts as unmatched, not as error 0
    measured = result["measured"]
    assert measured["samples"] == 2  # waits 32-10.5=21.5 vs 20 (+1.5) and 88-50=38 vs 40 (-2)
    assert measured["mae_s"] == 1.75 and measured["bias_s"] == -0.25
    assert measured["rmse_s"] == pytest.approx(1.768, abs=1e-3)
    predicted = result["predicted"]
    assert predicted["samples"] == 1 and predicted["bias_s"] == -5.0
    assert predicted["unknown_or_missing"] == 1
    first, second = predicted["records"]
    assert first["predicted_wait_s"] == 15.0 and first["prediction_time_s"] == 10.0
    assert first["horizon_s"] == 20.0  # observed service start 30 - prediction time 10
    assert first["status"] == "ok" and first["estimator"] == "queue_length_over_service_rate/v1"
    assert second["predicted_wait_s"] is None and second["status"] == "unknown"
    assert result["per_clip"]["c1"]["measured"]["samples"] == 2
    assert result["per_clip"]["c2"]["matched"] == 0


def test_cli_observed_format_writes_report(tmp_path: Path) -> None:
    gt = write_gt(tmp_path, ["c1,1,10,30,40,20,high", "c1,2,12,,,,high"])
    waits = tmp_path / "c1_waits.csv"
    waits.write_text(
        "person_key,track_ids,join_s,service_s,wait_s,abandoned,left_s\np1,1,10.2,31.0,20.8,false,\n",
        encoding="utf-8",
    )
    queue = tmp_path / "c1_queue.csv"
    queue.write_text("time_s,predicted_wait_s,prediction_status\n10.000,18.0,ok\n", encoding="utf-8")
    output = tmp_path / "out.json"
    code = evaluate_waiting_main(
        ["--ground-truth", str(gt), "--clip", f"c1={waits}:{queue}", "--output", str(output)]
    )
    assert code == 0
    report = json.loads(output.read_text())
    assert report["ground_truth_format"] == "observed_wait"
    assert report["metrics"]["excluded_rows"]["not_served"] == 1
    assert report["metrics"]["measured"]["samples"] == 1
    assert report["metrics"]["predicted"]["bias_s"] == -2.0


def test_cli_observed_format_requires_clip_id_for_single_file(tmp_path: Path, capsys) -> None:
    gt = write_gt(tmp_path, ["c1,1,10,30,40,20,high"])
    waits = tmp_path / "w.csv"
    waits.write_text("person_key,join_s,service_s,abandoned\np1,10,30,false\n", encoding="utf-8")
    assert evaluate_waiting_main(["--ground-truth", str(gt), "--predicted-waits", str(waits)]) == 1
    assert "--clip-id" in capsys.readouterr().err
