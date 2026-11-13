# Fix pass, session 1: code structure and small gaps (`fix/structure`)

Branch `fix/structure` from `main` at `b5bb0f9`. Not pushed; the team merges. Plan:
[superpowers/plans/2026-11-13-fix-structure.md](superpowers/plans/2026-11-13-fix-structure.md); brief recorded in
[superpowers/specs/2026-11-13-fix-pass-brief.md](superpowers/specs/2026-11-13-fix-pass-brief.md).

## What changed, by task

| Task | Brief item | Commits | Status |
|---|---|---|---|
| 1 | 1a: split `create_app` into routers | `68001b8` | Completed |
| 2 | 1b: split `process_video`, reusable per-frame step | `29d1d74` | Completed |
| 3 | 1c: ordered DB migrations, acknowledge API | `3742b3e`, `f8771e8` (cross-process race fix) | Completed |
| 4 | 1c: Acknowledge button saves the alert | `20ef938` | Completed (see notes) |
| 5 | 1d: documentation gaps, this report | the commit that adds this file | Completed; real-browser check of `docs/demo.md` is pending (needs a person) |

**Task 1 (1a).** `api/main.py` is now assembly only (85 lines). `api/deps.py` holds the shared
dependencies read from `app.state`; `api/routes/` has `health`, `cameras`, `accuracy`, `videos`, `preview`,
`websocket` and `static`. The flattened route order is identical to before (compared route by route,
including route type and methods), and `scripts/export_openapi.py` gave a byte-identical
`dashboard/openapi.json` before the 1c additions. The videos routes live in three routers only to keep the
OpenAPI path order. Layout and include order: [architecture.md](architecture.md).

**Task 2 (1b).** New `ai_engine/frame_processor.py` (`FrameProcessor.process / draw / record`), `RunWriters` in
`run_tracking.py`, and `iter_source_frames` / `track_frame` in `pipeline.py`. `process_video`, its CLI, flags
and outputs are unchanged. `tests/test_frame_processor_golden.py` runs the old code (copied verbatim from
`68001b8` into the test) and the new code on a synthetic clip with a camera config (queue CSVs, events, waits,
alerts, MOT; the stitch-diagnostics file is empty in this scenario, so it is compared but not exercised) and on a plain stride-2 run, and compares every file byte for byte (video by
decoded frame hash). Five temporary mutations each made it fail.

**Task 3 (1c, backend).** `MIGRATIONS` in `api/db.py` (list in [architecture.md](architecture.md)), backup
`<db>.bak-v<from>-<UTC timestamp>` before the first pending migration of an existing database, each migration in
its own `BEGIN IMMEDIATE` transaction. Migration 1 to 2 adds `alerts.acknowledged_at` and `acknowledged_by`.
`POST /api/videos/{id}/alerts/{alert_id}/ack` is idempotent and returns 404 for unknown ids; `GET .../alerts`
returns the fields. Verified by hand on a database made by the real `b5bb0f9` code: it migrated to version 2
with the row kept and one backup, and a second start did nothing. Review found a cross-process race
(two processes starting at once); the fix round took the write lock before re-reading the version and made the
backup after the lock, with a 30 s busy timeout. Two tests cover it.

**Task 4 (1c, dashboard).** The Live page Acknowledge button calls the new route for the latest open
`congestion_start` of that run; reload keeps it hidden; the "only in this browser" note is replaced by
"Acknowledging records the time for this alert."; the recorded run's Alerts tab shows "Acknowledged HH:MM:SS
NPT". Notes: tests and implementation were written in one pass (no separate red step; green was confirmed);
`acknowledged_at` is written as `...+00:00`, not `Z`; there is no unit test for the 2 s alert polling
lifecycle; the real Playwright test uses `cad_seq27` because `cad_seq25` produces no congestion alert.

**Task 5 (1d).** `docs/demo.md` (new); the one-queue-zone-per-camera limit and the `draw_zone.py` command in
README section 10 and in [architecture.md](architecture.md); router layout, `FrameProcessor` seam and migration list in
architecture; the `_observed` clip-id suffix in `datasets/README.md`; `docs/api.md` now says `schema_version`
is 2; a results section in `docs/testing_report.md`.

## Files changed (excluding `.superpowers/`)

Python: `ai_engine/frame_processor.py` (new), `ai_engine/pipeline.py`, `ai_engine/run_tracking.py`,
`api/db.py`, `api/deps.py` (new), `api/main.py`, `api/models.py`, `api/routes/*.py` (new).
Tests: `tests/test_api.py`, `tests/test_api_db.py`, `tests/test_frame_processor_golden.py` (new).
Dashboard: `lib/api.ts`, `lib/api.test.ts`, `lib/api-types.ts` (regenerated), `openapi.json`,
`components/AlertBanner(.test).tsx`, `LiveRunView(.test).tsx`, `RunDetail(.test).tsx`, `e2e/dashboard.spec.ts`.
Docs: `README.md`, `datasets/README.md`, `docs/api.md`, `docs/architecture.md`, `docs/demo.md` (new),
`docs/testing_report.md`, `docs/fix_structure_report.md` (new), plus the plan and brief under `docs/superpowers/`.

## Dependency changes

None. No Python requirement and no npm package or lockfile entry changed.

## Behaviour changes

Only the intended 1c additions: the ack route; `acknowledged_at` and `acknowledged_by` on alerts (optional
fields, default null); schema version 2. The `dashboard/openapi.json` diff against `b5bb0f9` is 74 insertions
and 0 deletions (those fields and the ack path). All other routes, `/ws` messages, event schema 1.0, CLI outputs
and queue defaults are unchanged.

Final-fix wave: the SQLite busy timeout was raised from 5 s to 30 s (`api/db.py`, `BUSY_TIMEOUT_S`) in the
migration-safety work, and `JobManager.start()` now waits `BUSY_TIMEOUT_S + 15` s for the worker instead of a
fixed 30 s so a worker blocked on a lock is not declared failed. `init_db` raises a clear error for a database
whose schema version is newer than the code supports.

## Baselines vs final

Baselines from `b5bb0f9`; final run at the end of this session on this branch.

| Command | Baseline | Final |
|---|---|---|
| `.venv/bin/pytest -q` | 454 passed | 472 passed |
| `.venv/bin/pytest -q -m "not integration"` | 443 passed, 11 deselected | 461 passed, 11 deselected |
| `.venv/bin/ruff check .` | clean | clean |
| `.venv/bin/ruff format --check .` | clean | clean (137 files already formatted) |
| `npm run lint` | clean | clean |
| `npm run typecheck` | clean | clean |
| `npm test` (Vitest) | 125 passed | 133 passed |
| `npm run build` | succeeded | succeeded |
| `npm run e2e` | 12/12 | 14/14 (Chromium 7, WebKit 7), run by Task 4 at `20ef938`; not re-run at the end of Task 5 because only documentation changed |

Python increase: 15 new tests counted from `def test_` additions per commit: 4 in Task 2 (`29d1d74`: 2 golden, 2 FrameProcessor), 9 in Task 3 (`3742b3e`: 3 in `test_api.py`, 6 in `test_api_db.py`), 2 in the race fix (`f8771e8`); the final-fix wave adds 3 more (18 total). Vitest increase: 8 (Task 4).

## Measurements

No performance or accuracy measurement was made in this session. The correctness evidence is the byte-identity
checks above (OpenAPI, flattened route order, golden test) and the hand migration on a real version-1 database.

## Pending (needs a person)

- The real Chrome, Safari and phone checklist in `docs/testing_report.md`, and a person walking through
  `docs/demo.md` once. WebKit in Playwright is not Safari.
- The demo's congestion alert needs `cad_seq27`; the clips are not committed (no licence stated) and must be on
  the machine.

## For Sprint 8 session 2 (live cameras)

- Router layout: add a camera router under `api/routes/`, take settings, jobs and preview from `api/deps.py`
  (`app.state`), and place it in `create_app`'s include order; check `dashboard/openapi.json` after.
  The catch-all `static.router` must stay last.
- `FrameProcessor` needs no file, frame count or writer. A live worker must still call
  `tracker.configure(effective_fps)` and `tracker.warmup()` itself, and call `processor.record(result)` after the
  sinks finish; the golden test does not lock the order of timing calls, and its clock is frozen, so a drawn FPS
  of 0.0 would not be caught. The stitch-diagnostics golden file is empty in the test scenario.
- Migrations: add `(3, _migrate_2_to_3)` to `MIGRATIONS` and a test from the version 2 DDL.
  `service_point_pixels` moved to `ai_engine/frame_processor.py`.
- One queue zone and one service point per camera config; two counters need two configs.
- Acknowledge: `acknowledged_by` is NULL until a login exists; the Live view polls alerts every 2 s while an
  alert is active.
- Not done in this pass (not in scope): `PreviewDep` typed object, `websocket.py` imports the private `_percent`
  from `videos.py`, fresh-database init is not under `BEGIN IMMEDIATE` (benign), a reader connection can leak if the
  backup destination connect raises.
