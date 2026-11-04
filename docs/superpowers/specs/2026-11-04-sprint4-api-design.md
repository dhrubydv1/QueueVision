# Sprint 4 design: upload API, live progress and SQLite

Status: draft for approval. Date: 2026-11-04.

## Intent

QueueVision is a college project. There is no live camera, so Sprint 4 turns
the existing command-line engine into a small local web service for a
**demo with recorded clips**: someone uploads a video, watches a progress bar
and live queue numbers while it is processed, then plays the annotated video
and browses the results. Results are kept in SQLite so a processed clip can
be shown again without re-processing. The Sprint 5 Next.js dashboard will use
the same API; until then a single plain-HTML test page is the demo screen.

## Goals

1. Upload a video file with a chosen camera config and processing options;
   processing starts in the background.
2. Live updates over a WebSocket while it runs: progress, people in frame,
   queue length, service rate, predicted wait, alert state, and each event as
   it is emitted.
3. Persist each run in SQLite: video/job record, all events, waits, alerts and
   the per-second time series, plus the final metrics report.
4. REST endpoints to list runs and fetch a run's status, summary, events,
   waits, alerts and time series, and to stream the annotated H.264 video.
5. A test page served by the API: upload form, camera-config dropdown,
   progress bar, live numbers, event list, then the video player and summary.
6. Tests that run without torch (fake tracker), plus one integration test with
   the real model.

## Non-goals

- Live camera monitoring (no webcam endpoint).
- Streaming annotated frames during processing (video is played afterwards).
- Authentication, user accounts, multiple users, HTTPS, deployment, Docker.
- More than one job processing at a time (others wait in a queue).
- Drawing or editing queue zones in the browser (still `draw_zone.py`).
- The Next.js dashboard (Sprint 5).
- Celery, Redis, an ORM, or a separate worker process.

## Anti-goals (would make it a failure even if it "works")

- The server freezes or the WebSocket stalls while a video is processed.
- Refreshing the page loses a run's results, or a server restart leaves a run
  stuck in "processing" forever.
- Uploaded or generated videos end up in Git (the repository is public).
- The CLI (`run_tracking.py`) changes behaviour or its outputs.
- Numbers shown in the demo that the engine did not compute.

## Constraints

- MacBook Air M1, 8 GB RAM, Python 3.11, existing `.venv/`; one model in memory.
- Processing runs at roughly 31 inference FPS on MPS for 960×540 clips.
- `ai_engine/` must not import the API (dependency points one way:
  `api → ai_engine`). Existing dependency rules inside `ai_engine` stay.
- Unit tests and CI `lint-and-unit` must keep running without torch.
- Event payloads are the schema 1.0 dicts from `to_dict()` (docs/event_schema.md).
- Pinned dependencies, ruff clean, tests for all new code, branch `sprint-4`,
  PR into `main`.

## Approaches considered

| Approach | Trade-off | Decision |
|---|---|---|
| **A. One worker thread inside the FastAPI process** | Least code, one command to run, model loaded once. A crash in model code can take the server down; processing shares the process. | **Chosen** by the user: right size for a single-machine demo. |
| B. Separate worker process, communicating through SQLite | Crash isolation; about twice the moving parts, two processes to start, live updates by polling the database. | Rejected: more than a demo needs. |
| C. Task queue (Celery + Redis) | Built for multi-machine work; adds a service to install and run. | Rejected. |

Storage: Python's built-in `sqlite3`, no ORM (planner's call, accepted with
approach A), keeping the project's dependency list lean.

## Decided by the user

- Uploads of recorded clips, not live cameras.
- During processing: progress bar and live numbers only; play the annotated
  video afterwards.
- A simple test page in Sprint 4.
- Approach A.

## Design (planner's calls unless marked above)

### Layout

New top-level package `api/` (next to `ai_engine/`):

| Module | Role |
|---|---|
| `api/settings.py` | Paths and limits from environment variables with defaults: data dir `data/` (`QUEUEVISION_DATA_DIR`), max upload size 500 MB, allowed extensions as in `VideoLoader` (.mp4 .avi .mov .mkv .m4v), camera config dir `configs/cameras/`, host 127.0.0.1, port 8000. |
| `api/db.py` | `sqlite3` schema creation and small repository functions; one connection per thread; WAL mode; foreign keys on. |
| `api/jobs.py` | `JobManager`: in-memory FIFO queue, one worker thread, per-job live state, subscriber fan-out to WebSockets. |
| `api/main.py` | `create_app(settings, tracker_factory)` FastAPI app factory; REST routes, WebSocket route, static test page; `app = create_app()` for uvicorn. |
| `api/models.py` | Pydantic response models. |
| `api/static/index.html` | Test page: plain HTML, CSS and JavaScript, no build step, no external CDN. |
| `api/__main__.py` | `python -m api` starts uvicorn with the settings. |

Runtime data (gitignored): `data/queuevision.db`, `data/uploads/<video_id>/<original name>`,
`data/outputs/<video_id>/` (the existing output tree: videos, metrics, events, timeseries).

### Engine change (small, CLI-compatible)

`run_tracking.process_video()` gains an optional `on_frame` callback (and an
optional `should_stop` callable for cancellation/shutdown). The callback gets a
small frozen `FrameUpdate` with: frame index, total frames, timestamp, people
in frame, and the `QueueFrameState` (or None). Everything else, including files
written and the returned report, is unchanged. The CLI passes neither, so its
behaviour and outputs stay identical; a test checks this.

### Job lifecycle

`queued → processing → completed | failed | cancelled`.

1. `POST /api/videos` validates the camera id and options, streams the upload
   to disk (rejects wrong extension and files over the size limit with 4xx and
   no leftover files), inserts a `videos` row with status `queued`, enqueues it,
   returns 202 with the run record.
2. The worker thread takes one job, builds a tracker via `tracker_factory`
   (real `PersonTracker` by default, a fake in tests; the real detector is
   created once and reused across jobs), calls `process_video(...,
   on_frame=...)`.
3. `on_frame` (worker thread): updates the job's live state; once per second of
   video time writes a time-series row; writes every event to `events` (and to
   `waits`/`alerts` as appropriate) as it occurs; publishes a message to
   subscribers. Database writes are batched per second to keep the loop fast.
4. On success: store the metrics report JSON and output paths, status
   `completed`. On exception: status `failed` with the error text. The worker
   continues with the next job.
5. On server start, any `queued` or `processing` rows left by a previous run are
   marked `failed` with error "interrupted by server restart" (uploads are not
   re-processed automatically).
6. On shutdown, the worker is asked to stop via `should_stop`; the current job
   ends as `cancelled`.

### WebSocket: `GET /ws/videos/{video_id}`

JSON messages, each with `"schema_version": "1.0"` and a `type`:

- `snapshot` — sent immediately on connect: the current run record and the
  latest live numbers (so a page opened mid-run or after a refresh catches up).
- `progress` — about once per second of video: `frames_processed`,
  `frames_total`, `percent`, `video_time_s`, `people_in_frame`,
  `queue_length`, `raw_queue_count`, `service_rate_per_min` and its status,
  `predicted_wait_s` with low/high and status, `alert_active`, `alert_reasons`,
  processing FPS.
- `event` — each queue event as its `to_dict()` payload.
- `status` — on every status change; on `completed`/`failed`/`cancelled` the
  server sends it and closes the socket.

Connecting to a finished run sends `snapshot` + final `status` and closes.
Unknown id → close with code 4404. Thread-to-asyncio hand-off uses
`loop.call_soon_threadsafe` onto per-subscriber `asyncio.Queue`s with a bound;
a slow client drops `progress` messages (never `event`/`status`).

### REST endpoints

| Method and path | Returns |
|---|---|
| `GET /api/health` | status, device, model, queue depth |
| `GET /api/cameras` | camera configs found in `configs/cameras/*.yaml` (id, description, has service point, alerts enabled); invalid files listed with their error |
| `POST /api/videos` (multipart: `file`, `camera_id`, optional `frame_stride`, `blur_heads`) | 202 + run record |
| `GET /api/videos` | runs, newest first |
| `GET /api/videos/{id}` | run record: status, progress, options, timings, error, summary (queue section of the metrics report) |
| `GET /api/videos/{id}/events?type=&limit=&offset=` | event payloads in time order |
| `GET /api/videos/{id}/waits` | wait rows |
| `GET /api/videos/{id}/alerts` | alert rows |
| `GET /api/videos/{id}/timeseries` | per-second rows |
| `GET /api/videos/{id}/video` | annotated H.264 MP4 with HTTP range support (browser seeking); 404 until completed |
| `GET /api/videos/{id}/report` | full metrics JSON |
| `DELETE /api/videos/{id}` | deletes rows and files; 409 while processing (cancel first) |
| `POST /api/videos/{id}/cancel` | cancels a queued or processing run |
| `GET /` | the test page |

Errors use FastAPI's `{"detail": ...}` with proper status codes (400, 404,
409, 413, 422).

### Database tables

The four tables proposed in `docs/event_schema.md`, adapted:

- `videos`: `video_id`, `original_name`, `camera_id`, `status`, `error`,
  `options` (JSON), `fps`, `frames_total`, `frames_processed`, `created_at`,
  `started_at`, `finished_at` (wall-clock ISO UTC, for the run record only),
  `report` (JSON), `output_dir`.
- `events`, `waits`, `alerts` as in `docs/event_schema.md` (cascade delete).
- `timeseries`: same columns as `<name>_queue.csv` plus `video_id`.
- A `schema_meta` table with the DB schema version (1) so later sprints can migrate.

The CSV/JSONL files are still written in the run's output dir (unchanged
engine behaviour); SQLite is what the API reads.

### Test page (`/`)

Single page, works offline, light and dark readable:

1. Upload card: file picker, camera-config dropdown (from `/api/cameras`),
   frame stride, head-blur checkbox (on by default, per `docs/privacy.md`),
   Start button.
2. While processing: progress bar with percent and frames, live numbers (queue
   length, people in frame, service rate, predicted wait with range or
   "insufficient data"/"unknown", alert badge) and a scrolling event list.
3. When completed: `<video>` player for the annotated clip, summary numbers
   (completed waits, mean/median/P90 wait, abandonment, alerts), and the waits
   table.
4. A "Previous runs" list to reopen any stored run.

### Dependencies (new, pinned)

Runtime: `fastapi`, `uvicorn[standard]`, `python-multipart`. Dev: `httpx` (for
FastAPI's TestClient). The CI `lint-and-unit` job also installs FastAPI,
uvicorn, python-multipart and httpx (still no torch).

### Testing

- Torch-free unit tests with a fake tracker factory: DB schema and repository,
  upload validation (extension, size, unknown camera), job lifecycle including
  failure and restart recovery, WebSocket message sequence (snapshot →
  progress/event → status → close), REST endpoints, range requests on the video
  endpoint, delete/cancel rules, `process_video` with and without `on_frame`
  producing identical files.
- One integration test (marked) that uploads a few seconds of a real clip with
  the real model.
- Manual demo check: run the server, upload MOT17-04 from the test page in a
  browser, confirm live numbers, then video playback and seeking.

### Documentation

README (how to start the server and use the test page), `docs/api.md`
(endpoints and WebSocket messages with examples from a real run),
architecture/implementation updates, testing report with real results.

## Demo dataset and queue-length accuracy (decided by the user)

The demo and the first real accuracy check use the **Collective Activity
Dataset** (Choi, Shahid & Savarese, VSWS/ICCV 2009), supplied locally as
`~/Downloads/ActivityDataset.zip`: 44 sequences of 720×480 JPEG frames, each
person annotated every 10th frame as `frame x y w h action pose`
(actions: 1 NA, 2 crossing, 3 waiting, 4 queueing, 5 walking, 6 talking). There
are no person IDs, so waiting-time ground truth is not available; queue-length
ground truth is (number of people labelled 4 in an annotated frame).

Queueing sequences (measured): seq10 (302 frames), seq25 (617), seq26 (734),
seq27 (1804), seq39 (518), seq40 (401), seq41 (707). Cameras are handheld:
seq25, seq27 and seq40 are the steadiest and lead the demo; seq39 moves most.

Added scope:

1. `ai_engine/tools/prepare_cad.py`: converts chosen sequences from the zip (or
   an extracted folder) to `datasets/raw/cad_seqNN.mp4` and writes
   `datasets/annotations/cad/cad_seqNN_queue_counts.csv` in the format
   `evaluate_queue.py` already reads (`time_s, frame, queue_count`, one row per annotated frame:
   the number labelled queueing). The dataset does not state its frame rate; the tool takes
   `--fps` (default **30, an assumption** recorded in the outputs and the
   testing report, since every time value depends on it). Images and videos
   stay out of Git; the zip is extracted into a fresh folder and treated as
   untrusted data.
2. One camera config per demo sequence, `configs/cameras/cad_seqNN.yaml`,
   with the queue zone and service line drawn with `draw_zone.py` on that
   clip (generous margins because of camera movement). These YAML files are
   committed (no images).
3. Queue-length accuracy on all 7 queueing sequences with `evaluate_queue.py`
   (MAE, RMSE, bias, exact and within-one rates), reported in the testing
   report as the first real accuracy result, with the limits stated: handheld
   cameras, the dataset's "queueing" label versus our zone-based membership
   rule, and the fps assumption. Membership settings are not tuned on the
   same clips they are scored on unless that is reported as such.
4. The test page's camera dropdown then lists these configs, so a demo is:
   upload `cad_seq27.mp4`, choose `cad_seq27`, watch, play.

## Settled after the builder check (planner's calls)

- **Camera config is required** on upload (`camera_id` mandatory): the demo is
  about queues, and it avoids a whole "no queue data" mode.
- **Cancellation keeps partial results.** `should_stop` ends the loop the same
  way the CLI's `q` key does; the report and files for the processed part are
  still written, rows already stored stay, status becomes `cancelled`. The
  `/video` endpoint serves only `completed` runs (404 otherwise), so a partial
  video is never shown as a finished result.
- **Progress counts source frames** (matching the overlay's frame index).
  `frames_total` comes from the container; when it is unknown, `frames_total`
  and `percent` are null and the test page shows an indeterminate bar.
- **`on_frame` is called after the frame is drawn and written**, so numbers never
  run ahead of the file. An exception in it (e.g. a DB error) aborts the job,
  which is marked `failed` with the error; the worker moves on.
- **API defaults:** `blur_heads` true (also the test page default, per
  `docs/privacy.md`); `frame_stride` integer 1–10, default 1; optional
  `max_frames` (≥ 1) is exposed, which also keeps the integration test short.
  Out-of-range values → 422.
- **IDs and file names:** `video_id` is an `INTEGER PRIMARY KEY AUTOINCREMENT`
  (never reused after a delete). The upload is stored as
  `data/uploads/<video_id>/video<video_id>.<ext>`, so engine outputs are named
  `video<id>_tracked.mp4` etc.; the user's original file name is kept only in
  `videos.original_name` and is never used as a path.

## Left to the builder

- Exact Pydantic model fields beyond those listed, HTML/CSS styling, message
  batching details, and the module split inside `api/` if a file grows large.
