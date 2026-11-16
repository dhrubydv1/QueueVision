# ruff: noqa: E501
"""Run the detector/tracker comparisons governed by docs/evaluation_protocol.md (version 1).

Steps (run one model job at a time; wrap in ``caffeinate -i`` so the Mac does not sleep)::

    caffeinate -i .venv/bin/python scripts/run_comparisons.py track B      # tracks, 7 CAD + MOT17-04/09
    caffeinate -i .venv/bin/python scripts/run_comparisons.py analyze B    # replay, intervals, attribution, MOT17
    caffeinate -i .venv/bin/python scripts/run_comparisons.py benchmark A  # fps + peak RSS on cad_seq27
    .venv/bin/python scripts/run_comparisons.py decide                     # comparisons.json

A is the existing Sprint 5 run (``outputs/s5_tracks``); it is never re-run, only analysed again so every
configuration passes through the same code. B, C, D write to ``outputs/s9_eval/<name>/``. Nothing is tuned and
nothing is adopted here: ``decide`` applies the committed rule and reports.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
OUT = ROOT / "outputs" / "s9_eval"
CAD = ["cad_seq10", "cad_seq25", "cad_seq26", "cad_seq27", "cad_seq39", "cad_seq40", "cad_seq41"]
MOT = ["mot17_04", "mot17_09"]
MOT_CAMERA = "configs/cameras/example_queue.yaml"  # what the Sprint 5 MOT17 runs used
BENCH_CLIP = "cad_seq27"
PROTOCOL = "docs/evaluation_protocol.md v1"
CONFIGS = {
    "A": {
        "model": "yolo11n.pt",
        "tracker": "bytetrack",
        "reid": "off",
        "label": "YOLO11n + ByteTrack (default)",
    },
    "B": {"model": "yolo11s.pt", "tracker": "bytetrack", "reid": "off", "label": "YOLO11s + ByteTrack"},
    "C": {"model": "yolo11n.pt", "tracker": "botsort", "reid": "off", "label": "YOLO11n + BoT-SORT"},
    "D": {"model": "yolo11n.pt", "tracker": "botsort", "reid": "on", "label": "YOLO11n + BoT-SORT + ReID"},
}
TEST_FILE_NOTE = (
    "datasets/splits/test.txt also lists mot17_04 and mot17_09; they have no queue labels, are not in the "
    "replayed clip set, and so are excluded from every queue metric (they are used for MOT17 tracking only)."
)


def run(cmd: list[str], log: Path | None = None) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    if log is None:
        subprocess.run(cmd, cwd=ROOT, check=True)
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as handle:
        subprocess.run(cmd, cwd=ROOT, check=True, stdout=handle, stderr=subprocess.STDOUT)


def tracks_dir(name: str, kind: str) -> Path:
    base = ROOT / "outputs" / "s5_tracks" if name == "A" else OUT / name / "tracks_run"
    return base / kind


def track(name: str) -> None:
    cfg = CONFIGS[name]
    if name == "A":
        raise SystemExit("A is the existing Sprint 5 run; it is not re-run")
    for clip in CAD + MOT:
        kind = "cad" if clip.startswith("cad") else "mot17"
        camera = f"configs/cameras/{clip}.yaml" if kind == "cad" else MOT_CAMERA
        cmd = [
            PY, "-m", "ai_engine.run_tracking", "--video", f"datasets/raw/{clip}.mp4",
            "--model", cfg["model"], "--device", "mps", "--imgsz", "640", "--camera-config", camera,
            "--save-mot", "--no-h264", "--tracker", cfg["tracker"], "--reid", cfg["reid"],
            "--output", str(tracks_dir(name, kind).relative_to(ROOT)),
        ]  # fmt: skip
        run(cmd, OUT / name / "logs" / f"track_{clip}.log")


def labelled_peak(clip: str) -> int:
    path = ROOT / "datasets" / "annotations" / "cad" / f"{clip}_queue_counts.csv"
    with path.open(encoding="utf-8") as handle:
        return max(int(row["queue_count"]) for row in csv.DictReader(handle))


def clip_metrics(name: str, clip: str) -> dict:
    kind = "cad" if clip.startswith("cad") else "mot17"
    path = tracks_dir(name, kind) / "metrics" / f"{clip}_metrics.json"
    return json.loads(path.read_text(encoding="utf-8"))


def analyze(name: str) -> None:
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    cad_tracks = str(tracks_dir(name, "cad").relative_to(ROOT) / "tracks")
    py = [PY, "-m"]
    run([*py, "ai_engine.replay_queue", "--clips", *CAD, "--tracks-dir", cad_tracks, "--splits-dir",
         "datasets/splits", "--output", str(out / "queue_640.json"),
         "--frame-errors-output", str(out / "frame_errors.json")], out / "queue_640.txt")  # fmt: skip
    run([*py, "ai_engine.analyze_queue_errors", "--clips", *CAD, "--tracks-dir", cad_tracks, "--splits-dir",
         "datasets/splits", "--output", str(out / "errors_640.json")], out / "errors_640.txt")  # fmt: skip
    run([PY, "scripts/summarize_attribution.py", "--errors", str(out / "errors_640.json"), "--output",
         str(out / "attribution.json")])  # fmt: skip
    default_fe = str(OUT / "frame_errors_default.json")
    own_fe = default_fe if name == "A" else str(out / "frame_errors.json")
    run(
        [
            PY,
            "scripts/evaluate_queue_intervals.py",
            "--frame-errors",
            own_fe,
            "--output",
            str(out / "intervals.json"),
        ]
    )
    if name != "A":
        run(
            [
                PY, "scripts/evaluate_queue_intervals.py", "--frame-errors", default_fe, "--other", own_fe,
                "--output", str(out / "intervals_vs_A.json"),
            ]
        )  # fmt: skip
    for clip in MOT:
        seq = "MOT17-" + clip.split("_")[1]
        run([*py, "ai_engine.evaluate_tracking", "--tracks",
             str(tracks_dir(name, "mot17") / "tracks" / f"{clip}_mot.txt"), "--sequence-dir",
             f"datasets/annotations/mot17/{seq}", "--video", f"datasets/raw/{clip}.mp4", "--output",
             str(out / f"{clip}_tracking_eval.json")])  # fmt: skip
    # joins and stitching links come from the per-clip run metrics (live pipeline with the shipped configs)
    joins = {}
    for clip in CAD + MOT:
        queue = clip_metrics(name, clip)["queue"]
        entry = {
            "queue_join": queue["event_counts"]["queue_join"],
            "stitching_links": queue["stitching"]["stitches"],
            "tracker": clip_metrics(name, clip)["configuration"]["tracker"],
        }
        if clip.startswith("cad"):
            entry["labelled_peak_queue_count"] = labelled_peak(clip)
            entry["joins_per_labelled_peak_person"] = round(
                entry["queue_join"] / entry["labelled_peak_queue_count"], 3
            )
        joins[clip] = entry
    (out / "joins_links.json").write_text(json.dumps(joins, indent=2), encoding="utf-8")


def benchmark(name: str) -> None:
    cfg = CONFIGS[name]
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "scripts/benchmark.py", "--video", f"datasets/raw/{BENCH_CLIP}.mp4", "--camera-config",
        f"configs/cameras/{BENCH_CLIP}.yaml", "--device", "mps", "--model", f"ai_engine/models/{cfg['model']}",
        "--tracker", cfg["tracker"], "--reid", cfg["reid"], "--output", str(out / f"benchmark_{BENCH_CLIP}.json"),
    ]  # fmt: skip
    run(cmd, out / f"benchmark_{BENCH_CLIP}.log")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def mot_metrics(name: str) -> dict:
    keys = ("idf1", "mota", "num_switches", "precision", "recall")
    result = {}
    for clip in MOT:
        metrics = load(OUT / name / f"{clip}_tracking_eval.json")["metrics"]
        result[clip] = {k: metrics[k] for k in keys}
    return result


def decide() -> None:
    a_queue = load(OUT / "A" / "queue_640.json")["variants"]["as_configured"]
    rows: list[dict] = []
    summary: dict = {}
    for name, cfg in CONFIGS.items():
        out = OUT / name
        queue = load(out / "queue_640.json")["variants"]["as_configured"]
        bench = load(out / f"benchmark_{BENCH_CLIP}.json")
        fps = bench["summary"]["processing_fps"]
        rss = bench["summary"]["resources"]["peak_rss_mb"]
        clip_fps = bench["video"]["fps"]
        splits = {s: queue["pooled"][s]["smoothed_from5s"] for s in ("val", "test", "all_clips")}
        entry: dict = {
            "label": cfg["label"],
            "model": cfg["model"],
            "tracker": cfg["tracker"],
            "reid": cfg["reid"],
            "per_clip_smoothed_from5s": {c: m["smoothed_from5s"] for c, m in queue["per_clip"].items()},
            "per_split_smoothed_from5s": splits,
            "benchmark": {"processing_fps": fps, "clip_fps": clip_fps, "peak_rss_mb": rss, "source": str(out / f"benchmark_{BENCH_CLIP}.json")},
            "mot17": mot_metrics(name),
            "joins_links": load(out / "joins_links.json"),
            "attribution": load(out / "attribution.json"),
            "sources": {"queue": str(out / "queue_640.json"), "attribution": str(out / "attribution.json")},
        }  # fmt: skip
        if name == "A":
            entry["intervals"] = load(out / "intervals.json")["splits"]
            entry["decision"] = "reference (current default)"
        else:
            paired = load(out / "intervals_vs_A.json")["splits"]
            entry["intervals"] = load(out / "intervals.json")["splits"]
            test = paired["test"]
            crit = test["adoption_criterion_a"]
            dropped = {s: paired[s]["paired_clip_bootstrap"]["n_frames_dropped_unaligned"] for s in paired}
            if any(dropped.values()):
                raise SystemExit(f"{name}: unaligned frames dropped {dropped}; stop and report")
            half = crit["paired_half_width"]
            delta = crit["delta_mae_new_minus_current"]
            val_new, val_a = splits["val"]["mae"], a_queue["pooled"]["val"]["smoothed_from5s"]["mae"]
            a_ok = crit["improvement_beyond_half_width"]
            b_ok = val_new <= val_a
            c_ok = fps >= clip_fps
            entry["paired_vs_A"] = {
                s: {
                    "clip": paired[s]["paired_clip_bootstrap"],
                    "per_frame_optimistic": paired[s]["paired_per_frame_optimistic"],
                }
                for s in paired
            }
            entry["n_frames_dropped_unaligned"] = dropped
            entry["decision"] = {
                "adopt": bool(a_ok and b_ok and c_ok),
                "criterion_a": {"passes": a_ok, "delta_test_mae_new_minus_A": delta, "paired_half_width": half, "required": "-delta > half_width", "minus_delta": -delta, "test_split_only": True},
                "criterion_b": {"passes": b_ok, "val_mae_new": val_new, "val_mae_A": val_a},
                "criterion_c": {"passes": c_ok, "processing_fps": fps, "clip_fps": clip_fps},
                "action": "report only; adoption is a separate decision",
            }  # fmt: skip
            rows.append(
                {
                    "configuration": name,
                    "metric": "test smoothed queue-length MAE (from 5 s)",
                    "value": splits["test"]["mae"],
                    "interval_clip_bootstrap_95": [test["clip_bootstrap"]["mae"]["low"], test["clip_bootstrap"]["mae"]["high"]],
                    "paired_delta_vs_A": delta,
                    "decision": entry["decision"],
                    "source": [str(out / "intervals_vs_A.json"), str(out / "queue_640.json"), str(out / f"benchmark_{BENCH_CLIP}.json")],
                }
            )  # fmt: skip
        summary[name] = entry
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "protocol": PROTOCOL,
        "series": "smoothed queue length, annotated frames from 5 s, nearest sample within 0.6 s",
        "bootstrap": "clip-level, 10,000 resamples, seed 20261116, 95 % percentile (paired: same clips drawn for both)",
        "notes": [TEST_FILE_NOTE, "adoption_criterion_a is read from the test split only.", "joins_per_labelled_peak_person = queue_join events / peak labelled queue_count (the CAD labels carry no person ids, so this is a proxy)."],
        "rows": rows,
        "configurations": summary,
    }  # fmt: skip
    (OUT / "comparisons.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for row in rows:
        print(row["configuration"], json.dumps(row["decision"]))
    print(f"Wrote {OUT / 'comparisons.json'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("step", choices=["track", "analyze", "benchmark", "decide"])
    parser.add_argument("config", nargs="?", choices=sorted(CONFIGS))
    args = parser.parse_args()
    if args.step == "decide":
        decide()
    elif args.config is None:
        parser.error("config required")
    else:
        {"track": track, "analyze": analyze, "benchmark": benchmark}[args.step](args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
