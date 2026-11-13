# QueueVision API (Sprint 4)

A small local web service around the engine: upload a recorded video, watch
progress and live queue numbers over a WebSocket, then play the annotated
video and read the results. It is meant for a single-machine demo with recorded
clips (no live cameras, no authentication, no HTTPS).

The JSON examples below are copied from a real run on the Collective Activity
Dataset sequence `cad_seq25` (617 frames, 30 fps assumed, camera `cad_seq25`,
default options). Long arrays are trimmed and marked with `...`.

## Running the server

```bash
source .venv/bin/activate
python -m api                      # http://127.0.0.1:8000
python -m api --host 127.0.0.1 --port 8001
python -m api --reload             # restart on code changes (development)
```

Open http://127.0.0.1:8000 for the demo test page. Interactive API docs are at
`/docs` (FastAPI's built-in page).

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8000` | Port |
| `--reload` | off | Restart on code changes |

Settings come from environment variables (read when the server starts; `.env`
is not auto-loaded):

| Variable | Default | Meaning |
|---|---|---|
| `QUEUEVISION_DATA_DIR` | `data/` in the repository | Uploads, outputs and the SQLite database |
| `QUEUEVISION_CAMERA_DIR` | `configs/cameras/` | Where camera YAML files are listed from |
| `QUEUEVISION_MAX_UPLOAD_MB` | `500` | Largest accepted upload |
| `QUEUEVISION_HOST` | `127.0.0.1` | Default for `--host` |
| `QUEUEVISION_PORT` | `8000` | Default for `--port` |
| `QUEUEVISION_MODEL` | `ai_engine/models/yolo11n.pt` | Detector weights |
| `QUEUEVISION_DEVICE` | the engine default (`auto`) | `auto`, `cpu`, `mps`, `cuda` |
| `QUEUEVISION_MAX_QUEUED_JOBS` | `5` | Runs allowed to *wait* in the queue (the running one is not counted); further uploads get `429` |
| `QUEUEVISION_MAX_VIDEO_SECONDS` | `1800` | Longest accepted video (frame count / fps); longer uploads get `400` |
| `QUEUEVISION_MAX_FRAME_PIXELS` | `8294400` (3840x2160) | Largest accepted frame (width x height); larger uploads get `400` |

The three limits above must be positive numbers (`QUEUEVISION_MAX_QUEUED_JOBS`
an integer). A bad value stops the server at start-up with an error naming the
variable. `python -m api` logs a prominent warning when `--host` is not a
loopback address: the API has no authentication and must not be exposed. See
[security_review.md](security_review.md).

Runtime data lives under the data directory and is git-ignored:

```
data/
├── queuevision.db                       # SQLite
├── uploads/<video_id>/video<id>.<ext>   # the uploaded file; the name is generated from the id, the client's name is only stored (sanitized) in the DB
└── outputs/<video_id>/                  # annotated video, metrics, events, timeseries (as the CLI writes them)
```

## Job model

One worker thread inside the server process runs one video at a time; other
uploads wait in a first-in first-out queue. The detector is loaded once, on the
first job, and reused. Run status:

```
queued -> processing -> completed | failed | cancelled
```

* On start-up, any run still `queued` or `processing` from a previous server
  process is marked `failed` with the error `interrupted by server restart`.
  Uploads are not re-processed automatically.
* Cancelling (or shutting the server down) during processing keeps the partial
  results: rows already stored stay, the files for the processed part are
  written and the status becomes `cancelled`. `/video` serves only `completed`
  runs.
* Progress counts source frames. `frames_total` comes from the video container;
  when it is unknown, `frames_total` and `percent` are `null`. A completed run
  always reports `percent` 100.
* Times in events, waits, alerts and the time series are video time (frame
  index divided by the video's frame rate), not wall-clock time. `created_at`,
  `started_at` and `finished_at` of a run are wall-clock ISO 8601 UTC.

## REST endpoints

Errors use FastAPI's `{"detail": ...}` body with status 400, 404, 409, 413, 422,
429 or 500. Error messages are deliberately generic (no file paths or internals;
details go to the server log): an unexpected server error is
`{"detail": "internal server error"}`, a failed run's `error` is
`processing failed (<ExceptionClass>)`, and a broken camera file is reported as
`invalid camera config`. A `video_id` outside `1` to `2**63-1` gives `422` on
every route (the WebSocket is closed with code 1008 before it is accepted).

| Method and path | Returns |
|---|---|
| `GET /api/health` | `status`, `device` (the device the loaded detector runs on, e.g. `mps`; `null` until a job has loaded the model), `configured_device` (the `--device` setting, e.g. `auto`), `model`, `queue_depth` |
| `GET /api/cameras` | One entry per `*.yaml` in the camera directory: `id`, `description`, `has_service_point`, `alerts_enabled`, `error` (set, with the rest empty, for an invalid file) |
| `POST /api/videos` | `202` and the run record. Multipart form, see below |
| `GET /api/videos` | All runs, newest first |
| `GET /api/videos/{id}` | Run record: status, progress, options, timings, error, `summary` |
| `GET /api/videos/{id}/events?type=&limit=&offset=` | Event payloads in time order; `type` filters by `event_type`, `limit` 1 to 10000 (default 1000), `offset` >= 0 |

Event types: besides the original types, `service_complete` and `queue_exit` are new
(additive, schema version still `"1.0"`; see [event_schema.md](event_schema.md)). They are
stored in `events` like any other event, appear in `event_counts` and in the WebSocket
`event` messages (the unchanged `to_dict()` payload), but create **no** `waits` row: `waits`
keeps meaning `service_start` / `queue_abandon`. Clients must ignore event types they do not
know. `service_complete` only occurs for cameras with a `service_zone`.

The report's `queue.service_rate` section gains `estimator` (`"queue_length_over_service_rate/v1"`),
`basis` and `rate_basis` (both the configured `service_rate.basis`, `"service_start"` or
`"service_complete"`), and `queue.service_completions` (observed service durations, or a
`note` without a service zone). Existing fields are unchanged.
| `GET /api/videos/{id}/waits` | Wait rows |
| `GET /api/videos/{id}/alerts` | Alert rows |
| `GET /api/videos/{id}/timeseries` | Per-second rows (same columns as the CLI's `<name>_queue.csv`) |
| `GET /api/videos/{id}/report` | The full metrics JSON; `404` until the run has a report |
| `GET /api/videos/{id}/video` | The annotated H.264 MP4 with HTTP range support (seeking); `404` unless the run is `completed` |
| `POST /api/videos/{id}/cancel` | Cancels a queued or processing run and returns the run record; `409` if it is not active |
| `DELETE /api/videos/{id}` | `204`; deletes the database rows, the upload and the outputs; `409` while processing (cancel first) |
| `GET /` | The test page |
| `WS /ws/videos/{id}` | Live messages, see below |

### Upload: `POST /api/videos`

Multipart form fields:

| Field | Required | Rules |
|---|---|---|
| `file` | yes | Extension `.mp4`, `.avi`, `.mov`, `.mkv` or `.m4v`; non-empty; at most the upload limit (default 500 MB); the content must match the extension and be a readable video (see below) |
| `camera_id` | yes | Letters, digits, `_` and `-`; must match a valid file `<camera_id>.yaml` in the camera directory. A camera config is required |
| `frame_stride` | no | Integer 1 to 10, default 1 |
| `blur_heads` | no | Boolean, default `true` (see [privacy.md](privacy.md)) |
| `max_frames` | no | Integer >= 1; stops after that many processed frames |

A wrong extension, an empty file, a file that is not a readable video, a video
over the duration or frame-size limit, or a bad or unknown camera gives `400`;
a file over the byte limit gives `413`; an out-of-range option gives `422`; and
a full queue gives `429` (`too many queued runs; try again later`). A rejected
upload leaves no database row or files behind.

Content validation: the first bytes must match the extension (MP4/MOV/M4V:
`ftyp`; AVI: `RIFF`...`AVI `; MKV: EBML header), and OpenCV must open the file,
decode the first frame and report a positive width and height. Messages are
`file is not a readable video`, `video too long: limit N s` and
`video frame too large: limit N pixels`. The upload is first written as
`video<id><ext>.part` and renamed only after validation, so an interrupted upload
never looks complete; leftover `.part` files are deleted at server start-up. The
queue-full (`429`) and declared-size (`413`) checks run before the request body
is parsed. The stored original name is the basename only, without control
characters, NFC-normalized and at most 255 characters (`upload` if nothing is
left).

Behaviour changes in Sprint 5: `POST /api/videos` can now return `429`; videos
that are mislabelled or undecodable are rejected at upload instead of failing
later as a run; a run's `error` text and the `400` camera message no longer
include the underlying exception text.

```bash
curl -F file=@datasets/raw/cad_seq25.mp4 -F camera_id=cad_seq25 \
     -F frame_stride=1 -F blur_heads=true http://127.0.0.1:8000/api/videos
```

### Examples (real run)

`GET /api/health` (the model path is the absolute path on the machine that ran it, shortened here):

```json
{"status": "ok", "device": null, "configured_device": "auto", "model": ".../ai_engine/models/yolo11n.pt", "queue_depth": 0}
```

`GET /api/cameras` (trimmed to two of the eight entries):

```json
[
  {"id": "cad_seq10", "description": "CAD seq10: cafeteria serving line (counter on the right), handheld. Zone drawn on frames of the clip; 30 fps assumed.", "has_service_point": true, "alerts_enabled": true, "error": null},
  {"id": "cad_seq25", "description": "CAD seq25: cafe counter queue (register on the left). Zone drawn on frames of the clip; 30 fps assumed.", "has_service_point": true, "alerts_enabled": true, "error": null}
]
```

`GET /api/videos/1` after completion (the `summary` is the `queue` section of
the metrics report; only the leading fields are shown):

```json
{
  "video_id": 1,
  "original_name": "cad_seq25.mp4",
  "camera_id": "cad_seq25",
  "status": "completed",
  "error": null,
  "options": {"frame_stride": 1, "blur_heads": true, "max_frames": null},
  "fps": 30.0,
  "frames_total": 617,
  "frames_processed": 617,
  "percent": 100.0,
  "created_at": "2026-11-04T08:30:48.586384+00:00",
  "started_at": "2026-11-04T08:30:48.588884+00:00",
  "finished_at": "2026-11-04T08:31:18.655124+00:00",
  "summary": {
    "camera_id": "cad_seq25",
    "zone": [[0.06, 0.6], [0.94, 0.6], [0.94, 0.93], [0.06, 0.93]],
    "members_still_in_queue_at_end": 5,
    "maximum_queue_length": 7.0,
    "average_queue_length": 3.942,
    "event_counts": {"zone_enter": 13, "zone_exit": 8, "queue_join": 11, "service_start": 4,
                     "queue_abandon": 5, "congestion_start": 0, "congestion_end": 0},
    "waiting": {"completed": 4, "abandoned": 5, "mean_wait_s": 4.95, "median_wait_s": 5.083,
                "p90_wait_s": 6.58, "throughput_per_min": 11.669, "abandonment_rate": 0.556},
    "...": "membership, smoothing, stitching, service_rate, alerts, waiting_at_end, ..."
  }
}
```

These numbers describe what the engine computed on that clip. They are not an
accuracy result; there is no waiting-time ground truth for this dataset and
the queue-length comparison is reported in [testing_report.md](testing_report.md).

`GET /api/videos/1/events?type=queue_join&limit=2` (events are exactly the
schema 1.0 `to_dict()` payloads described in [event_schema.md](event_schema.md)):

```json
[
  {"schema_version": "1.0", "event_type": "queue_join", "person_key": "p0007", "track_id": 4,
   "timestamp_s": 0.0, "frame_index": 0, "detected_s": 3.0},
  {"schema_version": "1.0", "event_type": "queue_join", "person_key": "p0004", "track_id": 8,
   "timestamp_s": 0.0, "frame_index": 0, "detected_s": 3.6333333333333333}
]
```

`GET /api/videos/{id}/waits` returns rows with these fields (`abandoned` is 0
or 1; `service_s`, `wait_s` and `left_s` are `null` when they do not apply).
`track_ids` and alert `reasons` are strings joined with `;`. The row below is
the `service_start` event of person `p0004` from the same run, written in the
shape of a wait row (not a captured database dump):

```json
{"video_id": 1, "person_key": "p0004", "track_ids": "4;8", "join_s": 0.0,
 "service_s": 3.6333333333333333, "wait_s": 3.6333333333333333, "abandoned": 0, "left_s": null}
```

The `alerts` rows have `video_id`, `alert_id`, `event_type`, `timestamp_s`,
`frame_index`, `condition_since_s`, `reasons`, `queue_length` and
`predicted_wait_s`; `event_type` is `congestion_start` or `congestion_end`. This run raised no congestion alert, so its list is empty.
The `timeseries` rows have `time_s`, `frame`, `people_in_frame`,
`raw_queue_count`, `queue_length`, `service_rate_per_min`, `service_rate_status`,
`predicted_wait_s`, `predicted_wait_low_s`, `predicted_wait_high_s` and
`prediction_status`; there is one row per second of video time.

## WebSocket: `WS /ws/videos/{id}`

Every message is JSON with `"schema_version": "1.0"`, a `type` and the
`video_id`. Connect as soon as the upload returns the id.

| `type` | When | Content |
|---|---|---|
| `snapshot` | Immediately on connect | `run` (the run record) and `progress` (the latest live numbers, `null` before the first one) so a page opened mid-run or refreshed catches up |
| `progress` | About once per second of video time | See the example |
| `event` | Each queue event as it is emitted | `event`: the schema 1.0 event payload, unchanged |
| `status` | Every status change | `status`, `error`, `frames_processed`, `frames_total`, `started_at`, `finished_at`, `percent` |

On `completed`, `failed` or `cancelled` the server sends the `status` message
and closes the socket. Connecting to a run that is already finished sends
`snapshot` and the final `status`, then closes. An unknown id closes the socket
with code `4404`. A slow client may miss `progress` messages (the backlog is
bounded); `event` and `status` messages are never dropped.

Message order in the real run: 1 `snapshot`, 21 `progress`, 41 `event` and a
final `status` (64 messages). Examples, in the order they arrived:

```json
{"schema_version": "1.0", "type": "snapshot", "video_id": 1,
 "run": {"video_id": 1, "original_name": "cad_seq25.mp4", "camera_id": "cad_seq25", "status": "processing",
         "error": null, "options": {"frame_stride": 1, "blur_heads": true, "max_frames": null},
         "fps": 30.0, "frames_total": 617, "frames_processed": 0,
         "created_at": "2026-11-04T08:30:48.586384+00:00", "started_at": "2026-11-04T08:30:48.588884+00:00",
         "finished_at": null, "percent": 0.0, "summary": null},
 "progress": null}
```

```json
{"schema_version": "1.0", "type": "progress", "video_id": 1, "frames_processed": 91, "frames_total": 617,
 "percent": 14.7, "video_time_s": 3.0, "people_in_frame": 6, "processing_fps": 24.74,
 "queue_length": 0.0, "raw_queue_count": 1,
 "service_rate_per_min": null, "service_rate_status": "insufficient_data",
 "predicted_wait_s": null, "predicted_wait_low_s": null, "predicted_wait_high_s": null,
 "prediction_status": "unknown", "alert_active": false, "alert_reasons": []}
```

`queue_length` and the other queue fields are `null` when the camera config
gives no queue data. Here the service rate is `insufficient_data` and the
prediction `unknown`, because the engine never guesses (see
[event_schema.md](event_schema.md)).

```json
{"schema_version": "1.0", "type": "event", "video_id": 1,
 "event": {"schema_version": "1.0", "event_type": "service_start", "person_key": "p0004", "track_ids": [4, 8],
           "timestamp_s": 3.6333333333333333, "frame_index": 109, "detected_s": 3.6333333333333333,
           "join_s": 0.0, "wait_s": 3.6333333333333333}}
```

```json
{"schema_version": "1.0", "type": "status", "video_id": 1, "status": "completed", "error": null,
 "frames_processed": 617, "frames_total": 617,
 "started_at": "2026-11-04T08:30:48.588884+00:00", "finished_at": "2026-11-04T08:31:18.655124+00:00",
 "percent": 100.0}
```

Because `timestamp_s` can be earlier than `detected_s`, order events by
`timestamp_s` when drawing history.

## Database

SQLite file `data/queuevision.db` (Python's built-in `sqlite3`, WAL mode,
foreign keys on, one connection per thread). The API reads from it, so a
finished run can be shown again without re-processing. The CSV/JSONL files are
still written to `data/outputs/<video_id>/` exactly as the CLI writes them.

| Table | Content |
|---|---|
| `schema_meta` | `key`/`value`; holds `schema_version` (1) |
| `videos` | One row per run: `video_id`, `original_name`, `camera_id`, `status`, `error`, `options` (JSON), `fps`, `frames_total`, `frames_processed`, `created_at`, `started_at`, `finished_at`, `report` (JSON), `output_dir` |
| `events` | `event_id`, `video_id`, `schema_version`, `event_type`, `timestamp_s`, `detected_s`, `frame_index`, `person_key`, `payload` (the full event JSON) |
| `waits` | `video_id`, `person_key`, `track_ids`, `join_s`, `service_s`, `wait_s`, `abandoned`, `left_s` |
| `alerts` | `alert_id`, `video_id`, `event_type`, `timestamp_s`, `frame_index`, `condition_since_s`, `reasons`, `queue_length`, `predicted_wait_s` |
| `timeseries` | `video_id` plus the CSV columns listed above |

`events`, `waits`, `alerts` and `timeseries` rows are deleted with their video
(`ON DELETE CASCADE`). `person_key` and `track_id` restart for every video, so
always scope them by `video_id`.

## Not in scope

No live cameras, no streaming of annotated frames during processing (the video
is played afterwards), no authentication or accounts, no HTTPS or deployment,
and only one job processes at a time. The Next.js dashboard is Sprint 5 and
will use this API.
