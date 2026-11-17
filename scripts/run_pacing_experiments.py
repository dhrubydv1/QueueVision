"""Run the Sprint 10 pacing experiments (fast reference + variants a-d) and apply the adoption rule.

Each run is its own process (so caffeinate can wrap one whole process), one at a time, with an idle
pause between runs. Order rotates each round. Results: outputs/s10_pacing/ (run_*.json, order.json,
decision.json). Nothing here changes product code.

    .venv/bin/python scripts/run_pacing_experiments.py --video datasets/raw/cad_seq25.mp4 --camera cad_seq25
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ("fast", "a", "b", "c", "d")
DESCRIPTION = {
    "fast": "unpaced reference",
    "a": "paced as now (baseline)",
    "b": "baseline under caffeinate -i",
    "c": "warmup: dummy MPS forward before each paced frame",
    "d": "spin-tail: sleep to 2 ms before deadline, then spin",
}
ROUNDS = (("fast", "a", "b", "c", "d"), ("b", "c", "d", "fast", "a"), ("d", "fast", "a", "b", "c"))
METRICS = ("wall_s", "waited_s", "non_wait_s", "update_median_ms", "cpu_s")
EXTRA_FILES = ["scripts/measure_realtime.py", "scripts/run_pacing_experiments.py", "api/jobs.py"]


def command_for(config: str, video: Path, camera: str, out_dir: Path, tag: str) -> list[str]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "measure_realtime.py"),
        "--video", str(video),
        "--camera", camera,
        "--stages",
        "--strides", "3",
        "--repeats", "1",
        "--stages-dir", str(out_dir),
        "--stages-file", f"run_{tag}.json",
    ]  # fmt: skip
    if config == "fast":
        cmd += ["--variant", "baseline", "--unpaced"]
    else:
        cmd += ["--variant", {"a": "baseline", "b": "baseline", "c": "warmup", "d": "spin-tail"}[config]]
    if config == "b":
        cmd = ["caffeinate", "-i", *cmd]
    return cmd


def spread(values: list[float]) -> dict:
    return {"min": min(values), "median": statistics.median(values), "max": max(values), "all": values}


def summarise(runs_by_config: dict[str, list[dict]]) -> dict:
    return {c: {m: spread([r[m] for r in rs]) for m in METRICS} for c, rs in runs_by_config.items() if rs}


def decide(summary: dict) -> dict:
    """Apply the pre-registered rule: close >= 2/3 of the gap and CPU <= 110 % of (a); lowest CPU wins."""
    fast = summary["fast"]["update_median_ms"]["median"]
    a_update = summary["a"]["update_median_ms"]["median"]
    a_cpu = summary["a"]["cpu_s"]["median"]
    gap = a_update - fast
    threshold = fast + gap / 3
    cpu_limit = 1.10 * a_cpu
    criteria = {}
    passing = []
    for c in ("b", "c", "d"):
        update = summary[c]["update_median_ms"]["median"]
        cpu = summary[c]["cpu_s"]["median"]
        closed = gap > 0 and update <= threshold
        cpu_ok = cpu <= cpu_limit
        criteria[c] = {
            "update_median_ms": update,
            "threshold_ms": threshold,
            "fraction_of_gap_closed": (a_update - update) / gap if gap > 0 else None,
            "criterion_1_closes_two_thirds_of_gap": closed,
            "cpu_median_s": cpu,
            "cpu_limit_s": cpu_limit,
            "cpu_ratio_to_a": cpu / a_cpu,
            "criterion_2_cpu_within_10pct_of_a": cpu_ok,
            "adopted_candidate": closed and cpu_ok,
        }
        if closed and cpu_ok:
            passing.append(c)
    winner = min(passing, key=lambda c: criteria[c]["cpu_median_s"]) if passing else None
    return {
        "gap_ms": gap,
        "fast_update_median_ms": fast,
        "a_update_median_ms": a_update,
        "a_cpu_median_s": a_cpu,
        "criteria": criteria,
        "passing": passing,
        "decision": winner,
        "decision_text": (
            "nothing adopted; findings documented"
            if winner is None
            else (
                "document/offer caffeinate for live runs (OS wrapper, no silent product change)"
                if winner == "b"
                else f"adopt variant {winner} ({DESCRIPTION[winner]}) as a product change"
            )
        ),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", type=Path, required=True)
    p.add_argument("--camera", required=True)
    p.add_argument("--out-dir", type=Path, default=ROOT / "outputs" / "s10_pacing")
    p.add_argument("--pause", type=float, default=20.0, help="idle seconds between runs")
    args = p.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    order: list[dict] = []
    runs: dict[str, list[dict]] = {c: [] for c in CONFIGS}
    first = True
    for rnd, configs in enumerate(ROUNDS, 1):
        for config in configs:
            if not first:
                time.sleep(args.pause)
            first = False
            tag = f"r{rnd}_{config}"
            cmd = command_for(config, args.video, args.camera, args.out_dir, tag)
            began = time.strftime("%Y-%m-%dT%H:%M:%S")
            print(f"[{tag}] {' '.join(cmd)}", flush=True)
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            (args.out_dir / f"run_{tag}.log").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
            if proc.returncode != 0:
                raise SystemExit(f"{tag} failed ({proc.returncode}); see run_{tag}.log")
            rec = json.loads((args.out_dir / f"run_{tag}.json").read_text())["runs"][0]
            rec["round"] = rnd
            runs[config].append(rec)
            order.append({"tag": tag, "config": config, "round": rnd, "started": began, "command": cmd})
            print(f"[{tag}] {json.dumps({m: rec[m] for m in METRICS})}", flush=True)
    (args.out_dir / "order.json").write_text(json.dumps(order, indent=2) + "\n")
    summary = summarise(runs)
    result = {
        "rule": "gap = median update (a) - median update (fast); adopt b/c/d only if "
        "update median <= fast + gap/3 and CPU median <= 1.10 x CPU median (a); "
        "lowest CPU among passing wins",
        "configs": DESCRIPTION,
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip(),
        "order": [o["tag"] for o in order],
        "pause_between_runs_s": args.pause,
        "per_config": summary,
        **decide(summary),
        "source_files": [f"run_r{r}_{c}.json" for r in (1, 2, 3) for c in CONFIGS],
        "code_files": EXTRA_FILES,
    }
    (args.out_dir / "decision.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("gap_ms", "passing", "decision", "decision_text")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
