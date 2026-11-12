# QueueVision Sprint 7: dashboard and first waiting-time accuracy (spec)

Source: the team's Sprint 7 prompt (from the team doc "QueueVision Sprint 7 Plan", 12 Nov 2026, and the
Claude Design canvas "QueueVision Dashboard"), recorded here verbatim as the spec for the implementation
plan `docs/superpowers/plans/2026-11-12-sprint7-dashboard.md`. Session 1 (merge readiness) was completed
on 2026-11-12 with no fixes needed. By the team's decision, session 2's branch `sprint-7/dashboard` starts
from the tip of PR #6 (which contains PR #5) instead of `main`, because #5 and #6 were not yet merged; its
PR targets the #6 branch until those merge, then is retargeted to `main`.

---

Run as **three Claude Code sessions**, in order:

- **SESSION 1 — Merge readiness** (short): get PRs #5 and #6 ready to merge. The team merges them.
- **SESSION 2 — Build:** API additions plus the Next.js dashboard. Branch `sprint-7/dashboard` from `main` after #5 and #6 are merged.
- **SESSION 3 — Accuracy:** after the team has filled the waiting-time sheets, score them, publish the results to the dashboard, and finish the docs. Same branch, or `sprint-7/accuracy` if session 2 is already merged.

The design canvas is private; its spec is written out in section D below. If `docs/design/` contains exported design files, they win over section D where they differ.

## SHARED RULES (every session)

Role: senior full-stack and computer-vision engineer working directly in `https://github.com/dhrubydv1/QueueVision`. Machine: MacBook Air M1, 8 GB RAM, macOS, Python 3.11 with the existing `.venv/`; Node.js via `nvm` (use the current LTS if none is set; record the version).

1. **Inspect first.** Read `README.md`, `docs/api.md`, `docs/architecture.md`, `docs/implementation.md`, `docs/event_schema.md`, `docs/security_review.md`, `docs/testing_report.md`, `docs/sprint5_implementation_report.md`, `docs/sprint6_report.md`, `datasets/metadata/annotation_guidelines.md`, and the code in `api/` (`main.py`, `jobs.py`, `db.py`, `models.py`, `settings.py`, `upload_validation.py`, `static/index.html`) and `ai_engine/run_tracking.py` (`process_video`, `FrameUpdate`). Run `pytest -q` and `pytest -m "not integration"` and record both baselines before changing anything.
2. **Never fabricate results.** Every number in docs, the dashboard, test reports or PR descriptions comes from something you ran. Anything you cannot run is marked pending with the reason.
3. **Contracts that must not change:** CLI behaviour and output files of `run_tracking.py`; all existing `/api/*` routes and response shapes; `/ws/videos/{id}` message types and fields; event schema 1.0; queue-logic defaults (Sprints 5 and 6 showed no variant beats them — do **not** tune anything this sprint). Existing tests pass unchanged unless a change is deliberate and explained.
4. **Dependency direction:** `api → ai_engine`; `ai_engine` imports nothing from `api`; the dashboard talks to the API only over HTTP and WebSocket.
5. Unit tests stay torch-free; real-model tests are marked `integration`. `ruff check .` and `ruff format --check .` clean. Python deps pinned in `requirements*.txt`; npm deps pinned to exact versions with a committed lockfile.
6. **Privacy and data rights:** heads blurred in every image the dashboard can show; no identities stored. The Collective Activity Dataset (CAD) has **no stated licence**: never commit CAD frames, videos or screenshots, and keep screenshots you take for checking in a gitignored folder. The API stays bound to 127.0.0.1 (no login exists yet).
7. Do not change repository visibility. Do not push to `main`. Small, meaningful commits; one PR per session's branch.
8. End every session with the report described in that session.

## SESSION 2 — BUILD (branch `sprint-7/dashboard`)

### A. API additions (Python)

#### A1. Real-time processing option

- Add `realtime: bool = False` to `POST /api/videos` (multipart form field), validated like the other options and stored in the run's `options` JSON. **No database schema change.**
- When true, the job paces itself to the clip's speed: inside the job's `on_frame` handling, wait until `start_monotonic + update.timestamp_s` before continuing. Never accumulate lag (if behind, continue immediately). Waiting must be interruptible by cancel/shutdown (use an `Event.wait(timeout)` tied to `should_stop`, not `time.sleep`).
- Pacing must not change any output: a test runs the same clip with `realtime` on and off (fake tracker, fake clock) and asserts identical waits CSV, time series and events.
- Expose `realtime` in the run record returned by `GET /api/videos/{id}`.

#### A2. Live preview of a processing run

- `process_video` currently passes no image to `on_frame`. Add the smallest backward-compatible change: optional fields on `FrameUpdate` — the annotated frame (after drawing) and the person boxes for that frame — populated **only** when the caller asks for them (e.g. a `frame_callback_wants_image=True` argument), so the CLI path does no extra work and its outputs stay byte-identical (add a test).
- In the API, keep the **latest preview per processing run in memory only**: a JPEG (quality ~70, at most 960 px wide), encoded at most 5 times per second. **Always blur heads** on the preview, even if the run's `blur_heads` option is off: blur a copy using `ai_engine/visualization/privacy.py` and the frame's person boxes. Drop the preview when the run ends.
- Routes:
  - `GET /api/videos/{id}/preview.jpg` → latest JPEG; 404 if the run is not processing or no frame yet.
  - `GET /api/videos/{id}/preview.mjpg` → `multipart/x-mixed-replace` stream of preview frames; ends when the run ends or the client disconnects.
- Tests: 404 rules; MJPEG boundary format; a test proving the preview's head region differs from the unblurred frame while the rest matches.

#### A3. Accuracy data for the dashboard

- `scripts/export_accuracy.py` builds `data/accuracy.json` (gitignored) from evaluation outputs that exist on disk (`outputs/s5_*`, `outputs/s6_*`, `outputs/metrics/*_queue_eval.json`, `*_wait_eval.json`, and the split files in `datasets/splits/`). Contents: queue-length results per clip with split (val/test), pooled per split (MAE, RMSE, bias, within ±1, frame count); the Sprint 6 error breakdown (per-category totals and per-frame rates); waiting-time results when present (session 3); a `limits` list; `generated_at` and the source file for every number.
- Every number is read from those files, never typed into the script. If a source file is missing, that section is `null` with a `missing_sources` entry.
- `GET /api/accuracy` serves the file (404 with a clear message if not generated).
- Tests with small synthetic input files.

#### A4. WebSocket Origin check

- New setting `QUEUEVISION_ALLOWED_ORIGINS` (comma-separated). Default: the API's own origin and `http://localhost:3000`. WebSocket connections whose `Origin` is not allowed are closed (code 1008) before `accept`. Requests with no `Origin` (non-browser clients) are allowed. Update `docs/security_review.md`.

#### A5. Serving the dashboard

- FastAPI serves the dashboard's static export (`dashboard/out/`) at `/`, with client-side routes resolving to their `.html` files. The current test page moves to `/legacy` and keeps working.
- If `dashboard/out/` does not exist, `/` shows a short HTML message explaining how to build it, and `/legacy` still works.

### B. Dashboard (`dashboard/`, new top-level folder)

#### B1. Setup

- Next.js (App Router) + TypeScript (strict) + Tailwind CSS + TanStack Query + Recharts; Vitest + Testing Library; Playwright. Exact versions pinned; record them in the README.
- `output: 'export'` (static). Ids are query parameters because static export cannot pre-render unknown ids: `/live?id=12`, `/run?id=12`.
- Fonts via `next/font` (Inter 400/500/600, Space Grotesk 500/600) so nothing loads from a CDN at runtime.
- Development: `next dev` on port 3000 with rewrites `/api/*` → `http://127.0.0.1:8000`; WebSockets connect directly to `NEXT_PUBLIC_API_WS` (default `ws://127.0.0.1:8000`), allowed by A4. Production/demo: same origin (served by FastAPI). Do not rely on Next.js rewrites for WebSockets.
- `npm run gen:api` generates `lib/api-types.ts` from `http://127.0.0.1:8000/openapi.json` with `openapi-typescript`; WebSocket message types are written by hand in `lib/live-types.ts`, matching `docs/api.md`. CI fails if generated types are stale.

#### B2. Shared code

- `lib/api.ts` — typed fetchers; errors become readable messages.
- `lib/useLiveSocket.ts` — connect, reconnect with backoff (1, 2, 4 … 15 s), apply the `snapshot` message on every (re)connect, expose `connectionState` and `lastMessageAt`.
- Components (names indicative): `AppHeader`, `StatusChip`, `QueueLengthCard`, `WaitCard` (value + range, or "Unknown" / "Insufficient data" exactly as the engine reports, plus an "Unvalidated" tag), `MetricCard`, `StaleBanner` (no message for 5 s or socket closed → greyed values + "Last update HH:MM:SS"), `AlertBanner` (with Acknowledge as a UI-only dismiss this sprint; say so in a tooltip), `QueueChart`, `EventFeed`, `PreviewPanel` (MJPEG `<img>`, reconnects, placeholder when no frame), `RunsTable`, `WaitsTable`, `UploadForm`.
- All times from the API are shown in the browser's time zone with a short zone label (the team is in Asia/Kathmandu, "NPT").
- The browser computes nothing beyond formatting and chart drawing. Every number comes from the API.

#### B3. Pages

| Route | Content | Data |
|---|---|---|
| `/` Overview | Header; "Overview" title with date and count of runs processing; **Upload a video** primary button; cards for runs processing now (queue length, predicted wait, status chip, sparkline from the live socket, "updated HH:MM:SS"); card for the latest completed runs; **Recent recorded runs** table; **How accurate is it?** card (queue-length held-out MAE, bias, within ±1; waiting time "Not measured yet" until session 3) | `/api/videos`, `/ws/videos/{id}`, `/api/accuracy` |
| `/live?id=` Live run | Title (clip name + camera config id), status chip (Live / Live · congested / Offline), last update; **Cancel run** button; alert banner when `alert_active` (reasons from `alert_reasons`); offline banner when the socket is down; preview panel (left, 3:2) with legend; right column: Queue length (big, with "Raw count N · smoothed over 2 s"), Predicted wait (Unvalidated tag), Service rate, People in frame ("Not the queue length"); queue-length chart since run start (smoothed line, raw dotted if available from time series, alert limit dashed when the camera config has one); event feed (Joined the queue / Service started / Service completed / Left without service / Left the area) | `/ws/videos/{id}`, `preview.mjpg`, `/api/videos/{id}/timeseries`, `/api/cameras` |
| `/runs` and `/run?id=` Recorded runs | Upload form (file, camera config, every Nth frame 1–10, blur heads default on, **Process in real time** checkbox, Start); run header with status chip, options summary, Report (JSON) / Time series (CSV) / Delete; annotated video player with seeking; summary cards (joined, service started, left without service with % of joins, congestion alerts, measured wait mean/median/P90 with Unvalidated tag); queue-length chart through the clip with service-start markers and a shaded "first 5 s: dwell and smoothing" band; tabs **Waits / Events / Alerts** with counts | existing `/api/videos/*` routes |
| `/accuracy` | Measured results only: headline cards (held-out MAE, bias, within ±1, waiting time); **Error by clip** horizontal bars (val vs test distinguished by lightness and a legend); **Where the count goes wrong, per labelled frame** bars (over-count vs under-count causes); **Changes tried and not adopted** table (from Sprint 5/6 data where available in the accuracy JSON; otherwise omit the table rather than typing numbers); **Limits of these numbers** list | `/api/accuracy` |

Empty and error states are explicit ("No runs yet — upload a video", "Accuracy data not generated — run scripts/export_accuracy.py").

### C. Tests and checks (session 2)

- `npm run lint`, `npm run typecheck`, `npm run build`; add a CI job (Node, no Python model stack) for these plus Vitest.
- Vitest: `WaitCard` (value + range, Unknown, Insufficient data), `StaleBanner`, `useLiveSocket` reconnect + snapshot with a fake WebSocket.
- Playwright against the real API (fake-tracker mode if available, else a short real clip with `max_frames`): upload with real time on → Live page updates → completion → video plays and seeks; API restart while the Live page is open → offline state → recovers without reload; phone viewport 390 × 844 shows no horizontal scroll and all controls are reachable.
- Manual check in Chrome and Safari with screenshots in a gitignored folder; look at them. Record results in `docs/testing_report.md`.
- Measure and record: a real-time run of a 20.6 s clip (cad_seq25) — wall time and per-frame overhead; that its outputs equal a normal run's.

### D. Design spec (from the Claude Design canvas)

- **Type:** Inter for UI text (14 px body, 13 px labels at weight 500, 12 px captions); Space Grotesk 600 for page titles (28 px) and big numbers (queue length 64 px on Live, 32–40 px elsewhere), tabular numerals for all numbers.
- **Colours:** page `#F4F5F7`; surfaces `#FFFFFF`; borders `#E4E7EC`, input borders `#D0D5DD`; text `#101828`, secondary `#475467`, tertiary `#667085`; primary `#1F4FD1` (hover `#163A9C`, soft `#E8EEFC`); warning/congested `#B54708` (soft `#FEF0E3`, border `#F7B27A`, dark text `#7A2E0B`); success/live `#067647` (soft `#E7F6EC`); neutral chip `#F2F4F7`; destructive text `#B42318`. Val bars `#84ADFF`, test bars `#1F4FD1`. Check every text/background pair meets WCAG AA (4.5:1).
- **Preview overlay legend** (matches the engine's drawing): in queue = solid orange box, joining (dwell) = dashed yellow, not in queue = green, service line = blue.
- **Layout:** max content width 1360 px, 24 px page padding; cards 12 px radius, 20 px padding, 1 px border, no shadows; 20 px gaps between sections, 16 px inside grids; controls 40 px tall (44 px on phone), 8 px radius; chips fully rounded. Header: logo mark + "QueueVision" wordmark, nav (Overview, Live camera, Recorded runs, Accuracy; active item on primary-soft), a "localhost" chip on the right.
- **Responsive:** two-column areas wrap to one column below ~900 px; tables scroll horizontally inside their card; on phones the nav becomes a bottom bar with four icon+label items.
- **Rules:** "Queue length" and "People in frame" always separate; raw count under the smoothed one; "Unknown"/"Insufficient data" shown verbatim, never 0; "Unvalidated" tag on predicted and measured waits until session 3 produces a measured accuracy; status chips always carry words (colour never alone); event markers differ in shape; no emoji; real `<button>`, `<a>`, `<label>` elements; icon-only buttons have `aria-label`.

### E. Docs and report (session 2)

Update README (how to build the dashboard and run the demo with one command), `docs/api.md` (A1–A4 with examples from a real run), `docs/architecture.md` (dashboard + serving), `docs/security_review.md`, `docs/testing_report.md`. Report: what was built, files changed, dependency versions, test baselines vs final, measurements, screenshots list (paths only), issues, each item in A–C marked Completed / Partially completed / Not completed, and anything session 3 needs.

## SESSION 3 — WAITING-TIME ACCURACY (after the team fills the sheets)

Prerequisite: the team has filled the waiting-time sheets for cad_seq25, cad_seq26 and cad_seq41 under `datasets/annotations/queue_events/` (gitignored), with cad_seq25 annotated by two people. **You do not label anything.** If any sheet is missing or empty, stop that part, report it, and do not produce or estimate a result.

1. Validate every sheet with `python -m ai_engine.tools.make_wait_annotation_kit --check <sheet>`; for cad_seq25 also `--compare <second sheet>`. Report agreement (matched people, join/service time differences). List disagreements for the team to settle; score only the settled sheet.
2. Produce predictions with the shipped defaults (no tuning): run `run_tracking.py` on each clip with its camera config (MPS, as in Sprint 5), and also a variant with stitching off for comparison.
3. Run `python ai_engine/evaluate_waiting.py` for each clip, measured waits and predicted waits (`--timeseries`), default tolerance 3 s; save JSON reports under `outputs/metrics/`.
4. Report per clip and pooled: MAE, RMSE, median absolute error, bias, number of people matched / missed / extra, rows excluded by reason, with and without stitching. State the sample size next to every number. If results are poor, report them as they are; do not change thresholds on these clips.
5. Re-run `scripts/export_accuracy.py` so `/api/accuracy` includes the waiting-time section; the Accuracy page and Overview card show it. Remove the "Unvalidated" tag only if the team decides the result supports it — otherwise keep it and add the measured error beside it.
6. Docs: `docs/testing_report.md` (method, results, limits: 3 short clips, CAD frame rate assumed 30 fps, human labels), README, and a **5-minute demo script** in `docs/demo.md` using only verified features (upload in real time → Live page → Recorded run → Accuracy).
7. Final report: results table, agreement figures, what changed in the dashboard, test results, and recommendations for Sprint 8 (webcam/RTSP camera workers, login before any non-local use, more labelled footage recorded by the team with permission).
