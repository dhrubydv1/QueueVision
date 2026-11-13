# QueueVision Sprint 8: live cameras (brief, recorded as the spec)

Recorded verbatim from the team's Sprint 8 prompt (13 Nov 2026). Session 1b's branch `sprint-8/hardening` starts from the tip of PR #7 by the user's decision (PRs #5–#7 not yet merged); its PR targets `sprint-7/dashboard` until they merge.

---

Sprint 8 turns QueueVision from "process a recorded video" into "watch a camera": webcam and RTSP cameras (and recorded clips looped in real time as stand-in cameras) feed the same queue engine, store results in the same tables, and show on the Sprint 7 dashboard.

Run as separate Claude Code sessions, in order. Each ends with a PR and a report; the team merges before the next starts.

| Session | Branch | What | Needs a person? |
|---|---|---|---|
| 1. Merge and carry-overs | fixes on `sprint-7/dashboard`, then `sprint-8/hardening` | Make PRs #5 → #6 → #7 mergeable, get CI green, fix Sprint 7's deferred items | The team merges; a person does the real Chrome/Safari check |
| 2. Camera engine and API | `sprint-8/cameras` | Camera worker process, camera registry, supervision, ingest | A webcam test needs the laptop's camera permission |
| 3. Camera dashboard | `sprint-8/camera-dashboard` | Cameras on Overview, a Live camera page, a Cameras settings page | — |
| 4. Login (only if the dashboard will be opened from another device) | `sprint-8/auth` | Users, roles, sessions, CSRF | — |
| 5. Own footage intake (after the team records) | `sprint-8/own-footage` | Validate, register and prepare annotation kits for the team's own queue recordings | The team records and labels |

**Before Sprint 8:** if the team has filled the Sprint 7 waiting-time sheets, run the Sprint 7 prompt's session 3 first (or alongside session 1 here). Nothing in Sprint 8 depends on it.

## SHARED RULES (every session)

Role: senior full-stack and computer-vision engineer working directly in `https://github.com/dhrubydv1/QueueVision`. Machine: MacBook Air M1, 8 GB RAM, macOS, Python 3.11 `.venv/`, Node v22 (as recorded in Sprint 7).

1. **Inspect first.** Read `README.md`, `docs/api.md`, `docs/architecture.md`, `docs/implementation.md`, `docs/event_schema.md`, `docs/security_review.md`, `docs/privacy.md`, `docs/testing_report.md`, `docs/sprint7_session2_report.md` (especially sections 7, 8 and 10), and the code in `api/` (`main.py`, `jobs.py`, `db.py`, `models.py`, `settings.py`, `preview.py`), `ai_engine/run_tracking.py` (`process_video`, `FrameUpdate`), `ai_engine/pipeline.py`, `ai_engine/queue/`, `ai_engine/video/video_loader.py`, and `dashboard/` (`lib/`, `components/LiveRunView.tsx`, `ProcessingRunCard.tsx`, `PreviewPanel.tsx`, `app/`). Record baselines: `pytest -q`, `pytest -m "not integration"`, `ruff check .`, and in `dashboard/`: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`, `npm run e2e`.
2. **Never fabricate results.** Every number in docs, the dashboard, reports and PR descriptions comes from something you ran. What you cannot run (no camera, no second device, no person) is marked pending with the reason.
3. **Contracts that must not break:** `run_tracking.py` CLI behaviour and outputs; every existing `/api/*` route and response field; `/ws/videos/{id}` message types and existing fields (adding optional fields is allowed); event schema 1.0; queue-logic defaults. **Do not tune anything.** Existing tests keep passing unless a change is deliberate and explained.
4. **Dependency direction:** `api → ai_engine`, `worker → ai_engine`; `ai_engine` imports neither; the dashboard talks to the API only over HTTP/WebSocket and computes nothing beyond formatting.
5. Unit tests torch-free (fake tracker, fake clock, fake capture, fake HTTP); real-model tests marked `integration`. Ruff clean. Python deps pinned; npm deps exact with the lockfile.
6. **Privacy:** heads blurred in every image the dashboard or API can show; **live sessions record no video by default**; no identities stored. Collective Activity Dataset (CAD) material has no stated licence: never commit or publish CAD frames, videos or screenshots; screenshots for checking stay in gitignored folders.
7. **Security:** without session 4, the server stays on 127.0.0.1. RTSP URLs (which may contain passwords) live only in environment variables, never in the database, logs, API responses or the dashboard.
8. Do not change repository visibility. Do not push to `main`. Small commits; one PR per session.

## SESSION 1 — MERGE AND CARRY-OVERS

PRs #5, #6 and #7 are stacked (#7 contains #6, which contains #5); `main` is still at Sprint 4 (`dd758b2`).

### 1a. Merge readiness (on the existing PR branches)

1. For #5, then #6, then #7: check out, run every baseline command (Python and dashboard), fix only genuine failures (with a test) on that PR's branch.
2. Push the #7 branch and make the CI `dashboard` job pass on GitHub for the first time (`gh run list`, `gh run view --log-failed`). The job needs network for `next/font/google` at build time; if that is the failure, fix the job (cache or allowed network), not the fonts.
3. Report the merge order (#5, #6, #7, each retargeted to `main` after the previous merge) and anything the team must do. **The team merges; you do not.**

### 1b. Sprint 7 deferred items (branch `sprint-8/hardening` from `main` after #7 is merged)

Fix each with a test. Skip any that turns out not to reproduce, and say so.

- `/api/videos/{id}/video` and `timeseries.csv` (and any route serving a report-recorded path): resolve the path and refuse anything outside the data directory.
- Origin check edge cases: `Origin: ""` and `null`, an env value of only commas, IPv6 own-origin with brackets, case-insensitive comparison. Document that a refused WebSocket appears as HTTP 403.
- `JobManager.stop()`: set the interrupt under the condition lock; add a test for the stop wait.
- `/legacy` redirect keeps the query string; `HEAD /legacy` handled; dashboard HTML served with `Cache-Control: no-cache` (static assets may cache).
- `scripts/export_accuracy.py`: the wait-eval glob also matches `*_observed_wait_eval.json` (clip names like `cad_seq25_observed`). Define the naming for measured versus predicted wait evaluations, and report unreadable files in `missing_sources` instead of skipping them.
- Dashboard: `npm run typecheck` works on a fresh checkout before any build (`next-env.d.ts`); desktop nav links at least 44 px tall; run-page tabs support arrow keys (WAI-ARIA tabs pattern); the AlertBanner explanation is reachable by keyboard and touch (not a hover-only tooltip); the Alerts tab never labels an unknown event type as "started"; `eventKey` does not collapse distinct events with the same timestamp; the Overview hydration mismatch (build-time date, "0 processing") is checked and fixed if present.
- Real-time pacing: investigate the unexplained 3.444 s non-wait gap at stride 3 (Sprint 7 report section 5) with timing around each stage; fix it if it is a bug, otherwise document the cause. Run each configuration at least 3 times and report the spread.
- **Needs a person:** a real Chrome and a real Safari check of all pages (desktop and a real phone if available), re-taking the gitignored screenshots after the Sprint 7 fixes. Prepare a checklist in `docs/testing_report.md` for the team to fill.

## SESSIONS 2–5

The full text of sessions 2 (camera engine and API: `kind='live'` runs, worker processes, schema v2, `ai_engine/live/` capture and pacing, `worker/` package, ingest/supervisor/routes, tests and measurements, recording off by default), 3 (camera dashboard), 4 (login and roles) and 5 (own footage intake) is in the team's Sprint 8 prompt; it is recorded here in full when each session starts.

## FINAL REPORT (each session)

What was built; files created and modified; dependency changes with versions; baselines vs final for every test command; measurements with how they were taken; what needs a person and is pending; issues found, fixes, remaining limits; each item of that session marked Completed / Partially completed / Not completed; what the next session needs.
