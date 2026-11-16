# Evaluation protocol: when a new detector or tracker may become the default

Version **1**, written 16 Nov 2026, **before** any YOLO11s or BoT-SORT comparison was run.
Changing it needs a new version, committed before the comparison it governs.

## Clips and splits

From `datasets/splits/` (fixed in Sprint 5, before any measuring):

- **Tuning / validation (val):** cad_seq10, cad_seq25, cad_seq26.
- **Held-out test:** cad_seq27, cad_seq39, cad_seq40, cad_seq41.
- MOT17-04 and MOT17-09 are used for tracking metrics only (no queue labels).

**Nothing is tuned on test clips.** Queue-logic thresholds stay at the Sprint 4 defaults
(camera configs in `configs/cameras/`). No threshold is changed to suit a detector or tracker.

## How queue length is scored

The Sprint 4 protocol, unchanged: annotated CAD frames from 5 s on (`from5s`); the system's
smoothed queue length at the nearest per-second sample within 0.6 s; errors pooled over frames.
Metrics: MAE, bias (system − label), share within ±1 person.

## Uncertainty

- **Clip-level (cluster) bootstrap:** resample the split's clips with replacement, recompute the
  pooled metric over all frames of the resampled clips; 10,000 resamples; seed **20261116**;
  95 % percentile interval.
- **Paired version** for a difference between two configurations: each resample draws the same
  clips for both, and the interval is of (new − current) on the pooled metric.
- A **per-frame bootstrap** is also reported, labelled "optimistic (ignores within-clip
  correlation)". It never decides anything.
- With 3 (val) and 4 (test) clips the intervals are wide; they are reported as they are.

## The adoption rule

A new detector or tracker configuration becomes the default **only if all three hold**:

- **(a) Better on test, beyond noise:** its test-split queue-length MAE is lower than the current
  default's by more than the half-width of the paired clip-bootstrap 95 % interval of the
  difference. Precisely: with Δ = MAE_new − MAE_current (pooled over test frames) and the paired
  interval [L, U], adopt only if −Δ > (U − L) / 2.
- **(b) Not worse on validation:** its val-split MAE is ≤ the current default's val MAE (point
  estimate).
- **(c) Real time:** end-to-end processing on MPS at the default frame stride is at or above
  1.0 × real time on cad_seq27 (`scripts/benchmark.py` processing FPS ≥ the clip's FPS).

Otherwise the configuration is **reported and not adopted**. A configuration that passes may be
adopted only in its own commit, citing this protocol version and the numbers.

## Current default (the reference)

YOLO11n (`ai_engine/models/yolo11n.pt`, imgsz 640, confidence as configured) + ByteTrack
(resolved by `PersonTracker` from Ultralytics' `bytetrack.yaml`), stitching as configured, MPS.

## Candidates in this round

- B: YOLO11s + ByteTrack.
- C: YOLO11n + BoT-SORT without re-identification.
- D: YOLO11n + BoT-SORT with re-identification (`with_reid: True`, `model: auto`).

Each runs the same clips, camera configs, imgsz, stride and device; BoT-SORT uses the same
resolved `new_track_thresh`, `track_buffer` and input confidence as ByteTrack, so only the
algorithm differs.

## Also reported (never deciding)

Error attribution at IoU 0.5 and 0.3 side by side; MOT17-04/09 IDF1, MOTA and ID switches; queue
joins per labelled person and stitching links; peak RSS.
