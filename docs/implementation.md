# Implementation Notes (Sprint 1)

## Module reference

### `ai_engine/config.py`
Holds the project paths and defaults: `MODEL_NAME="yolo11n.pt"`,
`PERSON_CLASS_ID=0`, `CONFIDENCE=0.35`, `IMAGE_SIZE=640` and
`TRACKER_CONFIG="bytetrack.yaml"`. The `QUEUEVISION_*` environment variables
override the defaults.

### `ai_engine/utils/device.py`
* `select_device(requested)` resolves `auto` to `cuda`, then `mps`, then `cpu`,
  based on `torch.cuda.is_available()` and
  `torch.backends.mps.is_built() and is_available()`. Explicit `mps` or `cuda`
  requests raise `DeviceUnavailableError` when that device is unavailable.
* `synchronize(device)` calls `torch.mps.synchronize()` or
  `torch.cuda.synchronize()`. It does nothing on CPU.

### `ai_engine/video/video_loader.py`
* `VideoLoader(path)` raises `VideoLoadError` for a missing file, an
  unsupported extension (.mp4/.avi/.mov/.mkv/.m4v), or a file OpenCV cannot
  open. Provides `.metadata`, `.read()`, iteration over `(index, frame)`, and
  `.release()`, which is safe to call more than once. It is a context manager.
* `VideoWriter(path, fps, (w, h))` uses the mp4v codec and creates parent
  folders. It rejects frames of the wrong size and counts the frames written.

### `ai_engine/detection/detector.py`
* `PersonDetection(bbox, confidence, track_id=None)` has `.center` and
  `.to_dict()`, which returns `{"class": "person", "confidence", "bbox", ["track_id"]}`.
* `load_yolo_model(path)` wraps any loading or download failure in `ModelLoadError`.
* `PersonDetector(model_path, confidence, image_size, device, model=None)`
  validates the settings, resolves the device and loads the model. It
  provides `detect(frame)`, `run_timed(method, frame, **kw)` and
  `last_inference_seconds`.

### `ai_engine/tracking/tracker.py`
* `PersonTracker(detector, tracker_config)` provides `update(frame)`
  (tracked people only), `reset()` (call it between videos), `warmup(shape)`
  and `last_inference_seconds`.

### `ai_engine/metrics/performance.py`
* `PerformanceTracker.record_frame(inference_s, processing_s, people_count, track_ids)`.
* Its properties are the average inference and processing FPS (totals), the
  rolling FPS (last 30 frames), the maximum and average people per frame, and
  the set of unique track IDs.
* `build_report(device, total_runtime_seconds, model, tracker, extra)` returns
  the JSON schema required by the sprint, extended with extra fields.
* `save_json(data, path)`.

### `ai_engine/visualization/metrics_overlay.py`
* `draw_people(frame, people)` draws green boxes with black-on-green `ID n  0.87` labels.
* `draw_metrics_panel(frame, OverlayStats(...))` draws a 60%-opacity dark
  panel with white text, clipped to the frame.

### `ai_engine/run_tracking.py`
The CLI flags are `--video` (required), `--model`, `--output`,
`--device {auto,cpu,mps,cuda}`, `--confidence`, `--imgsz`, `--show` and
`--max-frames`. `process_video()` can be called on its own, and the tests
use it that way.

## Processing loop (per frame)

1. `t0 = perf_counter()`
2. `frame = loader.read()`. The loop ends when this returns None or `--max-frames` is reached.
3. `people = tracker.update(frame)`. This is timed internally, with a device sync.
4. `draw_people`, then `draw_metrics_panel` with the rolling FPS values.
5. `writer.write(frame)`
6. `performance.record_frame(inference, perf_counter() - t0, len(people), ids)`
7. If `--show` is set: `imshow`/`waitKey`. This happens after timing ends, so it is excluded.

## Engineering decisions and deviations from the sprint brief

| Item | Decision |
|---|---|
| Added `ai_engine/metrics/` and `ai_engine/utils/` | The brief lists `tests/test_metrics.py` but no metrics module. Separating metrics and device handling keeps the other modules single-purpose. |
| Added `--max-frames` | Useful for quick checks and CLI tests. The default is the whole video. |
| Added `VideoWriter` to `video_loader.py` | Keeps all OpenCV video I/O in one module. |
| `--show` excluded from processing FPS | The brief notes that GUI rendering affects performance. Excluding it keeps the reports comparable. |
| Kept `person_detection.py` | This is the earlier teammate prototype with a queue zone. It was not deleted because it is useful for Sprint 2. |
| `pandas` is installed but unused | It was requested in the brief and is reserved for analytics in later sprints. |
| `lap` is pinned explicitly | ByteTrack needs it. Otherwise Ultralytics tries to pip-install it at runtime. |
| MOT17 videos converted from WebM to MP4 | The brief asks for an MP4 workflow, and H.264 MP4 decodes reliably in OpenCV on macOS. |
