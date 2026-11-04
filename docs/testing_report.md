# Testing Report

Sprint 3 results come first, then Sprint 2; the Sprint 1 report is kept at
the end. Every number here comes from a command that was actually run on the
machine described under Sprint 2 (same environment). Nothing was estimated.

## Sprint 3 (2026-11-02)

### Automated tests

| Run | Result |
|---|---|
| Baseline at the start of Sprint 3 (`main` at `4e3ee7a`) | **87 passed**, 0 failed, 0 skipped |
| Final, full suite (`pytest -q`) | **152 passed**, 0 failed, 0 skipped |
| Final, torch-free venv (`pytest -m "not integration"`, no torch/ultralytics installed) | **142 passed**, 10 deselected |
| `ruff check .` / `ruff format --check .` | clean |

### Waiting-time accuracy: PENDING (no labelled queue footage)

There is no real queue footage with ground truth yet, so waiting-time accuracy
(MAE, median error, missed/extra people), prediction error and the effect of
ID stitching on accuracy **have not been measured**. `evaluate_waiting.py` is
implemented and tested on synthetic data only (`tests/test_wait_eval.py`).
See `datasets/README.md` for how to label footage.

### Smoke runs on MOT17 (street footage, NOT a queue: mechanics only)

Command (MPS, example camera config with a service line at y = 0.9, head blur,
H.264):

```bash
python ai_engine/run_tracking.py --video datasets/raw/<clip>.mp4 --device mps \
  --camera-config configs/cameras/example_queue.yaml --blur-heads \
  --output outputs/s3_<clip>_stitch_on      # and --no-stitching for _stitch_off
```

| Run | Stitches | queue_join | service_start | queue_abandon | Still waiting at end | Inference FPS |
|---|---|---|---|---|---|---|
| MOT17-09, stitching on | 32 | 10 | 2 | 6 | 2 | 30.88 |
| MOT17-09, stitching off | 0 | 11 | 1 | 9 | 1 | 31.85 |
| MOT17-04, stitching on | 7 | 19 | 4 | 7 | 8 | 31.71 |
| MOT17-04, stitching off | 0 | 21 | 4 | 9 | 8 | 31.79 |

What this shows, and what it does not:

* All seven event types, the waits CSV, the alerts CSV and `events.jsonl` are
  produced from real tracking output; codec h264 in all runs. Queue-stage cost
  is small: inference ≈ 31 FPS, the same as Sprint 2 without stitching/alerts.
* Stitching merges fragmented IDs (fewer joins, fewer abandons). On MOT17-09
  it also produced one person with 8 chained track IDs over 11 s, which in a
  dense street crowd is almost certainly a wrong merge. Whether stitching
  helps accuracy is **unknown** until it is measured on queue footage.
* The "waits" and "abandons" here are people walking across a street, not
  queuing; the numbers are not accuracy results.
* The service rate stayed `insufficient_data` (and predictions `unknown`) for
  the whole of both clips, as designed: they are 17.5 s and 35 s long, shorter
  than `min_history_seconds` = 60. No congestion alert fired (max queue length
  7 ≤ threshold 8).

### Exercise run: prediction and alert paths

To exercise the paths that the clip length prevents, MOT17-04 was run with a
test copy of the config (`min_history_seconds` 10, `min_services` 1,
`max_queue_length` 4, `min_duration_seconds` 3; output `outputs/s3_exercise`):

* `congestion_start` at 10.9 s (condition since 7.9 s, queue length 6) and
  `congestion_end` at 22.0 s (condition cleared since 19.0 s).
* From 18 s the service rate became `ok` (e.g. 10.0/min at 18 s, predicted wait
  42.0 s, range 41.1–120.9 s; 6.43/min at 28 s, 37.3 s, range 23.5–69.1 s).
* The overlay showed the service line, the predicted wait with its range and
  the congestion banner; the banner overlapped the panel title, which was then
  fixed by drawing it at the opposite edge from the panel.
* This run averaged 19.16 inference FPS against ≈31 for the identical clip
  minutes earlier; the machine was under other load at the time, so it is not
  a benchmark.

### Not tested / pending (Sprint 3)

* Waiting-time, prediction and throughput accuracy on real labelled footage.
* Effect of stitching on accuracy (only stitch counts are reported above).
* Interactive (mouse) mode of `draw_zone.py --target service_line|service_zone`;
  the headless `--points` mode is tested.
* Webcam: still covered by a mocked capture only.

---

# Sprint 2

## Execution environment (Sprint 2, 2026-11-02)

| Item | Value |
|---|---|
| Machine | MacBook Air, Apple M1, 8 GB RAM, macOS 27.0.1 (arm64) |
| Python | 3.11.16 (`.venv/`) |
| PyTorch | 2.15.0. MPS available; no CUDA |
| Ultralytics | 8.4.198 (ByteTrack defaults: `track_high_thresh` 0.25, `track_low_thresh` 0.1, `new_track_thresh` 0.25, `track_buffer` 30) |
| OpenCV / NumPy / PyYAML | 5.0.0.93 / 2.4.6 / 6.0.3 |
| Evaluation | motmetrics 1.4.0, scipy 1.17.1 |
| Dev | pytest 9.2.0, ruff 0.17.3 |
| CoreML | coremltools 9.0. Export used a separate venv with torch 2.7.0, numpy 1.26.4 and ultralytics 8.4.198 |
| ffmpeg | Homebrew build (`/opt/homebrew/bin/ffmpeg`) |

## Automated tests

| Run | Result |
|---|---|
| Baseline at the start of Sprint 2 (`main` at `5bc11fb`) | **32 passed**, 0 failed, 0 skipped |
| Final, full suite (`pytest -v`, real YOLO11n) | **87 passed**, 0 failed, 0 skipped |
| Final, torch-free (`pytest -m "not integration"` in a clean venv with only opencv-python-headless, numpy, PyYAML and requirements-dev.txt; `import torch` fails there) | **77 passed**, 10 deselected |
| `ruff check .` and `ruff format --check .` | pass (in both environments) |

New test files: `test_queue.py`, `test_evaluation.py`, `test_pipeline.py`.
Existing tests changed on purpose:

* `test_tracker.py`: the fake detector now has `confidence`, and mocked
  tracker tests pass a ByteTrack config dict (the tracker now builds a
  resolved YAML). New tests check that `track()` receives `conf=0.1` and the
  resolved YAML, and that a low-score box with a track ID is still returned.
* `test_metrics.py`: `process_video()` now takes `RunOptions`, and the CLI
  test checks the new `configuration.tracker` fields.
* `conftest.py`: the fake Ultralytics results use a NumPy-backed fake tensor
  instead of torch, so the unit tests run without torch.

Mocked tests (fake detector, tracker or camera) are named `*mocked*` or use
`Fake*` classes and are documented as such. All detection-quality and tracking
assertions use the real model.

## Step 1: ByteTrack fix (before vs after)

Before the fix, the tracker was called with `conf=0.35`. ByteTrack's second
association stage only uses boxes scoring between 0.1 and 0.25, so it never
received any. After the fix, detections down to 0.1 reach ByteTrack, and
`--confidence 0.35` is the minimum score needed to *start* a track. "Before"
means `--tracker-input-confidence 0.35`. That flag reproduces Sprint 1
exactly: the before runs gave the same unique IDs (75 / 108) and the same
average people per frame (7.76 / 13.28) as the unmodified Sprint 1 code.

MPS, imgsz 640, stride 1, all 525 / 1050 frames. Scored against MOT17 ground
truth (see Step 2).

| Sequence | Setting | Unique IDs | Avg people/frame | MOTA | IDF1 | ID switches | Fragmentations | Precision | Recall | False positives | Misses |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MOT17-09 | before | 75 | 7.76 | **0.543** | **0.554** | 75 | 121 | 0.888 | 0.638 | 427 | 1929 |
| MOT17-09 | after | 74 | 8.88 | 0.520 | 0.508 | 68 | 78 | 0.832 | 0.668 | 718 | 1770 |
| MOT17-04 | before | 108 | 13.28 | 0.248 | 0.351 | 61 | 374 | 0.925 | 0.271 | 1050 | 34665 |
| MOT17-04 | after | 87 | 16.85 | **0.313** | **0.412** | 47 | 140 | 0.922 | 0.343 | 1388 | 31251 |

**The effect differs between the two sequences.** The fix sharply reduces
track fragmentation on both sequences (−36% on MOT17-09, −63% on MOT17-04),
along with ID switches. It also raises recall. On the crowded MOT17-04 scene
it clearly improves MOTA (+0.065) and IDF1 (+0.061). On MOT17-09, low-score
boxes keep some false tracks alive (+291 false positives), so MOTA (−0.024)
and IDF1 (−0.045) both drop. The fix is kept as the default because the
sprint's goal is stable IDs through occlusion, which queue membership and
waiting time depend on. `--tracker-input-confidence 0.35` restores the old
behaviour if real queue footage shows it is better.

Speed on MPS (same session, runs one at a time): MOT17-09 before 33.35 /
27.75, after 31.84 / 26.29; MOT17-04 before 30.41 / 25.72, after 27.07 /
22.87 (inference / processing FPS). After the fix there are more boxes per
frame for ByteTrack and drawing. FPS varies by several FPS between sessions on
this fanless M1 (Sprint 1 measured 25.46 inference FPS for the "before"
setting on MOT17-09), so compare runs only within this table.

## Step 2: MOT17 tracking evaluation

* Ground truth: `MOT17Labels.zip` from motchallenge.net (labels only,
  SHA-256 `0aa79322…dc82b2b9`). Only `train/MOT17-{04,09}-FRCNN/{seqinfo.ini, gt/gt.txt}`
  were extracted. Licence CC BY-NC-SA 3.0.
* Frame counts match `seqLength` (525 and 1050). Ground-truth boxes were
  scaled by 960/1920 = 540/1080 = 0.5.
* Scored: class 1 with consider flag 1. Hypotheses matched to distractor
  classes 2, 7, 8 and 12 were removed, as in the official devkit. IoU 0.5.
* **Alignment check:** the tracks were shifted by −2…+2 frames and re-scored.
  Offset 0 gave the best MOTA on MOT17-09 (0.520, against 0.516 at −1 and
  0.511 at +1) and tied for best on MOT17-04 (0.3127 at −1 and 0). The
  preview videos are frame-aligned with the ground truth.
* These are evaluation numbers on 960×540 preview videos with YOLO11n, not
  official MOTChallenge results.

Optional larger inference size (MPS, `--imgsz 960`, after the fix):

| Sequence | MOTA | IDF1 | ID switches | Precision | Recall | Inference FPS | Processing FPS |
|---|---|---|---|---|---|---|---|
| MOT17-09 | 0.567 | 0.498 | 61 | 0.856 | 0.696 | 28.89 | 24.53 |
| MOT17-04 | **0.446** | **0.519** | 93 | 0.907 | 0.498 | 18.76 | 16.08 |

imgsz 960 is the largest accuracy gain measured in this sprint, especially
on MOT17-04's small, distant people (MOTA 0.313 → 0.446). It costs about 30%
of the FPS on that scene.

## Step 7: benchmarks (MOT17-09, after the fix, imgsz 640)

The runs were made one after another in one session, with `--save-mot --no-h264`.
"Video throughput" is source frames covered per second of processing
(processed frames × stride ÷ processing time). At stride 3 the tracker sees
10 frames per second of video.

| Backend | Stride | Frames processed | Inference FPS | Processing FPS | Video throughput (frames/s) | MOTA | IDF1 | ID switches |
|---|---|---|---|---|---|---|---|---|
| CPU | 1 | 525 | 24.66 | 21.85 | 21.9 | 0.520 | 0.508 | 68 |
| CPU | 3 | 175 | 23.58 | 20.43 | 61.3 | 0.491 | 0.542 | 35 |
| MPS | 1 | 525 | 30.30 | 25.19 | 25.2 | 0.520 | 0.508 | 68 |
| MPS | 3 | 175 | 32.18 | 26.17 | **78.5** | 0.491 | 0.542 | 35 |
| CoreML | 1 | 525 | 20.84 | 18.85 | 18.8 | 0.516 | 0.473 | 68 |
| CoreML | 3 | 175 | 21.10 | 18.75 | 56.3 | 0.505 | 0.550 | 41 |

Findings:

* **MPS is fastest. At stride 3 it processes 30 fps video about 2.6× faster
  than real time** (78.5 source frames/s). At stride 1 nothing reaches 30 fps
  real time on this M1.
* CPU and MPS give **identical tracking results**; only speed differs.
* **CoreML was slower than MPS here** (20.8 against 30.3 inference FPS), and
  its results differ slightly because of numerical differences. Running
  inference through coremltools' Python API adds overhead, and Ultralytics'
  pre- and post-processing stays on the CPU. CoreML is not recommended over
  MPS for this setup. The FP16 (`--half`) export and Neural-Engine-only
  compute units were not tried.
* At stride 3, track continuity scores higher (IDF1 up, ID switches halved:
  there are fewer chances to switch). MOTA drops slightly because recall falls
  at the lower frame rate. These scores cover only the processed frames (1, 4,
  7, …), so they are not directly comparable with the stride-1 scores.
* CoreML export from the main environment (torch 2.15.0) **failed** inside
  coremltools 9.0, which is tested only up to torch 2.7.0
  (`TypeError: only 0-dimensional arrays can be converted to Python scalars`).
  Export succeeded in 2.6 s from a separate torch 2.7.0 venv, and the
  `.mlpackage` runs in the main environment (`requirements-coreml.txt`).

## Steps 3–5, 8 and 9: queue smoke test (NOT a queue scene, NOT an accuracy result)

MOT17-09 shows people walking along a shopping street, not queuing. This run
only checks that the queue pipeline works end to end with the example zone
`configs/cameras/example_queue.yaml`. Command: `--device mps --camera-config
configs/cameras/example_queue.yaml --blur-heads --panel-position top-right`
(H.264 on).

| Output | Result |
|---|---|
| `outputs/queue_smoke/videos/mot17_09_tracked.mp4` | H.264, 525 frames; zone, orange/yellow/green status colours, foot dots, head blur and a top-right panel showing "Queue length" separately from "People in frame" (checked visually on an extracted frame) |
| `outputs/queue_smoke/timeseries/mot17_09_queue.csv` | 18 rows (one per second of the 17.5 s video) |
| `outputs/queue_smoke/events/mot17_09_zone_events.csv` | 11 `zone_enter`, 10 `zone_exit` |
| `outputs/queue_smoke/metrics/mot17_09_metrics.json` | `queue` section: max smoothed length 3, average 1.466, max raw count 4, 1 member still in queue at the end; codec `h264` |
| Performance | 31.07 inference FPS, 23.49 processing FPS (with queue logic, blur and drawing) |

**What the smoke test revealed:** several walkers were confirmed as
"queuing". In the frame checked, a person walking *towards* the camera was
counted, because their foot point moves little on screen relative to their
large box height. The dwell-plus-speed filter has trouble with motion along
the camera axis. This has to be tuned (or replaced with a better motion
measure, such as foot-point motion in a ground-plane homography) on real
queue footage.

**Queue-length accuracy is PENDING.** There is no real queue footage in
`datasets/raw/`. `make_annotation_kit.py` and `evaluate_queue.py` are
implemented and unit-tested with synthetic CSVs only.

## Not tested / pending

| Item | Status |
|---|---|
| Queue-length accuracy (MAE, RMSE, exact and ±1 rates) | Pending real, labelled queue footage |
| Webcam input (`--video 0`) | Tested only with a mocked `cv2.VideoCapture`; **untested on a real camera** |
| Interactive `draw_zone.py` mouse mode | Not run (no interactive display in the test session); non-interactive `--points` mode is tested |
| `--show` live window | Exercised manually in Sprint 1 (quit with `q` worked); not part of automated tests |
| CI workflow | Written; the torch-free job was reproduced locally in a clean venv. GitHub Actions has not run it yet (it runs when the PR is opened). The integration job is `continue-on-error` because it downloads weights |
| CUDA | No NVIDIA hardware available |

---

## Sprint 1 report (2026-11-02, unchanged results)

Sprint 1 environment: same machine and the same core versions as above.
pandas 3.1.0 was installed then, and was removed in Sprint 2.

* `pytest -v`: 32 passed, 0 failed, 0 skipped.
* End-to-end runs (confidence 0.35 passed straight into the tracker,
  imgsz 640). The `--show` run on MOT17-09, which was stopped with `q` at
  frame 395, reached 30.75 / 25.66 FPS on MPS:

| Video | Device | Frames | Avg inference FPS | Avg processing FPS | Max people/frame | Track IDs |
|---|---|---|---|---|---|---|
| MOT17-09 | MPS | 525 | 25.46 | 20.90 | 12 | 75 |
| MOT17-04 | MPS | 1050 | 26.22 | 21.93 | 21 | 108 |
| MOT17-09 | CPU | 525 | 22.82 | 20.19 | 12 | 75 |

Sprint 1 limitations that Sprint 2 addressed: the mp4v-only output (H.264 is
now the default), the overlay covering the scene (the panel is now movable),
and unmeasured accuracy (MOT17 evaluation added). Still open: small or distant
people are missed at imgsz 640 (imgsz 960 helps, see above), and track IDs
still fragment.
