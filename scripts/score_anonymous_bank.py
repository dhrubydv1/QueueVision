"""Score QueueVision's wait predictor on the Anonymous Bank call logs (protocol v1).

Usage: .venv/bin/python -I scripts/score_anonymous_bank.py
Re-parses the raw monthly files (the tidy CSVs lack vru_entry, needed for the tie rule).
Writes outputs/metrics/anonymous_bank_wait_eval.json and PNG figures (both gitignored).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ai_engine.data.anonymous_bank import parse_month  # noqa: E402
from ai_engine.data.anonymous_bank_replay import (  # noqa: E402
    QUEUE_DEFINITIONS,
    carried_over_count,
    group_by_day,
    regression_fit,
    replay_queue,
    select_queue,
)
from ai_engine.evaluation import day_bootstrap as bs  # noqa: E402
from ai_engine.evaluation.anonymous_bank_metrics import (  # noqa: E402
    METHODS,
    Frame,
    breakdowns,
    hang_report,
    little_law,
    method_metrics,
    months_split,
    paired_vs_baselines,
)
from ai_engine.queue.camera_config import ServiceRateSettings  # noqa: E402

PROTOCOL_VERSION = 1
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
COLORS = {"predictor": "#1f77b4", "les": "#ff7f0e", "recent_mean": "#2ca02c", "regression": "#9467bd"}
LABELS = {"predictor": "QueueVision predictor", "les": "Last to enter service",
          "recent_mean": "Recent mean (20)", "regression": "Queue-length regression"}


def split_preds(preds):
    out = {"train": [], "validation": [], "test": []}
    for p in preds:
        out[months_split(p.date.month)].append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT / "datasets/raw/anonymous_bank/technion/extracted"))
    ap.add_argument("--out", default=str(ROOT / "outputs/metrics/anonymous_bank_wait_eval.json"))
    ap.add_argument("--figs", default=str(ROOT / "outputs/figures/anonymous_bank"))
    a = ap.parse_args()
    t0 = time.time()
    figs = Path(a.figs)
    figs.mkdir(parents=True, exist_ok=True)

    records = []
    for m in MONTHS:
        records.extend(parse_month(Path(a.raw) / f"{m}1999.txt"))
    kept = [r for r in records if r.outcome != "PHANTOM"]

    queues = {name: select_queue(kept, name) for name in QUEUE_DEFINITIONS}
    preds = {name: replay_queue(calls) for name, calls in queues.items()}
    sp = {name: split_preds(p) for name, p in preds.items()}

    a_coef, b_coef = regression_fit(sp["primary"]["train"])
    reg = (a_coef, b_coef)

    result: dict = {
        "protocol_version": PROTOCOL_VERSION,
        "seed": bs.SEED, "n_resamples": bs.N_RESAMPLES,
        "interval": "95% percentile, day-level cluster bootstrap",
        "service_rate_settings": "ServiceRateSettings() defaults (window 300 s, min_services 2, "
                                 "min_history 60 s, basis service_start)",
        "caveat": "Phone queue, 1999, Israel; measures the prediction formula, not the cameras.",
        "input_file_sha256": {},
        "regression_baseline": {"fitted_on": "January-August primary-queue AGENT calls",
                                "a_s": a_coef, "b_s_per_queue_position": b_coef,
                                "form": "wait = a + b * (queue_length + 1)"},
        "paired_note": "MAE difference = MAE(predictor) - MAE(baseline) on calls covered by both; "
                       "negative favours the predictor.",
        "population": {},
        "queues": {},
    }
    with open(ROOT / "datasets/metadata/anonymous_bank_files.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["source"] == "technion":
                result["input_file_sha256"][row["name"]] = row["sha256"]

    frames: dict = {}
    for qname in QUEUE_DEFINITIONS:
        result["population"][qname] = {
            "queued_calls": len(queues[qname]),
            "agent": sum(c.outcome == "AGENT" for c in queues[qname]),
            "hang": sum(c.outcome == "HANG" for c in queues[qname]),
            "q_exit_at_or_after_24h_not_carried_over": carried_over_count(queues[qname]),
            "q_start_after_24h_of_its_date": sum(c.q_start_ts >= 86_400 for c in queues[qname]),
        }
        result["queues"][qname] = {}
        for split in ("validation", "test"):
            fr = Frame(sp[qname][split], reg)
            frames[(qname, split)] = fr
            blk = {"n_days": fr.n_days, "methods": {m: method_metrics(fr, m) for m in METHODS},
                   "hang": hang_report(fr), "paired_mae_difference": paired_vs_baselines(fr)}
            if qname == "primary":
                blk["breakdowns"] = {m: breakdowns(fr, m) for m in METHODS}
            result["queues"][qname][split] = blk

    # Exploration: windows on Jan-Aug only (reported separately; nothing chosen from it).
    expl = {}
    for w in (60, 120, 300, 600):
        s = ServiceRateSettings(window_seconds=float(w))
        p = split_preds(replay_queue(queues["primary"], s))["train"]
        expl[str(w)] = method_metrics(Frame(p, reg), "predictor")
    result["exploration_training_months_only"] = {
        "note": "window_seconds on January-August, primary queue, predictor only; 300 is the default.",
        "windows_s": expl,
    }

    # Little's law, primary queue, all months.
    by_day = group_by_day(queues["primary"])
    result["littles_law_primary_all_months"] = {
        "n_days": len(by_day), "per_hour": little_law(by_day, len(by_day))}

    result["runtime_s"] = round(time.time() - t0, 1)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=1))

    make_figures(result, frames, figs)
    print(f"wrote {a.out} in {result['runtime_s']} s")


def make_figures(result, frames, figs: Path) -> None:
    t = result["queues"]["primary"]["test"]
    fr = frames[("primary", "test")]

    # 1 headline MAE with intervals
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, m in enumerate(METHODS):
        v = t["methods"][m]["mae_s"]
        ax.bar(i, v["value"], color=COLORS[m], yerr=[[v["value"] - v["lo"]], [v["hi"] - v["value"]]],
               capsize=4)
    ax.set_xticks(range(4), [LABELS[m] for m in METHODS], rotation=15, ha="right")
    ax.set_ylabel("MAE on test months (s)")
    ax.set_title("Nov-Dec 1999, primary queue: MAE with 95% day-bootstrap interval")
    fig.tight_layout()
    fig.savefig(figs / "headline_mae_test.png", dpi=130)
    plt.close(fig)

    # 2 predicted vs actual
    mk = fr.mask("predictor")
    fig, ax = plt.subplots(figsize=(5.5, 5))
    hb = ax.hexbin(fr.wait[mk], fr.m["predictor"][mk], gridsize=50, bins="log", mincnt=1,
                   extent=(0, 600, 0, 600), cmap="viridis")
    ax.plot([0, 600], [0, 600], "r--", lw=1, label="perfect")
    ax.set_xlabel("Actual wait (s)")
    ax.set_ylabel("Predicted wait (s)")
    ax.set_title("Test months, AGENT calls (log count)")
    ax.legend()
    fig.colorbar(hb)
    fig.tight_layout()
    fig.savefig(figs / "predicted_vs_actual_test.png", dpi=130)
    plt.close(fig)

    # 3 error histogram
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for m in ("predictor", "les", "recent_mean", "regression"):
        k = fr.mask(m)
        ax.hist(np.clip(fr.m[m][k] - fr.wait[k], -300, 300), bins=60, histtype="step",
                color=COLORS[m], label=LABELS[m])
    ax.set_xlabel("Predicted - actual wait (s), clipped at +-300")
    ax.set_ylabel("AGENT calls")
    ax.legend(fontsize=8)
    ax.set_title("Error distribution, test months")
    fig.tight_layout()
    fig.savefig(figs / "error_histogram_test.png", dpi=130)
    plt.close(fig)

    # 4 MAE by hour
    bd = result["queues"]["primary"]["test"]["breakdowns"]
    fig, ax = plt.subplots(figsize=(8, 4))
    for m in METHODS:
        hs = [h for h in range(24) if "mae_s" in bd[m]["hour"][str(h)]]
        ax.plot(hs, [bd[m]["hour"][str(h)]["mae_s"]["value"] for h in hs], marker="o", ms=3,
                color=COLORS[m], label=LABELS[m])
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("MAE (s)")
    ax.legend(fontsize=8)
    ax.set_title("MAE by hour of day, test months")
    fig.tight_layout()
    fig.savefig(figs / "mae_by_hour_test.png", dpi=130)
    plt.close(fig)

    # 5 MAE by queue length band
    bands = ["0", "1-2", "3-5", "6-10", ">10"]
    fig, ax = plt.subplots(figsize=(7, 4))
    for i, m in enumerate(METHODS):
        vals = [bd[m]["queue_length_band"][b].get("mae_s", {}).get("value", np.nan) for b in bands]
        ax.bar(np.arange(5) + i * 0.2 - 0.3, vals, 0.2, color=COLORS[m], label=LABELS[m])
    ax.set_xticks(range(5), bands)
    ax.set_xlabel("Queue length at arrival")
    ax.set_ylabel("MAE (s)")
    ax.legend(fontsize=8)
    ax.set_title("MAE by queue-length band, test months")
    fig.tight_layout()
    fig.savefig(figs / "mae_by_queue_length_test.png", dpi=130)
    plt.close(fig)

    # 6 Little's law
    ll = result["littles_law_primary_all_months"]["per_hour"]
    hs = [h for h in range(24) if ll[str(h)]["lambda_times_W"] is not None]
    fig, ax = plt.subplots(figsize=(5.5, 5))
    x = [ll[str(h)]["lambda_times_W"] for h in hs]
    y = [ll[str(h)]["time_avg_queue_length"] for h in hs]
    ax.scatter(x, y, c=hs, cmap="viridis")
    for h, xi, yi in zip(hs, x, y, strict=True):
        ax.annotate(str(h), (xi, yi), fontsize=7)
    top = max(x + y) * 1.05
    ax.plot([0, top], [0, top], "r--", lw=1, label="L = lambda W")
    ax.set_xlabel("lambda x mean wait (calls)")
    ax.set_ylabel("Time-average queue length (calls)")
    ax.set_title("Little's law per hour of day (labels = hour)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(figs / "littles_law.png", dpi=130)
    plt.close(fig)

    # 7 exploration windows
    ex = result["exploration_training_months_only"]["windows_s"]
    fig, ax = plt.subplots(figsize=(5.5, 4))
    ws = list(ex)
    vals = [ex[w]["mae_s"] for w in ws]
    ax.errorbar(range(len(ws)), [v["value"] for v in vals],
                yerr=[[v["value"] - v["lo"] for v in vals], [v["hi"] - v["value"] for v in vals]],
                fmt="o", capsize=4)
    ax.set_xticks(range(len(ws)), [f"{w} s" for w in ws])
    ax.set_xlabel("Service-rate window")
    ax.set_ylabel("MAE (s), Jan-Aug (exploration)")
    fig.tight_layout()
    fig.savefig(figs / "exploration_windows_train.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
