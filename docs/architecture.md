# QueueVision Architecture (Sprint 5, dashboard added in Sprint 7)

## Data flow

```
 source (file / webcam)
        │
 ┌──────▼──────┐  frames   ┌─────────────────────────────────────────────┐
 │ VideoLoader │──────────►│ pipeline.iter_source_frames → track_frame   │
 │  (OpenCV)   │ skip()    │                                             │
 └─────────────┘           │  PersonTracker ── PersonDetector (YOLO/CoreML)
                           │   ByteTrack settings from TrackerSettings   │
                           │        │ list[PersonDetection]              │
                           │  QueueAnalyzer  (only with a camera config) │
                           │   stitcher · membership · lifecycle · waits │
                           │   queue length · prediction · alerts        │
                           └──────────────────┬──────────────────────────┘
                                              │ FrameResult
          ┌───────────────────────────────────┴──────────────────────────────────┐
          │ run_tracking.process_video: iter_source_frames → FrameProcessor.process   │
          │   → RunWriters → record (the Sprint 4 API runs it too)                │
          │  blur_heads → draw zone / service point → draw_people                 │
          │  → draw_alert_banner (when active) → draw_metrics_panel               │
          │  → VideoWriter → ffmpeg H.264                                         │
          │  PerformanceTracker → *_metrics.json                                  │
          │  MotWriter → tracks/*_mot.txt                                         │
          │  QueueTimeSeriesWriter, ZoneEventWriter, WaitsWriter, AlertWriter,    │
          │  EventLogWriter → timeseries/*.csv, events/*.csv, events/*.jsonl      │
          └───────────────────────────────────────────────────────────────────────┘

 Offline:  evaluate_tracking.py  (mot_eval)  ◄── tracks/*_mot.txt + MOT17 gt
           evaluate_queue.py     (queue_eval) ◄── timeseries/*_queue.csv + manual counts
           evaluate_waiting.py   (wait_eval)  ◄── events/*_waits.csv + manual waits_gt.csv
```

### Inside `QueueAnalyzer.update()`

```
 people (track_id, box)
    │
    ▼
 TrackStitcher ──────────────► track_id -> person_key (anonymous, per video)
    │
    ▼
 QueueMembershipTracker ─────► zone_enter / zone_exit (per track) + in-zone flags
    │
    ▼
 QueueLifecycle ─────────────► queue_join / service_start / queue_abandon (per person_key)
    │                           (uses the service point: line crossing or zone)
    ▼
 WaitLog + ServiceRateEstimator   completed / abandoned waits; people served per minute
    │
    ▼
 QueueLengthEstimator ───────► smoothed queue length (median of the raw confirmed count)
    │
    ▼
 predict_wait ───────────────► queue length / service rate, with low/high, or "unknown"
    │
    ▼
 CongestionMonitor ──────────► congestion_start / congestion_end
```

All events of a frame are returned in `QueueFrameState.events` in this order.

## Data contracts

### `PersonDetection` (`ai_engine/detection/types.py`)
Frozen dataclass: `bbox (x1, y1, x2, y2)`, `confidence`, optional `track_id`.
It lives in its own module with no model imports, so queue and evaluation
code can use it without torch or ultralytics. IDs are per-video ByteTrack
integers, not identities.

### `FrameResult` (`ai_engine/pipeline.py`)
Frozen dataclass yielded once per processed frame:

| Field | Meaning |
|---|---|
| `frame_index` | Source frame index (0-based, counts skipped frames) |
| `timestamp_s` | Video time, `frame_index / source FPS` |
| `frame` | Original BGR image; consumers may draw on it |
| `people` | `list[PersonDetection]`, tracked people only |
| `queue` | `QueueFrameState` or `None` when no camera config is used |
| `inference_seconds` | Detection + tracking time for this frame |
| `started_at` | `perf_counter()` when work on the frame began (including skipping and decoding), so a consumer can compute full processing time |

### `QueueFrameState` (`ai_engine/queue/analyzer.py`)
Frozen dataclass: `raw_count` (confirmed members, including those briefly
occluded within the grace period), `queue_length` (median of `raw_count`
over `smoothing.window_seconds`), `statuses` (`track_id -> MemberStatus`:
`candidate`, `in_queue`, `left`), `events` (tuple of every event produced by
this frame: zone, lifecycle and congestion), `person_keys` (`track_id ->
person_key`), `at_service` (track ids that just reached the service point,
for the overlay), `service_rate` (`ServiceRate`), `prediction`
(`WaitPrediction`), `alert_active` and `alert_reasons`.

`QueueAnalyzer.report(duration_s)` returns the Sprint 3 part of the metrics
JSON `queue` section.

### Events (`ai_engine/queue/event_schema.py`)
Seven event types in schema version 1.0: `zone_enter`, `zone_exit`,
`queue_join`, `service_start`, `queue_abandon`, `congestion_start`,
`congestion_end`. All have `to_dict()`. The field-by-field contract, with
examples and suggested SQLite tables, is in [event_schema.md](event_schema.md).
`ZoneEvent` moved here from `membership.py` (still importable from both).

| Event | Fires when | `timestamp_s` |
|---|---|---|
| `zone_enter` | Membership is confirmed (dwell and speed rules met) | Confirmation time |
| `zone_exit` | A confirmed member has been outside the zone for longer than `exit_grace_seconds` | Last time seen inside the zone |

`zone_entered_s` (with `zone_entered_frame`) is when the person first stepped
into the zone and is the value used as the queue-join time. `detected_s` is
the video time at which the event was emitted. Only confirmed members
produce events; people walking through produce none. Nothing is emitted at
the end of a video; people still queued appear in the metrics JSON as
`queue.members_still_in_queue_at_end`, `queue.waiting_at_end` and
`queue.pending_abandon_at_end`.

## Components

### Video (`ai_engine/video/`)
`VideoLoader` opens a file or a camera index (`--video 0`), exposes
`VideoMetadata` (size, FPS, `fps_source` `container` or `measured`, frame
count) and yields frames one at a time. `skip()` grabs a frame without
decoding it, which implements `--frame-stride`. For a live source with unknown
FPS it measures the rate over 30 frames and buffers those frames. A mocked
`capture_factory` makes this testable without hardware. `VideoWriter` writes
mp4v at the effective FPS. `transcode.reencode_h264()` runs ffmpeg afterwards.

### Detection (`ai_engine/detection/`)
`PersonDetector` loads YOLO (`.pt`) or CoreML (`.mlpackage`) once and
reports the device (`coreml` for CoreML, where `--device` is ignored).
`resolve_model_path()` only places a bare string name in
`ai_engine/models/`; any path with a directory part or a `Path` is used as given.
`run_timed()` wraps an Ultralytics call with device sync and timing.
`ultralytics`/`torch` are imported lazily.

### Tracking (`ai_engine/tracking/tracker.py`)
`PersonTracker` wraps the detector's model and calls `model.track()` with a
**generated tracker YAML**. `TrackerSettings` holds `input_confidence`
(boxes passed into ByteTrack, default 0.1), `new_track_confidence`
(ByteTrack `new_track_thresh`, the `--confidence` value) and `buffer_seconds`.
`resolve_tracker_config()` copies Ultralytics' installed `bytetrack.yaml` and
sets `track_buffer = round(buffer_seconds × effective FPS)`; in Ultralytics
8.4.198 `BYTETracker` uses `track_buffer` directly as processed frames.
`configure(effective_fps)` regenerates the YAML and resets the tracker; the
file is named by content hash in `<tmp>/queuevision/trackers/`.

### Queue (`ai_engine/queue/`)
* `camera_config.py`: validated, frozen `CameraConfig` loaded from YAML.
* `zone.py`: `QueueZone`, a normalized polygon with a NumPy ray-casting test.
* `membership.py`: `QueueMembershipTracker`, per-track state machine
  (candidate, in_queue, left) using the foot point, dwell time, a speed
  filter and an exit grace period. State for tracks unseen for longer than
  the grace period is dropped.
* `queue_length.py`: `QueueLengthEstimator`, a sliding-window median.
* `event_schema.py`: the versioned event dataclasses (see above).
* `service_point.py`: `ServiceLine` (a segment crossing or touching the line
  between two consecutive foot points) and `ServiceZone` (foot point inside a
  polygon), built by `service_point_from_config()`.
* `stitching.py`: `TrackStitcher` maps ByteTrack ids to anonymous
  `person_key`s and bridges ID switches that happen inside the queue zone.
* `events.py`: `QueueLifecycle` turns zone events, person keys and the service
  point into `queue_join`, `service_start` and `queue_abandon`.
* `waiting_time.py`: `WaitLog` (completed and abandoned waits, summary
  statistics), `ServiceRateEstimator`, `predict_wait()`.
* `alerts.py`: `CongestionMonitor`.
* `analyzer.py`: `QueueAnalyzer` combines them into one `update()` call.

### Pipeline (`ai_engine/pipeline.py`)
`iter_frame_results(loader, tracker, queue_analyzer, frame_stride, max_frames)`
is kept as a compatibility generator (used by tests): it skips, decodes,
tracks, runs the analyzer and yields `FrameResult`; `process_video` does not
use it. It does no drawing, writing or timing aggregation. It is built from two parts:
`iter_source_frames(loader, frame_stride, max_frames)` (the file side: stride
skipping, timestamps, `started_at`) and `track_frame(...)` (tracker plus
analyzer, no drawing).

### Per-frame step (`ai_engine/frame_processor.py`)
`FrameProcessor(tracker, camera, *, blur_heads, head_fraction, panel_position,
stitching, stitch_diagnostics, frame_size)` is the per-frame step that does not
depend on a file, a known frame count or a video writer. It owns the
`QueueAnalyzer` and a `PerformanceTracker`.

* `process(frame, frame_index, timestamp_s, *, total_frames=None, started_at=None) -> FrameResult`
  tracks, runs the queue analysis and draws (blur, zone, service point, people,
  alert banner, metrics panel) on the frame in place. `total_frames=None` draws
  "Frame N" without a total.
* `draw(result, total_frames=None)` is the drawing part on its own.
* `record(result)` records the performance sample (processing time is now minus
  `started_at`).

`process_video` is now setup, loop and teardown: loader, `iter_source_frames`,
`processor.process`, `RunWriters.write_video`, `processor.record`,
`RunWriters.add`, then the callbacks. `RunWriters` (in `run_tracking.py`) opens
the video, MOT, time-series, zone-event, waits, alerts and event-log writers in
the order the old function did.

**What a live camera worker must still do.** The seam does not do these for
you: call `tracker.configure(effective_fps)` and `tracker.warmup()` before the
first frame (the device is read from `tracker.detector.device`), and call
`processor.record(result)` after the frame's sinks have finished, because the
rolling FPS drawn on later frames comes from those samples and the processing
time is meant to include the sink work. Passing `started_at` is optional but
without it the processing time is not meaningful. The golden test
(`tests/test_frame_processor_golden.py`) compares every output file of the old
and new code byte for byte (video by frame hash), with the clock frozen, so it
does not lock the order of the timing calls.

### Visualization (`ai_engine/visualization/`)
Pure drawing functions: `draw_queue_zone`, `draw_service_point` (magenta),
`draw_people` (green other, yellow candidate, orange in queue, magenta at the
service point, plus a foot dot), `draw_alert_banner` (red strip, top or
bottom; `run_tracking` puts it at the bottom when the panel is at a top
position so it never covers the panel), `draw_metrics_panel` (queue length
shown separately from people in frame, plus service rate and predicted wait,
any corner via `--panel-position`) and `privacy.blur_heads`.

### Metrics and outputs (`ai_engine/metrics/`)
`PerformanceTracker` and `save_json` are unchanged in role.
`queue_outputs.py` holds `QueueTimeSeriesWriter` (one row per video second),
`ZoneEventWriter`, `WaitsWriter`, `AlertWriter`, `EventLogWriter` (JSON
Lines of every event) and `QueueSummary`, on the standard `csv` and `json`
modules.

### Evaluation (`ai_engine/evaluation/`)
`mot_eval.py` exports and scores MOTChallenge tracks; `queue_eval.py`
compares queue-length predictions with manual counts; `wait_eval.py` compares
predicted waits, wait predictions and throughput with manual waiting-time
ground truth. CLI wrappers are `ai_engine/evaluate_tracking.py`,
`ai_engine/evaluate_queue.py` and `ai_engine/evaluate_waiting.py`.

### Tools (`ai_engine/tools/`)
`draw_zone.py` (queue zone, service zone or service line via `--target`),
`make_annotation_kit.py` (frames, queue-count CSV and waiting-time ground-truth
template) and `export_model.py`.

### One queue zone and one service point per camera
A camera config describes exactly one queue zone and at most one service point
(a line or a zone, not both). A place with two counters or two queues needs two
camera configs (one per queue view); the engine has no multiple queues per
camera. Zones are drawn with `ai_engine/tools/draw_zone.py`
(`--video`, `--config`, optional `--target {queue_zone,service_zone,service_line}`,
`--points`, `--preview`, `--camera-id`, `--frame-index`; see the README, section 10).

### Command line (`ai_engine/run_tracking.py`)
`process_video(source, tracker, RunOptions)` is the one path that writes files:
`iter_source_frames` → `FrameProcessor.process` → `RunWriters` → `record`; `main()` parses flags and maps known errors to
exit code 1.

## API layer (`api/`, Sprint 4)

```
 browser / client ── HTTP + WebSocket ──► api.main (FastAPI app, uvicorn)
                                             │ upload → disk, row in SQLite, job.submit(video_id)
                                             ▼
                                   api.jobs.JobManager ── one worker thread ──► run_tracking.process_video(
                                             │                                      on_frame=…, should_stop=…)
                                             │ per frame: events → batch; once per video second:
                                             │   time-series row + progress message, DB flush
                                             ├──► api.db (sqlite3, own connection in the worker)
                                             └──► subscribers (asyncio queues, via call_soon_threadsafe)
                                                        ▲
                                   WebSocket handlers ──┘  (snapshot, progress, event, status)
```

| Module | Role |
|---|---|
| `api/settings.py` | `ApiSettings`: data dir, camera dir, upload limit, host, port, model, device (environment variables) |
| `api/db.py` | `sqlite3` schema and repository functions; WAL; foreign keys on; one connection per thread |
| `api/jobs.py` | `JobManager`, message builders (`progress`, `event`, `status`), live state and subscriber fan-out |
| `api/main.py` | `create_app(settings, tracker_factory)`: assembly only (settings, `JobManager`, lifespan, exception handler, `include_router` calls); `app` for uvicorn |
| `api/deps.py` | Shared dependencies read from `request.app.state` (`get_settings`, `get_jobs`, `get_preview`, `SettingsDep`, `JobsDep`), `open_db`, `origin_allowed`, `contained_path`, `get_record`. No module-level app state |
| `api/routes/` | One router module per area, listed below |
| `api/preview.py` | `PreviewStore` (latest head-blurred JPEG per processing run, memory only, at most 5 per second), `render_preview`, `mjpeg_part` |
| `api/models.py` | Pydantic response models |
| `api/static/index.html` | Demo test page (plain HTML/CSS/JS, no build step, no CDN) |
| `api/__main__.py` | `python -m api` |

**Router layout (`api/routes/`).** `create_app` includes the routers in this
order, which is the order the routes are matched in:

| Order | Module | Routes |
|---|---|---|
| 1 | `health.py` | `GET /api/health` |
| 2 | `cameras.py` | `GET /api/cameras` |
| 3 | `videos.py` `router` | list, detail, events, waits, alerts, `POST .../alerts/{alert_id}/ack`, timeseries, report, video, `timeseries.csv` |
| 4 | `preview.py` | `preview.jpg`, `preview.mjpg` |
| 5 | `videos.py` `mutation_router` | `POST .../cancel`, `DELETE /api/videos/{video_id}` |
| 6 | `websocket.py` | `/ws/videos/{video_id}` (Origin check before `accept`) |
| 7 | `accuracy.py` | `GET /api/accuracy` |
| 8 | `videos.py` `upload_router` | `POST /api/videos`, using the `UploadGuardRoute` route class |
| 9 | `static.py` | `/legacy` static mount (`mount_legacy(app)`), the `/legacy` redirect, then the dashboard catch-all, always last |

The videos routes are split into three routers only so that the generated
OpenAPI document keeps its path order (the preview routes sit between the read
routes and cancel/delete; upload comes after the WebSocket and accuracy
routes). The split is not about URL matching. `dashboard/openapi.json` was
byte-identical before and after the split, and `tests/test_api.py`
(`test_route_registration_order`) pins that the catch-all is the last route and
the `/legacy` mount precedes its redirect. The upload guard route class wraps
only `POST /api/videos`.

**Job and thread model.** One worker thread processes one video at a time;
other uploads wait in a FIFO queue. The worker owns its SQLite connection and
the detector is created once and reused. Route functions are plain `def`, run
in FastAPI's threadpool, and open a short-lived connection each. WebSocket
handlers run on the asyncio loop and subscribe to a job's messages; the worker
hands messages over with `loop.call_soon_threadsafe` onto bounded
`asyncio.Queue`s (a slow client loses `progress` messages, never `event` or
`status`). `on_frame` runs after a frame is drawn and written; an exception in
it fails the job and the worker moves on. Status goes
`queued → processing → completed | failed | cancelled`; runs left unfinished by
a previous server process are marked `failed` ("interrupted by server restart")
at start-up, and shutdown or cancel stops the loop through `should_stop`,
keeping the partial results.

**SQLite tables** (`data/queuevision.db`): `schema_meta`, `videos`, `events`,
`waits`, `alerts` and `timeseries`; the last four reference `videos` with
`ON DELETE CASCADE`. Columns are listed in [api.md](api.md).

**Adding a schema migration.** `_SCHEMA` in `api/db.py` stays the version 1
schema and is never edited. To change the schema, write
`_migrate_N_to_M(conn)` (plain SQL on the given connection; do not commit) and
append `(M, _migrate_N_to_M)` to `MIGRATIONS` with consecutive versions. `init_db`
creates the version 1 tables for a new database and applies every migration;
for an existing older database it first copies the file to
`<db>.bak-v<from>-<UTC timestamp>` (SQLite backup API, WAL-safe), then runs each
migration and its `schema_version` bump in one transaction, so a failing
migration rolls back, the version stays and the backup remains. A database
already at the latest version is untouched. Add a test that builds the previous
schema from its DDL, runs `init_db`, and checks the rows, the version and the
backup (see `tests/test_api_db.py`).

Each migration runs under `BEGIN IMMEDIATE` and re-reads `schema_version`
inside the transaction, so two processes starting at once apply it only once;
the backup is taken after the write lock is held, from a separate reader
connection. Connections wait up to 30 s for a lock.

**Migration list** (`MIGRATIONS` in `api/db.py`):

| Version | Function | Change |
|---|---|---|
| 1 | `_SCHEMA` (not a migration) | Tables `schema_meta`, `videos`, `events`, `waits`, `alerts`, `timeseries` |
| 2 | `_migrate_1_to_2` | `alerts.acknowledged_at` (UTC ISO 8601 text, NULL until acknowledged) and `alerts.acknowledged_by` (nullable text; always NULL until a login exists) |

`POST /api/videos/{video_id}/alerts/{alert_id}/ack` sets `acknowledged_at` once
(repeat calls keep the first time); `GET .../alerts` returns both columns.

## Dashboard, serving and live preview (Sprint 7)

```
 browser ──► FastAPI (127.0.0.1:8000)
   │            ├─ /api/*, /ws/videos/{id}      existing routes, plus preview.jpg / preview.mjpg,
   │            │                               timeseries.csv, /api/accuracy
   │            ├─ /legacy/                     the Sprint 4 test page (api/static)
   │            └─ /{path}  catch-all (last)    dashboard/out/ static export
   └─ development only: next dev :3000 ── /api/* rewrite ──► :8000 ; WebSocket and media connect to :8000 directly
```

**Dashboard (`dashboard/`).** Next.js App Router with `output: 'export'`, so
`next build` writes plain files to `dashboard/out/`. Run and report ids are query
parameters (`/live?id=12`, `/run?id=12`) because a static export cannot
pre-render unknown ids. Fonts come from `next/font` and are bundled; nothing is
loaded from a CDN at run time. TanStack Query fetches REST data through the typed
client in `lib/api.ts` (types generated from the committed `dashboard/openapi.json`);
`lib/useLiveSocket.ts` owns the WebSocket (reconnect with 1, 2, 4, 8, 15 s
backoff, applies the `snapshot` message on every connect, marks the data stale
after 5 s of silence). `lib/live-types.ts` and `lib/accuracy-types.ts` are
hand-written message and file shapes. Design tokens are Tailwind theme variables in
`app/globals.css`. The browser only formats and draws; every number comes from
the API, and `Unknown` / `Insufficient data` are shown as the engine reports them.

**Serving (`api/routes/static.py`, assembled in `api/main.py`).** The routes are registered in this order: API and
WebSocket routes, `/legacy` (static mount plus a redirect from `/legacy`), then a
catch-all `GET`/`HEAD` route for the dashboard. The catch-all returns a file under
`dashboard/out/` if it exists, else `<path>.html`, else `<path>/index.html`, else
`404.html` with status 404; unknown `/api/*` and `/ws/*` paths get the JSON 404.
Every candidate is resolved and must be inside the dashboard folder (symlink and
`..` escapes give 404). With no `dashboard/out/`, `/` shows a "Dashboard not built"
page and everything else keeps working. `scripts/demo.sh` builds the dashboard,
exports accuracy data and starts the API.

**Live preview data flow.**

```
 engine frame ──► process_video(frame_callback_wants_image=True)
      │              FrameUpdate.image (the drawn frame) and FrameUpdate.people (boxes), only when asked
      ▼
 JobManager.on_frame ──► PreviewStore.offer(video_id, image, people)      (worker thread)
      │                    at most one frame per 0.2 s: copy, blur heads (privacy.py),
      │                    downscale to <= 960 px wide, JPEG quality 70; latest only, in memory
      ▼
 GET preview.jpg  (latest JPEG)        GET preview.mjpg  (multipart/x-mixed-replace, polls the store every 0.1 s,
                                                          ends when the store is dropped, the server stops or the client leaves)
```

`FrameUpdate.image` and `.people` default to `None` and are set only when the
caller passes `frame_callback_wants_image=True`; the CLI never does, so its work
and output files are unchanged (a test compares them). The preview is blurred
on a copy even when the run's `blur_heads` option is off, is never written to disk
and is dropped when the run ends. Real-time pacing (`realtime` option) lives in the
same `on_frame` handler: it waits on an event until `start + video_time` and never
accumulates lag.

**WebSocket Origin check.** `live()` compares the `Origin` header with
`ApiSettings.effective_allowed_origins()` before `accept` and closes with 1008 on
a mismatch; a missing header is allowed. The dashboard's dev origin
`http://localhost:3000` is in the default list.

## Sprint 5 additions

Sprint 5 adds opt-in components. With the default configuration the data flow
above is unchanged (verified by the replay gate, see
[testing_report.md](testing_report.md)). Measurements and the decision to keep
the defaults are in [sprint5_implementation_report.md](sprint5_implementation_report.md).

```
 people ─► stitcher ─► person keys ─┬─► membership (legacy: per track ID)
 (track_id, box)  legacy | guarded  │   membership (temporal: per person key)
                                    └─► lifecycle ─► queue_join / service_start /
                                         service_complete / queue_abandon / queue_exit
```

| Piece | Where it fits |
|---|---|
| Temporal membership (`queue/temporal_membership.py`) | Replaces `QueueMembershipTracker` inside `QueueAnalyzer` when `membership.algorithm: temporal`; same `zone_enter` / `zone_exit` output plus per-state counts in `QueueFrameState` |
| Guarded stitcher (`GuardedTrackStitcher` in `queue/stitching.py`) | Replaces `TrackStitcher` when `stitching.algorithm: guarded`; optional per-decision diagnostics |
| New lifecycle events (`queue/events.py`, `event_schema.py`) | `service_complete` (service zone only) and `queue_exit` are emitted by `QueueLifecycle` beside the existing events |
| Replay harness (`evaluation/replay.py`, `replay_queue.py`) | Offline: reads saved MOT tracks and drives `QueueAnalyzer` directly, so detector and tracker are not involved |
| Stitching evaluation (`evaluate_stitching.py`, `evaluation/stitch_eval.py`) | Offline: scores saved tracks with and without stitching against MOT17 ground truth |
| Data validation (`ai_engine/data/validation.py`, `scripts/validate_dataset.py`) | Offline: checks `datasets/metadata/` and the videos; imports nothing from the engine |
| Upload validation (`api/upload_validation.py`) | API layer: magic bytes and an OpenCV decode probe before a run is accepted |
| Benchmark (`scripts/benchmark.py`) | Offline: times decode, detection, tracking and queue logic per frame |

**Where membership identity comes from.** The stitcher always produces a map
from track ID to person key. The temporal classifier keeps one state per
*stitched person key*, so a person whose ByteTrack ID changed keeps their state.
The legacy tracker keeps one state per *raw track ID*; stitched keys are used
only afterwards by the lifecycle. Consequently the temporal option is only as
good as the stitcher; with stitching disabled every track ID is its own key.

### Temporal classifier state machine

Per person key, with every threshold taken from `MembershipSettings`:

```
OUTSIDE   -> CANDIDATE            visible and passes the entry test (boundary margin)
CANDIDATE -> WAITING              dwell, speed and observation rules pass (zone_enter)
CANDIDATE -> forgotten            fails the stay test or unseen > exit_grace_seconds
CANDIDATE -> SERVICE              foot in the service zone, rules not met (no join)
WAITING   -> TEMPORARILY_OCCLUDED not detected this frame
OCCLUDED  -> WAITING              detected again and passes the stay test
WAITING / OCCLUDED -> SERVICE     foot in the service zone
WAITING / OCCLUDED -> ABANDONED   outside the zone (past the margin) or unseen > grace (zone_exit)
SERVICE   -> EXITED               outside both zones or unseen > grace (zone_exit)
```

ABANDONED and EXITED are terminal and forgotten after
`terminal_retention_seconds`. A person in SERVICE counts toward the queue only
if they reached WAITING first and `count_service_as_queue` is true. Service
lines give no SERVICE state; only a service zone does. The membership-confidence
value is a heuristic, not a probability.

### Guarded stitching and replay

The guarded stitcher does not link a lost person until they have been unseen
for `min_lost_seconds`, because ByteTrack can still revive the lost ID until its
`track_buffer` runs out. It then scores candidates (velocity-predicted position,
size ratio, direction, duplicate overlap) and links nothing when the runner-up
is within `ambiguity_margin`. The replay harness exists so that these options
can be compared on identical tracks: differences come only from queue logic.

## Dependency rules

```
config  ◄── detection/types ◄── queue/*  ◄── pipeline ◄── run_tracking
                 ▲                              ▲
                 └── evaluation/*               └── tracking ◄── detection/detector (YOLO)
```

* `queue/` and `evaluation/` must not import YOLO, torch, ultralytics or
  drawing code. They depend only on NumPy, PyYAML, the standard library and
  `detection/types.py` (and `config`).
* `visualization/` imports no model or queue-state code except constants and
  `PersonDetection`.
* Only `detection/detector.py` and `tracking/tracker.py` touch ultralytics,
  and they import it lazily, so the unit-test suite runs without torch. The CI
  `lint-and-unit` job enforces this by not installing it.
* The dependency points one way: `api` imports `ai_engine`, and `ai_engine`
  never imports `api`. `run_tracking.process_video` only offers two optional
  hooks (`on_frame`, `should_stop`); the CLI passes neither and is unchanged.
* Scripts run directly replace `sys.path[0]` with the project root, because
  `ai_engine/queue` would otherwise shadow the standard-library `queue` module.

## Design decisions

| Decision | Reason |
|---|---|
| Ultralytics' built-in ByteTrack with a generated YAML | Keeps one `track()` call, but lets us pass low-score boxes and set the buffer in seconds |
| Tracker reset by `persist=False` | Public API, less fragile across Ultralytics versions |
| Foot point for membership | The floor position is stable under partial occlusion and matches how the zone is drawn |
| Zone in normalized coordinates | One camera config works at any resolution |
| Events only for confirmed members | Walkers through the zone must not look like queue joins |
| Video time, never wall-clock | Results are reproducible and independent of processing speed |
| Explicit device errors | Reports must not claim acceleration that was not used |
| Per-frame step (`FrameProcessor`) separate from the source and the writers | File runs and the planned live cameras share one per-frame path; only the frame source and the outputs differ |
| Join time = first time in the zone (earliest across stitched tracks), not the confirmation time | Confirmation only happens after the dwell time; using it would shorten every wait by about the dwell time |
| Abandon is decided after `max(exit_grace_seconds, stitching max_gap_seconds)` and only when a service point exists | A person may reappear under a new ID within the stitching gap; without a service point nobody can be served, so "abandoned" would be meaningless |
| Service point must lie inside (or right at the edge of) the queue zone | If a member must walk outside the zone for longer than the abandon timeout to reach it, they are recorded as abandoned. Observed with stitching off, where the timeout equals `exit_grace_seconds` |
| No events at the end of a video | People still waiting have no known outcome; `waiting_at_end` and `pending_abandon_at_end` are reported instead of invented waits |
| Service rate is `insufficient_data` until `min_history_seconds` and `min_services` | Early estimates from one or two services are noise |
| Prediction is `unknown`, never 0 or infinity | A zero rate cannot give a finite wait, and 0 would look like a real answer |
| Prediction uncertainty from the min/max service rate over the window | Cheap and honest: the range reflects how much the rate actually varied. It is not a statistical confidence interval |
| Alerts need the condition for `min_duration_seconds` both to start and to end, then a cooldown | Avoids flicker and repeated alerts around a threshold |
| Stitching is conservative but can merge different people | New ids link only to someone who vanished inside the queue zone within `max_gap_seconds` and `max_distance_heights`. In a dense non-queue crowd it can chain different people: on MOT17-09 (street footage) it chained up to 8 track ids. It must be validated on real queue footage; `--no-stitching` allows comparison |
| `person_key` is anonymous and per video | It only ties tracks of one person together within one video; no appearance features or identity are used |
| `detected_s` next to `timestamp_s` | Lets a consumer tell when something happened from when the system knew it |

## Extension points for later sprints

* **Sprint 4 (done; see [api.md](api.md))** runs `process_video` with its
  `on_frame` hook, so the API gets the annotated video and every CLI output as
  well as live numbers. The notes below were the plan and still apply to a
  future live-camera source: build on `FrameProcessor` with
  `pipeline.iter_source_frames` / `track_frame` (a live `VideoLoader` source
  already exists) and serialize
  `FrameResult` fields, `PersonDetection.to_dict()` and each event's
  `to_dict()`; the contract is [event_schema.md](event_schema.md) (schema
  version 1.0, with suggested SQLite tables). Scope every `track_id` and
  `person_key` by video or camera run, because both restart per video.
  Stream `QueueFrameState` values (`queue_length`, `service_rate`,
  `prediction`, `alert_active`) for live views and the events for history.
  Drawing and H.264 are optional consumers and are not needed for the API.
  For a live source, events are emitted as the video proceeds, but
  `timestamp_s` can be earlier than `detected_s`, so order by `timestamp_s`
  for history and by arrival for a live feed.
* **Dashboard (Sprint 7, done; see above):** reads the stored events, waits,
  alerts and time series through the API.
* **Model comparison / speed:** `--model yolo11s.pt`, CoreML via
  `export_model.py`, `--frame-stride`. The report records model, device and
  resolved tracker settings.
* **Evaluation:** new datasets plug in as sequence folders for
  `evaluate_tracking.py`, manual counts for `evaluate_queue.py` or manual
  waiting-time ground truth for `evaluate_waiting.py`.
