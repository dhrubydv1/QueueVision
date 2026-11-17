# Fix pass, session 2: evidence, uncertainty and comparisons (`fix/evaluation`)

Branch `fix/evaluation` from `main` at `3099a83` (#9 and #10 merged). Not pushed; the team merges. Plan:
[superpowers/plans/2026-11-16-fix-evaluation.md](superpowers/plans/2026-11-16-fix-evaluation.md); brief recorded in
[superpowers/specs/2026-11-13-fix-pass-brief.md](superpowers/specs/2026-11-13-fix-pass-brief.md). Results, method,
sample sizes and limits are in [testing_report.md](testing_report.md) (section "Fix pass, session 2"); the
adoption rule is [evaluation_protocol.md](evaluation_protocol.md) version 1. **The evaluation protocol was written
and committed (`84a0b43`) by the controller before any comparison was run.**

## Outcome in short

No candidate is adopted; the default stays YOLO11n + ByteTrack (ByteTrack tracker, MPS, imgsz 640). Held-out
queue-length MAE is 1.62 people, 95 % clip-bootstrap interval 0.97 to 2.03, 4 clips, 285 frames. B (YOLO11s +
ByteTrack), C (YOLO11n + BoT-SORT) and D (YOLO11n + BoT-SORT with ReID) all fail criterion (a) and criterion (c)
of the protocol; (b) passes for all three.

## What changed, by task

| Task | Brief item | Commits | Status |
|---|---|---|---|
| 1 | Baselines and exact reproduction of the Sprint 5/6 figures | none (outputs in `outputs/s9_eval/repro/`) | Completed |
| 2 | 2a: adoption rule written first | `84a0b43` (controller, before any comparison) | Completed |
| 3 | 2b, 2c: clip bootstrap, paired version, attribution at IoU 0.5 and 0.3 | `d38d292` | Completed |
| 4 | 2e: `--tracker bytetrack\|botsort`, `--reid on\|off` | `d32a645`, `ec16995` (review fix: ReID guard, default output unchanged) | Completed |
| 5 | 2d, 2e: detector and tracker comparisons, decisions | `64eb24a`, `30b10a5` (review fix: candidate intervals), `0adc27b` | Completed |
| 6 | 2f: export, API and Accuracy page | `8c2ee11` | Completed |
| 7 | Documentation and this report | the commit that adds this file | Completed; real-browser check pending (see Pending) |

Also committed before the tasks: `55d74c7` (session 2 brief and plan).

**Task 1.** Everything reproduced exactly from the existing scripts: test MAE 1.619, bias +0.960, within +-1 0.537
(285 frames); val MAE 1.176 (122 frames); MOT17-04 IDF1 0.412 and MOT17-09 IDF1 0.508. The re-run
`queue_640.json` equals the Sprint 6 file apart from `generated_at`. `replay_queue` needs `--splits-dir` for the
val and test pooled blocks.

**Task 2.** `docs/evaluation_protocol.md` v1: splits, no tuning on test, bootstrap definition, rule (a)(b)(c),
current default and the candidates B, C and D. Committed alone, before any detector or tracker was run.

**Task 3.** `ai_engine/evaluation/bootstrap.py` (clip-level cluster bootstrap, 10,000 resamples, seed 20261116,
95 % percentile; paired version using the same drawn clips; per-frame bootstrap labelled optimistic), reusing the
day-bootstrap machinery without changing `day_bootstrap.py`. `replay_queue.py --frame-errors-output FILE` writes
per-frame errors to a side file; its `--output` is unchanged. New `scripts/evaluate_queue_intervals.py` and
`scripts/summarize_attribution.py` (IoU 0.5 and 0.3 side by side). `tests/test_bootstrap.py` has 11 torch-free
synthetic tests.

**Task 4.** `PersonTracker` and `TrackerSettings` take a tracker type and a ReID flag; the BoT-SORT YAML is
resolved exactly as ByteTrack's (same `new_track_thresh`, `track_buffer`, input confidence); shared keys are equal
and only the algorithm differs (tested). CLI flags `--tracker` and `--reid` on `run_tracking.py` and
`scripts/benchmark.py` (plus `--gmc-method` on the benchmark only). Defaults unchanged: ByteTrack, ReID off; the
default `describe()` dict equals its pre-change value, so the metrics JSON tracker block is unchanged. Review fix:
ReID requires a PyTorch `.pt` detector (see Notes for later sessions).

**Task 5.** `scripts/run_comparisons.py` runs track, benchmark, analyze and decide for A (the existing Sprint 5
outputs), B, C and D on the 7 CAD clips and MOT17-04/09, and writes `outputs/s9_eval/comparisons.json`.
`yolo11s.pt` was downloaded to `ai_engine/models/` (gitignored, 19.3 MB, not committed). Review found the first
pass copied A's interval into the candidate rows; fixed and re-run (decisions unaffected, they use the paired
files). `tests/test_run_comparisons.py` guards it.

**Task 6.** `scripts/export_accuracy.py` and `/api/accuracy` gain, additively: per-split clip-bootstrap and
per-frame (labelled optimistic) intervals with clip and frame counts, `attribution_iou` (IoU 0.5 and 0.3) and a
`comparisons` section (configuration, metric, value, interval, decision, sources). The Accuracy page shows
"1.62 people, 95% interval 0.97-2.03, 4 clips" and a comparison table, only from the JSON. Review confirmed every
old export key is unchanged. A second table of the IoU 0.5 attribution on the Accuracy page duplicates the
existing error table (accepted; removable).

## Files

Added: `docs/evaluation_protocol.md`, `docs/fix_evaluation_report.md`,
`docs/superpowers/plans/2026-11-16-fix-evaluation.md`, `ai_engine/evaluation/bootstrap.py`,
`scripts/evaluate_queue_intervals.py`, `scripts/summarize_attribution.py`, `scripts/run_comparisons.py`,
`tests/test_bootstrap.py`, `tests/test_run_comparisons.py`.
Changed: `ai_engine/tracking/tracker.py`, `ai_engine/run_tracking.py`, `ai_engine/replay_queue.py`,
`scripts/benchmark.py`, `scripts/export_accuracy.py`, `tests/test_tracker.py`, `tests/test_export_accuracy.py`,
`dashboard/components/AccuracyView.tsx`, `dashboard/components/AccuracyView.test.tsx`,
`dashboard/lib/accuracy-types.ts`, `docs/testing_report.md`, the session brief
(`docs/superpowers/specs/2026-11-13-fix-pass-brief.md`).
Not committed, by design: `outputs/s9_eval/` (gitignored), `ai_engine/models/yolo11s.pt`, any CAD frames.

## Dependency changes

None. `git diff 3099a83..HEAD --stat` touches no requirements file, `pyproject.toml`, `package.json` or lockfile
(the Ultralytics 8.4.198 BoT-SORT and its ReID path are already installed; `yolo11s.pt` is a model download, not a
dependency).

## Baselines vs final

Baselines from Task 1 (`.superpowers/sdd/2026-11-16-fix-evaluation/task-1-report.md`, on `3099a83`); final
from the commands re-run on the final tree; raw outputs saved in `outputs/s9_eval/final_commands/` (local, gitignored: `pytest.txt`, `pytest_not_integration.txt`, `ruff_*.txt`, `dashboard_*.txt`, `e2e.txt`).

| Command | Baseline | Final |
|---|---|---|
| `.venv/bin/python -m pytest -q` | 514 passed | 544 passed |
| `.venv/bin/python -m pytest -q -m "not integration"` | 503 passed, 11 deselected | 533 passed, 11 deselected |
| `.venv/bin/ruff check .` | All checks passed | All checks passed |
| `.venv/bin/ruff format --check .` | 155 files already formatted | 162 files already formatted (161 in the first run at the end of the session; the count differs by one, cause not investigated, ruff check and format --check both pass) |
| dashboard `npm run lint` | clean | clean |
| dashboard `npm run typecheck` | clean | clean |
| dashboard `npm test` (Vitest) | 135 passed | 140 passed (18 files) |
| dashboard `npm run build` | success | success |
| dashboard `npm run e2e` (chromium + webkit) | 14 passed | 14 passed (4.1 m) |

Net change in tests: Python 514 to 544, Vitest 135 to 140 (not a per-file count). The pytest, ruff, lint, typecheck, Vitest and build rows were re-run after the final whole-branch review fixes (saved in `outputs/s9_eval/final_commands/`); the e2e row is from before them and was not re-run (the review fixes change one table cell in `AccuracyView` and no e2e-covered flow). The e2e suite uses port 8765 only (`dashboard/e2e/server.ts`), never 8000, 8001 or 8770.

## Measurements

All with the method in [testing_report.md](testing_report.md): clip bootstrap 10,000 resamples, seed 20261116,
percentile 95 %; paired for differences; per-frame bootstrap optimistic. Sources in `outputs/s9_eval/`.

| Measurement | Value | Source |
|---|---|---|
| Test MAE / bias / within +-1 (4 clips, 285 frames) | 1.619 [0.968, 2.026] / +0.960 [-0.426, 1.372] / 0.537 [0.432, 0.753] | `intervals_default.json` |
| Val MAE (3 clips, 122 frames) | 1.176 [0.989, 1.305] | `intervals_default.json` |
| All clips MAE (7 clips, 407 frames) | 1.486 [1.034, 1.782] | `intervals_default.json` |
| Test MAE, per-frame (optimistic) | 1.619 [1.477, 1.768] | `intervals_default.json` |
| Test attribution fn_no_detection, IoU 0.5 / 0.3 | 1.82 / 0.25 per frame | `attribution_default.json` |
| B paired delta MAE on test | -0.223 [-0.403, 0.216], half-width 0.309 | `B/intervals_vs_A.json` |
| C paired delta MAE on test | +0.775 [-0.043, 1.172], half-width 0.607 | `C/intervals_vs_A.json` |
| D paired delta MAE on test | +0.868 [0.048, 1.315], half-width 0.634 | `D/intervals_vs_A.json` |
| Processing fps on cad_seq27 (A / B / C / D) | 31.94 / 20.53 / 25.22 / 17.96 | `{A,B,C,D}/benchmark` via `comparisons.json` |
| Peak RSS MB (A / B / C / D) | 400.6 / 519.1 / 502.5 / 512.7 | `comparisons.json` |
| MOT17-04 IDF1 (A / B / C / D) | 0.4119 / 0.4078 / 0.4376 / 0.4424 | `comparisons.json` |
| MOT17-09 IDF1 (A / B / C / D) | 0.5082 / 0.5745 / 0.4993 / 0.5329 | `comparisons.json` |

## Rulings made during the session

- Task 4 review: `describe()` gains the `reid` key only for BoT-SORT, so the default CLI output and metrics JSON
  stay byte-identical (cost: none).
- Task 6: the extra IoU 0.5 attribution table on the Accuracy page is accepted; showing it costs little and it is
  removable if the team objects.
- The evaluation protocol was written and committed (`84a0b43`) by the controller before any comparison was run.
- Test criterion (a) reads the test split only (4 CAD clips). `mot17_04/09` appear in `datasets/splits/test.txt` but
  are excluded from queue metrics.
- The benchmark is run once per configuration, as the rule names `scripts/benchmark.py`; no re-run was requested.

## Notes for later sessions

- **GMC cost.** BoT-SORT's bundled global motion compensation (`gmc_method: sparseOptFlow`) costs about 5.4
  ms/frame, about 6 fps (derived: mean of two runs each in `outputs/s9_eval/gmc/final/`; sparseOptFlow `run2`, `run6`
  tracking 9.56 and 9.31 ms, mean 9.44, 25.55 and 26.61 fps, mean 26.1; `gmc_method` none `run3`, `run7` tracking 4.03
  and 4.14 ms, mean 4.08, 32.61 and 31.76 fps, mean 32.2; differences 5.35 ms and 6.1 fps). ByteTrack (`run1`, `run5`)
  is 31.86 and 32.73 fps, so the two-run spread is 0.87 fps (BoT-SORT 1.06); n = 2, indicative only. ReID adds about 20
  ms more (derived: tracking 29.91 ms in the single ReID run `run4` against the 9.44 ms sparseOptFlow mean, difference 20.5 ms; n = 1). A later session that wants BoT-SORT in real time should start with GMC off (exposed only through
  `scripts/benchmark.py --gmc-method`; not wired to `run_tracking`; speed only, **not evaluated for accuracy**).
- **ReID CoreML guard.** Ultralytics takes ReID features from the detector only for a PyTorch `.pt` model with a
  non-end2end Detect head. For a CoreML `.mlpackage` (or end2end) detector it silently switches to
  `yolo26n-cls.pt` (an extra download and a separate classifier). `--reid on` is therefore rejected with
  `parser.error` (CLI, benchmark) and `ValueError` (`PersonTracker`) for non-`.pt` models or device `coreml`. The
  64-dim feature is a slice of the Detect head input, not a purpose-trained ReID embedding.
- **Margins.** The default passes real time (criterion c) at 31.94 fps against 30.0 on one benchmark run; treat
  any new feature on the per-frame path as able to flip it. The benchmark excludes drawing and video writing.
- **Wide intervals.** With 3 and 4 clips only large differences are resolvable; adding labelled clips is the
  cheapest way to sharpen any later decision.
- `scripts/run_comparisons.py analyze A` must run before the other analyses (they are paired against A's frame
  errors); candidates' joins/links come from current code, A's from Sprint 5 (older code).

## Pending

- Real-browser check of the new Accuracy page parts (interval beside the headline, comparison table) by a
  person, against the real `/api/accuracy`. Vitest (140) and the production build cover the rendering from JSON;
  e2e and unit tests do not look at it in a person's browser. Not run by this session.
- Five cosmetic review minors on the Accuracy page left as is (unconditional "intervals are wide" caption, missing
  marker when a paired interval is absent, vague fallback wording, fixture default change, duplicated IoU 0.5
  table). None affects a number.
- Nothing pushed, no PR opened; the team merges.
