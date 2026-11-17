"""Export measured accuracy for the dashboard from analysis files on disk.

Example::

    python scripts/export_accuracy.py --outputs outputs --splits datasets/splits --out data/accuracy.json

Every number is read from a source file; a missing or unreadable source makes its
section ``null`` and adds a ``missing_sources`` entry. Nothing is typed in here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ai_engine.config import PROJECT_ROOT

SCHEMA = "accuracy-1"
QUEUE_640 = "outputs/s6_analysis/queue_640.json"
QUEUE_960 = "outputs/s6_analysis/queue_960.json"
ERRORS_640 = "outputs/s6_analysis/errors_640.json"
CHOSEN_ALL = "outputs/s6_sweep/chosen_all.json"
WAIT_GLOB = "outputs/metrics/*_wait_eval.json"
INTERVALS = "outputs/s9_eval/intervals_default.json"
ATTRIBUTION = "outputs/s9_eval/attribution_default.json"
COMPARISONS = "outputs/s9_eval/comparisons.json"
CALL_CENTRE = "outputs/metrics/anonymous_bank_wait_eval.json"
CALL_CENTRE_FILE = "anonymous_bank_wait_eval.json"  # not a camera clip: kept out of waiting_time
CALL_CENTRE_CITATION = (
    "\u2018Anonymous Bank\u2019 Call-Center Data, Ilan Guedj and Avi Mandelbaum, Technion SEE Center"
)
CALL_CENTRE_METHODS = ("predictor", "les", "recent_mean", "regression")
BASELINE = "as_configured"
SERIES_KEY = "smoothed_from5s"

# (key, label, direction) in display order; outside-the-zone is the sum of two keys.
CATEGORIES = [
    ("tp", "Counted correctly", "correct"),
    ("fp_unmatched", "Counted, no matching labelled box", "over"),
    ("counted_not_visible", "Counted while out of view (grace)", "over"),
    ("fp_label:walking", "Counted, labelled walking", "over"),
    ("fn_no_detection", "Labelled queuer not detected", "under"),
    ("fn_candidate_dwell", "Still in the dwell time", "under"),
]
OUTSIDE_KEYS = ("fn_foot_outside_zone", "gt_queueing_outside_zone")
OUTSIDE_LABEL = "Labelled queuer outside the zone"


class _Missing(Exception):
    """A source file is absent or unusable; ``args[0]`` is the reason."""


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise _Missing("file not found") from None
    except (OSError, ValueError) as error:
        raise _Missing(f"unreadable: {error}") from None


def _read_splits(splits_dir: Path, missing: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name in ("val", "test"):
        path = splits_dir / f"{name}.txt"
        try:
            lines = path.read_text().splitlines()
        except OSError:
            missing.append(
                {"section": "splits", "path": f"datasets/splits/{name}.txt", "reason": "file not found"}
            )
            out[name] = []
            continue
        out[name] = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    return out


def _metrics(block: dict) -> dict:
    return {
        "mae": block["mae"],
        "rmse": block["rmse"],
        "bias": block["bias"],
        "within_one_rate": block["within_one_rate"],
        "frames": block["matched"],
    }


def _queue_variant(data: dict, variant: str = BASELINE) -> dict:
    try:
        return data["variants"][variant]
    except (KeyError, TypeError):
        raise _Missing(f"no variant {variant!r}") from None


def _queue_length(outputs: Path, splits: dict[str, list[str]]) -> dict:
    data = _load(outputs / "s6_analysis" / "queue_640.json")
    try:
        variant = _queue_variant(data)
        split_of = {clip: name for name, clips in splits.items() for clip in clips}
        per_clip = [
            {"clip": clip, "split": split_of.get(clip), **_metrics(block[SERIES_KEY])}
            for clip, block in variant["per_clip"].items()
        ]
        membership = variant.get("pooled_clip_membership", {})
        pooled = {
            name: {
                **_metrics(variant["pooled"][name][SERIES_KEY]),
                "clips": list(membership.get(name, splits[name])),
            }
            for name in ("val", "test")
        }
        return {
            "source": QUEUE_640,
            "protocol": {
                "series": "smoothed",
                "window": "from5s",
                "tolerance_seconds": data["tolerance_seconds"],
                "from_seconds": data["from_seconds"],
            },
            "per_clip": per_clip,
            "pooled": pooled,
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise _Missing(f"unexpected layout: missing {error}") from None


def _categories(totals: dict, per_frame: dict) -> list[dict]:
    categories = [
        {"key": k, "label": label, "direction": d, "total": totals[k], "per_frame": per_frame[k]}
        for k, label, d in CATEGORIES
        if k in totals
    ]
    present = [k for k in OUTSIDE_KEYS if k in totals]
    if present:
        categories.append(
            {
                "key": "outside_zone",
                "label": OUTSIDE_LABEL,
                "direction": "other",
                "total": sum(totals[k] for k in present),
                "per_frame": round(sum(per_frame[k] for k in present), 4),
            }
        )
    return categories


def _count_errors(outputs: Path) -> dict:
    data = _load(outputs / "s6_analysis" / "errors_640.json")
    try:
        pooled = _queue_variant(data)["pooled"]
        per_split = {}
        for name, key in (("all", "all_clips"), ("val", "val"), ("test", "test")):
            block = pooled[key]["iou_0.5"]["from5s"]
            categories = _categories(block["totals"], block["mean_per_frame"])
            per_split[name] = {"frames": block["frames"], "categories": categories}
        return {"source": ERRORS_640, "iou": float("0.5"), "window": "from5s", "per_split": per_split}
    except (KeyError, TypeError, AttributeError) as error:
        raise _Missing(f"unexpected layout: missing {error}") from None


def _variant_name(key: str) -> str:
    match = re.fullmatch(r"grace([0-9.]+)_dwell([0-9.]+)", key)
    return f"grace {match[1]} s, dwell {match[2]} s" if match else key


def _pooled_mae(variant: dict, split: str) -> float:
    return variant["pooled"][split][SERIES_KEY]["mae"]


def _changes(outputs: Path, missing: list[dict]) -> list[dict] | None:
    entries: list[dict] = []

    def row(name: str, source: str, variant: dict, base: dict) -> dict:
        return {
            "name": name,
            "source": source,
            "sprint": "Sprint 6",
            "val_mae": _pooled_mae(variant, "val"),
            "test_mae": _pooled_mae(variant, "test"),
            "baseline_val_mae": _pooled_mae(base, "val"),
            "baseline_test_mae": _pooled_mae(base, "test"),
        }

    def attempt(source: str, build) -> None:
        try:
            entries.extend(build())
        except _Missing as error:
            missing.append({"section": "changes_not_adopted", "path": source, "reason": str(error)})
        except (KeyError, TypeError, AttributeError) as error:
            missing.append(
                {
                    "section": "changes_not_adopted",
                    "path": source,
                    "reason": f"unexpected layout: missing {error}",
                }
            )

    def detector_960():
        base = _queue_variant(_load(outputs / "s6_analysis" / "queue_640.json"))
        wide = _queue_variant(_load(outputs / "s6_analysis" / "queue_960.json"))
        return [row("Detector input 960 px", QUEUE_960, wide, base)]

    def sweep():
        variants = _load(outputs / "s6_sweep" / "chosen_all.json")["variants"]
        base = variants[BASELINE]
        return [row(_variant_name(k), CHOSEN_ALL, v, base) for k, v in variants.items() if k != BASELINE]

    attempt(QUEUE_960, detector_960)
    attempt(CHOSEN_ALL, sweep)
    return entries or None


def _waiting_time(outputs: Path, missing: list[dict]) -> dict | None:
    """Group wait-eval files by clip; ``kind`` is predicted (``_wait_eval``) or observed.

    Unreadable or malformed files are reported in ``missing`` rather than skipped.
    """
    per_clip, pooled = [], {}
    for path in sorted((outputs / "metrics").glob("*_wait_eval.json")):
        if path.name == CALL_CENTRE_FILE:
            continue
        stem = path.name.removesuffix("_wait_eval.json")
        observed = stem.endswith("_observed")
        clip = stem.removesuffix("_observed") if observed else stem
        kind = "observed" if observed else "predicted"
        source = f"outputs/metrics/{path.name}"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            detail = getattr(error, "strerror", None)
            reason = f"unreadable: {type(error).__name__}" + (f" ({detail})" if detail else "")
            missing.append({"section": "waiting_time", "path": source, "reason": reason})
            continue
        if not isinstance(data, dict) or "metrics" not in data:
            missing.append(
                {"section": "waiting_time", "path": source, "reason": "unexpected layout: no metrics"}
            )
            continue
        per_clip.append({"clip": clip, "kind": kind, "source": source, "metrics": data["metrics"]})
        if "pooled" in data:
            pooled[f"{clip}_observed" if observed else clip] = data["pooled"]
    if not per_clip:
        return None
    per_clip.sort(key=lambda entry: (entry["clip"], entry["kind"]))
    return {"source": WAIT_GLOB, "per_clip": per_clip, "pooled": pooled or None}


def _call_centre(outputs: Path) -> dict:
    """Test-month headline of the Anonymous Bank replay, copied from its eval JSON.

    The post-hoc S1 summary is flagged ``post_hoc`` and never replaces the headline.
    """
    data = _load(outputs / "metrics" / CALL_CENTRE_FILE)
    try:
        test = data["queues"]["primary"]["test"]
        s1 = data["post_hoc_sensitivity"]["S1_all_service_starts"]
        s1_test = s1["test"]
        return {
            "source": CALL_CENTRE,
            "citation": CALL_CENTRE_CITATION,
            "caveat": data["caveat"],
            "protocol_version": data["protocol_version"],
            "interval": data["interval"],
            "seed": data["seed"],
            "queue": "primary",
            "split": "test",
            "n_days": test["n_days"],
            "n_calls": test["methods"]["predictor"]["n_agent"],
            "methods": {name: test["methods"][name] for name in CALL_CENTRE_METHODS},
            "paired_mae_difference": test["paired_mae_difference"],
            "hang": test["hang"],
            "post_hoc_s1": {
                "post_hoc": s1["post_hoc"],
                "protocol_version": s1["protocol_version"],
                "definition": s1["definition"],
                "n_days": s1_test["n_days"],
                "n_calls": s1_test["methods"]["predictor"]["n_agent"],
                "methods": {name: s1_test["methods"][name] for name in CALL_CENTRE_METHODS},
                "paired_mae_difference": s1_test["paired_mae_difference"],
                "hang": s1_test["hang"],
            },
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise _Missing(f"unexpected layout: missing {error}") from None


def _band(block: dict, key: str) -> list[float]:
    return [block[key]["low"], block[key]["high"]]


def _interval_fields(block: dict, per_frame: dict) -> dict:
    keys = ("mae", "bias", "within_one_rate")
    return {
        "interval_clip_bootstrap_95": {k: _band(block, k) for k in keys},
        "n_clips": block["n_clips"],
        "n_frames": block["n_frames"],
        "interval_per_frame_optimistic_95": {
            "label": per_frame["label"],
            **{k: _band(per_frame, k) for k in keys},
        },
    }


def _add_intervals(queue_length: dict, outputs: Path, missing: list[dict]) -> None:
    """Attach clip-bootstrap intervals (and the optimistic per-frame ones) to each pooled split."""
    try:
        data = _load(outputs / "s9_eval" / "intervals_default.json")
        meta = {"protocol": data["protocol"], "series": data["series"], "bootstrap": data["bootstrap"]}
        fields = {
            name: _interval_fields(
                data["splits"][name]["clip_bootstrap"], data["splits"][name]["per_frame_bootstrap_optimistic"]
            )
            for name in ("val", "test")
        }
    except _Missing as error:
        missing.append({"section": "queue_length_intervals", "path": INTERVALS, "reason": str(error)})
        return
    except (KeyError, TypeError, AttributeError) as error:
        reason = f"unexpected layout: missing {error}"
        missing.append({"section": "queue_length_intervals", "path": INTERVALS, "reason": reason})
        return
    queue_length["intervals_source"] = INTERVALS
    queue_length["intervals_meta"] = meta
    for name, extra in fields.items():
        queue_length["pooled"][name].update(extra)


def _attribution_iou(outputs: Path) -> dict:
    """Error attribution at IoU 0.5 and 0.3 side by side (protocol window, raw count)."""
    data = _load(outputs / "s9_eval" / "attribution_default.json")
    try:
        per_split = {}
        for name, key in (("all", "all_clips"), ("val", "val"), ("test", "test")):
            block = data["splits"][key]["from5s"]
            per_split[name] = {
                f"iou_{iou}": {
                    "frames": block[f"iou_{iou}"]["frames"],
                    "categories": _categories(
                        block[f"iou_{iou}"]["totals"], block[f"iou_{iou}"]["per_frame"]
                    ),
                }
                for iou in ("0.5", "0.3")
            }
        return {
            "source": ATTRIBUTION,
            "window": "from5s",
            "iou_thresholds": [0.5, 0.3],
            "per_split": per_split,
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise _Missing(f"unexpected layout: missing {error}") from None


def _rel_source(path: str) -> str:
    try:
        return str(Path(path).relative_to(PROJECT_ROOT))
    except ValueError:
        return path


def _comparisons(outputs: Path, missing: list[dict]) -> dict:
    """Candidate-vs-current rows and adoption decisions, copied from comparisons.json."""
    data = _load(outputs / "s9_eval" / "comparisons.json")
    try:
        configs = data["configurations"]
        current_interval = None
        try:
            current_test = _load(outputs / "s9_eval" / "intervals_default.json")["splits"]["test"][
                "clip_bootstrap"
            ]["mae"]
            current_interval = [current_test["low"], current_test["high"]]
        except (_Missing, KeyError, TypeError) as error:
            missing.append(
                {"section": "comparisons", "path": INTERVALS, "reason": f"current interval: {error}"}
            )
        base = configs["A"]
        bench = base.get("benchmark") or {}
        rows = []
        for r in data["rows"]:
            cfg, decision = r["configuration"], r["decision"]
            paired = None
            paired_path = f"outputs/s9_eval/{cfg}/intervals_vs_A.json"
            try:
                pb = _load(outputs / "s9_eval" / cfg / "intervals_vs_A.json")["splits"]["test"][
                    "paired_clip_bootstrap"
                ]["mae"]
                paired = [pb["low"], pb["high"]]
            except (_Missing, KeyError, TypeError) as error:
                missing.append(
                    {"section": "comparisons", "path": paired_path, "reason": f"paired interval: {error}"}
                )
            failed = [
                c[-1] for c in ("criterion_a", "criterion_b", "criterion_c") if not decision[c]["passes"]
            ]
            rows.append(
                {
                    "configuration": cfg,
                    "label": configs[cfg]["label"],
                    "metric": r["metric"],
                    "test_mae": r["value"],
                    "interval_clip_bootstrap_95": r["interval_clip_bootstrap_95"],
                    "paired_delta_vs_A": r["paired_delta_vs_A"],
                    "paired_delta_interval_95": paired,
                    "val_mae": decision["criterion_b"]["val_mae_new"],
                    "processing_fps": decision["criterion_c"]["processing_fps"],
                    "clip_fps": decision["criterion_c"]["clip_fps"],
                    "adopt": decision["adopt"],
                    "failed_criteria": failed,
                    "decision": decision,
                    "source": [_rel_source(x) for x in r["source"]],
                }
            )
        return {
            "source": COMPARISONS,
            "protocol": data["protocol"],
            "series": data["series"],
            "bootstrap": data["bootstrap"],
            "notes": data["notes"],
            "current": {
                "configuration": "A",
                "label": base["label"],
                "test_mae": base["per_split_smoothed_from5s"]["test"]["mae"],
                "test_interval_clip_bootstrap_95": current_interval,
                "interval_source": INTERVALS,
                "val_mae": base["per_split_smoothed_from5s"]["val"]["mae"],
                "processing_fps": bench.get("processing_fps"),
                "clip_fps": bench.get("clip_fps"),
            },
            "rows": rows,
        }
    except (KeyError, TypeError, AttributeError) as error:
        raise _Missing(f"unexpected layout: missing {error}") from None


def build_accuracy(outputs_dir: Path, splits_dir: Path, now: datetime) -> dict:
    """Build the accuracy JSON from ``outputs_dir`` and ``splits_dir``; ``now`` stamps it."""
    missing: list[dict] = []
    splits = _read_splits(splits_dir, missing)

    def section(name: str, path: str, build):
        try:
            return build()
        except _Missing as error:
            missing.append({"section": name, "path": path, "reason": str(error)})
            return None

    queue_length = section("queue_length", QUEUE_640, lambda: _queue_length(outputs_dir, splits))
    count_errors = section("count_errors", ERRORS_640, lambda: _count_errors(outputs_dir))
    if queue_length is not None:
        _add_intervals(queue_length, outputs_dir, missing)
    attribution = section("attribution_iou", ATTRIBUTION, lambda: _attribution_iou(outputs_dir))
    comparisons = section("comparisons", COMPARISONS, lambda: _comparisons(outputs_dir, missing))
    changes = _changes(outputs_dir, missing)
    waiting = _waiting_time(outputs_dir, missing)
    call_centre = section("predictor_on_call_centre_logs", CALL_CENTRE, lambda: _call_centre(outputs_dir))
    if waiting is None:
        missing.append(
            {"section": "waiting_time", "path": WAIT_GLOB, "reason": "not measured yet (session 3)"}
        )

    limits = []
    cad = {name: [c for c in clips if c.startswith("cad_")] for name, clips in splits.items()}
    if cad["val"] or cad["test"]:
        n_val, n_test = len(cad["val"]), len(cad["test"])
        limits.append(f"Queue length is measured on {n_val} val and {n_test} test CAD clips only")
    limits.append("CAD frame rate is assumed to be 30 fps")
    limits.append("CAD boxes are looser than the detector's, which inflates the matched-box error categories")
    if waiting is None:
        limits.append("Waiting-time accuracy is not measured yet")

    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(),
        "missing_sources": missing,
        "queue_length": queue_length,
        "count_errors": count_errors,
        "changes_not_adopted": changes,
        "waiting_time": waiting,
        "predictor_on_call_centre_logs": call_centre,
        "attribution_iou": attribution,
        "comparisons": comparisons,
        "limits": limits,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export measured accuracy for the dashboard.")
    p.add_argument("--outputs", type=Path, default=Path("outputs"))
    p.add_argument("--splits", type=Path, default=Path("datasets/splits"))
    p.add_argument("--out", type=Path, default=Path("data/accuracy.json"))
    args = p.parse_args(argv)

    def rel(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path

    result = build_accuracy(rel(args.outputs), rel(args.splits), datetime.now(UTC))
    out = rel(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")
    for entry in result["missing_sources"]:
        print(f"missing: {entry['section']} ({entry['path']}): {entry['reason']}", file=sys.stderr)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
