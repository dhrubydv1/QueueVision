# QueueVision

**AI-Based Queue Length and Waiting-Time Estimation System**
Team: Dhrub Narayan Yadav and Prabin Kumar Sharma

## 1. Introduction

QueueVision watches a fixed camera view of a queue and estimates how long
the queue is and how long people wait. This repository now contains
**Sprint 5**: the Sprint 4 local upload API and Sprint 3 AI engine, plus opt-in accuracy options and validation tooling. The engine
detects and tracks people, decides who is
standing in a configured queue zone, reports the queue length over time,
measures waiting times at a service point, estimates the service rate,
predicts the wait, raises congestion alerts, and can score itself against
ground truth.

## 2. Objectives

Sprint 1 (still the base of the pipeline). Given a recorded video, the engine:

1. Detects people with **YOLO11n** (COCO class 0 only).
2. Tracks them across frames with **ByteTrack** and assigns track IDs.
3. Draws boxes, track IDs, confidence and a metrics panel on every frame.
4. Measures **inference FPS** and **overall processing FPS** separately.
5. Writes an annotated MP4 and a metrics JSON file.

Sprint 2 adds:

1. A **queue zone** per camera (YAML, drawn with a tool) and **queue membership**:
   a person counts as queuing only after standing in the zone for a minimum
   time without walking through.
2. A smoothed **queue length** time series and **zone enter/exit events**.
3. **Evaluation**: tracking accuracy against MOT17 ground truth, and queue
   length against manual counts.
4. A ByteTrack configuration fix, frame skipping, CoreML export, H.264 output,
   optional head blur, webcam input, CI and a ruff-checked code base.

Sprint 3 adds:

1. A **service point** (a line or a zone at the head of the queue) and
   **queue lifecycle events**: `queue_join`, `service_start`, `queue_abandon`
   (plus the Sprint 2 zone events), in a versioned schema
   ([docs/event_schema.md](docs/event_schema.md)).
2. **ID-switch stitching** inside the queue zone, so a person whose ByteTrack
   ID changes is still one person with one join time. `--no-stitching` turns
   it off.
3. **Waiting time** per person (join to service), with mean, median and p90,
   throughput and abandonment rate.
4. **Service rate** (people served per minute, moving window or EWMA) and a
   **predicted wait** with a low/high range. Both say "insufficient data" /
   "unknown" instead of guessing.
5. **Congestion alerts** (queue length and/or predicted wait above a
   threshold for a minimum duration, with a cooldown) and a red banner in the video.
6. **Waiting-time evaluation** against manual ground truth
   (`ai_engine/evaluate_waiting.py`).

Sprint 4 adds a small local web service (`api/`): upload a recorded video,
watch progress and live queue numbers over a WebSocket, then play the annotated
video. Results are stored in SQLite. A plain test page is the demo screen; see
[section 9a](#9a-api-and-demo-page) and [docs/api.md](docs/api.md). The demo
uses clips from the Collective Activity Dataset ([datasets/README.md](datasets/README.md)).

Sprint 6 analysed where the count error comes from, tested 960-px detection and shorter grace/dwell (neither generalized to held-out clips, defaults unchanged), checked dataset licences and added a waiting-time annotation kit; see [docs/sprint6_report.md](docs/sprint6_report.md).

Sprint 5 (accuracy and validation) adds opt-in alternatives and the tooling to
measure them, without changing any default:

1. A **temporal queue-membership classifier** (`membership.algorithm: temporal`)
   with explicit per-person states, and a **guarded track stitcher**
   (`stitching.algorithm: guarded`) that defers linking and rejects ambiguous
   matches.
2. Two new lifecycle events, `service_complete` (service zones only) and
   `queue_exit`, and `service_rate.basis` to choose what the service rate counts.
3. A **replay harness** (`ai_engine/replay_queue.py`): YOLO and ByteTrack run once,
   then every queue-logic variant is scored on the same saved tracks.
4. MOT17 **stitching evaluation**, a **dataset manifest, licence register and
   validator**, a **benchmark** script, a waiting-time evaluator that reads
   per-person ground truth, and **upload validation** for the API.

**Headline result: none of the new queue-counting options beat the Sprint 4
algorithm on the held-out clips, so the defaults are unchanged.** The new code is
available behind configuration switches, with the measurements that show why it
is not the default. Waiting-time accuracy on real video could not be measured
because no footage with per-person ground truth exists. Details:
[docs/sprint5_implementation_report.md](docs/sprint5_implementation_report.md),
[docs/sprint5_initial_audit.md](docs/sprint5_initial_audit.md),
[docs/security_review.md](docs/security_review.md) and the Sprint 5 section of
[docs/testing_report.md](docs/testing_report.md).

> "People in frame" is the number of people visible. "Queue length" is the
> number of confirmed members of the queue. Queue logic runs only when a
> camera config is given; without `--camera-config` the behaviour is Sprint 1.

No real queue footage has been recorded yet. The queue logic is unit-tested
and exercised on MOT17 walking footage, but its accuracy on real queues is
not measured. Stitching in particular can merge different people in a dense
crowd, so it must be validated on real queue footage before its results are trusted. See [datasets/README.md](datasets/README.md).

## 3. Technology stack

| Component | Technology (tested version) |
|---|---|
| Language | Python 3.11.16 |
| Detection | Ultralytics YOLO11n (`ultralytics==8.4.198`) |
| Tracking | ByteTrack (Ultralytics built-in, `lap==0.5.13`) |
| Deep learning | PyTorch 2.15.0 (MPS / CPU) |
| Video | OpenCV 5.0.0 (`opencv-python==5.0.0.93`), optional `ffmpeg` |
| Numerics / config | NumPy 2.4.6, PyYAML 6.0.3 |
| Evaluation | motmetrics 1.4.0, SciPy 1.17.1 |
| Outputs | JSON and CSV (standard library) |
| Testing / lint | pytest 9.2.0, ruff 0.17.3 |
| Dashboard | Next.js 16.4.3, React 19.3.1, Tailwind 4.3.3, TanStack Query 5.105.0, Recharts 3.10.1 on Node v22.23.2 (full list in [section 9c](#9c-dashboard-sprint-7)) |

## 4. Architecture

```
video / webcam ─► VideoLoader ─► iter_frame_results() ─► per-frame FrameResult
                  (OpenCV)        │  PersonTracker (YOLO11n + ByteTrack)
                                  │  QueueAnalyzer (stitching, membership, lifecycle, waits,
                                  │                 service rate, prediction, alerts)  [optional]
                                  ▼
          run_tracking.process_video: blur_heads → draw zone/people/panel → VideoWriter → H.264
                                  └─► PerformanceTracker ─► *_metrics.json
                                  └─► MotWriter / QueueTimeSeriesWriter / ZoneEventWriter /
                                      WaitsWriter / AlertWriter / EventLogWriter ─► tracks, CSVs, JSONL
```

| Package | Responsibility |
|---|---|
| `ai_engine/video/` | `VideoLoader` (files, webcams, frame skipping), `VideoWriter`, H.264 re-encoding |
| `ai_engine/detection/` | `PersonDetector` (YOLO or CoreML, loaded once) and model-free `PersonDetection` |
| `ai_engine/tracking/` | `PersonTracker` and the generated ByteTrack settings |
| `ai_engine/queue/` | Camera config, queue zone, membership rules, queue length, service point, stitching, lifecycle events, waiting time and service rate, alerts, event schema (no model or drawing code) |
| `ai_engine/pipeline.py` | `iter_frame_results()` generator yielding `FrameResult` |
| `ai_engine/visualization/` | Boxes, zone, service point, metrics panel, congestion banner, head blur |
| `ai_engine/metrics/` | `PerformanceTracker`, JSON export, queue CSV writers and summary |
| `ai_engine/evaluation/` | MOT track export and scoring, queue-count comparison, waiting-time comparison |
| `ai_engine/tools/` | `draw_zone`, `make_annotation_kit`, `export_model` |
| `ai_engine/run_tracking.py` | Main command-line pipeline |
| `ai_engine/evaluate_tracking.py`, `ai_engine/evaluate_queue.py`, `ai_engine/evaluate_waiting.py` | Evaluation commands |
| `api/` | Sprint 4 FastAPI service: upload, background job, WebSocket, SQLite, test page. Depends on `ai_engine`; `ai_engine` never imports it |

See [docs/architecture.md](docs/architecture.md),
[docs/implementation.md](docs/implementation.md),
[docs/event_schema.md](docs/event_schema.md),
[docs/api.md](docs/api.md) and
[docs/privacy.md](docs/privacy.md) for details.

## 5. Installation

Requirements: macOS (Apple Silicon or Intel), Python 3.11, and optionally
`ffmpeg` (H.264 output and dataset preparation: `brew install ffmpeg`).

```bash
git clone <repo-url> QueueVision && cd QueueVision
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt    # pytest, ruff, motmetrics, scipy
```

| File | Contents |
|---|---|
| `requirements.txt` | Runtime: ultralytics, torch, torchvision, OpenCV, NumPy, lap, PyYAML |
| `requirements-dev.txt` | Tests, lint and MOT evaluation (pytest, ruff, motmetrics, scipy) |
| `requirements-coreml.txt` | CoreML export only; needs its own venv (section 15) |

## 6. Environment check

```bash
python -c "import torch, cv2, ultralytics; print(torch.__version__, torch.backends.mps.is_available(), cv2.__version__, ultralytics.__version__)"
```

Device selection (`--device auto`) uses CUDA if it is genuinely available,
then Apple MPS if it is built and available, then CPU. Asking for `--device mps`
or `--device cuda` on hardware without it fails with a clear error. The engine
never falls back silently, so a report never claims acceleration that was not used.

Optional defaults can be set with environment variables (see `.env.example`).

## 7. Model and tracker configuration

| Setting | Default | CLI flag |
|---|---|---|
| Model weights | `ai_engine/models/yolo11n.pt` | `--model` (`.pt` or CoreML `.mlpackage`) |
| Confidence threshold | 0.35 | `--confidence` |
| Tracker input confidence | 0.1 | `--tracker-input-confidence` |
| Track buffer | 1.0 s | `--track-buffer-seconds` |
| Inference image size | 640 | `--imgsz` (multiple of 32) |
| Person class | COCO id 0 | fixed |

**How the thresholds work.** ByteTrack needs low-score detections to keep
tracks alive through occlusion, so detections down to
`--tracker-input-confidence` are passed to it. `--confidence` is the minimum
score needed to *start* a new track (it is also the threshold of plain
`PersonDetector.detect()`). The tracker YAML is generated from Ultralytics'
installed `bytetrack.yaml` into the system temp folder, with
`track_buffer = round(buffer seconds × effective FPS)`, where effective FPS is
source FPS ÷ `--frame-stride`. The resolved values are recorded in the metrics
JSON under `configuration.tracker`. `--tracker-input-confidence 0.35`
reproduces Sprint 1 behaviour.

The weights download automatically to `ai_engine/models/` on first use and
are not committed to Git. A bare name such as `--model yolo11s.pt` is also
cached in `ai_engine/models/`.

## 8. Datasets

See [datasets/README.md](datasets/README.md): the Collective Activity Dataset
clips used for the Sprint 4 demo, the two MOT17 preview videos,
the MOT17 ground-truth labels used for tracking evaluation, and a guide for
recording and labelling real queue footage. Videos and annotations stay out of Git.

## 9. Running

```bash
source .venv/bin/activate

# Sprint 1 behaviour: detect, track, annotate
python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --device auto

# Quick check on the first 100 frames
python ai_engine/run_tracking.py --video datasets/raw/mot17_04.mp4 --max-frames 100

# Queue analysis with a camera config, every 3rd frame, panel bottom-right
python ai_engine/run_tracking.py --video datasets/raw/clip.mp4 \
  --camera-config configs/cameras/example_queue.yaml \
  --frame-stride 3 --panel-position bottom-right

# Save MOT-format tracks for evaluation, blur heads in the saved video
python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --save-mot --blur-heads

# Webcam (see section 14)
python ai_engine/run_tracking.py --video 0 --max-frames 300
```

`python -m ai_engine.run_tracking ...` also works. The command exits with
status 1 and logs an error for a missing or unreadable video, an unavailable
device, a bad camera config or invalid settings.

### All flags of `run_tracking.py`

| Flag | Default | Meaning |
|---|---|---|
| `--video` | required | Video file, or a camera index such as `0` |
| `--model` | `ai_engine/models/yolo11n.pt` | `.pt` weights or CoreML `.mlpackage` |
| `--output` | `outputs/` | Output root |
| `--device` | `auto` | `auto`, `cpu`, `mps`, `cuda` (ignored for CoreML) |
| `--confidence` | 0.35 | Minimum score to start a new track |
| `--tracker-input-confidence` | 0.1 | Minimum score of detections passed to ByteTrack |
| `--track-buffer-seconds` | 1.0 | How long lost tracks are kept |
| `--imgsz` | 640 | Inference size |
| `--frame-stride` | 1 | Process every Nth frame |
| `--camera-config` | none | Camera YAML with the queue zone; enables queue logic |
| `--no-stitching` | off | Disable ID-switch stitching inside the queue zone (overrides the camera config) |
| `--save-mot` | off | Write a MOTChallenge track file |
| `--stitch-diagnostics` | off | Write `events/<name>_stitch_diagnostics.jsonl`, one stitch decision per line (needs `stitching.algorithm: guarded`) |
| `--blur-heads` | off | Blur head regions in the saved video |
| `--head-fraction` | 0.2 | Top fraction of each person box that is blurred |
| `--panel-position` | `top-left` | `top-left`, `top-right`, `bottom-left`, `bottom-right` |
| `--no-h264` | off | Skip the ffmpeg H.264 re-encode |
| `--keep-mp4v` | off | Also keep the original mp4v file |
| `--show` | off | Live window (press q to stop) |
| `--max-frames` | all | Stop after this many processed frames |

## 9a. API and demo page

The API needs the same environment as the engine (`pip install -r requirements.txt`
includes FastAPI, uvicorn and python-multipart). Start it from the repository root:

```bash
source .venv/bin/activate
python -m api                  # then open http://127.0.0.1:8000
```

`python -m api --help` lists `--host`, `--port` and `--reload`. The server
binds to 127.0.0.1 by default and has no authentication, so keep it local.

**Demo flow** (Collective Activity Dataset clips):

1. Get `ActivityDataset.zip` from the dataset's project page (check its terms
   of use first) and convert the queueing sequences:
   `python ai_engine/tools/prepare_cad.py --source ~/Downloads/ActivityDataset.zip`
   (writes `datasets/raw/cad_seqNN.mp4`; see [datasets/README.md](datasets/README.md)).
2. Open http://127.0.0.1:8000, choose `datasets/raw/cad_seq27.mp4` and the
   camera `cad_seq27`, then press Start.
3. Watch the progress bar, the live queue numbers and the event list.
4. When the run completes, play the annotated video and read the summary and
   waits table. Earlier runs stay listed under "Previous runs".

Everything the server stores is local: uploads, outputs and the SQLite
database are under `data/` (git-ignored; override with `QUEUEVISION_DATA_DIR`).
Endpoints, WebSocket messages and settings are in [docs/api.md](docs/api.md).

## 9c. Dashboard (Sprint 7)

The web dashboard lives in `dashboard/` (Next.js, static export). FastAPI serves
the built export at `/`; the old plain test page is still at `/legacy`. The
dashboard talks to the API only over HTTP and WebSocket and computes nothing
itself: every number comes from the API. Pages: Overview (`/`), Live run
(`/live?id=N`), Recorded runs (`/runs`, `/run?id=N`) and Accuracy (`/accuracy`).

**One-command demo** (needs the Python environment, Node and npm):

```bash
scripts/demo.sh
```

It builds the dashboard if `dashboard/out/` is missing (`npm ci` then
`npm run build`; the only network use, apart from `next/font` downloading fonts at build time), runs `scripts/export_accuracy.py`
(writes the git-ignored `data/accuracy.json` from evaluation files on disk) and
starts `python -m api`. Open http://127.0.0.1:8000. If `dashboard/out/` does not
exist, `/` shows a short message that says how to build it.

**Build by hand:**

```bash
cd dashboard
npm ci
npm run build        # writes dashboard/out/
cd .. && python scripts/export_accuracy.py && python -m api
```

**Development mode** (hot reload): run the API on port 8000 and the dev server on 3000.

```bash
python -m api                       # terminal 1, http://127.0.0.1:8000
cd dashboard && npm run dev         # terminal 2, http://localhost:3000
```

In development `next dev` forwards `/api/*` to `http://127.0.0.1:8000`; the
live WebSocket and the video/preview media connect to port 8000 directly (the
API accepts the dev origin `http://localhost:3000` by default).

**Dashboard commands** (run in `dashboard/`): `npm run lint`, `npm run
typecheck`, `npm test` (Vitest), `npm run build`, `npm run e2e` (Playwright
against the real API; needs `npm run build`, the model weights and
`datasets/raw/cad_seq25.mp4`, and skips itself when they are missing).
`npm run gen:api` regenerates `lib/api-types.ts` from the committed
`dashboard/openapi.json`, which `python scripts/export_openapi.py` rewrites from
the API; CI fails if either is stale. `npm run gen:api:live` generates from a
running API on port 8000 instead.

**Pinned versions** (exact, from `dashboard/package.json` and the committed
`package-lock.json`; Node v22.23.2 and npm 10.9.8 were used):

| Package | Version |
|---|---|
| next | 16.4.3 |
| react, react-dom | 19.3.1 |
| @tanstack/react-query | 5.105.0 |
| recharts | 3.10.1 |
| tailwindcss, @tailwindcss/postcss | 4.3.3 |
| typescript | 5.9.3 |
| eslint, eslint-config-next | 9.39.5, 16.4.3 |
| vitest, vite, @vitejs/plugin-react | 5.0.5, 8.3.5, 6.1.2 |
| jsdom | 30.1.3 |
| @testing-library/react, dom, jest-dom, user-event | 16.3.3, 10.4.2, 7.0.1, 14.6.8 |
| @playwright/test | 1.64.1 |
| openapi-typescript | 7.13.0 |
| @types/node, @types/react, @types/react-dom | 22.20.5, 19.3.0, 19.3.0 |

Python dependencies are unchanged. TypeScript 5.9.3 and eslint 9 (not the
newest majors) are chosen because `openapi-typescript` and `eslint-config-next`
do not yet accept the newer ones.

**Settings used by the dashboard:**

| Variable | Where | Meaning |
|---|---|---|
| `QUEUEVISION_ALLOWED_ORIGINS` | API | Comma-separated browser origins allowed to open the WebSocket (default: the API's own origin and `http://localhost:3000`) |
| `QUEUEVISION_DASHBOARD_DIR` | API | Folder served at `/` (default `dashboard/out/`) |
| `NEXT_PUBLIC_API_WS` | dashboard build/dev | WebSocket base (default: `ws://127.0.0.1:8000` on port 3000, else the page's own host) |
| `NEXT_PUBLIC_API_URL` | dashboard build/dev | HTTP base for API calls and media (default: relative `/api` through the dev rewrite; media on `http://127.0.0.1:8000` from port 3000, else same origin) |

Sprint 7 session 2 report: [docs/sprint7_session2_report.md](docs/sprint7_session2_report.md).

## 9b. Sprint 5: replay, stitching evaluation, dataset checks, benchmark

```bash
# 1. Run YOLO + ByteTrack once per clip and keep the tracks (and guarded-stitching decisions)
python ai_engine/run_tracking.py --video datasets/raw/cad_seq27.mp4 \
  --camera-config configs/cameras/cad_seq27.yaml --save-mot --stitch-diagnostics

# 2. Replay the saved tracks through queue-logic variants (no detector needed)
python ai_engine/replay_queue.py --clips cad_seq10 cad_seq25 \
  --tracks-dir outputs/s5_tracks/cad/tracks \
  --variants outputs/s5_replay/variants_a.yaml --splits-dir datasets/splits \
  --output outputs/s5_replay/queue_variants_a.json

# 3. Identity metrics and link correctness of the stitchers on MOT17, stitch counts on CAD
python ai_engine/evaluate_stitching.py --sequences mot17_04 mot17_09 \
  --tracks-dir outputs/s5_tracks/mot17/tracks --output outputs/s5_replay/stitching_mot17.json

# 4. Check the dataset manifest, licence register, splits and videos
python scripts/validate_dataset.py

# 5. Per-stage timing, FPS, CPU and memory
python scripts/benchmark.py --video datasets/raw/cad_seq27.mp4 \
  --camera-config configs/cameras/cad_seq27.yaml --device mps
```

`replay_queue.py` always adds a variant called `as_configured` (the camera YAML
unchanged). `--variants` is a YAML list of `{name, membership, stitching, smoothing}`
overrides. `benchmark.py --tracks <mot file>` benchmarks only the queue logic.
`evaluate_waiting.py` also reads the observed-wait ground-truth format
(see [docs/implementation.md](docs/implementation.md)).

### New opt-in camera settings (all defaults keep Sprint 4 behaviour)

```yaml
membership:
  algorithm: temporal            # legacy (default) | temporal
  min_dwell_seconds: 3.0
  max_speed_heights_per_second: 0.5
  exit_grace_seconds: 1.5
  min_observed_fraction: 0.0     # 0..1, 0 disables
  min_track_observations: 1      # integer >= 1
  boundary_margin_heights: 0.0   # >= 0, box heights; entry/exit hysteresis
  min_mean_detection_confidence: 0.0   # 0..1, 0 disables
  scale_motion_weight: 0.0       # >= 0, 0 disables
  count_service_as_queue: true
  terminal_retention_seconds: 5.0

stitching:
  enabled: true
  algorithm: guarded             # legacy (default) | guarded
  min_lost_seconds: 1.0          # = ByteTrack track_buffer seconds
  max_size_ratio: 1.5
  ambiguity_margin: 0.2
  min_direction_similarity: -0.2
  direction_min_speed_heights: 0.2
  max_duplicate_iou: 0.5
  max_links_per_person: 4
  diagnostics: false

service:                         # used for service_complete (needs service_zone)
  min_service_seconds: 2.0
  exit_confirm_seconds: 1.0

service_rate:
  basis: service_start           # service_start (default) | service_complete (needs service_zone)
```

Measured results for these options are in
[docs/sprint5_implementation_report.md](docs/sprint5_implementation_report.md):
guarded stitching raises link precision on MOT17, but no temporal or guarded
variant has a lower queue-length MAE than the legacy default on the held-out
clips. The API must stay on localhost
([docs/security_review.md](docs/security_review.md)).

## 10. Camera config and the queue zone

A camera config is a YAML file in `configs/cameras/` (see
[configs/cameras/example_queue.yaml](configs/cameras/example_queue.yaml)):

```yaml
camera_id: example_queue
queue_zone:            # polygon, normalized 0-1 coordinates (x, y)
  - [0.20, 0.35]
  - [0.80, 0.35]
  - [0.90, 0.95]
  - [0.10, 0.95]
membership:
  min_dwell_seconds: 3.0               # must stay in the zone this long to count
  max_speed_heights_per_second: 0.5    # net foot speed / box height
  exit_grace_seconds: 1.5              # tolerate brief exits and occlusion
smoothing:
  window_seconds: 2.0                  # median filter on the queue length
```

Zone points are normalized, so one file works at any resolution. Sprint 3
adds optional keys, all shown with comments in the example file:

```yaml
service_line:          # OR service_zone: [[x, y], ...]  (not both)
  - [0.15, 0.90]
  - [0.85, 0.90]
stitching:
  enabled: true
  max_gap_seconds: 2.0
  max_distance_heights: 1.0
service_rate:
  method: window                       # "window" or "ewma"
  window_seconds: 300.0
  ewma_time_constant_seconds: 120.0    # only used by "ewma"
  min_history_seconds: 60.0
  min_services: 2
alerts:                # null disables a threshold; alerts are on if one is set
  max_queue_length: 8
  max_predicted_wait_seconds: 300
  min_duration_seconds: 10.0
  cooldown_seconds: 60.0
```

**Membership rules.** A person's *foot point* (bottom-centre of the box) must
be inside the zone for `min_dwell_seconds`, and their net foot displacement
over that window, divided by mean box height and time, must not exceed
`max_speed_heights_per_second`. This filters out people who merely walk
through. A confirmed member who disappears or steps out is kept for
`exit_grace_seconds` so that brief occlusions do not change the count. The
queue length is the median over `window_seconds` of the raw confirmed count.
All times are video time (frame index ÷ FPS).

**Draw a zone.**

```bash
# Interactive: left click adds a point, right click undoes, Enter saves, Esc cancels
python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 --config configs/cameras/clip.yaml

# Non-interactive (headless): give the points and write a preview image
python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 --config configs/cameras/clip.yaml \
  --points "0.2,0.35 0.8,0.35 0.9,0.95 0.1,0.95" --preview zone.png
```

Other options: `--camera-id`, `--frame-index`. An existing config keeps its
other settings; only the chosen target is replaced.

**Service point.** `--target {queue_zone,service_zone,service_line}` (default
`queue_zone`) chooses what to set. A service line takes exactly 2 points, a
service zone 3 or more. Setting one removes the other. The queue zone must
already exist in the config file before a service point is added.

```bash
python ai_engine/tools/draw_zone.py --video datasets/raw/clip.mp4 --config configs/cameras/clip.yaml \
  --target service_line --points "0.15,0.9 0.85,0.9" --preview zone.png
```

**Place the service point inside (or right at the edge of) the queue zone.**
A queued person who must walk outside the zone for longer than the abandon
timeout to reach the service point is recorded as abandoned instead of served.
Without stitching the timeout is `exit_grace_seconds`; with stitching it is
the larger of `exit_grace_seconds` and the stitching `max_gap_seconds`. This
was observed on MOT17 with stitching off.

**Service point and waiting time.** A person has a *wait* from the first time
they stand in the queue zone (not the later confirmation time) until they
reach the service point (a line crossing, or being inside the service zone).
A person who leaves the zone and is not seen again for the abandon timeout is
*abandoned*. Abandonment is only recorded when a service point is configured.
No events are produced at the end of a video; people still waiting are
counted in `queue.waiting_at_end` and `queue.pending_abandon_at_end`.

**Stitching.** ByteTrack assigns a new ID after an occlusion. A new ID that
appears within `max_gap_seconds` and `max_distance_heights` box heights of
where a person was last seen inside the queue zone is linked to that person
(one anonymous `person_key`). In a dense crowd that is not a queue this can
merge different people: on MOT17-09 (street footage) it was observed chaining
up to 8 track IDs. Validate it on real queue footage, and compare with
`--no-stitching`.

**Service rate and predicted wait.** The service rate is people served per
minute over `window_seconds` (or an EWMA). It is reported as
`insufficient_data` until `min_history_seconds` of video and `min_services`
services exist. The predicted wait is `queue_length / service rate`, with a
low/high range from the smallest and largest rate estimated in the window. It
is `unknown` (never 0 or infinity) when the rate is unavailable or zero.

**Alerts.** The condition (queue length above `max_queue_length`, predicted
wait above `max_predicted_wait_seconds`) must hold for `min_duration_seconds`
to start an alert and be false for `min_duration_seconds` to end it, and a
new alert waits `cooldown_seconds` after the previous one ended. While an
alert is active a red banner is drawn on the video; it sits at the bottom
when the metrics panel is at a top position, otherwise at the top.

**Overlay.** The zone is filled and outlined. Boxes are orange for confirmed
queue members, yellow for candidates (in the zone, not yet confirmed) and
green for everyone else (magenta for people who just reached the service
point). A dot marks the foot point and the service point is drawn in magenta.
The panel shows "Queue length" separately from "People in frame", plus
"Service rate" and "Predicted wait".

## 11. Outputs

For an input `datasets/raw/<name>.mp4` (a webcam is named `camera<N>`):

| File | Content | When |
|---|---|---|
| `outputs/videos/<name>_tracked.mp4` | Annotated video, H.264 when ffmpeg is available, at source FPS ÷ stride | always |
| `outputs/metrics/<name>_metrics.json` | Performance, statistics, video metadata, configuration, resolved tracker | always |
| `outputs/logs/<name>_tracking.log` | Run log | always |
| `outputs/tracks/<name>_mot.txt` | MOTChallenge tracks `frame,id,x,y,w,h,conf,-1,-1,-1` | `--save-mot` |
| `outputs/timeseries/<name>_queue.csv` | One row per video second: `time_s, frame, people_in_frame, raw_queue_count, queue_length, service_rate_per_min, service_rate_status, predicted_wait_s, predicted_wait_low_s, predicted_wait_high_s, prediction_status` | `--camera-config` |
| `outputs/events/<name>_zone_events.csv` | `event_type, track_id, timestamp_s, frame, zone_entered_s, zone_entered_frame` | `--camera-config` |
| `outputs/events/<name>_waits.csv` | One row per completed or abandoned wait: `person_key, track_ids, join_s, service_s, wait_s, abandoned, left_s` | `--camera-config` |
| `outputs/events/<name>_alerts.csv` | `event_type, timestamp_s, frame, condition_since_s, reasons, queue_length, predicted_wait_s` | `--camera-config` |
| `outputs/events/<name>_events.jsonl` | Every event, one JSON object per line, schema version 1.0 ([docs/event_schema.md](docs/event_schema.md)) | `--camera-config` |
| `queue` section of the metrics JSON | Zone, rules, max and average queue length, event counts, `members_still_in_queue_at_end`, plus `stitching`, `service_point`, `waiting` (completed, abandoned, mean/median/p90 wait, throughput, abandonment rate), `service_rate`, `alerts`, `waiting_at_end`, `pending_abandon_at_end`, `service_visits_without_join` | `--camera-config` |

Use `--output DIR` to write to `DIR/videos`, `DIR/metrics`, ... instead.

**Zone events.** `zone_enter` is emitted when membership is confirmed (its
`timestamp_s` is the confirmation time); `zone_entered_s` is when the person
first stepped into the zone, and is the value used as the queue-join time. `zone_exit` is emitted when a confirmed member has been out of
the zone for longer than the grace period (its `timestamp_s` is the last time
they were seen inside). Only confirmed members produce events. No events are
invented at the end of a video: people still queued then are counted in
`queue.members_still_in_queue_at_end` (and `queue.waiting_at_end`).

**Video encoding.** OpenCV writes mp4v, then ffmpeg re-encodes to H.264
(`libx264`, `yuv420p`, `veryfast`, `+faststart`) so the file plays in browsers.
`--no-h264` skips this, `--keep-mp4v` also keeps `<name>_tracked_mp4v.mp4`. If
ffmpeg is missing, a warning is logged and the mp4v file is kept. The result
is recorded as `configuration.video_codec`.

## 12. Performance metrics

| Metric | Meaning |
|---|---|
| `average_inference_fps` | frames ÷ total time inside YOLO + ByteTrack, with MPS/CUDA synchronized before each timer stops |
| `average_processing_fps` | frames ÷ total per-frame time: skipping, decoding, inference, queue logic, drawing and writing. The `--show` window and H.264 re-encoding are excluded. |
| `total_runtime_seconds` | Wall-clock time of the whole run, including model loading, warm-up and H.264 re-encoding |
| `maximum_people_detected_in_a_frame` | Highest number of tracked people in any single frame |
| `unique_track_ids_assigned` | Number of distinct ByteTrack IDs. This is **not** the number of unique people, because IDs can change after occlusion. |

All timings use `time.perf_counter()`. One blank warm-up frame runs before
timing starts. The on-screen FPS values are rolling averages over the last 30
frames; the JSON values are whole-video averages. Measured results for this
sprint (speed and tracking accuracy) are in
[docs/testing_report.md](docs/testing_report.md).

## 13. Evaluation

### Tracking accuracy (MOT17)

Needs `requirements-dev.txt` and the ground-truth files described in
[datasets/README.md](datasets/README.md).

```bash
python ai_engine/run_tracking.py --video datasets/raw/mot17_09.mp4 --save-mot
python ai_engine/evaluate_tracking.py \
  --tracks outputs/tracks/mot17_09_mot.txt \
  --sequence-dir datasets/annotations/mot17/MOT17-09 \
  --video datasets/raw/mot17_09.mp4
```

`--frame-stride N` (match the value used for tracking) and `--output` are also
available. The report goes to `outputs/metrics/<name>_tracking_eval.json`.
Ground truth is scaled from 1920×1080 to the 960×540 video, and the command
refuses to run if the video's frame count differs from `seqLength`. It scores
pedestrians (class 1, consider flag 1) at IoU 0.5 and, like the official
devkit, removes detections matched to distractor classes before scoring.

### Waiting time (manual ground truth)

```bash
# 1. the annotation kit also writes an empty datasets/annotations/clip_waits_gt.csv
#    (person, join_s, service_s, abandoned); fill it by watching the video
python ai_engine/tools/make_annotation_kit.py --video datasets/raw/clip.mp4 \
  --camera-config configs/cameras/clip.yaml
# 2. run the engine (the camera config needs a service point), then compare
python ai_engine/run_tracking.py --video datasets/raw/clip.mp4 --camera-config configs/cameras/clip.yaml
python ai_engine/evaluate_waiting.py \
  --predicted-waits outputs/events/clip_waits.csv \
  --ground-truth datasets/annotations/clip_waits_gt.csv \
  --timeseries outputs/timeseries/clip_queue.csv
```

Completed waits are matched one-to-one by service time (`--tolerance`, default
3.0 s). The report gives matched, missed and extra counts, the wait MAE,
median absolute error and bias, the number of abandoned people, and, with
`--timeseries`, the error of the predicted wait at each person's join time and
the throughput error (`--duration` sets the video length). `--output` sets the
report path (default `outputs/metrics/<stem>_wait_eval.json`). See
[datasets/README.md](datasets/README.md) for how to fill the ground truth.

### Queue length (manual counts)

```bash
# 1. frames every 5 s (with the zone drawn) and an empty CSV template
python ai_engine/tools/make_annotation_kit.py --video datasets/raw/clip.mp4 \
  --camera-config configs/cameras/clip.yaml --every 5
# 2. fill queue_count in datasets/annotations/clip_queue_counts.csv by looking at the frames
# 3. run the engine, then compare
python ai_engine/run_tracking.py --video datasets/raw/clip.mp4 --camera-config configs/cameras/clip.yaml
python ai_engine/evaluate_queue.py \
  --predicted outputs/timeseries/clip_queue.csv \
  --annotations datasets/annotations/clip_queue_counts.csv
```

`evaluate_queue.py` reports MAE, RMSE, bias, the exact-match rate and the
within-±1 rate (on rounded predictions), matching each label to the nearest
prediction within `--tolerance` seconds (default 0.6). `--column
raw_queue_count` evaluates the unsmoothed count instead of `queue_length`; `--output`
sets the report path. `make_annotation_kit.py` refuses to overwrite an
existing CSV unless `--force` is given.

## 14. Frame stride and webcam

`--frame-stride N` processes every Nth frame. Skipped frames are grabbed
without decoding; timestamps still come from the source frame index, and the
output video is written at source FPS ÷ N. The tracker's buffer is converted to
processed frames accordingly.

`--video 0` reads camera 0. If the driver reports no FPS, it is measured over
30 frames (those frames are kept, not lost) and recorded as
`fps_source: "measured"` in the metrics JSON. **Webcam support is tested only
with a mocked capture; it is untested on real camera hardware.**

## 15. Privacy and CoreML

**Head blur.** `--blur-heads` blurs the top `--head-fraction` (default 0.2) of
every person box in the *saved* video only, before boxes are drawn. It uses no
extra model and is best-effort. See [docs/privacy.md](docs/privacy.md) before
recording anyone.

**CoreML (Apple Silicon).** Export from a separate venv, because coremltools
9.0 fails with the torch 2.15.0 of the main environment:

```bash
python3.11 -m venv .venv-coreml-export
.venv-coreml-export/bin/pip install -r requirements-coreml.txt
.venv-coreml-export/bin/python ai_engine/tools/export_model.py --model yolo11n.pt --imgsz 640
```

Install `coremltools==9.0` in the main venv, then run with
`--model ai_engine/models/yolo11n.mlpackage`. The device is reported as `coreml`
and `--device` is ignored. The exported model has a fixed input size, so use
the same `--imgsz`. `export_model.py` also accepts `--half`.

## 16. Testing, linting and CI

```bash
pytest -v -m "not integration"   # unit tests: no torch/ultralytics or weights needed
pytest -v -m integration         # runs the real YOLO11n model (downloads weights if missing)
pytest -v                        # everything
ruff check . && ruff format --check .
```

The unit tests import only OpenCV, NumPy and PyYAML (torch and ultralytics are
imported lazily), and CI proves it: `.github/workflows/ci.yml` has a
`lint-and-unit` job that installs only those packages and runs ruff and the unit
tests, and an `integration` job (allowed to fail, because it downloads weights).
The lint-and-unit job also installs the light API dependencies (`fastapi`,
`uvicorn[standard]`, `python-multipart`, `httpx`, all pinned) but still no
torch; the API tests use a fake tracker. The integration job installs
`requirements.txt` and `requirements-dev.txt`.
Ruff is configured in `pyproject.toml`.

## 17. Troubleshooting

| Problem | Fix |
|---|---|
| `MPS was requested but is not available` | Use `--device auto` or `--device cpu`, and check `torch.backends.mps.is_available()`. Intel Macs have no MPS. |
| `OpenCV could not open the video` | Re-encode with `ffmpeg -i in.webm -c:v libx264 -pix_fmt yuv420p out.mp4` |
| Weights download fails | Download `yolo11n.pt` manually from the Ultralytics assets release into `ai_engine/models/` |
| `ModuleNotFoundError: ai_engine` | Run from the repository root, with the venv activated |
| `import queue` errors or odd behaviour | Run scripts as shown (the scripts fix `sys.path`). `ai_engine/queue` would otherwise shadow Python's `queue` module. |
| `--show` window does not appear | Requires a desktop session. Omit `--show` over SSH. |
| Output video is mp4v / does not play in a browser | Install ffmpeg (`brew install ffmpeg`); the log warns when it is missing |
| `Camera config ...` error | Zone points must be 3 or more `[x, y]` pairs in 0-1; a `service_line` has exactly 2; give `service_zone` or `service_line`, not both; rule values must be positive |
| Almost everyone is "abandoned" | The service point is probably outside the queue zone; move it inside (or to the edge of) the zone |
| CoreML export fails with a torch error | Export from the separate `.venv-coreml-export` (section 15) |
| Small or distant people are missed | Try `--imgsz 960` or `--imgsz 1280`, or a larger model (`--model yolo11s.pt`) |

## 18. Roadmap

| Sprint | Scope |
|---|---|
| 1 (done) | Video I/O, YOLO11n person detection, ByteTrack, overlay, FPS metrics |
| 2 (done) | Queue zone and membership, queue-length series, zone events, evaluation tools, ByteTrack fix, CoreML, H.264, head blur, CI |
| **3 (done)** | Service point, ID-switch stitching, lifecycle events, waiting time, service rate and predicted wait, congestion alerts, waiting-time evaluation |
| **4 (done)** | FastAPI upload API, WebSocket live progress, SQLite persistence, demo test page, Collective Activity Dataset clips |
| 5 | Next.js dashboard, live monitoring, analytics and historical reports |

## Legacy prototype

`person_detection.py`, the earlier single-file prototype (image or video
detection with a hard-coded queue-zone polygon), was **removed in Sprint 2**.
Its useful logic, a normalized queue polygon and in-zone counting, now lives
in `ai_engine/queue/`, with a foot-point test instead of the box centre and
the dwell and speed rules above. The file is still available in Git history.
