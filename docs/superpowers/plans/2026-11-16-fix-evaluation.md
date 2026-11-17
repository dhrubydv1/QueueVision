# Fix pass session 2: evidence — uncertainty and comparisons — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reproduce the current queue-length and MOT17 figures, pre-register an adoption rule, add clip-level bootstrap intervals, report attribution at IoU 0.5 and 0.3, compare YOLO11n vs YOLO11s and ByteTrack vs BoT-SORT (± ReID) under that rule, and show it all on the Accuracy page.

**Spec:** `docs/superpowers/specs/2026-11-13-fix-pass-brief.md` (SHARED RULES, SESSION 2).

## Global Constraints

- Branch `fix/evaluation` from `main` `3099a83` (#9 and #10 merged). Baselines to record in Task 1.
- Never fabricate. If a current figure does not reproduce exactly, STOP and report why.
- No tuning; thresholds stay at Sprint 4 defaults; nothing chosen on test clips. A default changes only through `docs/evaluation_protocol.md`'s rule, committed (by the controller) before any comparison is run.
- Existing pipeline (Sprint 5): `ai_engine/run_tracking.py --save-mot` once per clip (MPS, imgsz 640) on the 7 CAD clips (`datasets/raw/cad_seqNN.mp4`, configs `configs/cameras/cad_seqNN.yaml`) and MOT17-04/09; `ai_engine/replay_queue.py` replays saved tracks; `ai_engine/analyze_queue_errors.py` attributes errors (IoU 0.5 and 0.3 already supported); `ai_engine/evaluate_tracking.py` scores MOT17. Existing outputs: `outputs/s5_tracks/`, `outputs/s6_analysis/{queue_640,errors_640}.json`.
- Scoring protocol for queue length: annotated frames from 5 s, nearest per-second sample within 0.6 s; splits in `datasets/splits/` (val seq10/25/26, test seq27/39/40/41).
- `ai_engine/evaluation/day_bootstrap.py` exists (from the Anonymous Bank work); the new `ai_engine/evaluation/bootstrap.py` is the clip-level version — share code where natural, do not break the day bootstrap or its tests.
- Ultralytics 8.4.198: BoT-SORT with `with_reid: True, model: auto` takes ReID features from the detector (no extra download). `yolo11s.pt` is downloaded to `ai_engine/models/` (gitignored? check `.gitignore`; never commit weights).
- One heavy (model) job at a time on this 8 GB M1. Outputs under `outputs/s9_eval/` (gitignored). No CAD frames committed. Don't touch ports 8000/8001/8770.
- Unit tests torch-free; ruff clean; deps pinned; commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; don't push.

## Review Focus

1. Reproduction is exact (same files, same numbers to the stated decimals) before anything new is added.
2. Paired clip bootstrap resamples the same clips for both configurations; the per-frame bootstrap is labelled optimistic.
3. The BoT-SORT YAML differs from the resolved ByteTrack one only in algorithm keys (same new_track_thresh, track_buffer, input confidence); default tracker unchanged; CLI outputs unchanged by default.
4. Adoption decisions follow the committed rule exactly (test MAE improvement beyond the paired half-width, val not worse, ≥1.0× real time on cad_seq27).
5. Export/API additions don't break existing accuracy keys; the dashboard shows intervals and comparisons only from the JSON.

---

### Task 1: Baselines and reproduction
Record the shared-rule baselines (pytest, ruff, dashboard lint/typecheck/test/build; e2e). Reproduce: queue-length test MAE 1.619, bias +0.960, within ±1 0.537 (285 frames, 4 clips), val MAE 1.176 (re-run `replay_queue.py` on `outputs/s5_tracks` with shipped configs and the scoring above; compare with `outputs/s6_analysis/queue_640.json`); MOT17-04/09 IDF1 0.412/0.508 with ByteTrack, no stitching (re-score the saved MOT files with `evaluate_tracking.py`). Write the commands and outputs into the report; save under `outputs/s9_eval/repro/`. If any figure differs, stop (NEEDS_CONTEXT) with the diff.

### Task 2 (controller): `docs/evaluation_protocol.md`, committed alone

### Task 3: Clip-level bootstrap and attribution at two thresholds
`ai_engine/evaluation/bootstrap.py` (+ tests): clip bootstrap (10,000, fixed seed 20261116, percentile 95 %) for MAE, bias, within-±1 pooled over frames of resampled clips; paired version for differences; per-frame bootstrap labelled optimistic. `scripts/evaluate_queue_intervals.py` (or an option on an existing script) applies it to the reproduced current-default results for val, test, all → `outputs/s9_eval/intervals_default.json`. Re-run/collect the Sprint 6 attribution at IoU 0.5 and 0.3 side by side → `outputs/s9_eval/attribution_default.json`.

### Task 4: Tracker option (BoT-SORT ± ReID)
`ai_engine/tracking/tracker.py` + `ai_engine/config.py` + `run_tracking.py` CLI (`--tracker bytetrack|botsort`, `--reid on|off`; `--reid` only valid with botsort) and `benchmark.py` if it builds trackers. Resolve BoT-SORT from Ultralytics' `botsort.yaml` with the same overrides PersonTracker applies to ByteTrack (`new_track_thresh`, `track_buffer` from seconds, input confidence), `gmc_method` left at the bundled default (report it), `with_reid` from the flag, `model: auto`. Tests (torch-free): resolved YAML keys equal ByteTrack's resolved values for shared keys; default unchanged; describe() reports the tracker and reid. Measure GMC cost on the M1 (sparseOptFlow vs none) on cad_seq27 for the report (explore only; default stays as bundled).

### Task 5: Comparison runs and adoption decisions
Download `yolo11s.pt`. Configurations: A = YOLO11n + ByteTrack (current default, existing tracks), B = YOLO11s + ByteTrack, C = YOLO11n + BoT-SORT, D = YOLO11n + BoT-SORT + ReID. For B, C, D: `run_tracking.py --save-mot` on the 7 CAD clips and MOT17-04/09 (MPS, imgsz 640, default stride); replay queue length with shipped configs; per clip and per split MAE/bias/within-±1 with clip-bootstrap intervals and paired differences vs A; attribution at IoU 0.5/0.3; MOT17 IDF1/MOTA/ID switches; queue joins per labelled person and stitching links; `scripts/benchmark.py` fps and peak RSS on cad_seq27. Apply the committed rule to each; write `outputs/s9_eval/comparisons.json` (configuration, metric, value, interval, decision, source).

### Task 6: Export, API, Accuracy page
`scripts/export_accuracy.py`: intervals beside the headline, the two-threshold attribution, a `comparisons` section, each with its source file; missing files → null + missing_sources. `dashboard/lib/accuracy-types.ts`, `components/AccuracyView.tsx`: "1.62 people, 95% interval a–b, 4 clips" style headline and a comparison table; Vitest.

### Task 7: Docs and session report
`docs/testing_report.md` (method, sample sizes, limits, decisions), `docs/fix_evaluation_report.md` per shared rule 7.
