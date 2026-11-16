import numpy as np
import pytest

from ai_engine.evaluation.bootstrap import (
    OPTIMISTIC_LABEL,
    SEED,
    align_frames,
    paired_difference,
    pooled_intervals,
)


def clip(error, n, rounded=None, t0=0.0):
    r = round(error) if rounded is None else rounded
    return [(t0 + i, float(error), r) for i in range(n)]


def test_seed_is_protocol_seed():
    assert SEED == 20261116


def test_two_clips_constant_errors_known_endpoints():
    # Clip A error 0 (3 frames), clip B error +4 (3 frames). Pooled MAE of a resample is
    # 0, 2 or 4 with probability 1/4, 1/2, 1/4, so the 95 % percentile interval is [0, 4].
    data = {"a": clip(0, 3), "b": clip(4, 3)}
    r = pooled_intervals(data, ["a", "b"], n_resamples=4000)
    assert r["n_clips"] == 2 and r["n_frames"] == 6
    assert r["mae"]["point"] == pytest.approx(2.0)
    assert r["mae"]["low"] == pytest.approx(0.0)
    assert r["mae"]["high"] == pytest.approx(4.0)
    assert r["bias"]["point"] == pytest.approx(2.0)
    assert r["within_one_rate"]["point"] == pytest.approx(0.5)
    assert r["within_one_rate"]["low"] == pytest.approx(0.0)
    assert r["within_one_rate"]["high"] == pytest.approx(1.0)


def test_pooled_over_frames_not_clip_means():
    # 1 frame error 0 vs 9 frames error 10: pooled MAE 9, not mean of clip MAEs (5).
    data = {"a": clip(0, 1), "b": clip(10, 9)}
    assert pooled_intervals(data, ["a", "b"], n_resamples=100)["mae"]["point"] == pytest.approx(9.0)


def test_seed_stability_and_seed_sensitivity():
    data = {"a": clip(1, 4), "b": clip(3, 5), "c": clip(-2, 6)}
    r1 = pooled_intervals(data, ["a", "b", "c"], n_resamples=500)
    r2 = pooled_intervals(data, ["a", "b", "c"], n_resamples=500)
    r3 = pooled_intervals(data, ["a", "b", "c"], n_resamples=500, seed=SEED + 1)
    assert r1 == r2
    assert r1["mae"] != r3["mae"]


def test_per_frame_labelled_optimistic_and_narrower():
    data = {"a": clip(0, 50), "b": clip(4, 50)}
    c = pooled_intervals(data, ["a", "b"], unit="clip", n_resamples=1000)
    f = pooled_intervals(data, ["a", "b"], unit="frame", n_resamples=1000)
    assert f["label"] == OPTIMISTIC_LABEL and "label" not in c
    assert f["mae"]["point"] == c["mae"]["point"]
    assert (f["mae"]["high"] - f["mae"]["low"]) < (c["mae"]["high"] - c["mae"]["low"])


def test_paired_identical_configs_difference_zero():
    data = {"a": clip(1, 4), "b": clip(3, 5), "c": clip(-2, 6)}
    for unit in ("clip", "frame"):
        d = paired_difference(data, data, ["a", "b", "c"], unit=unit, n_resamples=500)
        for m in ("mae", "bias", "within_one_rate"):
            assert (d[m]["point"], d[m]["low"], d[m]["high"]) == (0.0, 0.0, 0.0)
            assert d[m]["half_width"] == 0.0


def test_paired_uses_same_draws_known_answer():
    # new is exactly 1 better in every clip -> difference is -1 in every resample (zero width).
    cur = {"a": clip(2, 3), "b": clip(4, 3), "c": clip(3, 2)}
    new = {"a": clip(1, 3), "b": clip(3, 3), "c": clip(2, 2)}
    d = paired_difference(new, cur, ["a", "b", "c"], n_resamples=500)
    assert d["mae"]["point"] == pytest.approx(-1.0)
    assert d["mae"]["low"] == pytest.approx(-1.0) and d["mae"]["high"] == pytest.approx(-1.0)
    # Independent draws would not give zero width; paired draws do.


def test_align_drops_unshared_frames():
    a = {"x": clip(1, 3)}
    b = {"x": clip(1, 2)}
    aa, bb, dropped = align_frames(a, b, ["x"])
    assert len(aa["x"]) == len(bb["x"]) == 2 and dropped == 1
    d = paired_difference(a, b, ["x"], n_resamples=50)
    assert d["n_frames"] == 2 and d["n_frames_dropped_unaligned"] == 1


def test_empty_split():
    r = pooled_intervals({}, ["a"], n_resamples=10)
    assert r["n_frames"] == 0 and r["mae"]["point"] is None
    assert not np.isnan(r["n_frames"])


def test_script_report_paired_identical(tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "evaluate_queue_intervals",
        Path(__file__).resolve().parents[1] / "scripts/evaluate_queue_intervals.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "val.txt").write_text("a\nb\n")
    (tmp_path / "test.txt").write_text("c\n")
    frames = {"a": clip(1, 3), "b": clip(2, 3), "c": clip(3, 3)}
    side = tmp_path / "fe.json"
    side.write_text(
        json.dumps(
            {
                "variants": {
                    "as_configured": {k: {"smoothed_from5s": [list(f) for f in v]} for k, v in frames.items()}
                }
            }
        )
    )
    out = tmp_path / "out.json"
    assert (
        mod.main(
            [
                "--frame-errors",
                str(side),
                "--other",
                str(side),
                "--splits-dir",
                str(tmp_path),
                "--n-resamples",
                "100",
                "--output",
                str(out),
            ]
        )
        == 0
    )
    rep = json.loads(out.read_text())
    assert set(rep["splits"]) == {"all", "val", "test"}
    assert rep["splits"]["val"]["clip_bootstrap"]["n_clips"] == 2
    paired = rep["splits"]["test"]["paired_clip_bootstrap"]["mae"]
    assert (paired["low"], paired["high"]) == (0.0, 0.0)
    assert rep["splits"]["test"]["adoption_criterion_a"]["improvement_beyond_half_width"] is False


def test_attribution_summary_side_by_side():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "summarize_attribution", Path(__file__).resolve().parents[1] / "scripts/summarize_attribution.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cell(tp):
        return {
            "frames": 2, "raw_count_total": 6, "gt_queueing_total": 5, "totals": {"tp": tp},
            "fp_total": 1, "fn_total": 0, "mean_per_frame": {"tp": tp / 2}, "mean_raw": 3.0,
            "mean_gt": 2.5, "mean_fp_total": 0.5, "mean_fn_total": 0.0, "bias": 0.5,
        }  # fmt: skip

    block = {t: {"from5s": cell(i), "all": cell(i + 1)} for i, t in enumerate(("iou_0.5", "iou_0.3"), 3)}
    errors = {
        "variants": {"as_configured": {"pooled": {"test": block}, "pooled_clip_membership": {"test": ["c"]}}}
    }
    s = mod.build_summary(errors)["splits"]["test"]
    assert s["clips"] == ["c"]
    assert s["from5s"]["iou_0.5"]["totals"]["tp"] == 3 and s["from5s"]["iou_0.3"]["totals"]["tp"] == 4
    assert s["from5s"]["iou_0.3"]["per_frame"]["tp"] == 2.0
