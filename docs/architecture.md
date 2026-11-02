# QueueVision Architecture (Sprint 2)

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
                           │   QueueZone · QueueMembershipTracker        │
                           │   QueueLengthEstimator                      │
                           └──────────────────┬──────────────────────────┘
                                              │ FrameResult
          ┌───────────────────────────────────┴──────────────────────────────────┐
          │ run_tracking.process_video (one consumer; Sprint 4 API is another)    │
          │  blur_heads → draw_queue_zone → draw_people → draw_metrics_panel      │
          │  → VideoWriter → ffmpeg H.264                                         │
          │  PerformanceTracker → *_metrics.json                                  │
          │  MotWriter → tracks/*_mot.txt                                         │
          │  QueueTimeSeriesWriter, ZoneEventWriter → timeseries/*.csv, events/*.csv
          └───────────────────────────────────────────────────────────────────────┘

 Offline:  evaluate_tracking.py  (mot_eval)  ◄── tracks/*_mot.txt + MOT17 gt
           evaluate_queue.py     (queue_eval) ◄── timeseries/*_queue.csv + manual counts
```

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
`candidate`, `in_queue`, `left`) and `events` (tuple of `ZoneEvent` produced
by this frame).

### `ZoneEvent` (`ai_engine/queue/membership.py`)
Frozen dataclass: `event_type`, `track_id`, `timestamp_s`, `frame_index`,
`zone_entered_s`.

| Event | Fires when | `timestamp_s` |
|---|---|---|
| `zone_enter` | Membership is confirmed (dwell and speed rules met) | Confirmation time |
| `zone_exit` | A confirmed member has been outside the zone for longer than `exit_grace_seconds` | Last time seen inside the zone |

`zone_entered_s` is when the person first stepped into the zone and is the
value Sprint 3 should use as the queue-join time. Only confirmed members
produce events; people walking through produce none. Nothing is emitted at
the end of a video; the number of members still queued appears in the
metrics JSON as `queue.members_still_in_queue_at_end`.

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
* `analyzer.py`: `QueueAnalyzer` combines them into one `update()` call.

### Pipeline (`ai_engine/pipeline.py`)
`iter_frame_results(loader, tracker, queue_analyzer, frame_stride, max_frames)`
skips, decodes, tracks, runs the analyzer and yields `FrameResult`. It does no
drawing, writing or timing aggregation.

### Visualization (`ai_engine/visualization/`)
Pure drawing functions: `draw_queue_zone`, `draw_people` (green other,
yellow candidate, orange in queue, plus a foot dot), `draw_metrics_panel`
(queue length shown separately from people in frame, any corner via
`--panel-position`) and `privacy.blur_heads`.

### Metrics and outputs (`ai_engine/metrics/`)
`PerformanceTracker` and `save_json` are unchanged in role.
`queue_outputs.py` adds `QueueTimeSeriesWriter` (one row per video second),
`ZoneEventWriter` and `QueueSummary`, all on the standard `csv` module.

### Evaluation (`ai_engine/evaluation/`)
`mot_eval.py` exports and scores MOTChallenge tracks; `queue_eval.py`
compares queue-length predictions with manual counts. CLI wrappers are
`ai_engine/evaluate_tracking.py` and `ai_engine/evaluate_queue.py`.

### Tools (`ai_engine/tools/`)
`draw_zone.py`, `make_annotation_kit.py` and `export_model.py`.

### Command line (`ai_engine/run_tracking.py`)
`process_video(source, tracker, RunOptions)` is the one consumer of the
generator that writes files; `main()` parses flags and maps known errors to
exit code 1.

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

## Extension points for later sprints

* **Sprint 3 (lifecycle events, waiting time, service rate, alerts):** consume
  `QueueFrameState.events`. Waiting time = `zone_exit.timestamp_s -
  zone_enter.zone_entered_s` for the same `track_id`; add tolerance for ID
  switches and handle members still queued at the end of a video explicitly.
  New logic goes in `ai_engine/queue/` as another consumer of `ZoneEvent`s,
  behind the same dependency rules.
* **Sprint 4 (FastAPI, WebSocket):** iterate `iter_frame_results()` directly
  (a live `VideoLoader` source already exists). Serialize `FrameResult`
  fields, `PersonDetection.to_dict()` and `ZoneEvent.to_dict()`. Drawing and
  H.264 are optional consumers and are not needed for the API.
* **Model comparison / speed:** `--model yolo11s.pt`, CoreML via
  `export_model.py`, `--frame-stride`. The report records model, device and
  resolved tracker settings.
* **Evaluation:** new datasets plug in as sequence folders for
  `evaluate_tracking.py` or manual counts for `evaluate_queue.py`.
