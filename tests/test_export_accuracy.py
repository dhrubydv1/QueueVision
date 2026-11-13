"""Accuracy export tests: synthetic files in tmp_path mirroring the real key layout (torch-free)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import ApiSettings
from scripts.export_accuracy import build_accuracy

NOW = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)


def metrics(mae: float, matched: int = 10) -> dict:
    return {
        "matched": matched,
        "unmatched": 0,
        "mae": mae,
        "rmse": mae + 0.5,
        "bias": mae - 0.25,
        "exact_match_rate": 0.1,
        "within_one_rate": 0.4 + mae / 100,
    }


def window(mae: float, matched: int = 10) -> dict:
    return {"smoothed_from5s": metrics(mae, matched), "raw_from5s": metrics(mae + 1, matched)}


def queue_file(mae_val: float, mae_test: float) -> dict:
    return {
        "tolerance_seconds": 0.6,
        "from_seconds": 5.0,
        "variants": {
            "as_configured": {
                "per_clip": {"cad_a": window(mae_val, 7), "cad_b": window(mae_test, 9)},
                "pooled": {
                    "all_clips": window(1.0),
                    "val": window(mae_val, 7),
                    "test": window(mae_test, 9),
                },
                "pooled_clip_membership": {"val": ["cad_a"], "test": ["cad_b"]},
            }
        },
    }


def errors_file() -> dict:
    totals = {
        "tp": 100,
        "fp_label:walking": 3,
        "fp_unmatched": 40,
        "fp_unmatched_overlaps_gt": 30,
        "fp_unmatched_no_gt_overlap": 10,
        "counted_not_visible": 20,
        "fn_no_detection": 15,
        "fn_foot_outside_zone": 2,
        "fn_candidate_dwell": 12,
        "fn_candidate_speed": 0,
        "fn_other": 0,
        "gt_queueing_outside_zone": 1,
    }
    per_frame = {k: v / 10 for k, v in totals.items()}
    block = {"frames": 10, "totals": totals, "mean_per_frame": per_frame}
    split = {"iou_0.5": {"all": block, "from5s": block}}
    return {"variants": {"as_configured": {"pooled": {"all_clips": split, "val": split, "test": split}}}}


def chosen_file() -> dict:
    base = queue_file(1.5, 1.7)["variants"]["as_configured"]
    other = queue_file(1.1, 1.3)["variants"]["as_configured"]
    return {"variants": {"as_configured": base, "grace0.75_dwell2.0": other}}


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def make_sources(tmp_path: Path, *, errors: bool = True) -> tuple[Path, Path]:
    outputs, splits = tmp_path / "outputs", tmp_path / "splits"
    splits.mkdir()
    (splits / "val.txt").write_text("# comment\ncad_a\n")
    (splits / "test.txt").write_text("cad_b\n")
    write(outputs / "s6_analysis" / "queue_640.json", queue_file(1.25, 1.6193))
    write(outputs / "s6_analysis" / "queue_960.json", queue_file(1.4, 1.8))
    if errors:
        write(outputs / "s6_analysis" / "errors_640.json", errors_file())
    write(outputs / "s6_sweep" / "chosen_all.json", chosen_file())
    return outputs, splits


def numbers(node, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            if key != "generated_at":
                numbers(value, found)
    elif isinstance(node, list):
        for value in node:
            numbers(value, found)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        found.append(node)
    return found


def test_build_accuracy_reads_numbers_from_files(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    out = build_accuracy(outputs, splits, NOW)

    assert out["schema"] == "accuracy-1"
    assert out["generated_at"] == NOW.isoformat()
    assert out["missing_sources"] == [
        {
            "section": "waiting_time",
            "path": "outputs/metrics/*_wait_eval.json",
            "reason": "not measured yet (session 3)",
        }
    ]
    q = out["queue_length"]
    assert q["source"] == "outputs/s6_analysis/queue_640.json"
    assert q["protocol"] == {
        "series": "smoothed",
        "window": "from5s",
        "tolerance_seconds": 0.6,
        "from_seconds": 5.0,
    }
    by_clip = {c["clip"]: c for c in q["per_clip"]}
    assert by_clip["cad_a"] == {
        "clip": "cad_a",
        "split": "val",
        "mae": 1.25,
        "rmse": 1.75,
        "bias": 1.0,
        "within_one_rate": 0.4 + 1.25 / 100,
        "frames": 7,
    }
    assert by_clip["cad_b"]["split"] == "test"
    test = q["pooled"]["test"]
    assert (test["mae"], test["frames"], test["clips"]) == (1.6193, 9, ["cad_b"])
    assert q["pooled"]["val"]["clips"] == ["cad_a"]

    ce = out["count_errors"]
    assert ce["source"] == "outputs/s6_analysis/errors_640.json"
    assert ce["iou"] == 0.5 and ce["window"] == "from5s"
    test_cats = {c["key"]: c for c in ce["per_split"]["test"]["categories"]}
    assert ce["per_split"]["test"]["frames"] == 10
    assert test_cats["tp"] == {
        "key": "tp",
        "label": "Counted correctly",
        "direction": "correct",
        "total": 100,
        "per_frame": 10.0,
    }
    assert test_cats["fn_candidate_dwell"]["direction"] == "under"
    assert test_cats["fp_unmatched"]["label"] == "Counted, no matching labelled box"
    outside = test_cats["outside_zone"]
    assert (outside["total"], outside["direction"]) == (3, "other")
    assert outside["per_frame"] == 0.3
    assert outside["label"] == "Labelled queuer outside the zone"
    assert "fp_unmatched_overlaps_gt" not in test_cats and "fn_other" not in test_cats
    assert "fn_candidate_speed" not in test_cats

    names = {c["name"]: c for c in out["changes_not_adopted"]}
    d960 = names["Detector input 960 px"]
    assert (d960["val_mae"], d960["test_mae"]) == (1.4, 1.8)
    assert (d960["baseline_val_mae"], d960["baseline_test_mae"]) == (1.25, 1.6193)
    assert d960["source"] == "outputs/s6_analysis/queue_960.json"
    sweep = names["grace 0.75 s, dwell 2.0 s"]
    assert (sweep["val_mae"], sweep["test_mae"]) == (1.1, 1.3)
    assert (sweep["baseline_val_mae"], sweep["baseline_test_mae"]) == (1.5, 1.7)
    assert sweep["source"] == "outputs/s6_sweep/chosen_all.json"
    assert "Queue length is measured on 1 val and 1 test CAD clips only" in out["limits"]
    assert "Waiting-time accuracy is not measured yet" in out["limits"]


def test_missing_sources_null_sections(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path, errors=False)
    out = build_accuracy(outputs, splits, NOW)
    assert out["count_errors"] is None
    entry = next(m for m in out["missing_sources"] if m["section"] == "count_errors")
    assert entry["path"] == "outputs/s6_analysis/errors_640.json"
    assert entry["reason"]
    assert out["queue_length"] is not None and out["changes_not_adopted"]


def test_waiting_time_absent_then_present(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    out = build_accuracy(outputs, splits, NOW)
    assert out["waiting_time"] is None
    assert "Waiting-time accuracy is not measured yet" in out["limits"]

    write(
        outputs / "metrics" / "cad_seq25_wait_eval.json",
        {"metrics": {"waits": {"matched": 4, "wait_mae_s": 2.5, "wait_bias_s": -1.0}}},
    )
    out = build_accuracy(outputs, splits, NOW)
    wt = out["waiting_time"]
    assert wt["per_clip"] == [
        {
            "clip": "cad_seq25",
            "kind": "predicted",
            "source": "outputs/metrics/cad_seq25_wait_eval.json",
            "metrics": {"waits": {"matched": 4, "wait_mae_s": 2.5, "wait_bias_s": -1.0}},
        }
    ]
    assert wt["pooled"] is None
    assert all(m["section"] != "waiting_time" for m in out["missing_sources"])
    assert "Waiting-time accuracy is not measured yet" not in out["limits"]


def test_wait_eval_kinds_and_suffix_stripping(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "cad_a_wait_eval.json", {"metrics": {"waits": {"matched": 1}}})
    write(outputs / "metrics" / "cad_a_observed_wait_eval.json", {"metrics": {"measured": {"samples": 2}}})
    write(outputs / "metrics" / "cad_b_observed_wait_eval.json", {"metrics": {"measured": {"samples": 3}}})
    clips = build_accuracy(outputs, splits, NOW)["waiting_time"]["per_clip"]
    assert [(c["clip"], c["kind"], c["source"]) for c in clips] == [
        ("cad_a", "observed", "outputs/metrics/cad_a_observed_wait_eval.json"),
        ("cad_a", "predicted", "outputs/metrics/cad_a_wait_eval.json"),
        ("cad_b", "observed", "outputs/metrics/cad_b_observed_wait_eval.json"),
    ]
    assert not any(c["clip"].endswith("_observed") for c in clips)


def test_unreadable_wait_eval_reported(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "cad_a_wait_eval.json", {"metrics": {"waits": {}}})
    (outputs / "metrics" / "bad_wait_eval.json").write_text("{not json")
    write(outputs / "metrics" / "nometrics_observed_wait_eval.json", {"other": 1})
    out = build_accuracy(outputs, splits, NOW)
    assert [c["clip"] for c in out["waiting_time"]["per_clip"]] == ["cad_a"]
    reported = {m["path"]: m for m in out["missing_sources"] if m["section"] == "waiting_time"}
    assert set(reported) == {
        "outputs/metrics/bad_wait_eval.json",
        "outputs/metrics/nometrics_observed_wait_eval.json",
    }
    assert all(m["reason"] for m in reported.values())


def test_no_typed_numbers(tmp_path: Path) -> None:
    out = build_accuracy(tmp_path / "no_outputs", tmp_path / "no_splits", NOW)
    assert out["queue_length"] is None and out["count_errors"] is None
    assert out["changes_not_adopted"] is None and out["waiting_time"] is None
    assert numbers(out) == []
    assert {m["section"] for m in out["missing_sources"]} >= {
        "queue_length",
        "count_errors",
        "changes_not_adopted",
        "waiting_time",
    }


def test_accuracy_route(tmp_path: Path) -> None:
    settings = ApiSettings(data_dir=tmp_path / "data", camera_dir=tmp_path / "cameras")
    assert settings.accuracy_path == tmp_path / "data" / "accuracy.json"
    with TestClient(create_app(settings)) as client:
        missing = client.get("/api/accuracy")
        assert missing.status_code == 404
        assert missing.json() == {
            "detail": "accuracy data not generated; run python scripts/export_accuracy.py"
        }

        payload = {"schema": "accuracy-1", "queue_length": None}
        settings.accuracy_path.write_text(json.dumps(payload))
        ok = client.get("/api/accuracy")
        assert ok.status_code == 200 and ok.json() == payload

        settings.accuracy_path.write_text("{not json")
        bad = client.get("/api/accuracy")
        assert bad.status_code == 500 and bad.json() == {"detail": "accuracy data unreadable"}
