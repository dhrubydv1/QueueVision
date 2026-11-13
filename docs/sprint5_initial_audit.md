# Sprint 5 Initial Audit (2026-11-08)

Written before any Sprint 5 code change. Every item below was checked in the
repository or by running a command; nothing is estimated.

## Repository state

| Item | Value |
|---|---|
| Branch at start | `main`, clean working tree, up to date with `origin/main` |
| Commit at start | `dd758b2` (Merge pull request #4 from dhrubydv1/sprint-4) |
| Uncommitted changes | none |
| Sprint 5 branch | `sprint-5/accuracy-and-validation`, created from `dd758b2` (did not exist before) |
| Python | 3.11.16 (`.venv/`) |
| `pytest -q` | **203 passed** in 38.0 s (includes the real-YOLO integration tests) |
| `ruff check .` | All checks passed |
| CI | `.github/workflows/ci.yml`: torch-free lint + unit job; integration job (`continue-on-error`) |

## Data actually present on this machine (all git-ignored)

* `datasets/raw/`: 7 Collective Activity Dataset (CAD) queueing clips
  `cad_seq{10,25,26,27,39,40,41}.mp4` (converted from JPEG frames at an
  **assumed** 30 fps) and the MOT17-04 / MOT17-09 preview videos.
* `datasets/annotations/cad/`: per-frame queue counts (every 10th frame) for
  the 7 CAD clips. CAD has **no person IDs**, so there is no waiting-time
  ground truth.
* `datasets/annotations/mot17/`: MOT17-04 and MOT17-09 `gt.txt` (with IDs) —
  usable for tracking-identity metrics, but these are street scenes, not queues.
* No dataset manifest, licence register, splits or validation tool existed.
  MIT Traffic and CUHK Square have not been acquired.

## Historical baseline (verified against `docs/testing_report.md`, Sprint 4)

Smoothed `queue_length`, 7 CAD clips pooled, frames from 5 s on (407 labelled
frames): **MAE 1.49, RMSE 1.91, bias +0.87, within ±1 0.59**. All frames:
MAE 2.41. Raw count from 5 s: MAE 1.53, within ±1 0.64. These numbers were
produced in Sprint 4; Sprint 5 re-runs the experiment rather than assuming
they reproduce.

## Existing functionality (confirmed by reading the code)

* Detection YOLO11n + ByteTrack via Ultralytics (`ai_engine/tracking/tracker.py`);
  low-score boxes (≥ 0.1) are passed to ByteTrack since Sprint 2.
* Queue: `QueueZone` (normalized polygon), `QueueMembershipTracker`
  (dwell 3 s + net-displacement speed ≤ 0.5 box heights/s, 1.5 s grace),
  median-smoothed length, `TrackStitcher`, `QueueLifecycle`
  (`queue_join`, `service_start`, `queue_abandon`), service-rate estimator
  (window/EWMA), wait prediction (`queue_length / rate`, `unknown` when not
  estimable), congestion alerts.
* API (FastAPI): upload, list/get runs, events, waits, alerts, timeseries,
  report, annotated video with range requests, cancel, delete, WebSocket
  progress; SQLite in WAL mode; single worker thread; bound to 127.0.0.1.

## Concrete problems found

### Queue membership (`ai_engine/queue/membership.py`)
1. **Membership is keyed by raw ByteTrack ID and ignores stitching.** The
   stitcher's person keys are only used by the lifecycle. After an ID switch
   the old track stays counted for the 1.5 s grace, then drops; the new track
   must dwell another 3 s before it counts. Each ID switch inside the queue
   therefore creates a gap of about 1.5 s with the person missing from the count.
2. **No service state.** A person standing in a service zone that overlaps
   the queue zone (e.g. `cad_seq25`) is still counted as queuing.
3. Entry is a binary point-in-polygon test with no boundary margin or
   hysteresis; people standing just outside a generously drawn zone, or
   flickering across its edge, are handled only by the dwell timer.
4. No check on track quality: a track seen on few of the dwell-window frames,
   or kept alive only by low-score (0.1–0.25) boxes, can be confirmed.
5. Speed is net foot displacement in image space; motion along the camera axis
   is under-measured (documented in Sprint 2's smoke test).
6. Only three statuses (`candidate`, `in_queue`, `left`); occlusion is not an
   explicit state.

### Stitching (`ai_engine/queue/stitching.py`)
7. Greedy nearest-first matching with only a gap and a distance limit: no
   ambiguity rejection, no size-similarity, velocity or direction check, no
   match confidence, no record of why a link was made or rejected.
8. Sprint 3 reported one MOT17-09 person with 8 chained track IDs over 11 s
   (likely wrong merges); there was no tool to measure merge correctness.

### Lifecycle and waiting time
9. No `service_complete` or `queue_exit` events; the service rate counts
   service **starts** (people reaching the counter), not completed services.
10. Waiting-time ground-truth reader supports only `person,join_s,service_s,abandoned`;
    no per-clip metrics, no median absolute error.
11. No real waiting-time ground truth exists (CAD has no IDs).

### API (`api/`)
12. Uploads are validated by extension only; file content is never checked.
13. No limit on queued jobs (each upload can be up to 500 MB).
14. Job failures store `str(exception)` and return it to clients.
15. Upload/camera errors echo raw config error text (may include paths).
16. `video_id` path parameters are unbounded; values beyond SQLite's int64
    range reach the database driver.
17. Uploads are written straight to their final file name.

### Evaluation
18. Comparing algorithms required re-running YOLO on every clip; tracking is
    not saved by default, so membership changes could not be compared on
    identical tracks.

## Sprint 5 approach

* Save raw tracks once per clip (`run_tracking.py --save-mot`), then replay
  the identical tracks through the old and new queue logic, so differences
  come only from queue logic.
* To limit in-sample tuning on 7 clips, a split is fixed before any new
  measurement: CAD `seq10, seq25, seq26` = tuning (val); `seq27, seq39,
  seq40, seq41` = held-out (test). Results are reported for both.
