# Sprint 7, session 2 report: API additions and the dashboard

Branch `sprint-7/dashboard`, started from the tip of PR #6 (which contains PR #5) by team decision, because #5 and #6 were not yet merged. Date: 2026-11-12. Spec: [superpowers/specs/2026-11-12-sprint7-dashboard-design.md](superpowers/specs/2026-11-12-sprint7-dashboard-design.md). Test details: [testing_report.md](testing_report.md).

Every number below comes from a command that was run or a file that is named next to it. Anything not run is marked pending.

## 1. What was built

* **API (Python):** an optional `realtime` pacing mode for runs; a head-blurred live preview of a processing run (`preview.jpg`, `preview.mjpg`); `scripts/export_accuracy.py` and `GET /api/accuracy`; a WebSocket `Origin` check; serving of the dashboard export at `/` with the Sprint 4 page moved to `/legacy`; and two additions the dashboard needed: `GET /api/videos/{id}/timeseries.csv` and the optional `CameraInfo` fields `max_queue_length` and `smoothing_window_s`. Details: [api.md](api.md).
* **Dashboard (`dashboard/`):** Next.js static export with four areas: Overview (`/`), Live run (`/live?id=`), Recorded runs (`/runs`, `/run?id=`) and Accuracy (`/accuracy`). Typed API client and WebSocket hook with reconnect, stale-data banner, tokens defined once in `app/globals.css`.
* **Tests:** 35 new Python tests, 107 Vitest tests, 10 Playwright end-to-end tests (Chromium and WebKit), a measurement script (`scripts/measure_realtime.py`), a CI `dashboard` job.
* **Demo:** `scripts/demo.sh` builds the dashboard if `dashboard/out/` is missing, exports the accuracy data and starts `python -m api`.

Nothing in the queue logic, event schema 1.0, the existing `/api/*` shapes, the WebSocket message types or `run_tracking.py`'s CLI output was changed. No database schema change. Nothing was tuned.

## 2. Files changed (summary by area, `git diff --shortstat` against the PR #6 base `b0f74a5`)

| Area | Change |
|---|---|
| `api/` | 6 files, +295 / -30: `jobs.py` (pacing, preview offer), new `preview.py`, `main.py` (routes, Origin check, catch-all), `settings.py`, `models.py`, `__main__.py` (graceful shutdown timeout 5 s) |
| `ai_engine/` | 1 file, +10 / -1: `run_tracking.py` (`FrameUpdate.image` / `.people`, `frame_callback_wants_image`) |
| `scripts/` | 3 new files, +513: `export_accuracy.py`, `export_openapi.py`, `measure_realtime.py` (plus `demo.sh`, added with the docs) |
| `tests/` | 7 files, +839 / -1 |
| `dashboard/` | new, 62 files, +15100 (including `package-lock.json` and generated `lib/api-types.ts`) |
| `.github/workflows/ci.yml` | +31: `dashboard` job and OpenAPI staleness step |
| `docs/` | spec and plan, then this task: README, `api.md`, `architecture.md`, `security_review.md`, `testing_report.md`, this report |

## 3. Dependency versions

Node v22.23.2, npm 10.9.8. Python dependencies unchanged. npm packages are exact (`npm install -E`) with a committed lockfile; the full table is in the README ([section 9c](../README.md#9c-dashboard-sprint-7)): next 16.4.3, react and react-dom 19.3.1, @tanstack/react-query 5.105.0, recharts 3.10.1, tailwindcss and @tailwindcss/postcss 4.3.3, typescript 5.9.3, eslint 9.39.5, vitest 5.0.5, vite 8.3.5, jsdom 30.1.3, @playwright/test 1.64.1, openapi-typescript 7.13.0 and the Testing Library packages.

Deviations from "newest stable at least two weeks old" (reasons from the Task 7 report): TypeScript 5.9.3 instead of 6/7 because `openapi-typescript` 7.13.0 peers `typescript ^5`; eslint 9.39.5 instead of 10 because `eslint-config-next` 16.4.3 peers `eslint <=9`; `@types/node` 22.x to match Node 22; vite listed explicitly as a peer of vitest (an npm 10 resolver crash otherwise). `npm audit` reports a moderate `minimatch` advisory in the dev-only eslint tree; not fixed (would need eslint major changes).

## 4. Test baselines vs final

| Command | Baseline | Final |
|---|---|---|
| `.venv/bin/pytest -q` | 403 passed | 440 passed |
| `.venv/bin/pytest -m "not integration"` | 392 passed, 11 deselected | 429 passed, 11 deselected |
| `ruff check .`, `ruff format --check .` | clean | clean |
| `npm run lint`, `npm run typecheck` | n/a | clean |
| `npm test` (Vitest) | n/a | 15 files, 114 passed |
| `npm run build` | n/a | succeeded (7 static pages) |
| `npm run e2e` (Playwright; re-run at the final head, after the final fix wave) | n/a | 10 passed (Chromium 5, WebKit 5) in 1.9 min. The earlier 10/10 in 2.2 min was measured at 3b3914c |

One existing test changed on purpose: the Sprint 4 test-page test now requests `/legacy/` (the page moved, spec A5).

## 5. Measurements (real-time run, `cad_seq25`, device `mps`)

Source: `outputs/s7_realtime/summary.json` (gitignored). Clip: 617 frames, 30 fps, 20.567 s.

| | Stride 1 | Stride 3 |
|---|---|---|
| Normal run wall time | 30.346 s (includes model load) | 9.163 s |
| Real-time run wall time | 26.309 s | 21.392 s |
| Real-time wall minus clip duration | +5.742 s | +0.825 s |
| Pacing waits / seconds waited | 0 / 0 | 205 / 8.785 |
| Outputs identical to the normal run (waits, time series, events) | yes | yes |

Stride 1 never paced (processing at about 25.5 fps is slower than the 30 fps clip), so it shows identical outputs but says nothing about pacing cost. Stride 3 paced; the real-time run's non-wait time (12.607 s) is 3.444 s more than the whole normal run (9.163 s) and the wait counter does not explain it. The cause was not measured. One run per pair, so no variance estimate. Spec asked for "per-frame overhead"; the honest per-frame figure is not separable from this gap, so none is claimed.

## 6. Browser check and screenshots

Done by the controller with Playwright Chromium and WebKit (desktop 1440 x 1000, phone 390 x 844, time zone Asia/Kathmandu) against the real API with real data. **Real Chrome: pending** (the Claude in Chrome extension was not connected). **Real Safari: pending** (WebKit through Playwright passed; that is not Safari). No real phone. Four bugs were found and fixed in Task 14b (phone overflow on `/accuracy`, "GMT+5:45" instead of "NPT", Live event order, finished Live run showing no events); see [testing_report.md](testing_report.md). Screenshots were taken before those fixes and not re-taken.

Screenshots (gitignored under `outputs/**`, CAD-derived, **never commit or embed**), all in `outputs/s7_screens/`:
`chromium_desktop_{accuracy,overview,run_id_1,runs}.png`, `chromium_phone_{accuracy,overview,run_id_1,runs}.png`, `webkit_desktop_{accuracy,overview,run_id_1,runs}.png`, `webkit_phone_{accuracy,overview,run_id_1,runs}.png`, `live_chromium_desktop_live_id_2.png`, `live_chromium_phone_live_id_2.png`, `live_webkit_desktop_live_id_2.png`, `live_webkit_phone_live_id_2.png`.

## 7. Deviations from the spec and decisions

* **Legend colours follow the engine**, not the spec text (solid yellow, magenta service point, blue zone), because the legend must match what the engine draws.
* **API types are generated from the committed `dashboard/openapi.json`** (`npm run gen:api`, rewritten by `scripts/export_openapi.py` with no torch) so CI can check staleness without a running API. `gen:api:live` (from port 8000, the spec's command) is kept.
* **Added routes and fields** that the spec did not list: `timeseries.csv` (Report/Time series buttons) and `CameraInfo.max_queue_length`, `smoothing_window_s` (alert limit line, "smoothed over 2 s"). `dashboard/openapi.json` and `lib/api-types.ts` were regenerated with them.
* **"Left without service" shows "N of M joins", not a percentage.** The report's `abandonment_rate` is abandoned / (completed + abandoned) (5/9 in the sample), not a share of joins (5/11), and the browser computes nothing.
* **Fetches use relative `/api`** (the Next dev rewrite); media (MJPEG, video) use port 8000 directly under `next dev`, because the dev proxy may buffer MJPEG.
* **Branch base:** PR #6 tip rather than `main`; its PR targets the #6 branch until #5/#6 merge.
* **Overview "waiting time" and measured-wait display** show a placeholder when `waiting_time` is non-null (its shape is unspecified until session 3).
* **Blur-off runs show no video in the dashboard (controller ruling).** A run processed with `blur_heads` off has an unblurred annotated video, so `RunDetail` does not render the `<video>` for it and shows "This run was processed with head blurring off, so its annotated video is not shown in the dashboard." The upload checkbox and the API option are kept.
* **Accuracy export:** the `sprint` field is the string "Sprint 6"; the "outside zone" category is the sum of two engine keys; the limits list counts only `cad_` clips.

## 7b. Final fix wave (whole-branch review of b0f74a5..f281c30)

1. `python -m api --port N` / `--host X` now set `QUEUEVISION_PORT` / `QUEUEVISION_HOST` before `uvicorn.run`, so the allowed-origins check matches the real address (also in `--reload` children). Tested.
2. Blur-off runs: video hidden in `RunDetail` (see section 7). Tested both ways.
3. Overview renders the date only after mount and the "N runs processing" count only once the runs query has succeeded.
4. `PreviewPanel` renders no `<img>` (so makes no request) when the run is not active; it shows "Run finished — open the recorded run".
5. A preview encode error is logged (`preview encode failed for run N`) and no longer fails the run. Tested.
6. CI `lint-and-unit` and `requirements.txt` pin `pydantic==2.14.2`.
7. `ProcessingRunCard` uses the LiveRunView offline rule (closed, or connecting after data); neutral "Processing" chip during the first connect.

Final counts after this wave: 440 passed (full), 429 passed / 11 deselected (not integration), 114 Vitest tests in 15 files, e2e 10 passed at the final head.

## 8. Issues and deferred minors worth knowing

* The Playwright e2e tests are not in CI (they need the model, clip and a venv); the CI `dashboard` job runs lint, typecheck, Vitest, build and the staleness check and has **not run on GitHub yet**.
* `next/font/google` downloads the font files at **build** time (CI build needs network); nothing loads from a CDN at run time.
* Mid-run API crash: the Live page shows Offline and recovers without reload. A graceful stop ends the run `cancelled`; a crash leaves it `failed` after restart.
* Real-time pacing: the unexplained stride-3 gap (section 5). `JobManager.stop()` sets its interrupt outside the condition lock, so a lost interrupt could delay shutdown by up to one frame interval (no test for the stop wait).
* Preview: an exception in `PreviewStore.offer` would fail the whole run (should be wrapped and logged); a test assumes a whole MJPEG part arrives in the first chunk. Heads are blurred from the detector's boxes, so a missed person is not blurred.
* Origin check: no tests for `Origin: ""`/`null` or an env value of only commas; an IPv6 own-origin lacks brackets; configured origins are not lower-cased; a real server answers the pre-accept close with HTTP 403 rather than a 1008 frame.
* Dashboard serving: `/legacy` redirect drops the query string; `HEAD /legacy` falls to the catch-all; HTML is served without `no-cache`.
* `next-env.d.ts` is gitignored but in the tsconfig `include`; `npm run typecheck` before any build on a fresh checkout is the thing to watch.
* UI minors: `AlertBanner` dismissal lasts for the component's lifetime (the Live page keys it per alert episode); its tooltip is not touch or keyboard accessible; tabs on the run page lack arrow-key support; the Alerts tab labels an unknown event type as "started"; desktop nav links are about 36 px tall; the Overview's build-time date and "0 processing" count may cause a hydration mismatch (a Task 10 review minor, noted for the final wave; not re-checked here); Accuracy chart y-axis label width is fixed at 160 px; `eventKey` collapses keyless events with the same timestamp.
* `/video` and CSV routes serve report-recorded paths without a containment check (a shared pre-existing pattern, noted by review).
* Accuracy export: an unreadable wait-eval file is skipped silently; the exporter's missing-splits path string is hardcoded; the stride-1 pair of the measurement includes the model load in the normal run's wall time.
* The local `npm audit` advisory above.

## 9. Spec items A to C

Status words as asked: Completed / Partially completed / Not completed.

### A. API additions

| Item | Status | One line |
|---|---|---|
| A1 Real-time option | Completed | `realtime` form field, stored in `options`, shown in the run record; interruptible wait, no lag build-up; test shows identical outputs with a fake clock |
| A2 Live preview | Completed | `FrameUpdate.image` / `.people` only on request (CLI outputs byte-identical, tested); blurred in-memory JPEG at most 5 per second, 960 px, quality 70; both routes, 404 rules, MJPEG format and blur-vs-unblurred tests |
| A3 Accuracy data | Completed | `scripts/export_accuracy.py` and `GET /api/accuracy` with source file and `missing_sources`; the waiting-time section is `null` by design until session 3 |
| A4 WebSocket Origin check | Completed | `QUEUEVISION_ALLOWED_ORIGINS`, close before accept, no-Origin allowed; `security_review.md` updated |
| A5 Serving the dashboard | Completed | static export at `/`, client routes to `.html`, test page at `/legacy`, not-built message |

### B. Dashboard

| Item | Status | One line |
|---|---|---|
| B1 Setup | Completed | Next.js App Router, strict TS, Tailwind, TanStack Query, Recharts, Vitest, Playwright, exact versions; `next/font`; dev rewrites; generated types from the committed OpenAPI file (deviation, section 7) |
| B2 Shared code | Completed | `lib/api.ts`, `useLiveSocket` (backoff 1, 2, 4, 8, 15 s; snapshot on each connect), all listed components, browser-zone times with "NPT"; Acknowledge is UI-only with a tooltip that says so |
| B3 Overview | Completed | cards for processing runs with sparkline, latest completed runs, recent runs table, "How accurate is it?" with waiting time "Not measured yet"; empty state text as specified |
| B3 Live | Partially completed | all panels, chip words, banners, cancel, chart with raw dotted and dashed alert limit, event feed; legend colours follow the engine (deviation); verified in Chromium and WebKit, not real Chrome or Safari |
| B3 Recorded runs | Partially completed | upload form, header, Report/CSV/Delete, video with seeking, summary cards, chart with markers and 5 s band, three tabs; "left without service" shows "N of M joins" instead of a percentage |
| B3 Accuracy | Completed | headline cards, error by clip (val vs test), count-error bars, changes-not-adopted table (omitted when absent), limits list; waiting time "Not measured yet" |
| Design rules (D) | Completed | tokens defined once, contrast pairs tested (all at least 4.5:1, the lowest text-3 on page at 4.56), phone bottom bar, status chips carry words, no emoji; phone 390 px no horizontal scroll (e2e and browser check) |

### C. Tests and checks

| Item | Status | One line |
|---|---|---|
| lint, typecheck, build, CI job | Completed locally; CI not yet run | `dashboard` job added; pending its first GitHub run |
| Vitest: `WaitCard`, `StaleBanner`, `useLiveSocket` | Completed | 107 tests in 13 files pass |
| Playwright against the real API | Completed | upload with real time, Live updates, completion, video plays and seeks; API restart (crash and graceful) recovers without reload; phone viewport; 10 passed. Uses a short real clip with `max_frames`, not a fake tracker |
| Manual check in Chrome and Safari with screenshots | Partially completed | Playwright Chromium and WebKit with screenshots, viewed; real Chrome and real Safari pending (need a person) |
| Measurement of the real-time run | Partially completed | wall time, waits and output identity measured for two pairs; stride 1 never paced; stride 3 has an unexplained non-wait gap; no clean per-frame overhead figure |

### E. Docs and report

Completed: README (build, dev, demo, versions, env vars), `api.md`, `architecture.md`, `security_review.md`, `testing_report.md` and this report.

## 10. What session 3 needs

* The team's filled waiting-time sheets for `cad_seq25` (two annotators), `cad_seq26` and `cad_seq41` under `datasets/annotations/queue_events/`. Nothing was labelled or estimated here; waiting-time accuracy remains "Not measured yet".
* **Naming quirk in `scripts/export_accuracy.py`:** the waiting-time glob is `outputs/metrics/*_wait_eval.json`, and it also matches `*_observed_wait_eval.json`, which yields a clip name like `cad_seq25_observed`. Fix or define the naming for measured versus predicted evaluations before exporting, and decide whether an unreadable wait-eval file should be reported in `missing_sources` instead of skipped silently.
* **Dashboard shape for measured waits:** `waiting_time` is passed through unreshaped; the Accuracy page shows only "Measured" and the clip count, and the Overview card a placeholder. Session 3 defines the shape and the display, and decides whether to keep the "Unvalidated" tags (keep them unless the team decides the result supports removing them).
* Real Chrome and Safari checks by a person, and re-taking the screenshots after the Task 14b fixes.
* The first green run of the CI `dashboard` job; the `docs/demo.md` 5-minute script (only verified features).
* If the real-time option matters for demos: measure why the stride-3 run has 3.444 s of unexplained non-wait time, and try a stride of 2.
* Before any use beyond localhost: login (none exists), CSRF protection, and the unencrypted storage of uploads (see [security_review.md](security_review.md)).
