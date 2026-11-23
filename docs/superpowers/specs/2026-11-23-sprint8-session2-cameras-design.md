# Sprint 8 session 2: camera engine and API — design

Status: draft for approval. Branch `sprint-8/cameras` from `main` `aba0024`.

**Source of this design.** The team's full session 2 text was not available. This design is **derived from the
one-line summary** in [2026-11-13-sprint8-brief.md](2026-11-13-sprint8-brief.md) ("camera engine and API:
`kind='live'` runs, worker processes, schema v2, `ai_engine/live/` capture and pacing, `worker/` package,
ingest/supervisor/routes, tests and measurements, recording off by default") and a design conversation with the
user on 2026-11-23. It is **not the team's text**. The brief's shared rules 1 to 8 apply in full.

## Intent

Today QueueVision processes a recorded upload once. Session 2 lets it **watch a camera**. The camera feeds the same
queue engine, and the results are stored in the same tables as a new kind of run.

- **Now: a demo.** One laptop webcam points at a queue, and the presenter starts and stops it with a command or an
  API call. The existing dashboard Live page (`/live?id=N`) shows the numbers updating. When the camera stops, the
  session stays as a recorded run that can be opened like any other.
- **Later: a pilot.** Real RTSP cameras run unattended for days. Session 2 must not block the pilot, but does not
  build for it.

The dashboard camera pages (camera list, Start/Stop buttons, settings) are session 3. Session 2 is engine, worker and
API only.

## Goals

1. Start and stop a live session for one camera from the API (or from a command that calls it).
2. Support three sources behind one interface: webcam (device index), RTSP (URL from an environment variable), and a
   recorded clip looped at its own frame rate. The looped clip is the stand-in camera for tests, CI and
   measurements.
3. Run each camera in its own OS process (`python -m worker`), started and supervised by the API. A camera crash or
   hang must not take the API down.
4. Store live results while the run is going: events, waits, alerts, per-second time series and progress, in the
   existing tables and through the existing `db` functions.
5. The existing Live page and `/ws/videos/{id}` work for a live run without dashboard changes. A finished live run
   opens on the existing run page. The one dashboard change this session makes is there: when `kind` is `live`,
   `RunDetail.tsx` shows "Live session — no video recorded" in place of the video player, and the options line
   doesn't claim a head-blur setting for a video that doesn't exist. This was the user's decision, and it covers
   about 10 lines plus a Vitest test. The "Time series (CSV)" link keeps working because the worker writes that CSV
   (section 2).
6. Record no video. Show only privacy-rendered previews: `frame` by default, `boxes` allowed, never `none`.
7. Tests and measurements as described below. Every number in the report comes from a command that was run.

## Non-goals (this session)

- Dashboard camera pages and controls (session 3), and any dashboard change beyond the run-page note in goal 5. Login, roles and CSRF (session 4). Own-footage intake (session 5).
- More than one live camera at a time.
- Pilot-grade operation: running for days, reconnect with backoff, restarting a dead worker automatically, metrics
  export.
- Any way to record video from a live session. "Off by default" is met by not building recording at all. Whether
  and how to offer it is a pilot question.
- Tuning queue logic, the detector or the tracker.

## Anti-goals (failure even if it works)

- An RTSP URL, or any part of it, appears in the database, a log line, an API response, the dashboard, a process
  argument list (`ps`), or an error message.
- Any frame or video of people written to disk by a live session. This includes debug dumps and temporary files.
- A preview image that is served without privacy applied.
- Any existing contract changed (shared rule 3): `run_tracking.py` CLI and outputs, every `/api/*` route and response
  field, `/ws/videos/{id}` message types and fields, event schema 1.0, queue-logic defaults.
- A worker process left running after the API stops or the run ends.
- Claiming the webcam works without a person having run it with the camera permission granted.

## Constraints

- MacBook Air M1, 8 GB RAM. One model job at a time: a live camera and an upload never run the model concurrently.
- Server on `127.0.0.1` only (no session 4). Ingest routes accept loopback clients only.
- Dependency direction: `api → ai_engine`, `worker → ai_engine`. `ai_engine` imports neither. `worker` does not
  import `api`; it talks to the API over HTTP only. The dashboard is unchanged.
- No new Python dependency is expected. The worker's HTTP client is the standard library (`urllib.request`). If one
  turns out to be needed, it is pinned and recorded in the report.
- Unit tests are torch-free (fake source, fake clock, fake tracker, fake HTTP, fake or tiny real subprocess).
  Real-model tests are marked `integration`. Ruff clean.
- Collective Activity Dataset (CAD) material: never committed or published. Measurement outputs go under a
  gitignored `outputs/s11_*` folder.
- macOS camera permission belongs to the app that launched the process (Terminal or the IDE). The worker inherits it
  from the API's launcher. Granting it needs a person.

## Approaches considered

| | Approach | Trade-off | Outcome |
|---|---|---|---|
| A | A camera thread inside the API, next to the upload worker in `api/jobs.py` | Least code and quickest demo; but an OpenCV or driver crash or hang takes the API down, restarting a camera means restarting the server, the pilot would need a rewrite, and it departs from the brief's summary | Not chosen |
| **B** | **A separate worker process per camera, started and supervised by the API; the worker sends results to the API over a localhost ingest route; the API stays the only database writer** | Crash isolation; matches the brief's architecture; the pilot extends it rather than rewriting it. Costs: process start/stop, ingest route and token, heartbeats, and about 400 MB more memory for a second model copy (Peak RSS 400.6 MB measured for YOLO11n + ByteTrack, fix pass session 2) | **Chosen by the user** |
| C | Worker processes that write SQLite themselves, with no ingest route | Several writers to one SQLite database (locking), and the API would have to poll the database to feed the live WebSocket | Dropped |

## Decided with the user

- Purpose: a demo with one webcam first, a pilot later.
- Start and stop by command or API call. The dashboard waits for session 3.
- Approach B: a separate worker process.
- A finished live run gets one small dashboard change on the run page (goal 5), rather than a broken player.

## Design

### 1. Camera live settings (config)

Each camera YAML in `configs/cameras/` gains an **optional** `live:` section. Existing files and fields keep their
meaning, and a camera without `live:` cannot be started live (`409`).

```yaml
live:
  source: webcam            # webcam | rtsp | clip
  webcam_index: 0           # source: webcam
  rtsp_url_env: QUEUEVISION_RTSP_FRONT   # source: rtsp — the NAME of an env var, never the URL
  clip: datasets/raw/cad_seq25.mp4       # source: clip — looped at its own fps
  process_fps: 10           # frames processed per second (default 10, i.e. stride 3 of 30 fps)
  preview_privacy_mode: frame            # frame | boxes
  max_session_s: 7200       # auto-stop after this many seconds (default 2 h)
```

**The webcam demo camera** (my call): session 2 adds `configs/cameras/webcam.yaml` with `live: source: webcam` and a
**placeholder** queue zone and service line. Before the demo, a person points the webcam at the real queue and
draws the zone and service point with the existing `ai_engine/tools/draw_zone.py`. If that tool cannot take a still
from a webcam yet (for example `--video 0`), adding that is in scope. The step is documented in `docs/demo.md` and
listed as pending for a person. The CAD camera configs are not changed, except that `cad_seq25.yaml` gains a
`live: source: clip` section for the stand-in camera.

The parsing and validation live in `ai_engine` (camera config). `GET /api/cameras` gains optional fields
`live_source` (`webcam` | `rtsp` | `clip` | null) and `live_ready` (bool; for RTSP, whether the named env var is set).
It never reports the URL or the env var's value. For a clip, the path is reported. These are local files, not
secrets.

### 2. `ai_engine/live/` — capture and the live loop

- **Sources** (`capture.py`): one small interface, implemented by `WebcamSource`, `RtspSource` and
  `LoopedClipSource`. Each returns the newest frame and a monotonic capture time. A capture thread keeps **only the
  latest frame**, so processing never falls behind a growing buffer. `LoopedClipSource` plays the clip at its own
  fps against the clock and restarts at the end, and time keeps increasing across loops. A read failure ends the
  run with a clear reason (`camera_unavailable`, `stream_ended`). This session does not reconnect.
- **The live loop** (`run_live`): reuses `FrameProcessor` for tracking, queue logic and drawing. `RunWriters` is not
  used, so there is no video writer. The loop takes the newest frame at most `process_fps` times per second.
  `timestamp_s` is the seconds since the session started (monotonic). Frames are numbered as processed frames.
  `frames_total` is `None`, so progress `percent` is `null`, which the message format already allows. Callbacks
  (`on_frame`, `should_stop`) follow `process_video`'s shape, so the API-side message builders (`progress_message`,
  `event_message`, `_timeseries_row`) apply unchanged. At the end it returns a report in the same shape as an
  upload's metrics report. It adds `kind: "live"`, `source_kind`, `stopped_by` (`operator` | `max_session` |
  `error`) and the duration. It writes no images or video. It **does** write the time-series CSV, which has the same
  columns as an upload's, and records it in `report.outputs.timeseries`, so the run page's CSV link works. Whether
  it also writes a metrics JSON is left to the builder; if it does, the JSON holds no images.
- **Looped clip restarts** (my call): the tracker and queue state are **not** reset at a restart, so the run stays
  one continuous session. Each restart shows up as people jumping back to where they started, which causes false
  leaves and joins. These are accepted, and each loop boundary (loop number, `timestamp_s`) is recorded in the
  report. Queue numbers from a looped clip are **never** quoted as accuracy figures. They show that the system runs,
  not that it is right. Changing queue logic to handle restarts would be tuning, which shared rule 3 rules out.
- Pacing waits between frames. The known cost is about 8 ms per detector update (fix pass session 3). It is measured,
  not fixed, in this session.

### 3. `worker/` package — one process per camera

`python -m worker --video-id N --camera <id> --api http://127.0.0.1:<port>`:

- It reads the **ingest token** from the environment variable `QUEUEVISION_WORKER_TOKEN`, never from argv. It reads
  an RTSP URL only from the env var named in the camera config.
- It loads the model (same settings as the API: `QUEUEVISION_MODEL`, `QUEUEVISION_DEVICE`), opens the source and runs
  `run_live`.
- It sends to the API (section 4) about once per second. Each batch carries the new events, time-series rows,
  `frames_processed` and the latest `progress` message. Previews are privacy-rendered JPEGs at a rate the builder
  sets, about the current preview rate. A heartbeat goes every 2 s, or the batch counts as one. At the end it sends a
  final message with the report.
- It **exits by itself** if the API is unreachable for 15 s. This is what prevents orphaned workers when the API
  dies.
- It stops cleanly on SIGTERM: it finishes the current frame, sends the final message and exits 0.
- Logs never include the source URL. Errors from OpenCV that could echo a URL are replaced by a fixed reason string.

### 4. API — supervisor, ingest and routes

**Supervisor** (`api/live.py` or `api/live/`):

- `start(camera_id)`:
  - It refuses with `409` if the camera has no `live:` section, a live session is already running (one at a time),
    or an upload is being processed. For RTSP it refuses if the env var is unset.
  - Otherwise it creates the run row (`kind='live'`, status `processing`), makes a random per-run token, and spawns
    the worker with `subprocess.Popen` (token in the child's env, no shell).
- While a live session runs, the upload worker (`JobManager`) **does not start new uploads**. They stay queued and
  start when the session ends. Uploads can still be submitted.
- A watchdog marks the run `failed` with a reason (`worker_exited`, `no_heartbeat`) if the worker exits without a
  final message or sends no heartbeat for 10 s. It then kills the process if it is still alive. There is no automatic
  restart.
- `stop(video_id)`: SIGTERM, wait up to 15 s for the final message, then SIGKILL. A stop by the operator or by
  `max_session_s` ends as **`completed`**, because it is the normal end of a live session. The report records
  `stopped_by`.
- API shutdown stops every worker before exit. API startup uses the existing `mark_interrupted` to mark leftover live
  runs failed. A leftover worker exits by itself (section 3).

**Ingest routes** (internal; loopback only; header `X-Worker-Token` compared with `hmac.compare_digest`; only for a
run that is `processing` and `kind='live'`):

- `POST /api/ingest/{id}/batch`: events go through `db.add_events`, which already fills `waits` and `alerts`. Rows go
  through `db.add_timeseries`, then `set_progress`, all in one transaction. The API then publishes `event` and
  `progress` messages to WebSocket subscribers exactly as `JobManager` does.
- `POST /api/ingest/{id}/preview` (JPEG body; size-limited): stored in the existing `PreviewStore` as
  already-private. The worker always renders the preview in a privacy mode, and the API only takes previews from the
  run's authenticated worker.
- `POST /api/ingest/{id}/heartbeat` and `POST /api/ingest/{id}/final` (the report; sets the final status and publishes
  `status`).

**Public routes** (additive):

- `POST /api/cameras/{id}/live` starts a session: `201` with the run record (`video_id`). Errors are `404` for an
  unknown camera and `409` with a reason otherwise.
- `POST /api/videos/{id}/stop` stops a live run. The existing `POST /api/videos/{id}/cancel` on a live run behaves
  as stop, so the Live page's Cancel button works. On an upload run, cancel is unchanged.
- `GET /api/live` lists running sessions: video_id, camera, `source_kind`, started_at, seconds since the last
  heartbeat, processed fps. There is never a URL.
- Run records gain optional `kind` (`upload` | `live`) and `source_kind`. Upload runs return `kind: "upload"`. All
  existing fields are unchanged. `openapi.json` and `dashboard/lib/api-types.ts` are regenerated for the additive
  fields only.
- A small command for the demo, `python -m worker.ctl start|stop|status <camera>` (name left to the builder), calls
  these routes. The presenter does not need curl.

### 5. Schema v3

This is one migration (`_migrate_2_to_3`), appended to `MIGRATIONS`. It uses the existing backup-before-migrate
step.

- `videos.kind TEXT NOT NULL DEFAULT 'upload'`.
- `videos.source_kind TEXT` (null for uploads).

There is no column for a URL or a token. The brief says "schema v2", but v2 has already been used for alert
acknowledgements, so this is v3.

### 6. Privacy and security

- No video, frame or JPEG of a live session is written to disk. Previews live only in memory (`PreviewStore`).
- The default preview is `frame`, `boxes` is allowed, and `none` is refused when the config is loaded.
- RTSP URL: it is read in the worker from the named env var and nowhere else. A test captures logs, API responses,
  the database and the worker's argv, and checks that a known fake URL (`rtsp://user:secret@…`) appears in none of
  them.
- The ingest token is per run, held in the child's env, and never logged or returned.
- `docs/privacy.md` and `docs/security_review.md` gain the live rows: what is held in memory, what is stored, the
  token, the loopback-only ingest, and the remaining risks.

## Tests (what must be shown)

- Unit tests (torch-free):
  - Sources: the looped clip keeps time increasing across loops; only the latest frame is kept; read failure gives
    a reason.
  - `run_live` with a fake source, tracker and clock: processes at `process_fps`, numbers frames, writes no image or
    video file (the output folder holds only the time-series CSV and, if written, the metrics JSON), and the report
    has its fields and its loop boundaries.
  - Worker: batching and heartbeat with fake HTTP; exits after 15 s of no API (fake clock); SIGTERM sends a final
    message.
  - Supervisor: start, stop, `409` cases, watchdog `no_heartbeat` and `worker_exited`, kill after the stop timeout,
    uploads held while live. This uses a tiny real fake-worker script that speaks the ingest protocol with no model.
  - Ingest: token required, wrong-run token refused, non-loopback refused, run not live or not processing refused,
    preview size limit.
  - Migration 2 → 3 with backup; old rows read `kind: "upload"`.
  - The URL-leak test from section 6.
  - Contract tests: existing route responses unchanged apart from the new optional fields; the WebSocket message
    shapes for a live run equal those for a real-time upload.
- Integration (marked): a real worker with the real model on a looped clip for a short session, end to end through
  the API.
- Dashboard e2e: one Playwright test that starts a looped-clip live session (fake or real worker, builder's choice),
  opens `/live?id=N`, sees the numbers update, stops it, and opens the recorded run. The recorded run must show
  "Live session — no video recorded" (no video player), and the time-series CSV link must return `200`. Existing
  e2e stays green.
- Vitest: `RunDetail` with `kind: "live"` shows the note and no player; with `kind: "upload"` or no `kind`
  (older runs) it is unchanged.

## Measurements (report how each was taken)

These use the looped clip `cad_seq25` at `process_fps` 10, with outputs under gitignored `outputs/s11_live/`:

- Per-frame detector-update median and p90.
- Worker CPU (`getrusage` or `psutil` if already present) and Peak RSS of the worker and the API.
- Time from capture to ingest (capture time to API receipt, same machine clock).
- Ingest batch size and rate.
- A 30-minute soak: RSS and update time at the start and end, to show whether memory grows.

The webcam measurement is the same set, run if the camera permission is available in the session. Otherwise it is
**pending, needs a person**, with the exact command written down.

## Left to the builder

These are not decided here; settle them and record each as the builder's call in the report:

- Module and file names inside `ai_engine/live/`, `worker/` and the API, and the name of the demo command.
- Batch encoding, the preview rate, the preview size limit, and the exact timeouts within the bounds given.
- Whether `run_live` also writes a metrics JSON (no images in any case).
- Fake or real worker in the e2e test.
- How `process_fps` maps onto a source whose fps is unknown (webcam): measure the fps or use the clock only.
