# Implementation Notes (Sprint 2)

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
  `load_camera_config()`, `save_camera_config()`, `CameraConfigError`.
  Validation: at least 3 zone points in [0, 1], positive rule values, unknown
  keys rejected.
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
* `queue_length.py`: `QueueLengthEstimator`, sliding-window median.
* `analyzer.py`: `QueueAnalyzer.update()` returns a `QueueFrameState`.

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
* `metrics_overlay.py`: `draw_queue_zone()`, status-coloured `draw_people()`
  with a foot dot, `draw_metrics_panel()` with a separate "Queue length" line
  and `PANEL_POSITIONS`.
* `privacy.py`: `blur_heads()` Gaussian-blurs the top `head_fraction` of each
  box, in place, before boxes are drawn.

### `ai_engine/metrics/queue_outputs.py`
`QueueTimeSeriesWriter` (first frame of each video second),
`ZoneEventWriter`, `QueueSummary`. Standard `csv` module only.

### `ai_engine/evaluation/`
* `mot_eval.py`: `MotWriter`, `load_ground_truth()`, `load_tracks()`,
  `evaluate_tracks()`, our own `iou_matrix()` and `drop_distractor_matches()`.
* `queue_eval.py`: `read_predictions()`, `read_annotations()`, `compare_counts()`.

### Command-line programs
* `run_tracking.py`: flags are listed in the README. `RunOptions` and
  `process_video()` consume `iter_frame_results()`. The report adds
  `configuration.{frame_stride, effective_fps, tracker, blur_heads,
  head_fraction, video_codec}` and, with a camera, a `queue` section.
* `evaluate_tracking.py`: `--tracks`, `--sequence-dir`, `--video`,
  `--frame-stride`, `--output`.
* `evaluate_queue.py`: `--predicted`, `--annotations`, `--column`,
  `--tolerance`, `--output`.
* `tools/draw_zone.py`: `--video`, `--config`, `--camera-id`, `--points`,
  `--preview`, `--frame-index`.
* `tools/make_annotation_kit.py`: `--video`, `--camera-config`, `--every`,
  `--output-dir`, `--force`.
* `tools/export_model.py`: `--model`, `--imgsz`, `--half`.

## Processing loop (per processed frame)

1. `iter_frame_results`: skip `stride - 1` frames without decoding, read one frame.
2. `tracker.update(frame)` (timed, device-synchronized), then
   `queue_analyzer.update(...)` if a camera config is set; yield `FrameResult`.
3. `process_video`: optional `blur_heads`, `draw_queue_zone`, `draw_people`,
   `draw_metrics_panel`, `writer.write`.
4. Record performance, MOT rows, queue time series and events.
5. If `--show`: `imshow`/`waitKey`, after timing, so it is excluded.
6. After the loop: ffmpeg H.264 re-encode, then the metrics JSON.

## Engineering decisions and deviations

| Item | Decision |
|---|---|
| ByteTrack input threshold | Sprint 1 passed only detections at `--confidence` to ByteTrack, which starved its low-score association stage. Detections down to `TRACKER_INPUT_CONFIDENCE=0.1` are now passed in; `--confidence` is the threshold to start a new track. `--tracker-input-confidence 0.35` reproduces Sprint 1 (verified: identical unique track IDs). |
| Track buffer | Defined in seconds and converted using the effective FPS. In Ultralytics 8.4.198 `BYTETracker` uses `track_buffer` directly as processed frames (`max_frames_lost = track_buffer`); the older `frame_rate / 30 × track_buffer` formula is not in this version. |
| Generated tracker YAML | Written to the system temp folder, not the repo; settings are recorded in the metrics JSON. |
| Membership events only for confirmed members | Walkers through the zone must not look like queue joins. |
| No end-of-video exits | Inventing exits at the end would distort waiting times. The count of members still queued is stored in `queue.members_still_in_queue_at_end`. |
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
