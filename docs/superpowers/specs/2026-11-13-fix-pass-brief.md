# QueueVision fix pass (before the camera work) — brief, recorded as the spec

Recorded from the team's fix-pass prompt (13 Nov 2026). Sessions run in order; each ends with a PR into `main`; the team merges.

| Session | Branch | Fixes |
|---|---|---|
| 1. Code structure and small gaps | `fix/structure` | Oversized `create_app` and `process_video`; alert acknowledgement not saved; ordered DB migrations; `docs/demo.md` missing; single-zone limit undocumented |
| 2. Evidence: uncertainty and comparisons | `fix/evaluation` | No confidence intervals; error breakdown only at IoU 0.5; YOLO11s never benchmarked; only ByteTrack tried (no BoT-SORT / re-identification) |
| 3. Privacy and real-time performance | `fix/privacy-performance` | Missed people are not blurred; unexplained ~40% per-frame slowdown when pacing |
| 4. Faster labelling and waiting-time scoring | `fix/labelling` | Waiting-time sheets unfilled (labelling is slow); no predicted-vs-actual check |

Not in this prompt: live cameras, camera dashboard, login, own-footage intake (Sprint 8 sessions 2–5); history pages, notifications, multiple queues per camera, a browser zone editor.

Needs a person: filling the waiting-time sheets; recording own footage with written permission; the real Chrome / Safari / phone checklist; deciding repository visibility.

## SHARED RULES (every session)

1. **Inspect first.** Read README, docs (architecture, implementation, api, testing_report, sprint5/6/7/8 reports, security_review, privacy) and the code each session touches. Record baselines: `pytest -q`, `pytest -m "not integration"`, `ruff check .`, `ruff format --check .`; in `dashboard/`: lint, typecheck, test, build, e2e. (Sprint 8 session 1 final: 454 / 443+11 / clean / Vitest 125 / e2e 12.)
2. **Never fabricate results.** Every number comes from a command you ran or a named file; what cannot be run is pending with the reason.
3. **No silent behaviour changes.** CLI outputs of `run_tracking.py`, every `/api/*` route and field, `/ws/videos/{id}` messages, event schema 1.0 and queue-logic defaults stay as they are unless a session says otherwise. Do not tune thresholds. A default may change only through session 2's adoption rule.
4. Unit tests torch-free; real-model tests marked `integration`. Ruff clean. Python deps pinned; npm exact with the lockfile.
5. CAD has no stated licence: never commit or publish CAD frames, videos or screenshots. Heads blurred in everything the dashboard shows.
6. Do not change repository visibility. Do not push to `main`. Small commits; one PR per session.
7. Each session ends with a report: what changed, files, dependency changes, baselines vs final for every command, measurements with method, pending items, each task Completed / Partially completed / Not completed.

## SESSION 1 — CODE STRUCTURE AND SMALL GAPS (`fix/structure`)

### 1a. Split `create_app` (api/main.py)
- Move routes into FastAPI `APIRouter` modules by area (e.g. `api/routes/health.py`, `cameras.py`, `videos.py` (list, detail, events, waits, alerts, time series, report, video, CSV, cancel, delete), `preview.py`, `accuracy.py`, `websocket.py`, `static.py` (dashboard and `/legacy`)). Shared dependencies (settings, database, job manager, preview store, origin check, path containment) come from one `api/deps.py` via `Depends` or `app.state`, not module globals.
- `create_app` becomes assembly only: settings, lifespan, middleware, `include_router` calls.
- Behaviour identical: `scripts/export_openapi.py` → `dashboard/openapi.json` byte-identical before and after, apart from intended 1c changes; all API tests pass unchanged.

### 1b. Split `process_video` (ai_engine/run_tracking.py)
- Extract the per-frame body into a small class or functions (e.g. `FrameProcessor.process(frame) -> FrameResult`, plus separate output writers for video, metrics, MOT tracks and queue CSVs), leaving `process_video` as setup, loop and teardown.
- This is the seam Sprint 8's camera worker will reuse: the per-frame step must not depend on a file source, a known frame count or a video writer.
- Behaviour identical: a golden test runs a short synthetic or saved-track replay through the old and new code paths and compares every output file byte for byte (videos by frame hash). Existing pipeline and CLI tests unchanged.

### 1c. Ordered database migrations and saved alert acknowledgement
- Replace the single `DB_SCHEMA_VERSION` check in `api/db.py` with an ordered list of migrations (`1 → 2`, later `2 → 3` …), each a function run in a transaction, with a backup of the database file before the first pending migration. Document how to add one in `docs/architecture.md`.
- Migration `1 → 2`: add `acknowledged_at` (UTC ISO 8601) and `acknowledged_by` (nullable text; NULL until login exists) to `alerts`.
- `POST /api/videos/{id}/alerts/{alert_id}/ack` sets them (idempotent; 404 for unknown ids). `GET .../alerts` returns them. The dashboard's Acknowledge button calls it, and the acknowledged state survives a reload; remove the "only in this browser" note.
- Tests: migration from a real version-1 database made by the current code (all existing tests still pass after it), idempotency, the route, and Vitest/Playwright for the button.

### 1d. Documentation gaps
- `docs/demo.md`: a 5-minute demo script using only features verified in tests or the testing report (start with `scripts/demo.sh`; upload a clip with real time on; Live page; Recorded run; Accuracy). Each step names what the audience should see. Mark anything not yet verified by a person in a real browser.
- README and `docs/architecture.md`: a camera config has one queue zone and one service point (two counters need two configs); zones are drawn with `ai_engine/tools/draw_zone.py`.
- Document the reserved `_observed` clip-id suffix from `scripts/export_accuracy.py` in `datasets/README.md`.

## SESSION 2 — EVIDENCE: UNCERTAINTY AND COMPARISONS (`fix/evaluation`)

Current figures to reproduce first (Sprint 5/6 reports): queue length, held-out (test) split, Sprint 4 rules: MAE 1.619, bias +0.960, within ±1 0.537 (285 frames, 4 clips); validation MAE 1.176. MOT17-04 / MOT17-09 IDF1 0.412 / 0.508 (ByteTrack, no stitching). Reproduce them from the existing scripts before adding anything; if they do not reproduce exactly, stop and report why.

### 2a. Write the adoption rule first
Before running any comparison, add `docs/evaluation_protocol.md` and commit it on its own. It must say:
- Which clips are tuning (val: seq10, 25, 26) and held-out (test: seq27, 39, 40, 41), as in `datasets/splits/`.
- A new detector or tracker becomes the default **only if** (a) its test-split queue-length MAE is lower than the current default's by more than the half-width of the paired clip-bootstrap 95% interval of the difference, (b) its validation MAE is not worse, and (c) end-to-end processing on MPS at the default stride stays at or above 1.0× real time on cad_seq27. Otherwise it is reported and not adopted.
- Nothing is tuned on test clips. Thresholds stay at the Sprint 4 defaults.

### 2b. Confidence intervals
- Add `ai_engine/evaluation/bootstrap.py`: a clip-level (cluster) bootstrap (resample clips with replacement, 10,000 resamples, fixed seed) for MAE, bias and within-±1, and a paired version for differences between two configurations. Frames within a clip are correlated, so also report the per-frame bootstrap but label it "optimistic (ignores within-clip correlation)".
- Apply it to the queue-length results for val, test and all clips. With 3–4 clips per split the intervals will be wide; report them as they are.
- Tests with small synthetic data (known answers, seed stability).

### 2c. Error breakdown at two match thresholds
- Queue-length MAE compares counts and does not use IoU; the error attribution does. Re-run the Sprint 6 attribution at IoU 0.5 and 0.3 and report both side by side. Do not change the default threshold of any metric.

### 2d. Detector comparison: YOLO11n vs YOLO11s
- Run all 7 CAD queue clips with `--model yolo11s.pt` (downloaded to `ai_engine/models/`, never committed), same camera configs, imgsz 640, MPS. Record queue-length MAE / bias / within-±1 per clip and per split with bootstrap intervals, the Sprint 6 error attribution, peak RSS, and end-to-end fps from `scripts/benchmark.py` on cad_seq27.
- Also run MOT17-09 and MOT17-04 tracking evaluation (IDF1, MOTA, ID switches) with each detector.
- Apply the adoption rule; report the decision.

### 2e. Tracker comparison: ByteTrack vs BoT-SORT
- Check in the installed Ultralytics source how BoT-SORT and its re-identification option (`with_reid`, `model`) work in the pinned version, and what global motion compensation (`gmc_method`) costs on the M1.
- Generate a resolved BoT-SORT YAML the same way `PersonTracker` resolves ByteTrack's (same `new_track_thresh`, `track_buffer` from seconds, input confidence 0.1), so only the algorithm differs. Make the tracker a config/CLI option (`--tracker bytetrack|botsort`, `--reid on|off`), default unchanged.
- Compare ByteTrack, BoT-SORT without re-identification, and BoT-SORT with re-identification on: MOT17-09/04 (IDF1, MOTA, ID switches); CAD queue-length MAE with intervals; the number of queue joins per labelled person and stitching links; fps and peak RSS on MPS.
- Apply the adoption rule; report the decision.

### 2f. Show it
- Extend `scripts/export_accuracy.py` and `/api/accuracy` with the intervals, the two-threshold attribution, and a `comparisons` section (configuration, metric, value, interval, decision), each with its source file. The Accuracy page shows intervals beside the headline numbers ("1.62 people, 95% interval a–b, 4 clips") and a comparison table. Vitest for the new parts.
- Results and the decisions go in `docs/testing_report.md` with method, sample sizes and limits.

## SESSIONS 3–4

Session 3 (`frame` privacy mode; pacing slowdown experiments) and session 4 (labelling page, scoring, predicted-vs-actual) are recorded in full from the team's prompt when each starts.

## AFTER THIS PROMPT
Continue with Sprint 8 session 2 (live cameras), told about: the router layout (1a), the reusable per-frame processor (1b), the migration list (1c), the `frame` privacy mode (3a), and the pacing findings (3b).
