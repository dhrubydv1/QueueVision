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


def band(point: float, low: float, high: float) -> dict:
    return {"point": point, "low": low, "high": high}


def interval_split(n_clips: int, n_frames: int) -> dict:
    def block(unit: str, extra: dict) -> dict:
        return {
            "unit": unit,
            "n_clips": n_clips,
            "n_frames": n_frames,
            "mae": band(1.5, 1.0, 2.0),
            "bias": band(0.5, -0.5, 1.0),
            "within_one_rate": band(0.6, 0.4, 0.8),
            **extra,
        }

    return {
        "clips": ["cad_a"],
        "clip_bootstrap": block("clip", {}),
        "per_frame_bootstrap_optimistic": block("frame", {"label": "optimistic (ignores correlation)"}),
    }


def intervals_file() -> dict:
    return {
        "protocol": "docs/evaluation_protocol.md v1",
        "series": "smoothed_from5s",
        "bootstrap": {"n_resamples": 77, "seed": 5},
        "splits": {"val": interval_split(1, 7), "test": interval_split(2, 9)},
    }


def attribution_file() -> dict:
    cat = errors_file()["variants"]["as_configured"]["pooled"]["test"]["iou_0.5"]["from5s"]
    block = {"frames": cat["frames"], "totals": cat["totals"], "per_frame": cat["mean_per_frame"]}
    window = {"iou_0.5": block, "iou_0.3": {**block, "frames": 11}}
    return {
        "splits": {"all_clips": {"from5s": window}, "val": {"from5s": window}, "test": {"from5s": window}}
    }


def comparison_row(cfg: str, adopt: bool, a_passes: bool) -> dict:
    return {
        "configuration": cfg,
        "metric": "test smoothed queue-length MAE (from 5 s)",
        "value": 1.4,
        "interval_clip_bootstrap_95": [1.1, 1.8],
        "paired_delta_vs_A": -0.2,
        "decision": {
            "adopt": adopt,
            "criterion_a": {"passes": a_passes},
            "criterion_b": {"passes": True, "val_mae_new": 0.8, "val_mae_A": 1.2},
            "criterion_c": {"passes": False, "processing_fps": 20.5, "clip_fps": 30.0},
        },
        "source": ["/elsewhere/B/queue_640.json"],
    }


def comparisons_file() -> dict:
    split = {"mae": 1.6, "matched": 9}
    return {
        "protocol": "docs/evaluation_protocol.md v1",
        "series": "smoothed",
        "bootstrap": "clip-level",
        "notes": ["note"],
        "configurations": {
            "A": {"label": "current", "per_split_smoothed_from5s": {"val": {"mae": 1.2}, "test": split}},
            "B": {"label": "candidate B"},
        },
        "rows": [comparison_row("B", False, False)],
    }


def paired_file() -> dict:
    return {"splits": {"test": {"paired_clip_bootstrap": {"mae": {"low": -0.4, "high": 0.2}}}}}


def make_sources(tmp_path: Path, *, errors: bool = True, s9: bool = True) -> tuple[Path, Path]:
    outputs, splits = tmp_path / "outputs", tmp_path / "splits"
    splits.mkdir()
    (splits / "val.txt").write_text("# comment\ncad_a\n")
    (splits / "test.txt").write_text("cad_b\n")
    write(outputs / "s6_analysis" / "queue_640.json", queue_file(1.25, 1.6193))
    write(outputs / "s6_analysis" / "queue_960.json", queue_file(1.4, 1.8))
    if errors:
        write(outputs / "s6_analysis" / "errors_640.json", errors_file())
    write(outputs / "s6_sweep" / "chosen_all.json", chosen_file())
    if s9:
        write(outputs / "s9_eval" / "intervals_default.json", intervals_file())
        write(outputs / "s9_eval" / "attribution_default.json", attribution_file())
        write(outputs / "s9_eval" / "comparisons.json", comparisons_file())
        write(outputs / "s9_eval" / "B" / "intervals_vs_A.json", paired_file())
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
            "section": "predictor_on_call_centre_logs",
            "path": "outputs/metrics/anonymous_bank_wait_eval.json",
            "reason": "file not found",
        },
        {
            "section": "waiting_time",
            "path": "outputs/metrics/*_wait_eval.json",
            "reason": "not measured yet (session 3)",
        },
    ]
    assert out["predictor_on_call_centre_logs"] is None
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
        "predictor_on_call_centre_logs",
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


def test_wait_eval_unreadable_reason_has_no_absolute_path(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "cad_a_wait_eval.json", {"metrics": {"waits": {}}})
    bad = outputs / "metrics" / "bad_wait_eval.json"
    bad.mkdir()  # reading a directory raises OSError that names the absolute path
    out = build_accuracy(outputs, splits, NOW)
    reason = next(m["reason"] for m in out["missing_sources"] if m["path"].endswith("bad_wait_eval.json"))
    assert str(tmp_path) not in reason and reason.startswith("unreadable: ")


def test_wait_eval_glob_is_sorted(tmp_path: Path, monkeypatch) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "cad_a_wait_eval.json", {"metrics": {}})
    for name in ("z", "m", "b"):
        (outputs / "metrics" / f"{name}_wait_eval.json").write_text("{bad")
    real_glob = Path.glob
    monkeypatch.setattr(Path, "glob", lambda self, pat: reversed(sorted(real_glob(self, pat))))
    out = build_accuracy(outputs, splits, NOW)
    paths = [m["path"] for m in out["missing_sources"] if m["section"] == "waiting_time"]
    assert paths == sorted(paths) and len(paths) == 3


def ci(value: float) -> dict:
    return {"value": value, "lo": value - 1, "hi": value + 1}


def method(mae: float, coverage: float, n_agent: int = 100) -> dict:
    return {
        "n_agent": n_agent,
        "n_covered": int(n_agent * coverage),
        "coverage": ci(coverage),
        "mae_s": ci(mae),
        "bias_s": ci(mae / 10),
    }


def call_centre_file(base: float) -> dict:
    def methods(offset: float, n: int) -> dict:
        return {
            "predictor": method(base + offset, 0.8, n),
            "les": method(base - 10, 0.99, n),
            "recent_mean": method(base - 12, 0.98, n),
            "regression": method(base - 20, 1.0, n),
        }

    paired = {"les": {"n_common": 5, "mae_diff_s": ci(3.0)}}
    hang = {"n_hang": 7, "n_hang_with_prediction": 6, "n_hung_up_after_longer_than_predicted": 2}
    return {
        "protocol_version": 1,
        "seed": 42,
        "interval": "95% percentile, day-level cluster bootstrap",
        "caveat": "Phone queue, 1999, Israel; measures the prediction formula, not the cameras.",
        "queues": {
            "primary": {
                "test": {
                    "n_days": 4,
                    "methods": methods(0, 100),
                    "paired_mae_difference": paired,
                    "hang": hang,
                },
                "validation": {"n_days": 3, "methods": methods(5, 90)},
            }
        },
        "post_hoc_sensitivity": {
            "S1_all_service_starts": {
                "protocol_version": 2,
                "post_hoc": True,
                "definition": "all service starts",
                "test": {
                    "n_days": 4,
                    "methods": methods(-15, 100),
                    "paired_mae_difference": paired,
                    "hang": hang,
                },
            }
        },
    }


def test_call_centre_section_from_synthetic_file(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "anonymous_bank_wait_eval.json", call_centre_file(90.0))
    out = build_accuracy(outputs, splits, NOW)
    cc = out["predictor_on_call_centre_logs"]
    assert cc["source"] == "outputs/metrics/anonymous_bank_wait_eval.json"
    assert cc["citation"] == (
        "\u2018Anonymous Bank\u2019 Call-Center Data, Ilan Guedj and Avi Mandelbaum, Technion SEE Center"
    )
    assert cc["caveat"] == "Phone queue, 1999, Israel; measures the prediction formula, not the cameras."
    assert (cc["protocol_version"], cc["split"], cc["queue"]) == (1, "test", "primary")
    assert (cc["n_days"], cc["n_calls"]) == (4, 100)
    assert list(cc["methods"]) == ["predictor", "les", "recent_mean", "regression"]
    assert cc["methods"]["predictor"]["mae_s"] == {"value": 90.0, "lo": 89.0, "hi": 91.0}
    assert cc["methods"]["regression"]["mae_s"]["value"] == 70.0
    assert cc["methods"]["predictor"]["coverage"]["value"] == 0.8
    assert cc["hang"]["n_hang"] == 7
    s1 = cc["post_hoc_s1"]
    assert s1["post_hoc"] is True and s1["protocol_version"] == 2
    assert s1["methods"]["predictor"]["mae_s"]["value"] == 75.0
    # the headline is the version-1 number, not S1
    assert cc["methods"]["predictor"]["mae_s"]["value"] != s1["methods"]["predictor"]["mae_s"]["value"]
    assert all(m["section"] != "predictor_on_call_centre_logs" for m in out["missing_sources"])


def test_call_centre_file_is_not_a_camera_clip(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "anonymous_bank_wait_eval.json", call_centre_file(90.0))
    out = build_accuracy(outputs, splits, NOW)
    assert out["waiting_time"] is None
    paths = [m["path"] for m in out["missing_sources"] if m["section"] == "waiting_time"]
    assert not any("anonymous_bank" in path for path in paths)


def test_call_centre_unexpected_layout_reported(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "anonymous_bank_wait_eval.json", {"protocol_version": 1})
    out = build_accuracy(outputs, splits, NOW)
    assert out["predictor_on_call_centre_logs"] is None
    entry = next(m for m in out["missing_sources"] if m["section"] == "predictor_on_call_centre_logs")
    assert entry["reason"].startswith("unexpected layout")


def test_call_centre_section_served_by_route(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    write(outputs / "metrics" / "anonymous_bank_wait_eval.json", call_centre_file(90.0))
    payload = build_accuracy(outputs, splits, NOW)
    settings = ApiSettings(data_dir=tmp_path / "data", camera_dir=tmp_path / "cameras")
    with TestClient(create_app(settings)) as client:
        settings.accuracy_path.write_text(json.dumps(payload))
        body = client.get("/api/accuracy").json()
    assert body["predictor_on_call_centre_logs"]["methods"]["predictor"]["mae_s"]["value"] == 90.0


def test_intervals_beside_headline(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    ql = build_accuracy(outputs, splits, NOW)["queue_length"]
    test = ql["pooled"]["test"]
    assert test["mae"] == 1.6193  # existing key untouched
    assert test["interval_clip_bootstrap_95"]["mae"] == [1.0, 2.0]
    assert (test["n_clips"], test["n_frames"]) == (2, 9)
    assert test["interval_per_frame_optimistic_95"]["label"].startswith("optimistic")
    assert ql["pooled"]["val"]["n_clips"] == 1
    assert ql["intervals_source"] == "outputs/s9_eval/intervals_default.json"


def test_attribution_both_thresholds(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    attr = build_accuracy(outputs, splits, NOW)["attribution_iou"]
    assert attr["source"] == "outputs/s9_eval/attribution_default.json"
    test = attr["per_split"]["test"]
    assert test["iou_0.5"]["frames"] == 10 and test["iou_0.3"]["frames"] == 11
    assert any(c["key"] == "outside_zone" for c in test["iou_0.3"]["categories"])


def test_comparisons_rows_and_decision(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    cmp = build_accuracy(outputs, splits, NOW)["comparisons"]
    assert cmp["protocol"] == "docs/evaluation_protocol.md v1"
    assert cmp["current"]["test_mae"] == 1.6 and cmp["current"]["val_mae"] == 1.2
    (row,) = cmp["rows"]
    assert row["label"] == "candidate B" and row["adopt"] is False
    assert row["failed_criteria"] == ["a", "c"]
    assert row["paired_delta_interval_95"] == [-0.4, 0.2]
    assert row["val_mae"] == 0.8 and row["processing_fps"] == 20.5


def test_new_sections_missing_reported(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path, s9=False)
    out = build_accuracy(outputs, splits, NOW)
    assert out["attribution_iou"] is None and out["comparisons"] is None
    assert "interval_clip_bootstrap_95" not in out["queue_length"]["pooled"]["test"]
    sections = {m["section"] for m in out["missing_sources"]}
    assert {"queue_length_intervals", "attribution_iou", "comparisons"} <= sections


def test_missing_paired_interval_reported(tmp_path: Path) -> None:
    outputs, splits = make_sources(tmp_path)
    (outputs / "s9_eval" / "B" / "intervals_vs_A.json").unlink()
    out = build_accuracy(outputs, splits, NOW)
    assert out["comparisons"]["rows"][0]["paired_delta_interval_95"] is None
    assert any(m["path"] == "outputs/s9_eval/B/intervals_vs_A.json" for m in out["missing_sources"])
