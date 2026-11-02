# Testing Report (Sprint 1)

Date: 2026-11-02. Every number below comes from real runs on the machine described here.

## Execution environment

| Item | Value |
|---|---|
| Machine | MacBook Air, Apple M1, 8 GB RAM |
| OS | macOS 27.0.1 (arm64) |
| Python | 3.11.16 (venv at `.venv/`) |
| PyTorch | 2.15.0. MPS built and available: **yes**. CUDA: **no** |
| Ultralytics | 8.4.198 |
| OpenCV | 5.0.0 (`opencv-python==5.0.0.93`) |
| NumPy / pandas / lap / pytest | 2.4.6 / 3.1.0 / 0.5.13 / 9.2.0 |
| Model | `yolo11n.pt`, auto-downloaded to `ai_engine/models/` (5.6 MB) |

## Automated tests: `pytest -v`

**Result: 32 passed, 0 failed, 0 skipped (3.8 s).**
`pytest -m "not integration"`: 24 passed in 0.09 s.

| File | Tests | What is covered |
|---|---|---|
| `test_video_loader.py` | 9 | Loading a valid video; metadata (size, FPS, frame count, duration); sequential iteration; reading after the end or after release; missing, unsupported and corrupt files; release on exception; writer output and size check |
| `test_detector.py` | 9 | Device priority and unavailable-device errors; mocked result parsing (class filter, empty results); detection dict structure; settings validation. **Integration:** the real model loads, detects ≥3 people in `bus.jpg` with valid boxes, and returns nothing on a blank frame |
| `test_tracker.py` | 6 | **Mocked:** track ID extraction, frames without tracks, persist/reset call sequence. **Integration:** IDs stay stable over 5 consecutive frames; `reset()` restarts IDs at 1; an empty frame gives no tracks |
| `test_metrics.py` | 8 | Average and rolling FPS calculations, zero-frame handling, negative timings rejected, report schema, JSON round trip, overlay pixels. **Integration:** full pipeline on a synthetic video writes a readable MP4 with the right frame count and size and a valid JSON file; the CLI honours `--max-frames`, writes a log, and returns exit code 1 for a missing video |

Mocked tests are named `test_mocked_*` and use fake Ultralytics `Results`
objects. They only check parsing and control flow. All detection-quality and
tracking assertions use the real YOLO11n model on CPU with Ultralytics'
bundled `bus.jpg` image. No dataset download is needed.

## End-to-end runs on public video

Command: `python ai_engine/run_tracking.py --video datasets/raw/<video>.mp4 --device <device>`
(confidence 0.35, imgsz 640, no live display). Each run was executed alone so
runs did not compete for the machine.

| Video | Device | Frames | Runtime (s) | Inference (s) | Avg inference FPS | Avg processing FPS | Max people/frame | Avg people/frame | Track IDs |
|---|---|---|---|---|---|---|---|---|---|
| MOT17-09 (960×540, 30 fps) | MPS | 525 | 28.65 | 20.62 | **25.46** | **20.90** | 12 | 7.76 | 75 |
| MOT17-04 (960×540, 30 fps) | MPS (auto) | 1050 | 51.94 | 40.05 | **26.22** | **21.93** | 21 | 13.28 | 108 |
| MOT17-09 (960×540, 30 fps) | CPU | 525 | 27.45 | 23.01 | **22.82** | **20.19** | 12 | 7.76 | 75 |

Runtime includes model loading (about 0.1 s from cache) and the warm-up
frame. The first-ever MPS warm-up, including kernel compilation, took about 8 s.

Generated files, which are not committed:
* `outputs/videos/mot17_09_tracked.mp4`, `outputs/metrics/mot17_09_metrics.json` (MPS)
* `outputs/videos/mot17_04_tracked.mp4`, `outputs/metrics/mot17_04_metrics.json` (MPS)
* `outputs/cpu_run/videos/mot17_09_tracked.mp4`, `outputs/cpu_run/metrics/mot17_09_metrics.json` (CPU)

The output videos were checked with `ffprobe`: 1050 frames for MOT17-04,
matching the input. Frames extracted from them show green boxes, `ID n conf`
labels and the metrics panel.

The error path was also checked: a missing video logs
`ERROR Video file does not exist: ...` and the process exits with code 1.

## Observations and known limitations

1. **MPS is only about 10–15% faster than CPU for YOLO11n on the M1.** The
   nano model is small enough that host↔GPU transfer, synchronization and
   ByteTrack (which runs on the CPU) take a large share of each frame. MPS
   should help more with YOLO11s or larger `imgsz`.
2. **Processing FPS (~21) is below the 30 fps source rate on this M1,** so
   the engine is not real-time at 960×540 here. Decoding, drawing and mp4v
   encoding cost about 7.5–8.6 ms per frame on top of inference (from
   `total_processing_seconds − total_inference_seconds`).
3. **Recall on small, distant people is limited.** In the high-angle night
   scene (MOT17-04), many far-away pedestrians are missed at imgsz 640.
   Options are `--imgsz 960/1280` or YOLO11s. Accuracy (MOTA/IDF1) was **not**
   measured against MOT ground truth in this sprint.
4. **Track IDs fragment.** 75 IDs were assigned in MOT17-09, which has far
   fewer distinct people. Occlusions cause ID switches. Waiting-time logic in
   Sprint 3 must tolerate this.
5. **The overlay panel covers the top-left 260×168 px.** Boxes under it remain visible but are dimmed.
6. **The output codec is mp4v,** which plays in QuickTime and VLC but not in
   most browsers. The dashboard sprint should re-encode to H.264.
7. **The `--show` live window was not exercised** in automated testing,
   because the session had no interactive display. The code path is the
   standard `cv2.imshow` / `waitKey` pattern.
8. **CUDA paths are untested.** No NVIDIA hardware was available.
