# Fix pass session 1: code structure and small gaps — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Split `create_app` into routers and `process_video` into a reusable per-frame step without changing behaviour; add ordered DB migrations and saved alert acknowledgement; fill three documentation gaps.

**Architecture:** API routes move to `api/routes/*.py` with shared dependencies in `api/deps.py`; the engine gains a `FrameProcessor` that needs no file source or writer; `api/db.py` gains a migration list.

**Spec:** `docs/superpowers/specs/2026-11-13-fix-pass-brief.md` (SHARED RULES, SESSION 1).

## Global Constraints

- Branch `fix/structure` from `main` at `b5bb0f9`. Baselines: `pytest -q` 454; `-m "not integration"` 443 passed, 11 deselected; ruff clean; dashboard lint/typecheck/build clean, Vitest 125, e2e 12/12.
- No behaviour change except 1c's intended additions: `run_tracking.py` CLI outputs, every `/api/*` route and field, `/ws` messages, event schema 1.0, queue defaults. No tuning.
- Unit tests torch-free; ruff clean; deps pinned. Never fabricate numbers. No CAD material committed.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## Review Focus

1. Route registration order after the split: `/legacy` redirect before its mount, the dashboard catch-all last, the upload guard route class still wrapping `POST /api/videos`, the WebSocket Origin check before accept.
2. OpenAPI byte-identity (Task 1) — compare against the committed `dashboard/openapi.json` at `b5bb0f9`.
3. Golden test (Task 2) must exercise a camera config (queue CSVs, events, waits, alerts) and MOT output, not only the video.
4. Migration on a real v1 database made by the current code; a second start is a no-op; a failed migration rolls back and leaves the backup.
5. Acknowledge on the Live page targets the right alert row (the latest open `congestion_start` of that run) and survives reload.

---

### Task 1: Split `create_app` into routers (1a)

**Files:** create `api/deps.py`, `api/routes/__init__.py`, `api/routes/{health,cameras,videos,preview,accuracy,websocket,static}.py`; modify `api/main.py`. Tests unchanged (they must pass as-is).

- `create_app(settings=None, tracker_factory=None)` keeps its signature (tests and `scripts/export_openapi.py` call it). It builds settings, `JobManager`, lifespan, the exception handler, then `include_router` in an order that reproduces today's route order exactly. Module-level `app = create_app()` stays.
- `api/deps.py`: accessor functions reading `request.app.state` / `websocket.app.state` (`get_settings`, `get_jobs`, `get_preview`, `open_db` context manager, `origin_allowed`, `contained_path`). No module globals holding app state.
- The upload guard (`UploadGuardRoute`) stays applied to `POST /api/videos` only.
- Verify: `.venv/bin/python scripts/export_openapi.py && git diff --exit-code dashboard/openapi.json`; full `pytest -q`; ruff. `api/main.py` ends well under 150 lines.

### Task 2: Split `process_video` into a reusable per-frame step (1b)

**Files:** `ai_engine/run_tracking.py` (and, if cleaner, a new `ai_engine/frame_processor.py`); new test `tests/test_frame_processor_golden.py`.

- `FrameProcessor` (constructed from tracker, optional camera/analyzer settings, options): `process(frame, frame_index, timestamp_s) -> FrameResult` doing tracking, queue analysis, blur and drawing on the frame — no `VideoLoader`, no frame count, no writer. `FrameResult` carries the annotated frame, people, queue state and timing. Output writers (video, MOT, queue time series, zone events, waits, alerts, event log, metrics) are separate objects fed from `FrameResult` by `process_video`.
- The overlay's "frame N/total" needs the total: pass `total_frames: int | None` into the drawing step as an optional argument (None for live).
- Golden test: before refactoring, copy the current `process_video` into the test module as a frozen reference (or capture reference outputs at the start of the task) and run both on a synthetic clip with `WalkerTracker`, `CAMERA_YAML` camera, `save_mot=True`, `blur_heads=True`; compare every output file byte for byte except the MP4, which is compared by decoding and hashing each frame; metrics JSON compared after removing run-varying fields (as `tests/test_pipeline.py` does). Also a test that `FrameProcessor` runs on frames from a plain list with `total_frames=None`.
- Existing `tests/test_pipeline.py`, CLI and API tests pass unchanged.

### Task 3: Ordered migrations and the alert acknowledgement API (1c, backend)

**Files:** `api/db.py`, `api/models.py` (`Alert` gains `acknowledged_at: str | None = None`, `acknowledged_by: str | None = None`), `api/routes/videos.py` (new route), `dashboard/openapi.json` + `dashboard/lib/api-types.ts` regenerated, `docs/architecture.md` (how to add a migration); tests `tests/test_api_db.py`, `tests/test_api.py`.

- `MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]]` = `[(2, _migrate_1_to_2)]`; `init_db(conn)` creates the v1 schema for a new DB, reads `schema_version`, and for each pending migration: before the first one, back up the DB file to `<db>.bak-v<from>-<UTC timestamp>` using the SQLite backup API (WAL-safe); run each migration and its version bump in one transaction. Fresh databases end at the latest version.
- `_migrate_1_to_2`: `ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT`, `ADD COLUMN acknowledged_by TEXT`.
- `POST /api/videos/{video_id}/alerts/{alert_id}/ack` → 200 with the updated `Alert`; first call sets `acknowledged_at` (UTC ISO 8601, seconds) and leaves `acknowledged_by` NULL; repeat calls return the same row unchanged (idempotent); 404 for unknown video, unknown alert, or an alert of another video. Registered with the other video routes.
- Tests: build a v1 DB with the code at `b5bb0f9`'s schema (copy the v1 `_SCHEMA` DDL into the test as a fixture, insert rows through the old shapes), run `init_db`, assert columns exist, rows intact, version 2, backup file present; second `init_db` makes no backup and no change; a migration that raises rolls back (version unchanged) and the backup remains; route tests (set, idempotent, 404 cases); existing tests pass.

### Task 4: Acknowledge button saves (1c, dashboard)

**Files:** `dashboard/lib/api.ts` (`ackAlert(videoId, alertId)`), `dashboard/components/AlertBanner.tsx`, `dashboard/components/LiveRunView.tsx`, `dashboard/components/RunDetail.tsx` (Alerts tab shows "Acknowledged HH:MM:SS NPT" for acknowledged alerts), tests; e2e.

- Live page: when `alert_active` turns on, fetch `getAlerts(id)` (refetch every 2 s while active and no matching row yet); the current alert = the latest `congestion_start` row. If it has `acknowledged_at`, the banner is hidden (also after reload). Acknowledge → `ackAlert` → hide on success; on failure show the API message and keep the banner. Remove the "only on this screen; nothing is recorded" note; replace with "Acknowledging records the time for this alert." (or similar truthful wording).
- Vitest: banner hidden when the current alert is acknowledged; clicking calls `ackAlert` with the right ids; failure shows the message. Playwright: hard to trigger a real congestion alert reliably — add an API-level e2e step that acknowledges an alert row on a run that produced one, if any fixture clip produces congestion; otherwise mark the Playwright part pending with the reason and cover with Vitest + API tests.

### Task 5: Documentation gaps and session report (1d)

**Files:** `docs/demo.md` (new), `README.md`, `docs/architecture.md`, `datasets/README.md`, `docs/testing_report.md`, `docs/fix_structure_report.md` (new).

- `docs/demo.md`: 5-minute script from `scripts/demo.sh` through upload (real time on) → Live → Recorded run → Accuracy; each step says what the audience sees; only features verified by tests or the testing report; items not yet verified by a person in a real browser are marked.
- README + architecture: one queue zone and one service point per camera config (two counters need two configs); zones drawn with `ai_engine/tools/draw_zone.py`. Architecture: router layout, `FrameProcessor` seam, migration list and how to add one.
- `datasets/README.md`: the reserved `_observed` clip-id suffix.
- Session report per shared rule 7, with baselines vs final for every command.
