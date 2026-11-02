# QueueVision Architecture (Sprint 1)

## Data flow

```
                         ┌─────────────────────────────────────────────┐
                         │ run_tracking.py (CLI / process_video)       │
                         └─────────────────────────────────────────────┘
                                             │
   ┌─────────────┐  frame (BGR ndarray)  ┌────▼──────────────┐ list[PersonDetection]
   │ VideoLoader │ ─────────────────────►│  PersonTracker    │─────────────────────┐
   │  (OpenCV)   │                       │  ByteTrack state  │                     │
   └─────────────┘                       │        │          │                     ▼
         │ VideoMetadata                 │  PersonDetector   │          ┌──────────────────────┐
         │ (w, h, fps, frames)           │  YOLO11n (loaded  │          │ visualization        │
         │                               │  once, MPS/CPU)   │          │ draw_people          │
         │                               └────────┬──────────┘          │ draw_metrics_panel   │
         │                                        │ inference seconds   └──────────┬───────────┘
         │                               ┌────────▼──────────┐                     │ annotated frame
         │                               │ PerformanceTracker│◄── frame seconds ───┤
         │                               └────────┬──────────┘                     ▼
         │                                        │ report            ┌──────────────────────┐
         └───────────────────────────────────────►│                   │ VideoWriter (mp4v)   │
                                         save_json▼                   └──────────┬───────────┘
                               outputs/metrics/<name>_metrics.json     outputs/videos/<name>_tracked.mp4
```

## Components

### Video loader (`ai_engine/video/video_loader.py`)
`VideoLoader` checks the path and extension, opens the file with
`cv2.VideoCapture`, and exposes a `VideoMetadata` record (size, FPS, frame
count, duration). It yields frames one at a time, so a video is never held
in memory. It is a context manager, so the capture is released even when
processing raises an exception. `VideoWriter` writes annotated frames at
the source FPS and size.

### YOLO detection (`ai_engine/detection/detector.py`)
`PersonDetector` resolves the device, then loads YOLO11n **once**. It
downloads the weights into `ai_engine/models/` if they are missing. It holds
the inference settings (classes=[0], confidence, imgsz, device).
`run_timed()` wraps any Ultralytics call (`predict` or `track`), synchronizes
the device, and records the wall-clock time. `parse_person_boxes()` converts
an Ultralytics `Results` object into immutable `PersonDetection` records
(bbox, confidence, optional track ID). Any non-person class is dropped.

### ByteTrack (`ai_engine/tracking/tracker.py`)
`PersonTracker` wraps the detector's **same model instance** and calls
`model.track(..., tracker="bytetrack.yaml")`. The first frame after
construction or `reset()` uses `persist=False`, which makes Ultralytics build
a new ByteTrack instance with IDs starting at 1. Later frames use
`persist=True` so tracks carry over. Detections that ByteTrack has not
confirmed have no ID and are not returned. IDs belong to one video only and
are not permanent identities.

### Visualization (`ai_engine/visualization/metrics_overlay.py`)
These are pure drawing functions that modify the frame in place:
`draw_people` draws green boxes with `ID n  conf` labels, and
`draw_metrics_panel` draws a translucent dark panel with the QueueVision
brand, the people-in-frame count, rolling inference and processing FPS, the
frame number and the device. They have no model or I/O dependencies, so
Sprint 2 can add a `draw_queue_zone` beside them.

### Performance metrics (`ai_engine/metrics/performance.py`)
`PerformanceTracker` keeps running totals, which are used for the true
averages, and a 30-frame window, which is used only for the live overlay.
Inference time is YOLO plus ByteTrack. Processing time runs from just before
the frame is decoded until it has been written. The optional GUI display is
excluded. `build_report()` produces the JSON schema, and `save_json()` writes it.

### Output generation (`ai_engine/run_tracking.py`)
`process_video()` connects the components for one video: it warms up, then
for each frame reads, tracks, draws, writes and records. It then saves the
report. `main()` parses CLI arguments, configures logging (console and file),
loads the detector and turns known errors into exit code 1.

## Design decisions

| Decision | Reason |
|---|---|
| Ultralytics' built-in ByteTrack | Required by the sprint. It runs in the same `track()` call, which avoids a second detection pass. |
| Tracker reset by `persist=False` instead of reaching into `predictor.trackers` | Uses the public API, so it is less fragile across Ultralytics versions. |
| Explicit device errors (no silent CPU fallback) | Reports must not claim acceleration that was not used. `auto` is the safe path. |
| Device sync inside the timed block | MPS/CUDA calls are asynchronous. Without a sync, inference time is under-reported. |
| Warm-up frame excluded from metrics | First-call model setup and MPS kernel compilation took about 8 s in testing and would distort the average FPS. |
| Frozen `PersonDetection` dataclass | Gives later sprints (ROI tests, entry/exit events) a stable, typed contract. |

## Extension points for later sprints

* **Sprint 2 (ROI and queue count):** add `ai_engine/queue/roi.py` that takes
  `list[PersonDetection]` and a polygon and returns the people inside it,
  using the bbox bottom-centre (feet) point. The tracker and detector need no changes.
* **Sprint 3 (entry/exit, waiting time):** use `track_id` across frames
  to record when each ID enters and leaves the ROI. Add tolerance for ID switches.
* **Sprint 4 (FastAPI):** `process_video()` already returns a report dict.
  A frame-level generator can be split out of it for WebSocket streaming.
* **Model comparison:** `--model yolo11s.pt`. The report records the model name and path.
