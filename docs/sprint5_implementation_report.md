# Sprint 5 Implementation Report (2026-11-08 – 2026-11-09)

Branch `sprint-5/accuracy-and-validation`, created from `main` at `dd758b2`.
Every number below was produced by a command run on the machine described under
"Benchmarks"; saved outputs are under `outputs/` (git-ignored). The initial state
is in [`sprint5_initial_audit.md`](sprint5_initial_audit.md).

**Headline result: none of the new queue-counting options beat the Sprint 4
algorithm on held-out clips, so the defaults are unchanged.** The new code is
available behind configuration switches, with the measurements that show why it
is not the default.

## 1. Method: replaying identical tracks

YOLO11n + ByteTrack was run once per clip (`run_tracking.py --save-mot`, MPS,
imgsz 640) on the 7 CAD queueing clips and MOT17-04/09. All queue-logic variants
then replay the **same saved tracks** (`ai_engine/replay_queue.py`), so
differences come only from queue logic.

* **Reproduction check:** scoring the new run's time series gave exactly
  the Sprint 4 per-clip MAE on all 7 clips.
* **Replay correctness gate:** replaying the saved tracks with the shipped
  configs reproduces the run's `raw_queue_count` and `queue_length` columns
  row for row on all 7 clips (0 differences). This gate is in
  `tests/test_replay.py`; it is skipped when the data is absent, as in CI.
* **Split fixed before measuring** (`datasets/splits/`): val (tuning) =
  cad_seq10, 25, 26; test (held out) = cad_seq27, 39, 40, 41. No parameter was
  tuned in this sprint; all variants below are one-at-a-time ablations.
* **Scoring:** the Sprint 4 protocol. Annotated frames from 5 s on (`from5s`),
  nearest per-second sample within 0.6 s, errors pooled over frames.

## 2. Queue counting (Workstream A)

**Implemented.** `ai_engine/queue/temporal_membership.py`, selected with
`membership.algorithm: temporal` (default `legacy`):

* Explicit states: OUTSIDE, CANDIDATE, WAITING, TEMPORARILY_OCCLUDED, SERVICE,
  EXITED, ABANDONED.
* Identity per stitched person key, not per raw track ID.
* Boundary margin with hysteresis.
* Observation-fraction, observation-count and mean-detection-confidence gates.
* Optional depth (scale-change) motion term.
* `count_service_as_queue`.
* A heuristic, uncalibrated membership-confidence score.
* Raw and smoothed counts, per-state counts in `QueueFrameState` and the report.
* 31 tests in `tests/test_temporal_membership.py`, plus the service-count fix in Workstream B.

**Results.** Smoothed count, frames from 5 s.

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

**What the measurements show:**

* Tracking identity per stitched person is only as good as the stitcher. On
  seq27 the legacy stitcher made 108 links in 60 s on a ~12-person queue and
  linked tracks that ByteTrack later revived under their original ID. One person
  then held two keys and was counted twice.
* With stitching off, the temporal classifier lowers the bias (+0.58 against
  +0.96 on test) but not the MAE (1.668 against 1.619).
* The one-at-a-time ablations in `outputs/s5_replay/queue_variants_a.txt`
  (margin 0.25, observed fraction 0.6, mean confidence 0.35, scale motion 1.0)
  all measured worse than legacy on the pooled data.
* **Conclusion:** no improvement is claimed. Legacy stays the default.
* Small sample: 7 clips from one dataset whose "queueing" label is a human
  judgement, at an assumed 30 fps.

## 3. Track stitching (Workstream B)

**Implemented.** `GuardedTrackStitcher` (`stitching.algorithm: guarded`;
default `legacy`; `stitching.enabled: false` disables stitching):

* Deferred linking until ByteTrack can no longer revive the lost ID
  (`min_lost_seconds` 1.0 = `track_buffer` 30 frames / 30 fps).
* Revived IDs are mapped back to their original owner.
* Velocity-predicted position; size-ratio, direction and duplicate-overlap checks.
* A chain-length cap.
* **Ambiguity rejection:** when the runner-up score is within the margin, nothing is linked.
* Per-decision diagnostics (JSONL via `--stitch-diagnostics`) in the requested
  format, plus a summary in the report.
* 31 tests in `tests/test_guarded_stitching.py`.

**MOT17 identity evaluation** (`ai_engine/evaluate_stitching.py`). The same saved
tracks are scored with motmetrics; link correctness is judged against MOT17
ground-truth IDs. HOTA is **not available** (motmetrics does not implement it;
TrackEval is not installed).

| Sequence | Config | IDF1 | ID switches | Frag. | Links | Correct / incorrect / unverifiable | Precision |
|---|---|---|---|---|---|---|---|
| MOT17-04 | no stitching | 0.412 | 47 | 140 | 0 | – | – |
| MOT17-04 | legacy | 0.427 | 44 | 140 | 7 | 3 / 2 / 2 | 0.600 |
| MOT17-04 | guarded | 0.418 | 50 | 140 | 4 | 3 / 1 / 0 | 0.750 |
| MOT17-09 | no stitching | 0.508 | 68 | 78 | 0 | – | – |
| MOT17-09 | legacy | 0.504 | 65 | 78 | 32 | 7 / 13 / 12 | 0.350 |
| MOT17-09 | guarded | 0.521 | 76 | 78 | 16 | 6 / 5 / 5 | 0.545 |

* **Precision:** guarded halves the links on MOT17-09 and raises link precision
  on both sequences.
* **ID switches:** guarded counts more switches than no stitching, partly
  because a track carries a provisional key for the ~1 s before its link applies.
  Scored under final keys: 45 (04) and 65 (09).
* **The Sprint 3 "8 chained IDs" case:** the cap (4) never triggered. On
  MOT17-09 legacy links are wrong more often than right (13 incorrect against
  7 correct).
* **CAD stitch statistics:** guarded links 3 / 6 / 2 / 63 / 16 / 8 / 9 times
  against legacy's 12 / 19 / 12 / 108 / 28 / 20 / 19 (seq10, 25, 26, 27, 39,
  40, 41). Most rejections are `distance_too_far`. Revived tracks: 0, because
  deferred linking already prevents them.

## 4. Lifecycle and waiting time (Workstream C)

* **`service_complete`:** recorded only with a service zone. The person must be
  inside for at least 2 s and then be *seen* outside for 1 s; it is never
  inferred from disappearance. It is never emitted with a service line.
* **`queue_exit`:** a joined person who is no longer seen anywhere, with an
  outcome of served, abandoned or unknown.
* **Existing events:** `queue_join`, `service_start` and `queue_abandon` are unchanged.
* **Service rate:** `service_rate.basis` is `service_start` (default) or
  `service_complete`; the latter is rejected for cameras without a service
  zone. The report gains `estimator` (`queue_length_over_service_rate/v1`) and
  `basis`. The prediction stays `unknown` (never 0) without history, with too
  few events, with zero completions in the window, or with a zero or missing rate.
* **Waiting-time evaluation:** `evaluate_waiting.py` now also reads
  `clip_id,track_id,queue_join_seconds,service_start_seconds,service_complete_seconds,observed_wait_seconds,annotation_confidence`.
  It reports MAE, RMSE, median absolute error, bias and sample count per clip,
  for measured waits and predicted waits separately, plus counts of excluded
  rows by reason.
* **Multiple counters:** not implemented (one service point per camera); documented.
* **CAD seq25 replay, default config:** 11 `queue_join`, 4 `service_start`,
  1 `service_complete`, 5 `queue_abandon`, 8 `queue_exit`.
* **Waiting-time accuracy on real video: BLOCKED.** No footage with per-person
  ground truth exists; CAD has no person IDs. The evaluator is tested on
  synthetic data only (`tests/test_wait_eval_observed.py`).

## 5. Datasets (Workstream D)

* **Layout:** the folder skeleton plus `datasets/metadata/dataset_manifest.csv`
  (27 rows), `dataset_licensing_register.csv`, `annotation_guidelines.md`, and
  `datasets/splits/{train,val,test}.txt`.
* **Validator:** `scripts/validate_dataset.py` with `ai_engine/data/validation.py`.
* **Validator result:**

| Item | Count |
|---|---|
| Clips in manifest | 27 (the 25-clip plan + 2 extra CAD clips) |
| Acquired | 9 (CAD 7 of 5 planned; MOTChallenge 2 of 5) |
| Planned, not acquired | 18 (MIT Traffic 10, CUHK Square 5, MOTChallenge 3) |
| Video found / readable | 9 / 9 |
| Annotated | 9 (CAD: queue counts only; MOT17: tracks with IDs) |
| Licences verified this sprint | 0. MOT17 recorded as CC BY-NC-SA 3.0 in Sprint 1, not re-checked; CAD, MIT Traffic and CUHK Square unknown |

* **Existing files:** the derived MP4s stay in `datasets/raw/` so existing
  configs, docs and tests keep working (documented in `datasets/README.md`).
* **Git-ignore fix:** the old `data/` rule silently ignored the new
  `ai_engine/data/` package; it is now `/data/`.

## 6. API security (Workstream E)

Details in [`security_review.md`](security_review.md). Fixed:

* Content validation: magic bytes plus an OpenCV decode probe, with duration
  and frame-size limits.
* Uploads written to `.part` files and renamed after validation; stale `.part`
  files are removed at startup.
* Sanitized stored filename; generated storage names.
* Queue cap of 5 waiting runs, answered with a 429 before the body is read.
* **Bug fixed:** a `video_id` beyond int64 used to produce a 500; all routes
  now return 422, and the WebSocket closes cleanly.
* Generic error text for job failures, bad cameras and unexpected exceptions;
  details are logged.
* Report and final status written in one transaction.
* Warning when bound to a non-loopback host.
* 48 tests in `tests/test_api_security.py`.

Remaining: no authentication, no CSRF/Origin check on the WebSocket, chunked
uploads without `Content-Length` are spooled before the size check, and the
queue cap is a soft limit. **The API must stay on localhost.**

## 7. Benchmarks (Workstream F)

`scripts/benchmark.py`. Apple M1, 8 GB, macOS 27.0.1, Python 3.11.16, torch
2.15.0, ultralytics 8.4.198, OpenCV 5.0.0.93, yolo11n.pt (sha256 `0ebbc80d4a76`),
confidence 0.35, imgsz 640, ByteTrack `track_buffer` 30. Video cad_seq27
(720×480, 30 fps). No drawing or video writing; 10 warm-up frames excluded.

| Run | Frames | Decode ms | Detection ms | Tracking ms | Queue ms | Total ms (mean / p95) | FPS | CPU % | Peak RSS |
|---|---|---|---|---|---|---|---|---|---|
| MPS, full clip | 1794 | 0.44 | 29.00 | 4.41 | 0.76 | 34.61 / 45.83 | 28.89 | 56.4 | 468 MB |
| CPU, first 600 | 590 | 0.51 | 56.12 | 2.07 | 0.92 | 59.61 / 94.27 | 16.78 | 234.6 | 377 MB |
| Replay, legacy queue logic | 1794 | – | – | – | 0.52 | – | 1934 | – | 81 MB |
| Replay, temporal + guarded | 1794 | – | – | – | 0.99 | – | 1009 | – | 81 MB |

* **Workloads differ:** the MPS and CPU runs cover different frame counts.
* **Tracking time:** the reason it is higher on MPS was not investigated.
* **Accuracy figures** for the same configuration are in sections 2 to 4.

## 8. Frontend (Workstream G)

* The test page shows "Raw count (unsmoothed)" next to the smoothed queue length.
* "unknown" / "insufficient data" were already shown instead of 0.
* No other change; the page was not re-tested in a browser this sprint.

## 9. Tests

| Run | Result |
|---|---|
| Start of sprint (`dd758b2`) | 203 passed |
| End of sprint, full suite | **370 passed** |
| Real-YOLO integration (`-m integration`) | 11 passed |
| `ruff check .` / `ruff format --check .` | clean / 100 files formatted |

## 10. Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| Sprint 4 functionality still works | Met | original tests pass within the 370 (a few adjusted for deliberate changes: generic API error text, `commit=` keyword, new event types in `EVENT_TYPES`); replay gate reproduces Sprint 4 output exactly; default configs unchanged |
| Explicit temporal membership states | Met (opt-in) | `temporal_membership.py`, 31 tests |
| Passing pedestrians not counted automatically | Met (both algorithms) | dwell + speed rule; pass-through tests |
| Temporary occlusion handled | Met | grace period / TEMPORARILY_OCCLUDED tests |
| Raw and stabilized counts available | Met | `raw_queue_count` + `queue_length` in CSV, API, page |
| Stitching rejects ambiguous matches | Met (guarded) | `ambiguous` rejections recorded on CAD; tests |
| Stitching diagnostics available | Met | `--stitch-diagnostics` JSONL; report summary |
| Service start and completion distinct | Met (service zones only) | `service_complete` event; 20 tests |
| Waiting-time evaluation supports real ground truth | Met (format); **accuracy not evaluated** | no labelled footage exists |
| Dataset manifest and licence register exist | Met | `datasets/metadata/` |
| Dataset validation utility works | Met | 9/9 acquired clips validated |
| Missing datasets reported honestly | Met | 18 clips `planned_not_acquired`; 0 licences verified |
| Security review documented | Met | `docs/security_review.md` |
| All tests pass | Met | 370 passed |
| No new Ruff violations | Met | clean |
| README and testing docs updated | Met | this sprint's doc updates |
| Queue counting improved | **Not met** | no variant beat legacy on held-out clips |

## 11. Remaining work / recommended Sprint 6

1. **Labelled queue footage with person IDs.** This is the blocker for
   waiting-time accuracy and for judging stitching on queues. Annotate 3–5 CAD
   clips with per-person join/service times (format in
   `annotation_guidelines.md`), or acquire the planned MIT Traffic / CUHK Square
   clips once their licences are checked.
2. **Verify licences** for CAD, MIT Traffic and CUHK Square before any further use.
3. **Error analysis of the legacy over-count** (+0.96 bias on test): which
   frames, which people. Zones drawn more tightly would be a configuration
   change; it must be checked on the val split only.
4. **Stitching:** link precision of 0.55–0.75 is too low for identity-based
   counting. Measure on real queue footage before investing more; a light
   appearance feature could be justified only after that measurement.
5. **imgsz 960** improved MOT17-04 detection in Sprint 2; test its effect on
   CAD under-counting (seq40) on the val split.
6. **API:** authentication and an Origin check before any non-local use.
