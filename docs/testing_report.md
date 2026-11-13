# Testing Report

Sprint 5 results come first, then Sprints 4, 3 and 2; the Sprint 1 report is
kept at the end. Every number here comes from a command that was actually run
on the machine described under Sprint 2 (same environment). Nothing was
estimated.

## Sprint 5 (2026-11-08/09)

Full write-up: [sprint5_implementation_report.md](sprint5_implementation_report.md);
starting state: [sprint5_initial_audit.md](sprint5_initial_audit.md). Headline:
**no new queue-counting option beat the Sprint 4 algorithm on held-out clips, so
the defaults are unchanged.**

### Automated tests

| Run | Result |
|---|---|
| Start of sprint (`dd758b2`) | 203 passed |
| End of sprint, full suite | **370 passed** |
| Real-YOLO integration (`-m integration`) | 11 passed |
| `ruff check .` / `ruff format --check .` | clean / 100 files formatted |

### Method: replaying identical tracks

YOLO11n + ByteTrack ran once per clip (`run_tracking.py --save-mot`, MPS, imgsz
640) on the 7 CAD queueing clips and MOT17-04/09. Every queue-logic variant
replays the same saved tracks (`ai_engine/replay_queue.py`).

* Reproduction check: scoring the new run's time series gave exactly the Sprint 4
  per-clip MAE on all 7 clips.
* Replay gate: replaying with the shipped configs reproduces `raw_queue_count`
  and `queue_length` row for row on all 7 clips (0 differences)
  (`tests/test_replay.py`, skipped when the data is absent, as in CI).
* Split fixed before measuring: val = cad_seq10, 25, 26; test (held out) =
  cad_seq27, 39, 40, 41. No parameter was tuned; variants are one-at-a-time ablations.
* Scoring: Sprint 4 protocol (frames from 5 s, nearest per-second sample within 0.6 s).

### Queue counting (smoothed count, frames from 5 s)

| Variant | All 7: MAE / RMSE / bias / ±1 | Val (122 frames): MAE | Test (285 frames): MAE / bias / ±1 |
|---|---|---|---|
| **legacy (Sprint 4, default)** | **1.486 / 1.911 / +0.875 / 0.595** | **1.176** | **1.619 / +0.960 / 0.537** |
| temporal + legacy stitching | 2.212 / 2.665 / +1.173 / 0.361 | 1.525 | 2.507 / +1.549 / 0.277 |
| temporal + guarded stitching | 2.016 / 2.583 / +1.316 / 0.460 | 1.188 | 2.370 / +1.672 / 0.379 |
| temporal, stitching off | 1.590 / 1.990 / +0.393 / 0.518 | 1.406 | 1.668 / +0.581 / 0.498 |

Raw (unsmoothed) count, test split: legacy MAE 1.751, temporal with stitching
off 1.807. Per-clip MAE from 5 s (smoothed):

| Clip | split | legacy | temporal+legacy stitch | temporal+guarded | temporal, no stitch |
|---|---|---|---|---|---|
| seq10 | val | 1.250 | 1.188 | 1.250 | 1.250 |
| seq25 | val | 0.989 | 1.702 | 1.000 | 1.692 |
| seq26 | val | 1.305 | 1.475 | 1.322 | 1.220 |
| seq27 | test | 1.852 | 3.036 | 3.102 | 1.852 |
| seq39 | test | 1.108 | 1.865 | 1.378 | 1.649 |
| seq40 | test | 2.462 | 2.788 | 1.981 | 2.231 |
| seq41 | test | 0.875 | 1.232 | 1.036 | 0.875 |

Reading: identity per stitched person is only as good as the stitcher (on seq27
the legacy stitcher made 108 links in 60 s on a ~12-person queue, linking tracks
ByteTrack later revived). With stitching off the temporal classifier lowers the
bias (+0.58 against +0.96 on test) but not the MAE (1.668 against 1.619). The
one-at-a-time ablations (margin 0.25, observed fraction 0.6, mean confidence
0.35, scale motion 1.0) all measured worse than legacy on the pooled data.
Small sample: 7 clips from one dataset at an assumed 30 fps.

### Stitching on MOT17 (`evaluate_stitching.py`, motmetrics)

HOTA is **not available** (motmetrics does not implement it; TrackEval is not
installed). Link correctness is judged against MOT17 ground-truth IDs.

| Sequence | Config | IDF1 | ID switches | Frag. | Links | Correct / incorrect / unverifiable | Precision |
|---|---|---|---|---|---|---|---|
| MOT17-04 | no stitching | 0.412 | 47 | 140 | 0 | – | – |
| MOT17-04 | legacy | 0.427 | 44 | 140 | 7 | 3 / 2 / 2 | 0.600 |
| MOT17-04 | guarded | 0.418 | 50 | 140 | 4 | 3 / 1 / 0 | 0.750 |
| MOT17-09 | no stitching | 0.508 | 68 | 78 | 0 | – | – |
| MOT17-09 | legacy | 0.504 | 65 | 78 | 32 | 7 / 13 / 12 | 0.350 |
| MOT17-09 | guarded | 0.521 | 76 | 78 | 16 | 6 / 5 / 5 | 0.545 |

Guarded halves the links on MOT17-09 and raises link precision on both
sequences, but counts more ID switches than no stitching, partly because a track
carries a provisional key for the ~1 s before its link applies (scored under
final keys: 45 on 04 and 65 on 09). The cap of 4 never triggered.

CAD stitch counts, guarded against legacy (seq10, 25, 26, 27, 39, 40, 41):
guarded 3 / 6 / 2 / 63 / 16 / 8 / 9 links, legacy 12 / 19 / 12 / 108 / 28 / 20 / 19.
Most rejections are `distance_too_far`. Revived tracks: 0.

### Lifecycle events

CAD seq25 replay, default config: 11 `queue_join`, 4 `service_start`, 1
`service_complete`, 5 `queue_abandon`, 8 `queue_exit`. Tests: 20 for
`service_complete`; `tests/test_wait_eval_observed.py` for the waiting-time
evaluator (synthetic data only).

### Dataset validation (`scripts/validate_dataset.py`)

| Item | Count |
|---|---|
| Clips in manifest | 27 (the 25-clip plan + 2 extra CAD clips) |
| Acquired | 9 (CAD 7 of 5 planned; MOTChallenge 2 of 5) |
| Planned, not acquired | 18 (MIT Traffic 10, CUHK Square 5, MOTChallenge 3) |
| Video found / readable | 9 / 9 |
| Annotated | 9 (CAD: queue counts only; MOT17: tracks with IDs) |
| Licences verified this sprint | 0 |

### API security tests

48 tests in `tests/test_api_security.py` (content validation, `.part` handling,
queue cap with a 429, oversized `video_id` returning 422 instead of 500, generic
error text). Remaining risks (no authentication, no WebSocket Origin check,
chunked uploads spooled before the size check, soft queue cap) are in
[security_review.md](security_review.md); the API must stay on localhost.

### Benchmark (`scripts/benchmark.py`)

Environment: Apple M1, 8 GB, macOS 27.0.1, Python 3.11.16, torch 2.15.0,
ultralytics 8.4.198, OpenCV 5.0.0.93, yolo11n.pt (sha256 `0ebbc80d4a76`),
confidence 0.35, imgsz 640, ByteTrack `track_buffer` 30. Video cad_seq27
(720×480, 30 fps). No drawing or video writing; 10 warm-up frames excluded.

| Run | Frames | Decode ms | Detection ms | Tracking ms | Queue ms | Total ms (mean / p95) | FPS | CPU % | Peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| MPS, full clip | 1794 | 0.44 | 29.00 | 4.41 | 0.76 | 34.61 / 45.83 | 28.89 | 56.4 | 468 MB |
| CPU, first 600 | 590 | 0.51 | 56.12 | 2.07 | 0.92 | 59.61 / 94.27 | 16.78 | 234.6 | 377 MB |
| Replay, legacy queue logic | 1794 | – | – | – | 0.52 | – | 1934 | – | 81 MB |
| Replay, temporal + guarded | 1794 | – | – | – | 0.99 | – | 1009 | – | 81 MB |

The MPS and CPU runs cover different frame counts, so they are not a like-for-like
comparison; why tracking time is higher on MPS was not investigated.

### Not tested / blocked

* **Waiting-time accuracy on real video: blocked.** No footage with per-person
  ground truth exists (CAD has no person IDs).
* **Browser re-test of the test page:** not done this sprint (the only change is
  the "Raw count (unsmoothed)" line).
* **CUDA:** not tested.
* **Licences:** none verified this sprint (MOT17 recorded as CC BY-NC-SA 3.0 in
  Sprint 1, not re-checked; CAD, MIT Traffic and CUHK Square unknown).
* HOTA is not available; multiple service counters are not implemented.

## Sprint 4 (2026-11-04)

### Automated tests

| Run | Result |
|---|---|
| Baseline at the start of Sprint 4 (`main` at `a83ee70`) | **152 passed** |
| Final, full suite (`pytest -q`, at `115f5cb`) | **203 passed**, 0 failed, 0 skipped, no warnings |
| Final, torch-free venv (`pytest -m "not integration"`, API deps but no torch/ultralytics) | **192 passed**, 11 deselected |
| `ruff check .` / `ruff format --check .` | clean |

### Queue-length accuracy on the Collective Activity Dataset (first real queue result)

Data: the 7 sequences labelled "queueing" (action 4) in the Collective Activity
Dataset (Choi, Shahid & Savarese, 2009), converted with
`ai_engine/tools/prepare_cad.py` at an **assumed 30 fps** (the dataset does not
state its frame rate). Ground truth = number of people labelled queueing in
each annotated frame (every 10th frame). Prediction = the per-second time
series from `run_tracking.py` (MPS, `--blur-heads`) with
`configs/cameras/cad_seqNN.yaml`, matched to the nearest second
(`evaluate_queue.py`, tolerance 0.6 s).

Method fixed before running: zones drawn by eye on frames of each clip, default
membership rules (dwell 3 s, speed 0.5 heights/s, grace 1.5 s, 2 s median), no
tuning on these clips. Scored twice: on every annotated frame, and from 5 s on
(the first 3 s dwell + 2 s smoothing cannot count anyone, while the labels
already show a full queue at frame 1).

Smoothed `queue_length`:

| Clip | Labelled frames (≥ 5 s) | Mean labelled queue | MAE all / ≥ 5 s | RMSE ≥ 5 s | Bias ≥ 5 s | Within ±1 ≥ 5 s |
|---|---|---|---|---|---|---|
| seq27 (garden path, 60 s) | 166 | 8.5 | 2.32 / 1.85 | 2.24 | +1.47 | 0.43 |
| seq25 (café counter) | 47 | 5.2 | 2.02 / 0.99 | 1.24 | −0.05 | 0.79 |
| seq26 (café, handheld) | 59 | 6.7 | 2.28 / 1.31 | 1.65 | +1.10 | 0.71 |
| seq40 (pier) | 26 | 7.8 | 3.84 / 2.46 | 2.98 | −1.08 | 0.46 |
| seq41 (lobby) | 56 | 8.8 | 2.23 / 0.88 | 1.20 | +0.41 | 0.79 |
| seq10 (cafeteria, handheld) | 16 | 5.1 | 2.90 / 1.25 | 1.58 | +1.25 | 0.62 |
| seq39 (kiosk, moving camera) | 37 | 5.8 | 2.21 / 1.11 | 1.35 | +0.95 | 0.70 |
| **All 7, pooled** | **407** | | **2.41 / 1.49** | **1.91** | **+0.87** | **0.59** |

Raw (unsmoothed) count, pooled: MAE 2.20 all / 1.53 from 5 s, within ±1 0.64.

Reading the numbers:

* From 5 s on, the count is off by about 1.5 people on queues of 5–9, and within
  one person on 59 % of labelled frames. It tends to **over-count** (+0.87):
  the zones are drawn as bands with margin for the handheld cameras, so people
  standing near the queue (crossing, waiting, talking) are counted too.
* The worst clip is seq40 (pier), where it under-counts: people overlap heavily
  in a side view, so the detector misses some.
* Limits: handheld cameras move the scene under a fixed zone; the dataset's
  "queueing" label is a human judgement, not our zone rule; 30 fps is assumed;
  no person IDs, so **waiting-time accuracy is still unmeasured**.

### API end-to-end (real model, MPS)

`python -m api` with a scratch data directory, upload of `cad_seq25.mp4` with
camera `cad_seq25` via `curl`, WebSocket captured with a Python client:

* 617 frames processed in 30.1 s; status `completed`, percent 100.
* WebSocket: 1 snapshot, 21 progress, 41 event and 1 final status message,
  then the server closed the socket.
* Waits CSV and the per-second time series are byte-identical (apart from the
  file name) to a CLI run of the same clip; SQLite held 41 events, 21
  time-series rows and 9 waits.
* `GET /api/videos/1/video` with `Range: bytes=0-999` → 206, 1000 bytes.
* After a server restart the run was still listed as `completed`.

### Browser check of the test page (2026-11-04)

The Chrome extension used earlier could not load the local server, so the page
was checked with **Playwright 1.58.0 driving headless Chromium 145.0.7632.6**
against `python -m api` (scratch data directory, real YOLO11n on MPS). Upload:
a 10 s cut of `cad_seq25.mp4` (302 frames) with camera `cad_seq25`.
Screenshots of each state were saved to `outputs/browser_check/` (gitignored)
and inspected.

| Step | Result | Observed |
|---|---|---|
| Page loads; camera dropdown lists the 7 `cad_seq*` configs | PASS | cad_seq10, 25, 26, 27, 39, 40, 41 (plus example_queue) |
| Upload starts a run, head blur on by default | PASS | |
| Progress bar and live numbers update during processing | PASS | 14 distinct progress texts; people in frame 5–8; service rate "insufficient data", predicted wait "unknown" (clip shorter than `min_history_seconds`) |
| Run completes | PASS | "Completed 100% (302 / 302 frames)" |
| Annotated video plays | PASS | 2.00 s played after 2 s; heads blurred in the frames |
| Seeking uses range requests | PASS | seek to 7.5 s landed at 7.50 s; video responses 200 and 206; no media error |
| Reopen from "Previous runs" after a page reload | PASS | status completed; summary (4 completed waits, mean 5 s, abandonment 20 %) and 5-row waits table shown |
| No horizontal scroll at 375 px width | PASS | |
| No console errors | PASS | |

One script bug was found and fixed during the check (the script tested the
`hidden` attribute, the page uses a `hidden` CSS class); no page bug was found.
Cosmetic only: "Previous runs" shows raw ISO timestamps. Not checked: Safari,
Firefox, real phones.

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
