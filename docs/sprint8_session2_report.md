# Sprint 8, session 2 report: live cameras (2026-11-23)

Branch `sprint-8/cameras` from `main` at `aba0024`. Design: [superpowers/specs/2026-11-23-sprint8-session2-cameras-design.md](superpowers/specs/2026-11-23-sprint8-session2-cameras-design.md)
(commit `58ebb44`); plan: [superpowers/plans/2026-11-23-sprint8-session2-cameras.md](superpowers/plans/2026-11-23-sprint8-session2-cameras.md) (`299081f`).
Method, test list and measurement details are in [testing_report.md](testing_report.md) (section "Sprint 8, session 2").

**The design was derived from the brief's one-line summary of session 2, not from the team's text** (which was not
available); the user approved it on 2026-11-23. It is stated in the design's first paragraph. If the team's text
differs, this report is to be compared with that text, not the other way round.

## Outcome in short

- A camera can be started and stopped from the API or from `python -m worker.ctl`. Each camera runs in its own
  process (`python -m worker`) that posts results to loopback ingest routes; the API stays the only database writer.
  Three sources sit behind one interface: webcam, RTSP (URL only from an environment variable) and a recorded clip
  looped at its own frame rate. Only the **looped clip** has run for real (real model, real worker, through the API,
  in the integration test, in the e2e test and in the measurements). **The webcam and RTSP have not been run.**
- Live results land in the existing tables; the existing Live page and WebSocket work for a live run; a finished
  live run opens on the run page, which says "Live session — no video recorded". Nothing is recorded: no frame or
  video of a live session is written to disk.
- A 30-minute soak of the looped clip completed (17954 frames); the worker's peak RSS did not rise above the 2-minute runs, but the update time was somewhat slower in the last minute (median 38.8 against 36.3 ms) on a machine whose load had risen. One baseline command, the integration test run alone, failed its frame floor (139 against 150), a known tight margin, unchanged here.
- Pending, needs a person: drawing the webcam zone, the webcam run with the camera permission, and the real-browser
  check (see "Pending").

## What was built

| Task | Subject | Commits | Status |
|---|---|---|---|
| 1 | Schema v3 (`videos.kind`, `videos.source_kind`), run-record fields, `RunOptions.process_fps`, baselines | `6e53972` | Completed |
| 2 | Camera `live:` settings, `webcam.yaml`, `cad_seq25` clip section, `live_source` and `live_ready` in `/api/cameras` | `44fe971` | Completed |
| 3 | `ai_engine/live/` (sources and `run_live`), `ai_engine/run_messages.py`, shared `build_queue_report` | `7765f73`, fix round 1 `55f349f` | Completed |
| side | Next.js 16.4.3 to 16.4.6 (user request, see "Dependency changes") | `a228aa4` | Completed |
| 4 | `worker/` package: session, ingest client, `worker.ctl` | `33fc244`, fix round 1 `5529033` | Completed |
| 5 | Loopback ingest routes, `JobManager` publish and upload hold, `seq` dedupe | `2d4dfe1` | Completed |
| 6 | `LiveSupervisor`, start, stop and list routes, cancel on a live run | `b772a4e`, fix round 1 `421f31e` | Completed |
| 7 | Run-page note, e2e test, real-worker integration test | `99d1130` | Completed |
| 8 | Measurements, docs and this report | the commit that adds this file | Completed except the pending items |

Files (from `git diff --stat 299081f..99d1130`, 41 files, +4211 -225): `ai_engine/live/{__init__,capture,loop}.py`,
`ai_engine/run_messages.py`, `ai_engine/queue/camera_config.py`, `ai_engine/run_tracking.py`; `worker/{__init__,__main__,client,ctl,session}.py`;
`api/{db,deps,jobs,live,main,models,preview}.py`, `api/routes/{cameras,ingest,videos}.py`; `configs/cameras/{cad_seq25,webcam}.yaml`;
`dashboard/components/RunDetail.{tsx,test.tsx}`, `dashboard/e2e/live.spec.ts`, `dashboard/{openapi.json,lib/api-types.ts}` (regenerated, additive),
`dashboard/{package.json,package-lock.json}` (the Next.js upgrade); `tests/{fake_worker,test_api,test_api_db,test_api_ingest,test_api_live,test_camera_config,test_draw_zone,test_live_capture,test_live_integration,test_live_loop,test_worker}.py`.
Task 8 adds `scripts/measure_live.py` and the docs (`api.md`, `architecture.md`, `privacy.md`, `security_review.md`, `demo.md`, `testing_report.md`, `README.md`, this report).

## Dependency changes

**One, made at the user's request and not part of the design: Next.js 16.4.3 to 16.4.6 (and `eslint-config-next` to 16.4.6), commit `a228aa4`.** It
came up in Task 1, when `npm run build` stopped on Next.js's security gate (a `SecurityFatalError` that looked up advisories at
registry.npmjs.org; on re-run the build passed with the advisory warning only). The session's ruling was not to upgrade inside the
tasks; the user then asked for the upgrade separately. It fixes GHSA-mfr8-8qww-v76h, GHSA-vc7v-qv4w-wrv8, GHSA-7qx7-33qx-rqx2,
GHSA-f3mh-rxg3-f5g2, GHSA-pc58-hwjq-p385, GHSA-rhh4-g77r-r9qj and GHSA-vvgp-ww4w-46vf; `npm audit fix` also moved `minimatch`
(GHSA-g3hv-75wg-m249) and `typescript-eslint` in the lockfile. Verified in a separate worktree before it was cherry-picked
(ledger): lint, typecheck, Vitest 142, build with no advisory warning, e2e 14/14, `npm audit` 0 vulnerabilities. **It is the session's only dependency
change.** There is no new Python or npm dependency for the feature itself (the worker's HTTP client is `urllib.request`; no `psutil`).

## Baselines against final results

Baselines: `outputs/s11_baseline/` and `task-1-report.md` (Task 1 re-ran them on the branch start). Final: `outputs/s11_final_commands/` (all re-run one at a time after the soak).

| Command | Baseline (`outputs/s11_baseline/`) | Final (`outputs/s11_final_commands/`) |
|---|---|---|
| `pytest -q` | 576 passed | 663 passed (76 s) |
| `pytest -q -m "not integration"` | 565 passed, 11 deselected | 651 passed, 12 deselected |
| `pytest -q -m integration tests/test_live_integration.py` | not a baseline command (new test) | **1 failed**: `assert run["frames_processed"] >= 150` gave `assert 139 >= 150` (`tests/test_live_integration.py:79`, 21.9 s). See below |
| `ruff check .` | clean | clean |
| `ruff format --check .` | clean, 169 files | clean, 190 files |
| `npm run lint` | clean | clean |
| `npm run typecheck` | clean | clean |
| `npm test` | 142 passed | 145 passed (19 files) |
| `npm run build` | **failed** (Next.js security gate, exit 1) | passes (Next.js 16.4.6, no advisory) |
| `npm run e2e` (build first) | 14 passed (4.8 min) | 16 passed (4.5 min) |

**The one failing command.** The integration test run on its own failed with 139 frames against a floor of 150. It passed inside the full `pytest -q` run (663 passed, which includes it) and in Task 7's own run. The test sleeps a fixed 20 s that includes the model load, so on a loaded machine (load average 4 to 6 here) it can end short of 150 frames; this is the margin the Task 7 review flagged as a minor (see "Deferred minors"). It was **not** changed in Task 8 and the failure is reported as it happened. The worker processed 139 real frames, completed, and exited; the failing assertion is the frame floor only.

Reading it: the unit count grew by 86 tests (565 to 651), the full count by 87 (576 to 663), and the integration-marked tests went from 11 to 12 (one new,
`tests/test_live_integration.py`). `ruff format --check` says 169 files at the baseline, where the global constraints said 166; the
difference is the number of files, not a failure. e2e: **16 = 8 specs x 2 browsers** (`chromium` and `webkit` projects); the plan
said 15 and the baseline 14 was 7 specs x 2, so the one new spec adds 2. Vitest 142 to 145 (3 new `RunDetail` tests). The baseline build failed on
the Next.js security gate; it passes since the upgrade.

## Measurements

All from `outputs/s11_live/`; method, wrappers and limits are in the testing report. **Queue numbers from the looped clip are not accuracy figures** (the clip restarts with the
tracker and queue state kept, so people jump back and cause false joins and leaves). Machine: MacBook Air M1, 8 GB, on AC power, load average 3.8 to 6.1 during the runs (other applications and the user's own `python -m api` were running), so absolute times depend on that load.

Three repeats of 120 s (`cad_seq25_120s_run{1,2,3}.json`, `process_fps` 10):

| Quantity | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Update median / p90 (ms) | 37.1 / 48.7 | 35.9 / 45.5 | 37.1 / 46.5 |
| Worker CPU, user + system (s in about 120 s) | 268.0 + 19.0 | 265.8 + 17.8 | 267.6 + 18.4 |
| Worker peak RSS (MB) | 516.9 | 534.1 | 518.3 |
| API peak RSS (MB) | 91.3 | 90.9 | 91.2 |
| Capture-to-ingest median / p90 (ms) | 89.4 / 176.7 | 78.2 / 148.2 | 85.8 / 159.6 |
| Batches; mean events and rows per batch | 111; 1.88, 1.06 | 113; 2.05, 1.04 | 112; 1.95, 1.05 |

### 30-minute soak (`cad_seq25`, `outputs/s11_live/soak_cad_seq25_1800s.json`)

One run of 1800.4 s, completed, 17954 frames processed, no worker left after the API exited. AC power at the start and the end; load average (1 / 5 / 15 min) 3.8 / 4.7 / 5.7 at the start and 5.8 / 9.8 / 8.9 at the end.

| Quantity | First 60 s | Last 60 s |
|---|---|---|
| Update median, p90 (ms; `worker.update.first_window`, `last_window`) | 36.3, 46.4 | 38.8, 54.8 |
| Update mean (ms) | 42.6 | 41.7 |
| Worker `ps` RSS, mean of the window (MB; `rss_over_time.worker`) | 171.7 (includes the model load) | 121.8 |
| Worker `ps` RSS, from `rss_samples_every_10s` (MB) | 111.8 (t = 120 to 180 s, after the load) | 122.0 (t = 1740 to 1800 s) |
| API `ps` RSS, mean of the window (MB) | 35.4 | 27.5 |

Whole run: update median 36.4 ms, p90 46.1 ms; worker CPU 4054.6 s user + 264.7 s system in 1800 s (about 2.4 cores' worth, the same as the 120 s runs); **worker peak RSS 501.7 MB** (`ru_maxrss`) against 517 to 534 MB in the 120 s runs, and API peak RSS 89.4 MB against 91 MB; 1721 batches (0.96 per second), mean 1.96 events and 1.04 rows per batch; capture-to-ingest median 79.8 ms, p90 153.7 ms, max 2831.9 ms (the first batch).

Reading it: **memory did not grow in a way this run can show.** The peak RSS of the soak is not above the peaks of the 2-minute runs (which are reached during model load), and the `ps` series sits at 112 to 126 MB for the worker after the first two minutes; the 10 MB difference between the 120 to 180 s and the last-minute means is within the spread of the early samples (117 to 122 MB between 20 and 70 s). The window means in the table use every second's sample; the `ps` values are lower than `ru_maxrss` because macOS compresses idle pages. **The update time did get somewhat slower: the last minute's median is 38.8 ms against 36.3 ms (+7%) and its p90 54.8 ms against 46.4 ms.** The machine was busier at the end (5-minute load average 9.8, from other applications), so this run cannot say whether the slowdown comes from the worker or from the machine; it is not repeated. Limits: one soak, on a loaded machine, a looped clip, no webcam; the `ps` series is a sample, not a peak.


Webcam: **pending, needs a person.** Command: `python scripts/measure_live.py --camera webcam --seconds 120`.

How each number was taken: update median and p90 by timing every `PersonTracker.update` call in the worker; worker CPU and peak RSS from the worker's own
`getrusage(RUSAGE_SELF)` written at exit; API peak RSS from the API's `ru_maxrss` at exit; capture-to-ingest as the receipt time minus `captured_at_unix` per batch, recorded by
a wrapper around `ingest._store_batch`. **No DEBUG log line was added to `api/`**; the latency comes from the batches. The two wrappers live in `scripts/measure_live.py` and
change no product code; the script starts the API through its own `--internal-api` mode (which only swaps the worker command and records batches) rather than `python -m api`.

## Pending, needs a person

| Item | Why it cannot be done here | Exact action |
|---|---|---|
| Draw the webcam zone and service point | Needs the camera pointed at the real queue, and a window to click in | `python ai_engine/tools/draw_zone.py --video 0 --config configs/cameras/webcam.yaml --target queue_zone`, then `--target service_line` ([demo.md](demo.md) section 6) |
| Webcam run | Opening the camera needs the macOS camera permission for the launching app; nobody was present to answer the prompt, so the camera was never opened | Grant the permission, then `python scripts/measure_live.py --camera webcam --seconds 120` and `python -m worker.ctl start webcam` |
| Real-browser check of the live page and the run page | Only Playwright (chromium, webkit) has driven them | Walk through [demo.md](demo.md) sections 1 to 6 in real Chrome and Safari and note differences in the checklist of [testing_report.md](testing_report.md) |
| Real RTSP | No camera or stream available | Set the variable named in `live.rtsp_url_env`, add a camera YAML with `live.source: rtsp`, start it; confirm that no URL appears in `data/outputs/<id>/worker.log`, the database or the API |
| Frame-mode pixelation on real close faces | Carried over from fix pass session 3; nobody has looked | Look at a webcam preview from about 1 m |


## The builder's calls (the design's "Left to the builder" list)

| Item | The call |
|---|---|
| Module and file names | `ai_engine/live/capture.py` (sources), `ai_engine/live/loop.py` (`run_live`), `ai_engine/run_messages.py` (message builders shared with the API), `worker/session.py`, `worker/client.py`, `worker/ctl.py`, `api/live.py` (supervisor), `api/routes/ingest.py`. The demo command is `python -m worker.ctl start\|stop\|status [camera] [--api URL]` |
| Batch encoding | JSON, about one batch per second (`events`, `timeseries`, `progress`, `frames_processed`, `captured_at_unix`, `seq`); the API ignores a `seq` that is not newer than the last one stored (ruling above) |
| Preview rate and size | At most 5 per second (every 0.2 s), JPEG quality 80, 2 MB limit on both sides (`413`) |
| Timeouts | HTTP request 5 s; worker exits after 15 s without the API; heartbeat after 2 s with no batch; watchdog 10 s without a heartbeat, 60 s from spawn to the first one (ruling above); stop 15 s before SIGKILL; first frame within 10 s, later frames within 10 s (`camera_unavailable`); a worker that sent its final is killed after 5 s if it has not exited; `max_session_s` default 7200 |
| Does `run_live` write a metrics JSON | Yes, `metrics/live<camera>_metrics.json` next to `timeseries/live<camera>_queue.csv`. No image or video in any case (tested) |
| Fake or real worker in the e2e test | Real worker, real model, looped clip (the test skips if the CAD clip is missing, like the other real-model e2e tests). The supervisor tests use a tiny fake-worker script (`tests/fake_worker.py`) |
| `process_fps` for a source with unknown fps (webcam) | The clock only: `run_live` takes the newest frame at most `process_fps` times per second and never needs the source's frame rate; a clip is played at its own fps by `LoopedClipSource` |
| Not in the list, also decided | Schema v3 (not v2, which was taken by alert acknowledgements); `ingest` excluded from OpenAPI; `worker.log` written by the supervisor into `data/outputs/<id>/`; measurement by wrappers in the script rather than a DEBUG log line |

## Design goals

| # | Goal | Status | Evidence and what is missing |
|---|---|---|---|
| 1 | Start and stop a live session from the API or a command | Completed | `tests/test_api_live.py`, `tests/test_worker.py` (`ctl`), the e2e test and measurement runs started and stopped sessions through the routes; `python -m worker.ctl` is tested with fake HTTP |
| 2 | Three sources behind one interface (webcam, RTSP, looped clip) | **Partially completed** | The looped clip is complete and used everywhere. Webcam and RTSP are implemented and tested only through a fake capture (`tests/test_live_capture.py`); neither a real camera nor a real stream has been opened (pending, needs a person) |
| 3 | One OS process per camera; a camera crash or hang cannot take the API down | Completed | Supervisor tests with a fake worker that crashes, goes silent and ignores SIGTERM; the real worker in the integration test; none left behind after the API exits in every measurement run |
| 4 | Store live results while the run is going | Completed | `tests/test_api_ingest.py` (events, waits, alerts, time series, progress in one transaction); the e2e and the measurements show batches of events and rows arriving about once per second |
| 5 | The existing Live page and WebSocket work unchanged; the run page shows the note | Completed for what automation can show | WebSocket shapes equal an upload's (test); e2e opens `/live?id=N`, then the recorded run with "Live session — no video recorded" and a working CSV link; Vitest for `RunDetail`. A real browser has not been used (pending) |
| 6 | Record no video; only privacy-rendered previews (`frame` default, `boxes` allowed, never `none`) | Completed | `tests/test_live_loop.py` and the integration test find no image or video file; `none` is refused when the config loads; previews are in memory only. The visual quality of `frame` on real close faces is unchecked (pending) |
| 7 | Tests and measurements, every number from a command that was run | **Partially completed** | Tests and the cad_seq25 measurements are done (three repeats and the soak). The webcam measurement is pending |
| A | Anti-goals: no URL leak, no frame on disk, no unprivate preview, no changed contract, no orphan worker, no claim that the webcam works | Held, as far as tested | URL-leak test with a fake URL; no-image test; contract tests; orphan checks; no webcam claim in this report. A real RTSP URL has not been tried |

## What session 3 needs

- **Dashboard camera pages** (the design's session 3): a camera list from `GET /api/cameras` (`live_source`, `live_ready`), start and stop buttons calling the routes above, a mapping from the `409` reasons to plain
  sentences, and the Live page's Cancel button (which already works through `cancel`).
- **The webcam run** by a person (draw the zone, grant the permission, measure it), and the real-browser check, before anything is shown to an audience as a webcam demo.
- **A decision on the deferred minors** below, in particular: a late first heartbeat consuming the stop budget (a stop during model load may end `stop_timeout`), a straggling batch landing after the final message,
  and a permanently rejected batch blocking later ones. None breaks the demo; all matter for a pilot.
- **Pilot questions, not built:** reconnecting with backoff, restarting a dead worker, days-long runs, metrics export, and whether and how recording could be offered.
- **Login and CSRF (session 4):** the ingest token and the loopback check are a local separation, not authentication; the start and stop routes have no login.


## Rulings made during the session

Every ruling is from the session ledger (`.superpowers/sdd/2026-11-23-sprint8-session2-cameras/progress.md`).

**Supervisor and runs**

- The watchdog allows the worker 60 s from spawn to its first heartbeat (the spec says only "no heartbeat for 10 s"), because the model loads before any heartbeat and takes seconds on the M1. Cost if wrong: a worker that dies silently at start-up is noticed after 60 s instead of 10 s (a process exit is still caught within 1 s).
- A stop that ends in SIGKILL (no final message) marks the run `failed` with `stop_timeout`, not `completed`; the spec defines `completed` only for a stop that finishes cleanly. Cost if wrong: an operator stop of a hung worker reads `failed`.
- Batch dedupe: every worker batch carries an integer `seq` (from 1, +1 per new batch, unchanged on a retry) and the API ignores (204, no writes, no publish) a batch whose `seq` is not newer than the last stored one; a batch without `seq` is still accepted. This fixes the Task 4 minor "a retry after a lost answer can duplicate events and alerts" at its source. Cost if wrong: a small cross-package change inside Task 5.

**Scope and counts**

- No Next.js upgrade inside the session's tasks (ruling made after Task 1, when `npm run build` stopped on the Next.js security gate): a dependency change is outside scope and the gate did not fail the build when re-run. The user then asked for the upgrade separately; see "Dependency changes".
- The e2e target is 16, not the plan's 15: Playwright runs each spec in both `chromium` and `webkit` projects, so the existing 14 were 7 specs x 2 and the new one makes 8 specs x 2 = 16. Cost if wrong: none, a count only.

**Review rounds**

- Task 3 fix round 1 added the "looped clip wraps on an over-counted frame count" finding at the user's request.
- Task 7 was reviewed by a Sonnet reviewer; its commit trailer says "Sonnet 5.5" (the subagent's own attribution) and was left as is.
- Pre-flight scan found no cross-task conflicts. Noted: Task 3 and Task 7 differ on whether `worker.log` is written (it is written by the supervisor, not by `run_live`); the plan's "TestClient `client=` parameter may vary by Starlette version" was left to the builder.

## Deferred minors (not fixed; one line each)

**Capture and loop (Tasks 3)**

- No test for the later-frame `frame_timeout_s` path or for the `camera_unavailable` versus `stream_ended` give-up.
- The loop bursts to catch up after a stall (no schedule reset); `api/jobs.py` paces the same way.
- Awkward alias formatting at `api/jobs.py:67-69`.
- A header-only CSV is left behind on a `camera_unavailable` failure.
- The looped-clip count correction uses the highest decoded index, so a seek past unread frames or a transient decode failure can shrink the clip permanently.
- `quiet_opencv_logging` swallows all exceptions.

**Worker (Task 4)**

- A batch retry could duplicate events (fixed at its source by the `seq` ruling above; the worker side is unchanged).
- `LiveSourceError` and engine-error paths send `failed` without flushing the roughly 1 s of buffered events.
- The client swallows exceptions without logging (a 401 looks like a dead API and ends with exit 3 and nothing logged).
- `tests/test_worker.py` imports helpers from `tests/test_live_loop.py`.
- No test for the API being unreachable during a mid-run frame stall (it ends by `frame_timeout`, which is acceptable).
- Test weaknesses: the `captured_at` test uses capture time equal to send time on the fake clock; the flush test checks rows, not events; `ctl` catches bare `TypeError` and `AttributeError`; an unused `garbled` FakeApi.

**Ingest (Task 5)**

- A straggling timed-out batch can land after the final message (`_store_batch` does not re-check the status; `_store_final` runs outside `_seq_lock`).
- Batch progress is republished without a type or `video_id` check.
- A permanently rejected (4xx) batch blocks all later batches in the worker.
- Tests: the duplicate-seq test does not assert that nothing was published to the WebSocket; the upload-hold test uses a fixed sleep; only the batch route is tested for `403`.
- The preview body is not checked for a JPEG SOI marker or for being empty.
- The `ingest_seq` entry was not removed when the supervisor ends a run: promoted to Task 6 and done (`api/live.py` pops it).

**Supervisor (Task 6)**

- A late first heartbeat consumes the whole stop budget, so SIGTERM then SIGKILL follow with almost no grace (a stop during a slow model load may end `stop_timeout`).
- No test for the start-up grace (never-heartbeat) path.
- `_end` holds the lock across `proc.wait(5)` and database I/O.
- `shutdown()` sets no closed flag.
- Cancel on a finished live run returns `409` with the detail `run is not live` (the code is unchanged, the detail differs from an upload's).
- The crash test (`worker_exited`) can pass trivially if the crash is seen before the upload is queued, and the fake crash mode could sleep 1 to 2 s.

**Dashboard, e2e and integration (Task 7)**

- The integration test's 150-frame floor over a fixed 20 s sleep that includes the model load is tight (wait on `frames_processed` with a cap instead).
- The teardown `proc.wait(timeout=10)` can mask the original failure.
- The `RunDetail` "unknown" and "?" fallbacks are untested.

**Earlier tasks**

- Task 1: no API-level live-row test (carried to Task 6 and covered there). Three other minors are in the task report.
- Task 2: the `__defaults__` patch in `test_draw_zone` is brittle. Two other minors are in the task report.
