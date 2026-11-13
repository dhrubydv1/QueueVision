# Sprint 8 Session 1b: Sprint 7 carry-over hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix the Sprint 7 deferred items listed in the Sprint 8 brief, session 1b, each with a test; investigate the stride-3 pacing gap; prepare the person-run browser checklist.

**Architecture:** Small, independent fixes in `api/`, `scripts/export_accuracy.py`, `dashboard/`, plus one measurement investigation in `scripts/measure_realtime.py`. No new features.

**Tech stack:** as Sprint 7 (Python 3.11 FastAPI, Next.js 16 static export, Vitest, Playwright).

**Spec:** the Sprint 8 brief, "SHARED RULES" and "SESSION 1 — 1b", recorded in `docs/superpowers/specs/2026-11-13-sprint8-brief.md`.

## Global Constraints

- Branch `sprint-8/hardening`, started from the tip of PR #7 (`160dcea`) by the user's decision because #5–#7 are not merged yet; its PR targets `sprint-7/dashboard` until they merge.
- Baselines (2026-11-13, at `160dcea`): `pytest -q` 440 passed; `pytest -m "not integration"` 429 passed, 11 deselected; ruff clean; dashboard lint/typecheck/build clean, Vitest 114 passed; e2e 10/10 (Chromium 5, WebKit 5).
- Contracts unchanged: `run_tracking.py` CLI and outputs, every existing `/api/*` route and response field, `/ws/videos/{id}` messages, event schema 1.0, queue-logic defaults. No tuning.
- Unit tests torch-free; ruff clean; npm deps exact. Never fabricate a number. CAD material never committed.
- Each item: write the failing test first; if an item does not reproduce, say so in the report and skip it.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Path containment must not break serving legitimate outputs written under `data/outputs/<id>/…` (the normal case) — test both.
2. Case-insensitive Origin comparison must still reject look-alike origins (`http://localhost:30000`, `http://localhost.evil:3000`).
3. Arrow-key tabs must keep focus and selection in sync and work with Home/End.
4. A new event key must still dedupe the same event arriving twice (history + socket).

---

### Task 1: API hardening

**Files:** `api/main.py`, `api/settings.py`, `api/jobs.py`, `docs/security_review.md`; tests in `tests/test_api.py`, `tests/test_api_security.py`, `tests/test_api_dashboard.py`, `tests/test_api_jobs.py`.

- **Report-path containment.** `/api/videos/{id}/video` and `/api/videos/{id}/timeseries.csv` (and any other route serving a path recorded in a report) resolve the path and serve it only if it lies under `settings.data_dir.resolve()`; otherwise 404 with the route's existing message, and log a warning. Tests: a report whose `outputs.video`/`outputs.timeseries` points outside the data dir (e.g. a tmp file elsewhere, or `../`) → 404; a normal run → 200.
- **Origin edge cases.** `Origin: ""` and `Origin: null` are rejected (pin with tests). `QUEUEVISION_ALLOWED_ORIGINS=","` → empty allowlist (pin with a test; document that it rejects every browser origin). IPv6 own origin: host `::1` → `http://[::1]:<port>`. Comparison case-insensitive on scheme and host (lower-case both the configured values and the header; keep port exact). Tests include look-alikes `http://localhost:30000`, `http://localhost.evil:3000` rejected. Docs: a refused WebSocket appears to a real browser as an HTTP 403 handshake failure (TestClient shows code 1008).
- **`JobManager.stop()`**: set `_stop` and `_interrupt` while holding `_cond`. Test: a real-time job blocked in a long pacing wait (low-fps clip as in `test_realtime_wait_interrupted_by_cancel`) → `stop()` returns and the run ends `cancelled` within 2 s.
- **`/legacy`**: the redirect keeps the query string (`/legacy?x=1` → `/legacy/?x=1`); `HEAD /legacy` → 307 too. Dashboard HTML responses (`.html` and `index.html`, `404.html`, the not-built page) carry `Cache-Control: no-cache`; `_next/static/…` assets do not. Tests for each.

### Task 2: Accuracy export naming and unreadable files

**Files:** `scripts/export_accuracy.py`, `tests/test_export_accuracy.py`, `ai_engine/evaluate_waiting.py` docstring only if naming needs stating there.

- Naming (fixed here): `<clip>_wait_eval.json` = predicted-wait evaluation (classic format); `<clip>_observed_wait_eval.json` = observed-format evaluation (measured and predicted waits scored separately). Both are what `evaluate_waiting.py` already writes. The export groups by clip: `waiting_time.per_clip[]` gets one entry per clip with `kind: "predicted" | "observed"` and `clip` never carries the `_observed` suffix. If both files exist for a clip, both entries appear (distinguished by `kind`).
- Unreadable or malformed wait-eval files go to `missing_sources` with the reason (never skipped silently).
- Update `dashboard/lib/accuracy-types.ts` for the new `kind` field (and regenerate nothing else). Tests: suffix stripping, both kinds for one clip, unreadable file reported.

### Task 3: Dashboard accessibility and correctness

**Files:** under `dashboard/` (`tsconfig.json`/`package.json` scripts, `components/AppHeader.tsx`, `components/RunDetail.tsx`, `components/AlertBanner.tsx`, `lib/live-types.ts`, `app/page.tsx`) and their tests.

- `npm run typecheck` on a fresh checkout before any build: make it independent of the generated `next-env.d.ts` (e.g. `typecheck` = `next typegen && tsc --noEmit`, or commit `next-env.d.ts` — choose one and state why). Verify in a temp worktree with `npm ci && npm run typecheck`.
- Desktop nav links ≥ 44 px tall (test the class or computed min-height).
- Run-page tabs implement the WAI-ARIA tabs pattern: roving `tabIndex` (active 0, others −1), ArrowLeft/ArrowRight cycle, Home/End, `aria-controls` → existing panel ids. Tests with user-event.
- AlertBanner: the "nothing is recorded" explanation is visible text or a toggle reachable by keyboard and touch, not a `title` tooltip. Test it is in the DOM and focusable/visible.
- Alerts tab: label `congestion_start` → "Congestion started", `congestion_end` → "Congestion ended", anything else → the raw type in words (never "started"). Test.
- `eventKey`: distinct events with the same type and timestamp but different `frame_index`, `track_ids` or `person_key` must not collapse; the same event delivered twice (history and socket) still dedupes. Build the key from the stable fields of the schema 1.0 payload (e.g. `event_type`, `timestamp_s`, `frame_index`, `person_key`, `track_id`, `track_ids` joined) — confirm against `docs/event_schema.md`. Tests both ways.
- Overview hydration: confirm with a production build + a browser that no hydration warning appears and the date/processing count are not shown from build time (Playwright console check in the e2e suite). Fix only if still present.

### Task 4: Stride-3 pacing gap investigation

**Files:** `scripts/measure_realtime.py` (timing instrumentation), output `outputs/s7_realtime/` (gitignored), findings in `docs/testing_report.md`.

- Add per-stage timing to the measurement (in the script's own wrappers, not in `ai_engine` hot paths unless behind a flag that defaults off): tracker update, queue analyzer, drawing/writing, on_frame callback (split: DB flush, publish, preview offer/encode, pacing wait), and the time between frames. Run stride 1 and stride 3, fast and real time, **3 times each**, sequentially, recording the spread (min/median/max) of wall time, waited time and non-wait time.
- Find where the stride-3 real-time non-wait time exceeds the fast run's. If it is a bug (e.g. preview encode or pacing causing extra work), fix it with a test; otherwise document the cause with numbers. No tuning of queue logic.
- MPS only one job at a time; do not touch ports 8000/8770.

### Task 5: Person-run browser checklist and docs

**Files:** `docs/testing_report.md`, `docs/sprint8_session1_report.md` (new), README if commands changed.

- Checklist for a person: real Chrome and real Safari (desktop) and a real phone if available — every page (Overview, Live while a real-time run is processing, Recorded run, Runs, Accuracy, /legacy), what to look at (heads blurred, NPT times, no horizontal scroll on phone, Unknown/Insufficient data wording, Offline banner when the API is stopped, video seek), where to save screenshots (`outputs/s8_screens/`, gitignored), and blank result columns.
- Session report per the brief's FINAL REPORT section: each 1a/1b item Completed / Partially completed / Not completed / Not reproduced; baselines vs final for every command; measurements with method; what needs a person; what session 2 needs.
