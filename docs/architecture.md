# QueueVision Architecture (Sprint 4)

## Data flow

```
 source (file / webcam)
        │
 ┌──────▼──────┐  frames   ┌─────────────────────────────────────────────┐
 │ VideoLoader │──────────►│ pipeline.iter_frame_results()  (generator)  │
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
          │ run_tracking.process_video (one consumer; the Sprint 4 API runs it too)    │
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
skips, decodes, tracks, runs the analyzer and yields `FrameResult`. It does no
drawing, writing or timing aggregation.

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

### Command line (`ai_engine/run_tracking.py`)
`process_video(source, tracker, RunOptions)` is the one consumer of the
generator that writes files; `main()` parses flags and maps known errors to
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
| `api/main.py` | `create_app(settings, tracker_factory)`; REST routes, WebSocket route, static test page; `app` for uvicorn |
| `api/models.py` | Pydantic response models |
| `api/static/index.html` | Demo test page (plain HTML/CSS/JS, no build step, no CDN) |
| `api/__main__.py` | `python -m api` |

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
| Generator instead of a monolithic loop | File output, tests and a future live API share one processing path |
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
  future live-camera source: iterate `iter_frame_results()`
  directly (a live `VideoLoader` source already exists) and serialize
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
* **Dashboard (Sprint 5):** reads the stored events, waits, alerts and time
  series; the wait statistics in the metrics JSON `queue.waiting` section show
  what to aggregate.
* **Model comparison / speed:** `--model yolo11s.pt`, CoreML via
  `export_model.py`, `--frame-stride`. The report records model, device and
  resolved tracker settings.
* **Evaluation:** new datasets plug in as sequence folders for
  `evaluate_tracking.py`, manual counts for `evaluate_queue.py` or manual
  waiting-time ground truth for `evaluate_waiting.py`.
