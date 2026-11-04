# Implementation Notes (Sprint 4)

Sprint 1 modules that did not change in purpose (`utils/device.py`,
`metrics/performance.py`, the basics of `VideoLoader`/`VideoWriter`) are
described in the Sprint 1 section at the end. Measured results are in
[testing_report.md](testing_report.md); no figures are repeated here.

## Module reference

### `ai_engine/config.py`
Defaults: `MODEL_NAME="yolo11n.pt"`, `PERSON_CLASS_ID=0`, `CONFIDENCE=0.35`,
`IMAGE_SIZE=640`, `TRACKER_CONFIG="bytetrack.yaml"`, plus new
`TRACKER_INPUT_CONFIDENCE=0.1` and `TRACK_BUFFER_SECONDS=1.0`. The environment
variables `QUEUEVISION_MODEL`, `QUEUEVISION_DEVICE`, `QUEUEVISION_CONFIDENCE`
and `QUEUEVISION_IMGSZ` override the first defaults (the tracker constants
have no environment variable).

### `ai_engine/detection/`
* `types.py`: `PersonDetection` moved here, with no model imports.
* `detector.py`: `PersonDetector` also loads CoreML `.mlpackage` models
  (device reported as `coreml`, `--device` ignored). `resolve_model_path()`
  caches only a bare string name in `ai_engine/models/`; any path with a
  directory part or a `Path` object is used as given. `ultralytics` is
  imported lazily so the module imports without torch.

### `ai_engine/tracking/tracker.py`
* `TrackerSettings(new_track_confidence, input_confidence, buffer_seconds)`.
* `resolve_tracker_config()` copies Ultralytics' installed `bytetrack.yaml`,
  sets `new_track_thresh` and `track_buffer = round(buffer_seconds × effective FPS)`.
* `write_tracker_config()` writes it to
  `<tmp>/queuevision/trackers/bytetrack_<hash>.yaml` (content-hashed name).
* `PersonTracker.configure(effective_fps)` regenerates and resets;
  `update()` calls `track(conf=input_confidence, tracker=<resolved yaml>)`;
  `describe()` feeds `configuration.tracker` in the metrics JSON.

### `ai_engine/queue/`
* `camera_config.py`: `CameraConfig`, `MembershipSettings`, `SmoothingSettings`,
  `StitchingSettings`, `ServiceRateSettings`, `AlertSettings`,
  `load_camera_config()`, `save_camera_config()`, `CameraConfigError`.
  Validation: at least 3 zone points in [0, 1], positive rule values, unknown
  keys rejected. Optional `service_zone` (polygon) or `service_line` (exactly
  2 points), never both; `service_rate.method` is `window` or `ewma`; alert
  thresholds may be null and alerts are enabled when at least one is set.
* `zone.py`: `QueueZone` (normalized polygon, `to_pixels()`, `contains()`) and
  a NumPy ray-casting `point_in_polygon()`.
* `membership.py`: `QueueMembershipTracker`, `MemberStatus`, `ZoneEvent`,
  `foot_point()`. Rules:
  * Membership uses the foot point (bottom-centre of the box).
  * Dwell: inside the zone for `min_dwell_seconds`.
  * Speed: net foot displacement over the dwell window ÷ mean box height ÷
    time must be at most `max_speed_heights_per_second`.
  * `exit_grace_seconds`: a member out of the zone (or unseen) for less than
    this stays a member; state for tracks unseen for longer is dropped.
  * `zone_enter` timestamp = confirmation time; `zone_entered_s` = first time
    in the zone. `zone_exit` timestamp = last time seen in the zone.
  * Exposes per-track in-zone flags (`last_in_zone_flags`) for the lifecycle.
* `queue_length.py`: `QueueLengthEstimator`, sliding-window median.
* `event_schema.py`: `SCHEMA_VERSION = "1.0"`, `EVENT_TYPES`, the event
  dataclasses (`ZoneEvent`, `QueueJoinEvent`, `ServiceStartEvent`,
  `QueueAbandonEvent`, `CongestionEvent`) with `to_dict()`. `ZoneEvent` is
  re-exported from `membership.py`. Contract: [event_schema.md](event_schema.md).
* `service_point.py`: `ServiceZone` (foot point inside the polygon) and
  `ServiceLine` (the step from the previous to the current foot point crosses
  or touches the segment; no previous point means not reached).
* `stitching.py`: `TrackStitcher.update()` returns `track_id -> person_key`
  (`p0001`, ... per video). A track id never seen before is linked to a person
  who was last seen inside the queue zone, `0 < gap <= max_gap_seconds` ago and
  within `max_distance_heights` box heights, nearest first, one new track per
  person. A retired id that reappears gets a new key. Lost persons are
  forgotten after the gap. Disabled stitching gives every id its own key.
* `events.py`: `QueueLifecycle` (per `person_key`): emits `queue_join` on the
  first `zone_enter` of a person (timestamp = earliest zone entry across their
  tracks), `service_start` when a joined person reaches the service point,
  and `queue_abandon` when an unserved joined person has not been in the zone
  for `abandon_after_seconds`. Counts `service_visits_without_join`; exposes
  `waiting_count`, `pending_abandon_count` and the tracks that just reached the
  service point (overlay colour).
* `waiting_time.py`: `WaitLog` (`CompletedWait`, `AbandonedWait`, summary with
  mean, median, linear-interpolated p90, throughput per minute, abandonment
  rate), `ServiceRate`, `ServiceRateEstimator` (window or EWMA, low/high from
  the recent estimates), `WaitPrediction` and `predict_wait()`.
* `alerts.py`: `CongestionMonitor` (start and end after `min_duration_seconds`,
  then `cooldown_seconds`).
* `analyzer.py`: `QueueAnalyzer(camera, stitching_enabled=None)`: `update()`
  returns a `QueueFrameState`; `report(duration_s)` returns the Sprint 3 part
  of the metrics JSON. Order: stitcher, membership, lifecycle, wait log and
  service rate, queue length, prediction, congestion monitor.

### `ai_engine/pipeline.py`
`iter_frame_results()` and `FrameResult` (see the architecture document).
`source_fps()` falls back to 30 FPS for timestamps when a source reports none.

### `ai_engine/video/`
* `video_loader.py`: `VideoLoader` accepts a camera index; `skip()` grabs
  without decoding; unknown live FPS is measured over 30 frames (buffered, not
  lost) and recorded as `fps_source: "measured"`; `capture_factory` allows
  mocking. `source_name()` gives `camera<N>` for a webcam.
* `transcode.py`: `reencode_h264()` (libx264, yuv420p, veryfast,
  `+faststart`, no audio). Missing ffmpeg or a failure keeps the mp4v file and
  logs a warning.

### `ai_engine/visualization/`
* `metrics_overlay.py`: `draw_queue_zone()`, `draw_service_point()`,
  status-coloured `draw_people()` with a foot dot (magenta for people at the
  service point), `draw_alert_banner(frame, reasons, position="top")` (also
  `"bottom"`), `draw_metrics_panel()` with a separate "Queue length" line plus
  service-rate and predicted-wait lines, `format_duration()`, and
  `PANEL_POSITIONS`.
* `privacy.py`: `blur_heads()` Gaussian-blurs the top `head_fraction` of each
  box, in place, before boxes are drawn.

### `ai_engine/metrics/queue_outputs.py`
`QueueTimeSeriesWriter` (first frame of each video second, with service rate
and prediction columns), `ZoneEventWriter` (zone events only), `WaitsWriter`,
`AlertWriter`, `EventLogWriter` (JSON Lines, one `to_dict()` per event),
`QueueSummary` (counts every event type). Standard `csv` and `json` only.

### `ai_engine/evaluation/`
* `mot_eval.py`: `MotWriter`, `load_ground_truth()`, `load_tracks()`,
  `evaluate_tracks()`, our own `iou_matrix()` and `drop_distractor_matches()`.
* `queue_eval.py`: `read_predictions()`, `read_annotations()`, `compare_counts()`.
* `wait_eval.py`: `read_ground_truth_waits()`, `read_predicted_waits()`,
  `read_timeseries_predictions()`, `match_by_service_time()` (one-to-one,
  nearest service time within the tolerance), `evaluate_waits()`,
  `evaluate_predictions()`, `evaluate_throughput()`.

### `ai_engine/run_tracking.py` hooks (Sprint 4)
* `process_video(..., on_frame=None, should_stop=None)`. `on_frame` receives a
  frozen `FrameUpdate` (`frame_index`, `frames_total`, `timestamp_s`,
  `people_in_frame`, `processing_fps`, `queue`) after the frame has been drawn
  and written. `should_stop` is polled like the `q` key of `--show`; when it
  returns true the loop ends and the files and report for the processed part
  are still written. The CLI passes neither, and its outputs are unchanged.

### `api/` (Sprint 4)
* `settings.ApiSettings`: frozen dataclass; `from_env()` reads
  `QUEUEVISION_DATA_DIR`, `QUEUEVISION_CAMERA_DIR`, `QUEUEVISION_MAX_UPLOAD_MB`,
  `QUEUEVISION_HOST`, `QUEUEVISION_PORT`, `QUEUEVISION_MODEL`, `QUEUEVISION_DEVICE`.
* `db`: `connect` (WAL, foreign keys), `init_db`, and small repository
  functions for videos, events (which also fill `waits` and `alerts`), time
  series and reports; `mark_interrupted` runs at start-up.
* `jobs.JobManager`: `start`, `stop`, `submit`, `cancel`, `subscribe`,
  `live_state`, `queue_depth`. The worker thread loads the camera config,
  builds a tracker from the factory, calls `process_video` and stores results
  once per second of video time. `default_tracker_factory` loads the detector
  once; tests pass a fake factory, so API tests need no torch.
* `main.create_app(settings, tracker_factory)`: REST routes, `WS /ws/videos/{id}`
  and the static test page. Upload is streamed to disk in 1 MiB chunks with a
  size check; `GET /api/videos/{id}/video` is a `FileResponse` (range requests).
* Full endpoint and message reference: [api.md](api.md).

### `ai_engine/tools/prepare_cad.py` (Sprint 4)
* `--source` (zip or folder), `--sequences` (default 10 25 26 27 39 40 41),
  `--fps` (default 30, an assumption), `--video-dir`, `--annotation-dir`,
  `--force`. Writes `cad_seqNN.mp4`, `cad_seqNN_queue_counts.csv` and
  `cad_seqNN.json` (records `fps_assumed`). Reads only
  `seqNN/frameN.jpg` and `seqNN/annotations.txt` from the zip, in memory.

### Command-line programs
* `run_tracking.py`: flags are listed in the README. `RunOptions` and
  `process_video()` consume `iter_frame_results()`. The report adds
  `configuration.{frame_stride, effective_fps, tracker, blur_heads,
  head_fraction, video_codec}` and, with a camera, a `queue` section that
  now also holds `stitching`, `service_point`, `waiting`, `service_rate`,
  `alerts`, `waiting_at_end`, `pending_abandon_at_end` and
  `service_visits_without_join`. `--no-stitching` forces stitching off.
  The congestion banner is drawn at the bottom when the metrics panel is at a
  top position, otherwise at the top.
* `evaluate_tracking.py`: `--tracks`, `--sequence-dir`, `--video`,
  `--frame-stride`, `--output`.
* `evaluate_queue.py`: `--predicted`, `--annotations`, `--column`,
  `--tolerance`, `--output`.
* `evaluate_waiting.py`: `--predicted-waits`, `--ground-truth`,
  `--timeseries`, `--duration`, `--tolerance`, `--output`.
* `tools/draw_zone.py`: `--video`, `--config`, `--target`, `--camera-id`,
  `--points`, `--preview`, `--frame-index`. Setting a service zone removes the
  service line and vice versa; a service point needs an existing config.
* `tools/make_annotation_kit.py`: `--video`, `--camera-config`, `--every`,
  `--output-dir`, `--force`. Also writes the `<name>_waits_gt.csv` template.
* `tools/export_model.py`: `--model`, `--imgsz`, `--half`.

## Processing loop (per processed frame)

1. `iter_frame_results`: skip `stride - 1` frames without decoding, read one frame.
2. `tracker.update(frame)` (timed, device-synchronized), then
   `queue_analyzer.update(...)` if a camera config is set; yield `FrameResult`.
3. `process_video`: optional `blur_heads`, `draw_queue_zone`,
   `draw_service_point`, `draw_people`, `draw_alert_banner` (while an alert is
   active), `draw_metrics_panel`, `writer.write`.
4. Record performance, MOT rows, queue time series and events (zone events,
   waits, alerts and the JSONL event log).
5. If `--show`: `imshow`/`waitKey`, after timing, so it is excluded.
6. After the loop: ffmpeg H.264 re-encode, then the metrics JSON.

## Engineering decisions and deviations

| Item | Decision |
|---|---|
| ByteTrack input threshold | Sprint 1 passed only detections at `--confidence` to ByteTrack, which starved its low-score association stage. Detections down to `TRACKER_INPUT_CONFIDENCE=0.1` are now passed in; `--confidence` is the threshold to start a new track. `--tracker-input-confidence 0.35` reproduces Sprint 1 (verified: identical unique track IDs). |
| Track buffer | Defined in seconds and converted using the effective FPS. In Ultralytics 8.4.198 `BYTETracker` uses `track_buffer` directly as processed frames (`max_frames_lost = track_buffer`); the older `frame_rate / 30 × track_buffer` formula is not in this version. |
| Generated tracker YAML | Written to the system temp folder, not the repo; settings are recorded in the metrics JSON. |
| Membership events only for confirmed members | Walkers through the zone must not look like queue joins. |
| No end-of-video events | Inventing exits or outcomes at the end would distort waiting times. Members still queued are reported in `queue.members_still_in_queue_at_end`, `queue.waiting_at_end` and `queue.pending_abandon_at_end`. |
| Join time = first time in the zone | The earliest zone entry across stitched tracks, not the confirmation time (`detected_s`). Confirmation lags by the dwell time. |
| Abandon timeout | Decided after `max(exit_grace_seconds, stitching max_gap_seconds)` (the gap only with stitching on), and only when a service point exists. |
| Service point placement | It must lie inside (or right at the edge of) the queue zone. A member who must walk outside the zone for longer than the abandon timeout to reach it is recorded as abandoned (observed with stitching off, where the timeout is `exit_grace_seconds`). |
| Service rate | `insufficient_data` until `min_history_seconds` of video and `min_services` services. A computed rate of exactly 0 is a valid estimate. |
| Wait prediction | `queue_length / service rate`. `unknown` when the rate is insufficient or zero, never 0 or infinity. Low/high come from the largest and smallest rate estimated within the window; this is a range, not a statistical interval. |
| Alerts | The condition must hold for `min_duration_seconds` to start and be false for `min_duration_seconds` to end an alert; a new alert waits `cooldown_seconds` after the previous end. |
| Stitching can merge different people | In dense non-queue crowds it can link different people; it chained up to 8 track ids on MOT17-09 (street footage). It must be validated on real queue footage; `--no-stitching` is available for comparison. |
| Banner position | The red congestion banner is drawn at the bottom when the metrics panel is at a top position so that it does not cover the panel title. |
| Anonymous `person_key` | A per-video counter; events and waits contain no identities and no images. |
| Raw count includes briefly occluded members | They stay members during the grace period; the median smoothing handles the rest. |
| `queue_length` is a median | Removes one-frame spikes without smearing them across the window. |
| Video time for all timestamps | `frame_index / FPS`, independent of processing speed. |
| `sys.path[0]` fix in scripts | Running a script directly puts its folder on `sys.path`; `ai_engine/queue` then shadows the standard-library `queue` module. Found as a real bug in Sprint 2; scripts now use the project root. |
| Own NumPy IoU for MOT scoring | motmetrics 1.4.0 is used for the metrics, but its `iou_matrix` calls `np.asfarray`, which NumPy 2 removed. |
| MOT scoring rules | Class 1 with consider flag 1; hypotheses matched to distractor classes 2, 7, 8, 12 are removed (as in the official devkit); IoU 0.5. Frame alignment was checked by shifting tracks by ±2 frames; offset 0 was best. |
| Ground-truth scaling | GT is scaled from 1920×1080 to the 960×540 preview, and evaluation refuses to run if the video frame count differs from `seqLength`. |
| CoreML export in a separate venv | coremltools 9.0 fails with torch 2.15.0; `requirements-coreml.txt` pins torch 2.7.0 for the export only. The `.mlpackage` is then used from the main environment. |
| H.264 via ffmpeg after writing | OpenCV only writes mp4v reliably; ffmpeg is optional and the fallback is explicit. |
| Head blur is best-effort | Uses the top fraction of the person box, no extra model; saved video only. See [privacy.md](privacy.md). |
| Webcam untested on hardware | Covered by a mocked capture only. |
| `pandas` removed | CSV is handled by the standard `csv` module. |
| Requirements split | `requirements.txt` (runtime), `requirements-dev.txt`, `requirements-coreml.txt`. |
| Lazy imports and CI | Unit tests run without torch/ultralytics; the CI `lint-and-unit` job proves it. The integration job is `continue-on-error` because it downloads weights. |
| `person_detection.py` and `media/` removed | The prototype's polygon and in-zone counting now live in `ai_engine/queue/` with a foot-point test. It stays in Git history. |
| ruff | Configured in `pyproject.toml` (line length 110, `E F W I B UP SIM RUF`); `ruff check` and `ruff format --check` pass. |
| No real queue footage yet | Queue accuracy is unmeasured; the evaluation tooling is ready for it (see [datasets/README.md](../datasets/README.md)). |

## Sprint 1 reference

* `utils/device.py`: `select_device()` resolves `auto` to `cuda`, `mps`, then
  `cpu`; explicit unavailable requests raise `DeviceUnavailableError`.
  `synchronize()` syncs MPS/CUDA and does nothing on CPU.
* `VideoLoader` rejects missing files, unsupported extensions
  (.mp4/.avi/.mov/.mkv/.m4v) and unreadable files with `VideoLoadError`.
  `VideoWriter` creates parent folders, rejects frames of the wrong size and
  counts frames written.
* `PerformanceTracker.record_frame()` keeps running totals (true averages)
  and a 30-frame window (overlay only); `build_report()` and `save_json()`
  produce the metrics JSON.
* MOT17 videos were converted from WebM to MP4 for reliable OpenCV decoding.
* `lap` is pinned explicitly because ByteTrack needs it; otherwise Ultralytics
  tries to pip-install it at runtime.
