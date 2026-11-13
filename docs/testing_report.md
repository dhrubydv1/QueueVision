# Testing Report

The Sprint 8 browser checklist (pending, for a person) and pacing investigation come first, then Sprint 7 (session 2) results, then Sprints 6, 5, 4, 3 and 2; the Sprint 1 report is
kept at the end. Every number here comes from a command that was actually run
on the machine described under Sprint 2 (same environment). Nothing was
estimated.

## Sprint 8: browser checklist for a person (PENDING, not run)

Status: **pending**. No real Chrome, real Safari or real phone check has been done by Claude. The only
browser runs so far are the automated Playwright e2e tests (Task 3, 2026-11-13: 12/12 passed, Chromium 6
and WebKit 6; WebKit is Playwright's engine, not Safari itself). The screenshots from Sprint 7 predate the
Sprint 7 fixes and the Sprint 8 dashboard changes, so they must be re-taken.

Setup: `scripts/run_api.sh` or `.venv/bin/uvicorn api.main:app` (default port 8000), build the dashboard
(`cd dashboard && npm run build`), open `http://127.0.0.1:8000`. For a phone, use the machine's LAN address and
add that origin to the API's allowed origins (see `docs/api.md`). Use a clip with people (for example
`cad_seq25.mp4`) so there is something to see. Save screenshots to `outputs/s8_screens/` (gitignored), named
`<browser>_<page>.png`, for example `safari_live.png`.

Fill the Result column with PASS, FAIL (say what you saw) or N/A. Leave blank what was not checked.

| # | Page | What to look at | Chrome | Safari | Phone | Notes |
|---|---|---|---|---|---|---|
| 1 | Overview (`/`) | Loads without console errors; date and "N runs processing" appear after load, no flash or hydration warning; nav links easy to click (at least 44 px tall) | | | | |
| 2 | Live, real-time run processing | Start a real-time run from the upload form. Preview shows video with **heads blurred**; counters, wait times and alerts update; times shown are NPT (non-productive-time) values as labelled; Stop works and the page shows the run as stopped | | | | |
| 3 | Recorded run | Pick a finished run. Overview/Alerts/Timeline tabs switch by click and by arrow keys (Left/Right, Home/End) with focus following; **video seek** by dragging the bar and clicking a timeline/alert item; heads blurred in the video; wait times (NPT) match the Runs table | | | | |
| 4 | Recorded run, Alerts tab | Congestion events read "Congestion started" and "Congestion ended"; no unknown event type is labelled "started"; the acknowledge note ("hides this banner on this screen only") is visible without hovering | | | | |
| 5 | Runs | List, sorting and links work; status badges readable; no horizontal scroll on phone | | | | |
| 6 | Accuracy | Wording: a missing or unmeasured metric reads **Unknown** or **Insufficient data**, never 0 or a made-up number; predicted and observed wait evaluations are shown separately; unreadable source files are listed as missing | | | | |
| 7 | `/legacy` | Old test page loads; `/legacy?x=1` keeps the query string; `/legacy` redirects to `/legacy/` | | | | |
| 8 | API stopped | Stop the API while a page is open: an **Offline banner** appears, and it goes away when the API returns; no blank page | | | | |
| 9 | Phone only | **No horizontal scroll** on any page in portrait; tabs and buttons are tappable; AlertBanner explanation reachable by touch | | | | |
| 10 | Any | Keyboard: Tab order visible focus ring; AlertBanner explanation reachable by keyboard | | | | |

Tester name, date, browser and OS versions, device: ______________________________

Anything that fails: write what you did, what you expected and what happened, and attach the screenshot.

## Sprint 8: real-time pacing gap (2026-11-13)

Question: at stride 3 the Sprint 7 real-time run spent 12.6 s outside the pacing waits against 9.2 s for
the whole fast run. Where does the extra non-wait time go? Method: `scripts/measure_realtime.py --stages`
(timing wrappers applied from the script; no `ai_engine` or `api` code changed) on `cad_seq25.mp4`
(617 frames, 20.567 s, MPS, yolo11n), one throwaway fast run first so the model load is in no measured
run, then fast and real-time jobs alternating, 3 repeats per stride, one job at a time. Raw data:
`outputs/s8_pacing/` (gitignored).

| Stride, mode | Wall s (min / median / max) | Waited s | Non-wait s (min / median / max) |
|---|---|---|---|
| 1, fast | 25.914 / 26.062 / 26.704 | 0 | 25.914 / 26.062 / 26.704 |
| 1, real time | 26.172 / 26.183 / 26.256 | 0 (never waited) | 26.172 / 26.183 / 26.256 |
| 3, fast | 8.927 / 9.079 / 9.279 | 0 | 8.927 / 9.079 / 9.279 |
| 3, real time | 21.200 / 21.381 / 21.457 | 8.459 / 8.534 / 8.690 (205 waits) | 12.691 / 12.741 / 12.923 |

The gap reproduces: stride 3 real-time non-wait is about 3.7 s more than the fast run's total. Stage
totals, stride 3, repeat 1 (fast -> real time, seconds): tracker update 6.925 -> 9.385; head blur 0.414 ->
0.669; video write 0.591 -> 0.869; preview offer 0.131 -> 0.365 (renders 39 -> 79); metrics panel, zone,
people and service-point drawing, decode and queue analyzer together 0.46 -> 0.79. DB writes (0.005 s),
publish (0.000 s) and the on_frame work other than the wait and preview are negligible. Per-frame
detector+tracker update: median 32.9 ms (p90 35.7) fast against 45.7 ms (p90 51.6) in real time; the
204 updates that follow a wait are the slow ones, the 3 that do not (the first frames) match the fast
run at about 33 ms.

Run order reversed (real time first, then fast; stride 3, 3 repeats, same throwaway warm-up, raw data in
`outputs/s8_pacing/stages_rtfirst.json`):

| Stride 3, order real time first | Wall s (min / median / max) | Waited s | Non-wait s | Update ms median |
|---|---|---|---|---|
| real time | 21.361 / 21.625 / 21.731 | 7.889 / 8.808 / 8.894 | 12.468 / 12.923 / 13.737 | 44.2 to 45.6 |
| fast | 8.968 / 9.019 / 9.169 | 0 | 8.968 / 9.019 / 9.169 | 32.5 to 33.0 |

The gap holds with the order reversed (real-time non-wait about 3.9 s above the fast run), so it is not an
artefact of the fast run going first.

Cause (inferred, not measured): every stage, CPU-bound OpenCV drawing as well as MPS inference, runs
about 1.3 to 1.7 times slower once the worker sleeps about 40 ms between frames. Inference alone accounts
for roughly 2.5 s of the 3.7 s. The preview contributes about 0.2 s: its 5-per-second limit is by wall
clock, so a run that lasts twice as long encodes about twice as many previews (79 against 39). The
evidence is that a busy-wait removes the gap: the same real-time runs with the pacing wait replaced by a
busy-wait (diagnostic `--spin-wait`, not product behaviour) give non-wait 9.599 / 9.584 / 10.172 s and a
per-frame update median of 34.1 to 34.4 ms, essentially the fast run's 32.4 to 32.7 ms (the fast run in
that batch has one slow outlier, 12.560 s). This is consistent with the machine slowing down while idle
between frames (for example a CPU/GPU power state); power, frequency and thermal counters were not
measured. The busy-wait also changes the CPU duty cycle, so it shows the slowdown depends on idling, not
which mechanism causes it. DB flush, publishing and the pacing logic do not explain it. No code defect
was found and nothing was changed. Effect on users: none beyond slightly slower inference; pacing
already skips a wait when the run is late, and real-time wall time stays within about 0.6 to 1.2 s of
the 20.567 s clip at stride 3. Stride 1 never waits because it processes at about 23.7 fps, below the clip's 30.

## Sprint 7, session 2 (2026-11-12): dashboard, preview, real-time option

Full write-up: [sprint7_session2_report.md](sprint7_session2_report.md). Machine, Python and
Node: MacBook Air M1, Python 3.11 `.venv`, Node v22.23.2, npm 10.9.8. Everything below was
run; items not run are marked pending.

### Automated tests: baselines vs final

| Command | Baseline (before any change, 2026-11-12) | Final (end of session 2) |
|---|---|---|
| `.venv/bin/pytest -q` | 403 passed | 440 passed (37 new Python tests; 35 in session 2 plus 2 in the final fix wave), 34.76 s |
| `.venv/bin/pytest -m "not integration" -q` | 392 passed, 11 deselected | 429 passed, 11 deselected, 21.67 s |
| `.venv/bin/ruff check .` and `ruff format --check .` | clean | clean |
| `cd dashboard && npm run lint` / `npm run typecheck` | n/a (no dashboard) | both clean |
| `npm test` (Vitest, jsdom, Testing Library) | n/a | 15 files, 114 tests passed |
| `npm run build` (static export) | n/a | succeeded; 7 static pages generated |

The existing tests pass unchanged except one deliberate change: the Sprint 4 test of the
served test page now requests `/legacy/` (the page moved; spec A5). New Python tests cover
real-time pacing (identical outputs with `realtime` on and off, no lag build-up, wait
interrupted by cancel; the cancel test was mutation-checked against `time.sleep`),
`FrameUpdate.image` / `people` and CLI output identity, the preview store and routes
(404 rules, MJPEG boundary format, blur of the head region even with `blur_heads` off, MJPEG
ending on shutdown), the accuracy export on small synthetic files, the Origin check, the
dashboard catch-all (including traversal and symlink escape) and `timeseries.csv`.
Vitest covers `WaitCard` (value and range, Unknown, Insufficient data), `StaleBanner`,
`useLiveSocket` (reconnect backoff and snapshot with a fake WebSocket), the API client,
formatting (including "NPT"), the Live, Run and Accuracy views and the accuracy card.
Not unit-tested: `QueueChart` geometry, `PreviewPanel` layout, `UploadForm`, `RunsTable`,
`WaitsTable` (checked in the browser instead). The Overview page has only a pre-load test (`app/page.test.tsx`).

CI: a `dashboard` job (Node 22: `npm ci`, generated-types staleness check, lint,
typecheck, Vitest, build) and an OpenAPI staleness step in `lint-and-unit` were added to
`.github/workflows/ci.yml`. They have **not been run on GitHub yet** (pending: the branch
is not pushed).

### Final fix wave (after the whole-branch review)

Fixed, each test-first where a test is named: `python -m api --port/--host` now reach the app settings (allowed origins); a blur-off run's video is not shown in the dashboard (controller ruling; upload option and API option kept); the Overview no longer flashes the build date or "0 runs processing"; `PreviewPanel` makes no request for a finished run; a preview encode error no longer fails a run; `pydantic==2.14.2` pinned in CI and `requirements.txt`; `ProcessingRunCard` shows a neutral chip during the first connect. Counts above include these.

### End-to-end (Playwright, real API, real model)

`cd dashboard && npm run build && npm run e2e`: **10 passed in 2.2 min** when measured at 3b3914c (Task 14b), and **10 passed in 1.9 min** re-run at the final head after the final fix wave,
Chromium 5 and WebKit 5, one run, no retries. Playwright 1.64.1; its bundled Chromium is
156.0.8078.4 (`browsers.json`). The API ran on port 8765 with a temporary data directory,
the real YOLO11n on `cad_seq25`. The five tests per browser:

1. Upload with real time on (`max_frames=150`) goes to the Live page, which updates; the run completes; the annotated video plays and seeks.
2. The upload form in the UI (file, camera, real time ticked, Start) opens `/live?id=`.
3. API crash (SIGKILL) while the Live page is open: the page shows Offline and "Last update", the API restarts, the page recovers without reload and shows Failed (a crash marks the run `failed`).
4. Graceful API stop (SIGTERM): Offline, restart, the page shows Cancelled without reload.
5. Phone viewport 390 x 844 on `/`, `/runs`, `/run`, `/live`, `/accuracy`: no horizontal scroll, controls reachable.

Finding: the first run of test 3 expected Failed after a graceful stop; the worker ends the
job `cancelled` then, so the assertion was split into two tests instead of weakened. The
e2e tests skip themselves when the clip, model or venv are missing, so CI does not run them.
The `/accuracy` phone check initially passed only because that e2e server had no
`accuracy.json`; the e2e server now writes a synthetic one (made-up values, used only for the
layout check).

### Browser check (controller, 2026-11-12)

Playwright Chromium and WebKit, desktop 1440 x 1000 and phone 390 x 844, time zone
Asia/Kathmandu, against the real API with real data (completed runs 1 and 2 of `cad_seq25`).
Screenshots are in `outputs/s7_screens/` (gitignored, derived from CAD, never committed; listed
in the session report). They were viewed. What was found and fixed (Task 14b):

| Finding | Cause | Status |
|---|---|---|
| `/accuracy` scrolled horizontally on a phone (scrollWidth 483) | Two visually hidden chart data tables (`sr-only`): a table ignores the 1 px width, so it stayed content-wide | Fixed; synthetic fully populated probe measured 718 before and 390 after in both engines |
| Times showed "GMT+5:45" instead of "NPT" | The zone name was mapped only when a zone was passed explicitly | Fixed, unit-tested |
| Live event feed not in time order | Events were shown in arrival order | Fixed: sorted by `timestamp_s`, ties newest arrival first |
| Live page opened after a run finished showed "No events yet" | Events came only from the socket | Fixed: history is loaded from `/api/videos/{id}/events` and merged |

Everything else checked looked right: Overview, Recorded runs and a run page (desktop and
phone, both engines); heads blurred in the annotated video; "5 of 11 joins"; "Unvalidated" tags; chart
band and markers; Live page (both engines, desktop and phone) with blurred preview, legend,
"Raw count N" caption, "Insufficient data", "Not the queue length", dashed alert line and the final
state with the recorded-run link. The screenshots were taken before the four fixes and were not re-taken; the fixes are verified by unit tests (each was written to fail first) and the final e2e run.

**Not done (pending, needs a person):** real Chrome (the Claude in Chrome extension was not
connected) and real Safari. WebKit through Playwright passed, which is not the same
as Safari. No real phone was used.

### Measurement: real-time run of `cad_seq25` (`scripts/measure_realtime.py`)

Source: `outputs/s7_realtime/summary.json` (gitignored). `cad_seq25.mp4`: 617 frames, 30 fps,
20.567 s, device `mps`, camera `cad_seq25`. Each pair is a normal run and a `realtime` run with
the same frame stride.

| | Stride 1 | Stride 3 |
|---|---|---|
| Frames processed (normal / real time) | 617 / 617 | 206 / 206 |
| Normal run wall time | 30.346 s (includes the model load) | 9.163 s |
| Real-time run wall time | 26.309 s | 21.392 s |
| Real-time wall minus clip duration | +5.742 s | +0.825 s |
| Pacing waits / total waited | 0 / 0.000 s | 205 / 8.785 s |
| Real-time wall minus time waited | 26.309 s | 12.607 s |
| Processing fps in the reports (normal / real time) | 25.67 / 25.45 | 25.22 / 18.11 |
| Pacing exercised | **no** | yes |
| Waits CSV, time series, events byte-identical to the normal run | yes (363, 1175, 10422 bytes) | yes (326, 1173, 7723 bytes) |

Reading these honestly:

* **Stride 1 never paced.** The detector processes about 25.5 frames a second, slower than the
  clip's 30, so the pacer never had to wait. The +5.742 s is the processing shortfall, not a
  pacing cost. This pair proves only that outputs are identical with the option on.
* **Stride 3 did pace** (205 waits). The real-time run finished 0.825 s after the clip's
  length. But its non-wait time (12.607 s) is 3.444 s longer than the whole normal run (9.163 s),
  and the wait counter does not explain that gap. The cause was **not measured** (possible
  suspects, unverified: per-frame callback and preview work, MPS variance). Figures vary from run to run
  (an earlier stride-3 run in the same session gave +0.769 s; the 9.316 s waited and
  9.645 s normal wall in that earlier run are superseded by the table).
* One run per pair; no repeats, so no variance estimate. Machine load from other macOS daemons affected an
  earlier e2e attempt (load average 40 to 120), so wall times are indicative.
* Real-time mode can only pace when the machine processes faster than the clip plays; at stride 1 on this M1 it does not (25.5 fps against 30). Stride 2 was not measured.

### Not run / pending

* Real Chrome and real Safari checks (see above).
* The `dashboard` CI job on GitHub.
* Waiting-time accuracy: still not measured; session 3 (the sheets are not filled). The dashboard shows "Not measured yet".

## Sprint 6 (2026-11-09)

Full write-up: [sprint6_report.md](sprint6_report.md).

| Item | Result |
|---|---|
| `pytest -q` | 403 passed (370 at end of Sprint 5); `ruff check .` / `ruff format --check .` clean |
| Error attribution (legacy, from 5 s, IoU 0.5) | reproduces raw bias +0.892 / +0.607 / +1.014 (all / val / test) exactly; per frame +2.10 counted-unmatched, +1.16 grace-period ghosts, −1.65 undetected, −0.77 still in dwell |
| imgsz 960 vs 640, smoothed MAE from 5 s | val 0.910 vs 1.176; test 1.723 vs 1.619 → not adopted |
| Grace 0.75 s + dwell 2.0 s (chosen on val) | val 0.984 vs 1.176; test 1.716 vs 1.619 → not adopted |
| Waiting-time accuracy on real video | still not measured: annotation kits generated, sheets not yet filled |

## Sprint 5 (2026-11-08/09)

Full write-up: [sprint5_implementation_report.md](sprint5_implementation_report.md);
starting state: [sprint5_initial_audit.md](sprint5_initial_audit.md). Headline:
**no new queue-counting option beat the Sprint 4 algorithm on held-out clips, so
the defaults are unchanged.**

### Automated tests

| Run | Result |
|---|---|
| Start of sprint (`dd758b2`) | 203 passed |
| End of sprint, full suite | **370 passed** |
| Real-YOLO integration (`-m integration`) | 11 passed |
| `ruff check .` / `ruff format --check .` | clean / 100 files formatted |

### Method: replaying identical tracks

YOLO11n + ByteTrack ran once per clip (`run_tracking.py --save-mot`, MPS, imgsz
640) on the 7 CAD queueing clips and MOT17-04/09. Every queue-logic variant
replays the same saved tracks (`ai_engine/replay_queue.py`).

* Reproduction check: scoring the new run's time series gave exactly the Sprint 4
  per-clip MAE on all 7 clips.
* Replay gate: replaying with the shipped configs reproduces `raw_queue_count`
  and `queue_length` row for row on all 7 clips (0 differences)
  (`tests/test_replay.py`, skipped when the data is absent, as in CI).
* Split fixed before measuring: val = cad_seq10, 25, 26; test (held out) =
  cad_seq27, 39, 40, 41. No parameter was tuned; variants are one-at-a-time ablations.
* Scoring: Sprint 4 protocol (frames from 5 s, nearest per-second sample within 0.6 s).

### Queue counting (smoothed count, frames from 5 s)

| Variant | All 7: MAE / RMSE / bias / ±1 | Val (122 frames): MAE | Test (285 frames): MAE / bias / ±1 |
|---|---|---|---|
| **legacy (Sprint 4, default)** | **1.486 / 1.911 / +0.875 / 0.595** | **1.176** | **1.619 / +0.960 / 0.537** |
| temporal + legacy stitching | 2.212 / 2.665 / +1.173 / 0.361 | 1.525 | 2.507 / +1.549 / 0.277 |
| temporal + guarded stitching | 2.016 / 2.583 / +1.316 / 0.460 | 1.188 | 2.370 / +1.672 / 0.379 |
| temporal, stitching off | 1.590 / 1.990 / +0.393 / 0.518 | 1.406 | 1.668 / +0.581 / 0.498 |

Raw (unsmoothed) count, test split: legacy MAE 1.751, temporal with stitching
off 1.807. Per-clip MAE from 5 s (smoothed):

| Clip | split | legacy | temporal+legacy stitch | temporal+guarded | temporal, no stitch |
|---|---|---|---|---|---|
| seq10 | val | 1.250 | 1.188 | 1.250 | 1.250 |
| seq25 | val | 0.989 | 1.702 | 1.000 | 1.692 |
| seq26 | val | 1.305 | 1.475 | 1.322 | 1.220 |
| seq27 | test | 1.852 | 3.036 | 3.102 | 1.852 |
| seq39 | test | 1.108 | 1.865 | 1.378 | 1.649 |
| seq40 | test | 2.462 | 2.788 | 1.981 | 2.231 |
| seq41 | test | 0.875 | 1.232 | 1.036 | 0.875 |

Reading: identity per stitched person is only as good as the stitcher (on seq27
the legacy stitcher made 108 links in 60 s on a ~12-person queue, linking tracks
ByteTrack later revived). With stitching off the temporal classifier lowers the
bias (+0.58 against +0.96 on test) but not the MAE (1.668 against 1.619). The
one-at-a-time ablations (margin 0.25, observed fraction 0.6, mean confidence
0.35, scale motion 1.0) all measured worse than legacy on the pooled data.
Small sample: 7 clips from one dataset at an assumed 30 fps.

### Stitching on MOT17 (`evaluate_stitching.py`, motmetrics)

HOTA is **not available** (motmetrics does not implement it; TrackEval is not
installed). Link correctness is judged against MOT17 ground-truth IDs.

| Sequence | Config | IDF1 | ID switches | Frag. | Links | Correct / incorrect / unverifiable | Precision |
|---|---|---|---|---|---|---|---|
| MOT17-04 | no stitching | 0.412 | 47 | 140 | 0 | – | – |
| MOT17-04 | legacy | 0.427 | 44 | 140 | 7 | 3 / 2 / 2 | 0.600 |
| MOT17-04 | guarded | 0.418 | 50 | 140 | 4 | 3 / 1 / 0 | 0.750 |
| MOT17-09 | no stitching | 0.508 | 68 | 78 | 0 | – | – |
| MOT17-09 | legacy | 0.504 | 65 | 78 | 32 | 7 / 13 / 12 | 0.350 |
| MOT17-09 | guarded | 0.521 | 76 | 78 | 16 | 6 / 5 / 5 | 0.545 |

Guarded halves the links on MOT17-09 and raises link precision on both
sequences, but counts more ID switches than no stitching, partly because a track
carries a provisional key for the ~1 s before its link applies (scored under
final keys: 45 on 04 and 65 on 09). The cap of 4 never triggered.

CAD stitch counts, guarded against legacy (seq10, 25, 26, 27, 39, 40, 41):
guarded 3 / 6 / 2 / 63 / 16 / 8 / 9 links, legacy 12 / 19 / 12 / 108 / 28 / 20 / 19.
Most rejections are `distance_too_far`. Revived tracks: 0.

### Lifecycle events

CAD seq25 replay, default config: 11 `queue_join`, 4 `service_start`, 1
`service_complete`, 5 `queue_abandon`, 8 `queue_exit`. Tests: 20 for
`service_complete`; `tests/test_wait_eval_observed.py` for the waiting-time
evaluator (synthetic data only).

### Dataset validation (`scripts/validate_dataset.py`)

| Item | Count |
|---|---|
| Clips in manifest | 27 (the 25-clip plan + 2 extra CAD clips) |
| Acquired | 9 (CAD 7 of 5 planned; MOTChallenge 2 of 5) |
| Planned, not acquired | 18 (MIT Traffic 10, CUHK Square 5, MOTChallenge 3) |
| Video found / readable | 9 / 9 |
| Annotated | 9 (CAD: queue counts only; MOT17: tracks with IDs) |
| Licences verified this sprint | 0 |

### API security tests

48 tests in `tests/test_api_security.py` (content validation, `.part` handling,
queue cap with a 429, oversized `video_id` returning 422 instead of 500, generic
error text). Remaining risks (no authentication, no WebSocket Origin check,
chunked uploads spooled before the size check, soft queue cap) are in
[security_review.md](security_review.md); the API must stay on localhost.

### Benchmark (`scripts/benchmark.py`)

Environment: Apple M1, 8 GB, macOS 27.0.1, Python 3.11.16, torch 2.15.0,
ultralytics 8.4.198, OpenCV 5.0.0.93, yolo11n.pt (sha256 `0ebbc80d4a76`),
confidence 0.35, imgsz 640, ByteTrack `track_buffer` 30. Video cad_seq27
(720×480, 30 fps). No drawing or video writing; 10 warm-up frames excluded.

| Run | Frames | Decode ms | Detection ms | Tracking ms | Queue ms | Total ms (mean / p95) | FPS | CPU % | Peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| MPS, full clip | 1794 | 0.44 | 29.00 | 4.41 | 0.76 | 34.61 / 45.83 | 28.89 | 56.4 | 468 MB |
| CPU, first 600 | 590 | 0.51 | 56.12 | 2.07 | 0.92 | 59.61 / 94.27 | 16.78 | 234.6 | 377 MB |
| Replay, legacy queue logic | 1794 | – | – | – | 0.52 | – | 1934 | – | 81 MB |
| Replay, temporal + guarded | 1794 | – | – | – | 0.99 | – | 1009 | – | 81 MB |

The MPS and CPU runs cover different frame counts, so they are not a like-for-like
comparison; why tracking time is higher on MPS was not investigated.

### Not tested / blocked

* **Waiting-time accuracy on real video: blocked.** No footage with per-person
  ground truth exists (CAD has no person IDs).
* **Browser re-test of the test page:** not done this sprint (the only change is
  the "Raw count (unsmoothed)" line).
* **CUDA:** not tested.
* **Licences:** none verified this sprint (MOT17 recorded as CC BY-NC-SA 3.0 in
  Sprint 1, not re-checked; CAD, MIT Traffic and CUHK Square unknown).
* HOTA is not available; multiple service counters are not implemented.

## Sprint 4 (2026-11-04)

### Automated tests

| Run | Result |
|---|---|
| Baseline at the start of Sprint 4 (`main` at `a83ee70`) | **152 passed** |
| Final, full suite (`pytest -q`, at `115f5cb`) | **203 passed**, 0 failed, 0 skipped, no warnings |
| Final, torch-free venv (`pytest -m "not integration"`, API deps but no torch/ultralytics) | **192 passed**, 11 deselected |
| `ruff check .` / `ruff format --check .` | clean |

### Queue-length accuracy on the Collective Activity Dataset (first real queue result)

Data: the 7 sequences labelled "queueing" (action 4) in the Collective Activity
Dataset (Choi, Shahid & Savarese, 2009), converted with
`ai_engine/tools/prepare_cad.py` at an **assumed 30 fps** (the dataset does not
state its frame rate). Ground truth = number of people labelled queueing in
each annotated frame (every 10th frame). Prediction = the per-second time
series from `run_tracking.py` (MPS, `--blur-heads`) with
`configs/cameras/cad_seqNN.yaml`, matched to the nearest second
(`evaluate_queue.py`, tolerance 0.6 s).

Method fixed before running: zones drawn by eye on frames of each clip, default
membership rules (dwell 3 s, speed 0.5 heights/s, grace 1.5 s, 2 s median), no
tuning on these clips. Scored twice: on every annotated frame, and from 5 s on
(the first 3 s dwell + 2 s smoothing cannot count anyone, while the labels
already show a full queue at frame 1).

Smoothed `queue_length`:

| Clip | Labelled frames (≥ 5 s) | Mean labelled queue | MAE all / ≥ 5 s | RMSE ≥ 5 s | Bias ≥ 5 s | Within ±1 ≥ 5 s |
|---|---|---|---|---|---|---|
| seq27 (garden path, 60 s) | 166 | 8.5 | 2.32 / 1.85 | 2.24 | +1.47 | 0.43 |
| seq25 (café counter) | 47 | 5.2 | 2.02 / 0.99 | 1.24 | −0.05 | 0.79 |
| seq26 (café, handheld) | 59 | 6.7 | 2.28 / 1.31 | 1.65 | +1.10 | 0.71 |
| seq40 (pier) | 26 | 7.8 | 3.84 / 2.46 | 2.98 | −1.08 | 0.46 |
| seq41 (lobby) | 56 | 8.8 | 2.23 / 0.88 | 1.20 | +0.41 | 0.79 |
| seq10 (cafeteria, handheld) | 16 | 5.1 | 2.90 / 1.25 | 1.58 | +1.25 | 0.62 |
| seq39 (kiosk, moving camera) | 37 | 5.8 | 2.21 / 1.11 | 1.35 | +0.95 | 0.70 |
| **All 7, pooled** | **407** | | **2.41 / 1.49** | **1.91** | **+0.87** | **0.59** |

Raw (unsmoothed) count, pooled: MAE 2.20 all / 1.53 from 5 s, within ±1 0.64.

Reading the numbers:

* From 5 s on, the count is off by about 1.5 people on queues of 5–9, and within
  one person on 59 % of labelled frames. It tends to **over-count** (+0.87):
  the zones are drawn as bands with margin for the handheld cameras, so people
  standing near the queue (crossing, waiting, talking) are counted too.
* The worst clip is seq40 (pier), where it under-counts: people overlap heavily
  in a side view, so the detector misses some.
* Limits: handheld cameras move the scene under a fixed zone; the dataset's
  "queueing" label is a human judgement, not our zone rule; 30 fps is assumed;
  no person IDs, so **waiting-time accuracy is still unmeasured**.

### API end-to-end (real model, MPS)

`python -m api` with a scratch data directory, upload of `cad_seq25.mp4` with
camera `cad_seq25` via `curl`, WebSocket captured with a Python client:

* 617 frames processed in 30.1 s; status `completed`, percent 100.
* WebSocket: 1 snapshot, 21 progress, 41 event and 1 final status message,
  then the server closed the socket.
* Waits CSV and the per-second time series are byte-identical (apart from the
  file name) to a CLI run of the same clip; SQLite held 41 events, 21
  time-series rows and 9 waits.
* `GET /api/videos/1/video` with `Range: bytes=0-999` → 206, 1000 bytes.
* After a server restart the run was still listed as `completed`.

### Browser check of the test page (2026-11-04)

The Chrome extension used earlier could not load the local server, so the page
was checked with **Playwright 1.58.0 driving headless Chromium 145.0.7632.6**
against `python -m api` (scratch data directory, real YOLO11n on MPS). Upload:
a 10 s cut of `cad_seq25.mp4` (302 frames) with camera `cad_seq25`.
Screenshots of each state were saved to `outputs/browser_check/` (gitignored)
and inspected.

| Step | Result | Observed |
|---|---|---|
| Page loads; camera dropdown lists the 7 `cad_seq*` configs | PASS | cad_seq10, 25, 26, 27, 39, 40, 41 (plus example_queue) |
| Upload starts a run, head blur on by default | PASS | |
| Progress bar and live numbers update during processing | PASS | 14 distinct progress texts; people in frame 5–8; service rate "insufficient data", predicted wait "unknown" (clip shorter than `min_history_seconds`) |
| Run completes | PASS | "Completed 100% (302 / 302 frames)" |
| Annotated video plays | PASS | 2.00 s played after 2 s; heads blurred in the frames |
| Seeking uses range requests | PASS | seek to 7.5 s landed at 7.50 s; video responses 200 and 206; no media error |
| Reopen from "Previous runs" after a page reload | PASS | status completed; summary (4 completed waits, mean 5 s, abandonment 20 %) and 5-row waits table shown |
| No horizontal scroll at 375 px width | PASS | |
| No console errors | PASS | |

One script bug was found and fixed during the check (the script tested the
`hidden` attribute, the page uses a `hidden` CSS class); no page bug was found.
Cosmetic only: "Previous runs" shows raw ISO timestamps. Not checked: Safari,
Firefox, real phones.

## Sprint 3 (2026-11-02)

### Automated tests

| Run | Result |
|---|---|
| Baseline at the start of Sprint 3 (`main` at `4e3ee7a`) | **87 passed**, 0 failed, 0 skipped |
| Final, full suite (`pytest -q`) | **152 passed**, 0 failed, 0 skipped |
| Final, torch-free venv (`pytest -m "not integration"`, no torch/ultralytics installed) | **142 passed**, 10 deselected |
| `ruff check .` / `ruff format --check .` | clean |

### Waiting-time accuracy: PENDING (no labelled queue footage)

There is no real queue footage with ground truth yet, so waiting-time accuracy
(MAE, median error, missed/extra people), prediction error and the effect of
ID stitching on accuracy **have not been measured**. `evaluate_waiting.py` is
implemented and tested on synthetic data only (`tests/test_wait_eval.py`).
See `datasets/README.md` for how to label footage.

### Smoke runs on MOT17 (street footage, NOT a queue: mechanics only)

Command (MPS, example camera config with a service line at y = 0.9, head blur,
H.264):

```bash
python ai_engine/run_tracking.py --video datasets/raw/<clip>.mp4 --device mps \
  --camera-config configs/cameras/example_queue.yaml --blur-heads \
  --output outputs/s3_<clip>_stitch_on      # and --no-stitching for _stitch_off
```

| Run | Stitches | queue_join | service_start | queue_abandon | Still waiting at end | Inference FPS |
|---|---|---|---|---|---|---|
| MOT17-09, stitching on | 32 | 10 | 2 | 6 | 2 | 30.88 |
| MOT17-09, stitching off | 0 | 11 | 1 | 9 | 1 | 31.85 |
| MOT17-04, stitching on | 7 | 19 | 4 | 7 | 8 | 31.71 |
| MOT17-04, stitching off | 0 | 21 | 4 | 9 | 8 | 31.79 |

What this shows, and what it does not:

* All seven event types, the waits CSV, the alerts CSV and `events.jsonl` are
  produced from real tracking output; codec h264 in all runs. Queue-stage cost
  is small: inference ≈ 31 FPS, the same as Sprint 2 without stitching/alerts.
* Stitching merges fragmented IDs (fewer joins, fewer abandons). On MOT17-09
  it also produced one person with 8 chained track IDs over 11 s, which in a
  dense street crowd is almost certainly a wrong merge. Whether stitching
  helps accuracy is **unknown** until it is measured on queue footage.
* The "waits" and "abandons" here are people walking across a street, not
  queuing; the numbers are not accuracy results.
* The service rate stayed `insufficient_data` (and predictions `unknown`) for
  the whole of both clips, as designed: they are 17.5 s and 35 s long, shorter
  than `min_history_seconds` = 60. No congestion alert fired (max queue length
  7 ≤ threshold 8).

### Exercise run: prediction and alert paths

To exercise the paths that the clip length prevents, MOT17-04 was run with a
test copy of the config (`min_history_seconds` 10, `min_services` 1,
`max_queue_length` 4, `min_duration_seconds` 3; output `outputs/s3_exercise`):

* `congestion_start` at 10.9 s (condition since 7.9 s, queue length 6) and
  `congestion_end` at 22.0 s (condition cleared since 19.0 s).
* From 18 s the service rate became `ok` (e.g. 10.0/min at 18 s, predicted wait
  42.0 s, range 41.1–120.9 s; 6.43/min at 28 s, 37.3 s, range 23.5–69.1 s).
* The overlay showed the service line, the predicted wait with its range and
  the congestion banner; the banner overlapped the panel title, which was then
  fixed by drawing it at the opposite edge from the panel.
* This run averaged 19.16 inference FPS against ≈31 for the identical clip
  minutes earlier; the machine was under other load at the time, so it is not
  a benchmark.

### Not tested / pending (Sprint 3)

* Waiting-time, prediction and throughput accuracy on real labelled footage.
* Effect of stitching on accuracy (only stitch counts are reported above).
* Interactive (mouse) mode of `draw_zone.py --target service_line|service_zone`;
  the headless `--points` mode is tested.
* Webcam: still covered by a mocked capture only.

---

# Sprint 2

## Execution environment (Sprint 2, 2026-11-02)

| Item | Value |
|---|---|
| Machine | MacBook Air, Apple M1, 8 GB RAM, macOS 27.0.1 (arm64) |
| Python | 3.11.16 (`.venv/`) |
| PyTorch | 2.15.0. MPS available; no CUDA |
| Ultralytics | 8.4.198 (ByteTrack defaults: `track_high_thresh` 0.25, `track_low_thresh` 0.1, `new_track_thresh` 0.25, `track_buffer` 30) |
| OpenCV / NumPy / PyYAML | 5.0.0.93 / 2.4.6 / 6.0.3 |
| Evaluation | motmetrics 1.4.0, scipy 1.17.1 |
| Dev | pytest 9.2.0, ruff 0.17.3 |
| CoreML | coremltools 9.0. Export used a separate venv with torch 2.7.0, numpy 1.26.4 and ultralytics 8.4.198 |
| ffmpeg | Homebrew build (`/opt/homebrew/bin/ffmpeg`) |

## Automated tests

| Run | Result |
|---|---|
| Baseline at the start of Sprint 2 (`main` at `5bc11fb`) | **32 passed**, 0 failed, 0 skipped |
| Final, full suite (`pytest -v`, real YOLO11n) | **87 passed**, 0 failed, 0 skipped |
| Final, torch-free (`pytest -m "not integration"` in a clean venv with only opencv-python-headless, numpy, PyYAML and requirements-dev.txt; `import torch` fails there) | **77 passed**, 10 deselected |
| `ruff check .` and `ruff format --check .` | pass (in both environments) |

New test files: `test_queue.py`, `test_evaluation.py`, `test_pipeline.py`.
Existing tests changed on purpose:

* `test_tracker.py`: the fake detector now has `confidence`, and mocked
  tracker tests pass a ByteTrack config dict (the tracker now builds a
  resolved YAML). New tests check that `track()` receives `conf=0.1` and the
  resolved YAML, and that a low-score box with a track ID is still returned.
* `test_metrics.py`: `process_video()` now takes `RunOptions`, and the CLI
  test checks the new `configuration.tracker` fields.
* `conftest.py`: the fake Ultralytics results use a NumPy-backed fake tensor
  instead of torch, so the unit tests run without torch.

Mocked tests (fake detector, tracker or camera) are named `*mocked*` or use
`Fake*` classes and are documented as such. All detection-quality and tracking
assertions use the real model.

## Step 1: ByteTrack fix (before vs after)

Before the fix, the tracker was called with `conf=0.35`. ByteTrack's second
association stage only uses boxes scoring between 0.1 and 0.25, so it never
received any. After the fix, detections down to 0.1 reach ByteTrack, and
`--confidence 0.35` is the minimum score needed to *start* a track. "Before"
means `--tracker-input-confidence 0.35`. That flag reproduces Sprint 1
exactly: the before runs gave the same unique IDs (75 / 108) and the same
average people per frame (7.76 / 13.28) as the unmodified Sprint 1 code.

MPS, imgsz 640, stride 1, all 525 / 1050 frames. Scored against MOT17 ground
truth (see Step 2).

| Sequence | Setting | Unique IDs | Avg people/frame | MOTA | IDF1 | ID switches | Fragmentations | Precision | Recall | False positives | Misses |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MOT17-09 | before | 75 | 7.76 | **0.543** | **0.554** | 75 | 121 | 0.888 | 0.638 | 427 | 1929 |
| MOT17-09 | after | 74 | 8.88 | 0.520 | 0.508 | 68 | 78 | 0.832 | 0.668 | 718 | 1770 |
| MOT17-04 | before | 108 | 13.28 | 0.248 | 0.351 | 61 | 374 | 0.925 | 0.271 | 1050 | 34665 |
| MOT17-04 | after | 87 | 16.85 | **0.313** | **0.412** | 47 | 140 | 0.922 | 0.343 | 1388 | 31251 |

**The effect differs between the two sequences.** The fix sharply reduces
track fragmentation on both sequences (−36% on MOT17-09, −63% on MOT17-04),
along with ID switches. It also raises recall. On the crowded MOT17-04 scene
it clearly improves MOTA (+0.065) and IDF1 (+0.061). On MOT17-09, low-score
boxes keep some false tracks alive (+291 false positives), so MOTA (−0.024)
and IDF1 (−0.045) both drop. The fix is kept as the default because the
sprint's goal is stable IDs through occlusion, which queue membership and
waiting time depend on. `--tracker-input-confidence 0.35` restores the old
behaviour if real queue footage shows it is better.

Speed on MPS (same session, runs one at a time): MOT17-09 before 33.35 /
27.75, after 31.84 / 26.29; MOT17-04 before 30.41 / 25.72, after 27.07 /
22.87 (inference / processing FPS). After the fix there are more boxes per
frame for ByteTrack and drawing. FPS varies by several FPS between sessions on
this fanless M1 (Sprint 1 measured 25.46 inference FPS for the "before"
setting on MOT17-09), so compare runs only within this table.

## Step 2: MOT17 tracking evaluation

* Ground truth: `MOT17Labels.zip` from motchallenge.net (labels only,
  SHA-256 `0aa79322…dc82b2b9`). Only `train/MOT17-{04,09}-FRCNN/{seqinfo.ini, gt/gt.txt}`
  were extracted. Licence CC BY-NC-SA 3.0.
* Frame counts match `seqLength` (525 and 1050). Ground-truth boxes were
  scaled by 960/1920 = 540/1080 = 0.5.
* Scored: class 1 with consider flag 1. Hypotheses matched to distractor
  classes 2, 7, 8 and 12 were removed, as in the official devkit. IoU 0.5.
* **Alignment check:** the tracks were shifted by −2…+2 frames and re-scored.
  Offset 0 gave the best MOTA on MOT17-09 (0.520, against 0.516 at −1 and
  0.511 at +1) and tied for best on MOT17-04 (0.3127 at −1 and 0). The
  preview videos are frame-aligned with the ground truth.
* These are evaluation numbers on 960×540 preview videos with YOLO11n, not
  official MOTChallenge results.

Optional larger inference size (MPS, `--imgsz 960`, after the fix):

| Sequence | MOTA | IDF1 | ID switches | Precision | Recall | Inference FPS | Processing FPS |
|---|---|---|---|---|---|---|---|
| MOT17-09 | 0.567 | 0.498 | 61 | 0.856 | 0.696 | 28.89 | 24.53 |
| MOT17-04 | **0.446** | **0.519** | 93 | 0.907 | 0.498 | 18.76 | 16.08 |

imgsz 960 is the largest accuracy gain measured in this sprint, especially
on MOT17-04's small, distant people (MOTA 0.313 → 0.446). It costs about 30%
of the FPS on that scene.

## Step 7: benchmarks (MOT17-09, after the fix, imgsz 640)

The runs were made one after another in one session, with `--save-mot --no-h264`.
"Video throughput" is source frames covered per second of processing
(processed frames × stride ÷ processing time). At stride 3 the tracker sees
10 frames per second of video.

| Backend | Stride | Frames processed | Inference FPS | Processing FPS | Video throughput (frames/s) | MOTA | IDF1 | ID switches |
|---|---|---|---|---|---|---|---|---|
| CPU | 1 | 525 | 24.66 | 21.85 | 21.9 | 0.520 | 0.508 | 68 |
| CPU | 3 | 175 | 23.58 | 20.43 | 61.3 | 0.491 | 0.542 | 35 |
| MPS | 1 | 525 | 30.30 | 25.19 | 25.2 | 0.520 | 0.508 | 68 |
| MPS | 3 | 175 | 32.18 | 26.17 | **78.5** | 0.491 | 0.542 | 35 |
| CoreML | 1 | 525 | 20.84 | 18.85 | 18.8 | 0.516 | 0.473 | 68 |
| CoreML | 3 | 175 | 21.10 | 18.75 | 56.3 | 0.505 | 0.550 | 41 |

Findings:

* **MPS is fastest. At stride 3 it processes 30 fps video about 2.6× faster
  than real time** (78.5 source frames/s). At stride 1 nothing reaches 30 fps
  real time on this M1.
* CPU and MPS give **identical tracking results**; only speed differs.
* **CoreML was slower than MPS here** (20.8 against 30.3 inference FPS), and
  its results differ slightly because of numerical differences. Running
  inference through coremltools' Python API adds overhead, and Ultralytics'
  pre- and post-processing stays on the CPU. CoreML is not recommended over
  MPS for this setup. The FP16 (`--half`) export and Neural-Engine-only
  compute units were not tried.
* At stride 3, track continuity scores higher (IDF1 up, ID switches halved:
  there are fewer chances to switch). MOTA drops slightly because recall falls
  at the lower frame rate. These scores cover only the processed frames (1, 4,
  7, …), so they are not directly comparable with the stride-1 scores.
* CoreML export from the main environment (torch 2.15.0) **failed** inside
  coremltools 9.0, which is tested only up to torch 2.7.0
  (`TypeError: only 0-dimensional arrays can be converted to Python scalars`).
  Export succeeded in 2.6 s from a separate torch 2.7.0 venv, and the
  `.mlpackage` runs in the main environment (`requirements-coreml.txt`).

## Steps 3–5, 8 and 9: queue smoke test (NOT a queue scene, NOT an accuracy result)

MOT17-09 shows people walking along a shopping street, not queuing. This run
only checks that the queue pipeline works end to end with the example zone
`configs/cameras/example_queue.yaml`. Command: `--device mps --camera-config
configs/cameras/example_queue.yaml --blur-heads --panel-position top-right`
(H.264 on).

| Output | Result |
|---|---|
| `outputs/queue_smoke/videos/mot17_09_tracked.mp4` | H.264, 525 frames; zone, orange/yellow/green status colours, foot dots, head blur and a top-right panel showing "Queue length" separately from "People in frame" (checked visually on an extracted frame) |
| `outputs/queue_smoke/timeseries/mot17_09_queue.csv` | 18 rows (one per second of the 17.5 s video) |
| `outputs/queue_smoke/events/mot17_09_zone_events.csv` | 11 `zone_enter`, 10 `zone_exit` |
| `outputs/queue_smoke/metrics/mot17_09_metrics.json` | `queue` section: max smoothed length 3, average 1.466, max raw count 4, 1 member still in queue at the end; codec `h264` |
| Performance | 31.07 inference FPS, 23.49 processing FPS (with queue logic, blur and drawing) |

**What the smoke test revealed:** several walkers were confirmed as
"queuing". In the frame checked, a person walking *towards* the camera was
counted, because their foot point moves little on screen relative to their
large box height. The dwell-plus-speed filter has trouble with motion along
the camera axis. This has to be tuned (or replaced with a better motion
measure, such as foot-point motion in a ground-plane homography) on real
queue footage.

**Queue-length accuracy is PENDING.** There is no real queue footage in
`datasets/raw/`. `make_annotation_kit.py` and `evaluate_queue.py` are
implemented and unit-tested with synthetic CSVs only.

## Not tested / pending

| Item | Status |
|---|---|
| Queue-length accuracy (MAE, RMSE, exact and ±1 rates) | Pending real, labelled queue footage |
| Webcam input (`--video 0`) | Tested only with a mocked `cv2.VideoCapture`; **untested on a real camera** |
| Interactive `draw_zone.py` mouse mode | Not run (no interactive display in the test session); non-interactive `--points` mode is tested |
| `--show` live window | Exercised manually in Sprint 1 (quit with `q` worked); not part of automated tests |
| CI workflow | Written; the torch-free job was reproduced locally in a clean venv. GitHub Actions has not run it yet (it runs when the PR is opened). The integration job is `continue-on-error` because it downloads weights |
| CUDA | No NVIDIA hardware available |

---

## Sprint 1 report (2026-11-02, unchanged results)

Sprint 1 environment: same machine and the same core versions as above.
pandas 3.1.0 was installed then, and was removed in Sprint 2.

* `pytest -v`: 32 passed, 0 failed, 0 skipped.
* End-to-end runs (confidence 0.35 passed straight into the tracker,
  imgsz 640). The `--show` run on MOT17-09, which was stopped with `q` at
  frame 395, reached 30.75 / 25.66 FPS on MPS:

| Video | Device | Frames | Avg inference FPS | Avg processing FPS | Max people/frame | Track IDs |
|---|---|---|---|---|---|---|
| MOT17-09 | MPS | 525 | 25.46 | 20.90 | 12 | 75 |
| MOT17-04 | MPS | 1050 | 26.22 | 21.93 | 21 | 108 |
| MOT17-09 | CPU | 525 | 22.82 | 20.19 | 12 | 75 |

Sprint 1 limitations that Sprint 2 addressed: the mp4v-only output (H.264 is
now the default), the overlay covering the scene (the panel is now movable),
and unmeasured accuracy (MOT17 evaluation added). Still open: small or distant
people are missed at imgsz 640 (imgsz 960 helps, see above), and track IDs
still fragment.
