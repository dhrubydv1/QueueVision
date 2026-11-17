"""Torch-free checks for scripts/run_comparisons.py helpers."""

import json
from pathlib import Path

from scripts.run_comparisons import own_test_mae_interval


def test_row_interval_is_the_configurations_own(tmp_path: Path) -> None:
    out = tmp_path / "B"
    out.mkdir()
    own = {"splits": {"test": {"clip_bootstrap": {"mae": {"point": 1.4, "low": 1.08, "high": 1.84}}}}}
    (out / "intervals.json").write_text(json.dumps(own), encoding="utf-8")
    # the paired file carries the reference's interval under the same key; it must not be used
    ref = {"splits": {"test": {"clip_bootstrap": {"mae": {"point": 1.6, "low": 0.97, "high": 2.03}}}}}
    (out / "intervals_vs_A.json").write_text(json.dumps(ref), encoding="utf-8")
    assert own_test_mae_interval(out) == [1.08, 1.84]
