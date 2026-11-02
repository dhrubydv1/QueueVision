# QueueVision

**AI-Based Queue Length and Waiting-Time Estimation System**
Team: Dhrub Narayan Yadav and Prabin Kumar Sharma

## 1. Introduction

QueueVision watches a fixed camera view of a queue and will estimate how long
the queue is and how long people wait. This repository currently contains
**Sprint 1**: the AI engine that detects and tracks people in recorded video
and measures its own performance.

## 2. Sprint 1 objective

Given a recorded video, the engine:

1. Detects people with **YOLO11n** (COCO class 0 only).
2. Tracks them across frames with **ByteTrack** and assigns track IDs.
3. Draws boxes, track IDs, confidence and a metrics panel on every frame.
4. Measures **inference FPS** and **overall processing FPS** separately.
5. Writes an annotated MP4 and a metrics JSON file.

> "People in frame" is the number of people visible in the current frame.
> It is **not** the queue length. Queue-region counting starts in Sprint 2.

## 3. Technology stack

| Component | Technology (tested version) |
|---|---|
| Language | Python 3.11.16 |
| Detection | Ultralytics YOLO11n (`ultralytics==8.4.198`) |
| Tracking | ByteTrack (Ultralytics built-in, `lap==0.5.13`) |
| Deep learning | PyTorch 2.15.0 (MPS / CPU) |
| Video | OpenCV 5.0.0 (`opencv-python==5.0.0.93`) |
| Numerics | NumPy 2.4.6 |
| Metrics export | JSON |
| Testing | pytest 9.2.0 |

## 4. Architecture

```
video file ──► VideoLoader ──► PersonTracker ──► draw_people ──► VideoWriter ──► *_tracked.mp4
               (OpenCV)        (YOLO11n +        draw_metrics_panel
                                ByteTrack)            ▲
                                    │                 │
                                    └──► PerformanceTracker ──► *_metrics.json
```

| Package | Responsibility |
|---|---|
| `ai_engine/video/` | `VideoLoader` (sequential frame reading and metadata) and `VideoWriter` |
| `ai_engine/detection/` | `PersonDetector` (loads YOLO once, filters to person, timed inference) |
| `ai_engine/tracking/` | `PersonTracker` (ByteTrack IDs, per-video reset) |
| `ai_engine/visualization/` | Boxes, ID labels and the metrics panel |
| `ai_engine/metrics/` | `PerformanceTracker` (FPS and statistics) and JSON export |
| `ai_engine/utils/device.py` | Hardware detection (CUDA → MPS → CPU) and device sync |
| `ai_engine/run_tracking.py` | Command-line pipeline |

See [docs/architecture.md](docs/architecture.md) and
[docs/implementation.md](docs/implementation.md) for details.

## 5. Installation

Requirements: macOS (Apple Silicon or Intel), Python 3.11, and optionally
`ffmpeg` for preparing dataset videos.

```bash
git clone <repo-url> QueueVision && cd QueueVision
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

## 6. Environment check

```bash
python -c "import torch, cv2, ultralytics; print(torch.__version__, torch.backends.mps.is_available(), cv2.__version__, ultralytics.__version__)"
```

Device selection (`--device auto`) uses CUDA if it is genuinely available,
then Apple MPS if it is built and available, then CPU. Asking for `--device mps`
or `--device cuda` on hardware without it fails with a clear error. The engine
never falls back silently, so a report never claims acceleration that was not used.

Optional defaults can be set with environment variables (see `.env.example`).

## 7. Model configuration

| Setting | Default | CLI flag |
|---|---|---|
| Model weights | `ai_engine/models/yolo11n.pt` | `--model` |
| Confidence threshold | 0.35 | `--confidence` |
| Inference image size | 640 | `--imgsz` (multiple of 32) |
| Person class | COCO id 0 | fixed |
| Tracker | `bytetrack.yaml` | fixed |

The weights download automatically to `ai_engine/models/` on first use and
are not committed to Git. A bare name such as `--model yolo11s.pt` is also
cached in `ai_engine/models/`, which makes comparing YOLO11s later a one-flag change.

## 8. Datasets

See [datasets/README.md](datasets/README.md). Sprint 1 used two short
MOTChallenge MOT17 preview videos (CC BY-NC-SA 3.0). Videos stay out of Git.

## 9. Running

```bash
source .venv/bin/activate

# Full run, automatic device selection
python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --device auto

# Force CPU, custom threshold/size, show a live window (press q to stop)
python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 \
  --device cpu --confidence 0.35 --imgsz 640 --show

# Quick check on the first 100 frames
python ai_engine/run_tracking.py --video datasets/raw/mot17_04.mp4 --max-frames 100

# Tests
pytest -v                    # all tests (integration tests use the real model)
pytest -v -m "not integration"   # fast unit tests only
```

`python -m ai_engine.run_tracking ...` also works. The command exits with
status 1 and logs an error for a missing or unreadable video, an unavailable
device, or invalid settings.

## 10. Outputs

For an input `datasets/raw/<name>.mp4`:

| File | Content |
|---|---|
| `outputs/videos/<name>_tracked.mp4` | Annotated video (mp4v codec, same size and FPS as the input) |
| `outputs/metrics/<name>_metrics.json` | Performance and detection statistics, video metadata, configuration |
| `outputs/logs/<name>_tracking.log` | Run log |

Use `--output DIR` to write to `DIR/videos`, `DIR/metrics` and `DIR/logs` instead.

## 11. Performance metrics

| Metric | Meaning |
|---|---|
| `average_inference_fps` | frames ÷ total time inside YOLO + ByteTrack, with MPS/CUDA synchronized before each timer stops |
| `average_processing_fps` | frames ÷ total per-frame time: decoding, inference, drawing and writing. The `--show` window is excluded. |
| `total_runtime_seconds` | Wall-clock time of the whole run, including model loading and warm-up |
| `maximum_people_detected_in_a_frame` | Highest number of tracked people in any single frame |
| `unique_track_ids_assigned` | Number of distinct ByteTrack IDs. This is **not** the number of unique people, because IDs can change after occlusion. |

All timings use `time.perf_counter()`. One blank warm-up frame runs before
timing starts, so one-time model setup does not skew the averages. The
on-screen FPS values are rolling averages over the last 30 frames. The JSON
values are whole-video averages.

Measured on an Apple M1 MacBook Air (8 GB): about 25–26 inference FPS and
about 21–22 processing FPS on MPS for 960×540 video. See
[docs/testing_report.md](docs/testing_report.md).

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| `MPS was requested but is not available` | Use `--device auto` or `--device cpu`, and check `torch.backends.mps.is_available()`. Intel Macs have no MPS. |
| `OpenCV could not open the video` | Re-encode with `ffmpeg -i in.webm -c:v libx264 -pix_fmt yuv420p out.mp4` |
| Weights download fails | Download `yolo11n.pt` manually from the Ultralytics assets release into `ai_engine/models/` |
| `ModuleNotFoundError: ai_engine` | Run from the repository root, with the venv activated |
| `--show` window does not appear | Requires a desktop session. Omit `--show` over SSH. |
| Output MP4 does not play in a browser | The `mp4v` codec plays in QuickTime and VLC. Re-encode to H.264 with ffmpeg for web use. |
| Small or distant people are missed | Try `--imgsz 960` or `--imgsz 1280`, or a larger model (`--model yolo11s.pt`) |

## 13. Roadmap

| Sprint | Scope |
|---|---|
| **1 (done)** | Video I/O, YOLO11n person detection, ByteTrack, overlay, FPS metrics |
| 2 | Queue polygon / ROI configuration, in-queue counting from tracked people |
| 3 | Queue entry/exit events, service rate, waiting-time estimation, congestion alerts |
| 4 | FastAPI backend, WebSocket streaming, SQLite persistence |
| 5 | Next.js dashboard, live monitoring, analytics and historical reports |

## Legacy prototype

`person_detection.py` is the earlier single-file prototype (image or video
detection with a hard-coded queue-zone polygon). It is kept for reference
while its queue-zone logic is moved into the modular engine in Sprint 2.
