# Testing Report

The fix-pass session 2 evidence (clip-bootstrap intervals, IoU 0.5/0.3 attribution, detector and tracker comparisons) comes first, then the Anonymous Bank call-centre results (predictor accuracy on real call-centre queues), then the Sprint 8 browser checklist (pending, for a person) and pacing investigation, then Sprint 7 (session 2) results, then Sprints 6, 5, 4, 3 and 2; the Sprint 1 report is
kept at the end. Every number here comes from a command that was actually run
on the machine described under Sprint 2 (same environment). Nothing was
estimated.

## Fix pass, session 2: uncertainty and detector/tracker comparisons (`fix/evaluation`, 2026-11-16)

**Result in one sentence: the current default (YOLO11n + ByteTrack) stays; none of the three candidates (YOLO11s + ByteTrack, YOLO11n + BoT-SORT, YOLO11n + BoT-SORT with re-identification) passes the adoption rule committed before the comparison.** The held-out queue-length MAE of the default is 1.62 people, 95 % clip-bootstrap interval 0.97 to 2.03, from 4 clips (285 frames).

All numbers in this section come from `outputs/s9_eval/` (local, gitignored; `intervals_default.json`, `attribution_default.json`, `comparisons.json`, `{A,B,C,D}/`, `gmc/`, `repro/`) or from the commands named here. The protocol is [evaluation_protocol.md](evaluation_protocol.md) version 1, written and committed (`84a0b43`) by the controller before any comparison was run. Nothing earlier in this report was changed.

### Method

- Reproduction first: the Sprint 5/6 figures were re-run from the existing scripts and matched exactly before anything new was added (test MAE 1.619, bias +0.960, within +-1 0.537, 285 frames; val MAE 1.176, 122 frames; MOT17-04 IDF1 0.412; MOT17-09 IDF1 0.508; `outputs/s9_eval/repro/`). The new `queue_640.json` equals the Sprint 6 file apart from `generated_at`.
- Splits: from `datasets/splits/`. Val (tuning): cad_seq10, 25, 26. Test (held out): cad_seq27, 39, 40, 41. Nothing was tuned on test; thresholds are the Sprint 4 defaults.
- Scoring: Sprint 4 protocol unchanged. Smoothed queue length at the nearest per-second sample within 0.6 s of each annotated frame, frames from 5 s on.
- Clip bootstrap (`ai_engine/evaluation/bootstrap.py`): clips of the split resampled with replacement, pooled MAE / bias / within +-1 over all frames of the drawn clips, 10,000 resamples, seed 20261116, 95 % percentile interval. It reuses the day-bootstrap machinery; `day_bootstrap.py` and its seed (20261113) are untouched.
- Paired version: both configurations use the same drawn clips; the interval is of (new - current). Frames are aligned on (clip, time); unaligned frames are dropped and counted, and the count was 0 in every comparison (val, test, all; B, C, D).
- Per-frame bootstrap: every frame its own cluster. It ignores within-clip correlation, is labelled "optimistic (ignores within-clip correlation)", and decides nothing.
- Candidates ran every clip with the same camera configs, imgsz 640, stride 1, MPS, one model job at a time. The BoT-SORT YAML is resolved like ByteTrack's: `new_track_thresh`, `track_buffer` (from seconds) and the input confidence are identical, so only the algorithm differs (shared keys equal, checked by a test).
- Real time (criterion c): `scripts/benchmark.py` on cad_seq27 (full clip, 1804 frames, clip 30.0 fps), one run per configuration. Benchmark excludes drawing and video writing.

### Sample sizes

| Set | Clips | Labelled frames scored |
|---|---|---|
| val (cad_seq10, 25, 26) | 3 | 122 |
| test (cad_seq27, 39, 40, 41) | 4 | 285 |
| all CAD | 7 | 407 |
| MOT17-04, MOT17-09 | 2 | tracking only, no queue labels |

`datasets/splits/test.txt` also lists mot17_04 and mot17_09. They have no queue labels, are not in the replayed clip set, and are excluded from every queue metric.

### Headline with intervals (default A: YOLO11n + ByteTrack; source `outputs/s9_eval/intervals_default.json`)

| Split | Frames | MAE [95 % clip bootstrap] | bias [95 %] | within +-1 [95 %] |
|---|---|---|---|---|
| val (3 clips) | 122 | 1.176 [0.989, 1.305] | +0.676 [-0.053, 1.250] | 0.730 [0.625, 0.787] |
| test (4 clips) | 285 | 1.619 [0.968, 2.026] | +0.960 [-0.426, 1.372] | 0.537 [0.432, 0.753] |
| all (7 clips) | 407 | 1.486 [1.034, 1.782] | +0.875 [0.083, 1.284] | 0.595 [0.482, 0.754] |

Per-frame bootstrap (optimistic, ignores within-clip correlation), test MAE: 1.619 [1.477, 1.768]; val 1.176 [1.012, 1.344]; all 1.486 [1.370, 1.604]. It is much narrower than the clip interval and should not be used for claims. The test bias interval includes 0, so the over-counting on the test clips is not distinguishable from no bias at this sample size.

### Error attribution at IoU 0.5 and 0.3 (default A, from 5 s, mean per frame; source `attribution_default.json`)

Queue-length MAE compares counts and does not use IoU; the attribution does. The default threshold of every metric is unchanged; the table shows both side by side. The bias column of the attribution is the raw (unsmoothed) count's bias, so it differs from the smoothed +0.960.

| Split | IoU | tp | fp_unmatched | of which overlaps a labelled person | counted_not_visible | fn_no_detection | fn_candidate_dwell | FP total | FN total |
|---|---|---|---|---|---|---|---|---|---|
| val | 0.5 | 3.861 | 1.549 | 1.500 | 0.795 | 1.262 | 0.500 | 2.377 | 1.762 |
| val | 0.3 | 4.967 | 0.434 | 0.385 | 0.795 | 0.123 | 0.533 | 1.271 | 0.656 |
| test | 0.5 | 5.354 | 2.330 | 1.621 | 1.319 | 1.821 | 0.881 | 3.691 | 2.709 |
| test | 0.3 | 6.526 | 1.151 | 0.442 | 1.319 | 0.246 | 1.267 | 2.519 | 1.537 |

Reading: moving from IoU 0.5 to 0.3 turns most of the unmatched detections that overlap a labelled person, and most of "no detection" misses, into matches (test: fn_no_detection 1.82 to 0.25; overlapping fp 1.62 to 0.44). Much of the error at 0.5 is box-localisation mismatch (labels and detections disagree on box extent), not missed or invented people. What remains at 0.3 on test is people counted but not visible in the label (1.32), unmatched detections with no labelled person near them (0.71) and dwell-candidate misses (1.27).

### Comparison and adoption decisions (rule: evaluation_protocol.md v1; source `outputs/s9_eval/comparisons.json`)

Paired difference on test (new - A), 4 clips, clip bootstrap:

| Candidate | test MAE [95 %] | delta MAE vs A [paired 95 %] | half-width | (a) test improvement beyond half-width | (b) val MAE vs A (1.176) | (c) fps on cad_seq27 (>= 30.0) | Decision |
|---|---|---|---|---|---|---|---|
| B: YOLO11s + ByteTrack | 1.396 [1.082, 1.841] | -0.223 [-0.403, 0.216] | 0.309 | FAIL (0.223 < 0.309) | pass (0.844) | FAIL (20.53) | not adopted |
| C: YOLO11n + BoT-SORT | 2.395 [0.925, 3.107] | +0.775 [-0.043, 1.172] | 0.607 | FAIL (worse) | pass (1.098) | FAIL (25.22) | not adopted |
| D: YOLO11n + BoT-SORT + ReID | 2.488 [1.075, 3.172] | +0.868 [0.048, 1.315] | 0.634 | FAIL (worse) | pass (1.160) | FAIL (17.96) | not adopted |

Other measurements (reported, never deciding):

| Config | val MAE | test bias | test within +-1 | processing fps | peak RSS MB |
|---|---|---|---|---|---|
| A | 1.176 | +0.960 | 0.537 | 31.94 | 400.6 |
| B | 0.844 | +0.365 | 0.639 | 20.53 | 519.1 |
| C | 1.098 | +1.893 | 0.326 | 25.22 | 502.5 |
| D | 1.160 | +2.116 | 0.323 | 17.96 | 512.7 |

MOT17 tracking (IoU 0.5, `ai_engine.evaluate_tracking` as in Sprint 5; IDF1 / MOTA / ID switches):

| Config | MOT17-04 | MOT17-09 |
|---|---|---|
| A | 0.4119 / 0.3127 / 47 | 0.5082 / 0.5200 / 68 |
| B | 0.4078 / 0.2896 / 40 | 0.5745 / 0.5831 / 47 |
| C | 0.4376 / 0.3222 / 39 | 0.4993 / 0.5345 / 70 |
| D | 0.4424 / 0.3220 / 47 | 0.5329 / 0.5215 / 91 |

Queue joins (`queue_join` events) and stitching links per clip, from each run's metrics JSON (joins / links; labelled peak queue count in the second column). CAD labels carry no person ids and Sprint 5/6 did not report joins per labelled person, so the labelled peak count is only a proxy denominator:

| Clip | labelled peak | A | B | C | D |
|---|---|---|---|---|---|
| cad_seq10 | 6 | 6 / 12 | 6 / 4 | 6 / 12 | 7 / 12 |
| cad_seq25 | 6 | 11 / 19 | 6 / 16 | 12 / 18 | 10 / 17 |
| cad_seq26 | 8 | 12 / 12 | 10 / 7 | 12 / 12 | 10 / 22 |
| cad_seq27 | 11 | 41 / 108 | 31 / 98 | 32 / 37 | 28 / 58 |
| cad_seq39 | 7 | 13 / 28 | 10 / 25 | 9 / 26 | 9 / 29 |
| cad_seq40 | 10 | 10 / 20 | 12 / 22 | 11 / 22 | 11 / 22 |
| cad_seq41 | 11 | 13 / 19 | 15 / 18 | 15 / 17 | 14 / 22 |

A's joins and links come from the Sprint 5 run (older code); B, C and D from current code. Queue MAE is unaffected, because all four are replayed through the same current `replay_queue`.

Attribution of the candidates on test, per frame (raw count, FP total / FN total at IoU 0.5 and 0.3): A 9.05, 3.69/2.71 and 2.52/1.54; B 8.45, 3.02/2.63 and 1.97/1.58; C 9.94, 4.24/2.36 and 2.98/1.11; D 10.14, 4.48/2.41 and 3.12/1.05.

What the comparison shows:

- B (YOLO11s) has the lowest point MAE on test (1.396 against 1.619), but the paired interval [-0.403, 0.216] includes 0 and the improvement (0.223) is below the half-width (0.309). It is also too slow: 20.5 fps against 30.0 needed.
- BoT-SORT (C, D) raises the raw count on test (more unmatched detections) and the test MAE (+0.775, +0.868); only D's interval excludes 0, and on the worse side. BoT-SORT cuts stitching links sharply on cad_seq27 (108 to 37 and 58) but that does not give better counts.
- Neither BoT-SORT variant is real time on this clip (25.2 and 18.0 fps).
- On MOT17, B improves MOT17-09 (IDF1 0.5745, ID switches 47 against 68); C and D change IDF1 modestly; D has more ID switches on MOT17-09 (91 against 68). MOT17 results are reported, not used in the rule.

### GMC and ReID cost (BoT-SORT, cad_seq27 first 150 frames, 140 measured, MPS, `scripts/benchmark.py`; `outputs/s9_eval/gmc/final/`)

| Config | processing fps (runs) | tracking ms/frame |
|---|---|---|
| ByteTrack | 31.9 / 32.7 | 4.2 / 4.0 |
| BoT-SORT, bundled `gmc_method` sparseOptFlow | 25.6 / 26.6 | 9.6 / 9.3 |
| BoT-SORT, `gmc_method` none | 32.6 / 31.8 | 4.0 / 4.1 |
| BoT-SORT sparseOptFlow + ReID (1 run) | 17.0 | 29.9 |

Global motion compensation costs about 5.4 ms/frame, about 6 fps, on the M1 (derived, not a separate measurement: mean of two runs each from `outputs/s9_eval/gmc/final/run2` and `run6` (sparseOptFlow) against `run3` and `run7` (`gmc_method` none); tracking ms 9.56 and 9.31, mean 9.44, against 4.03 and 4.14, mean 4.08, difference 5.35; processing fps 25.55 and 26.61, mean 26.1, against 32.61 and 31.76, mean 32.2, difference 6.1). With it off BoT-SORT costs the same as ByteTrack (tracking 4.08 ms against 4.17 and 4.01 in `run1` and `run5`). Whether BoT-SORT without GMC is as accurate was not evaluated: only its speed was measured. ReID adds about 20 ms of tracking on top. The ReID features are the detector's own 64-dimensional head slice, not a purpose-trained embedding, and need a PyTorch `.pt` detector (see the report's CoreML note). The `gmc_method` option is exposed on `scripts/benchmark.py` only; defaults stay as bundled.

### Limits

- Few clips: 3 val and 4 test clips give wide intervals (test MAE 0.97 to 2.03; test bias interval includes 0). The paired half-widths (0.31 to 0.63) are larger than most of the differences measured.
- A single benchmark run per configuration for criterion (c). The current default passes real time only narrowly (31.94 fps against 30.0 for the clip); any load on the machine could flip it. The separate GMC runs show how small two-run spreads are: derived from `gmc/final/run1` and `run5` (ByteTrack, 31.86 and 32.73 fps, difference 0.87) and `run2` and `run6` (BoT-SORT, 25.55 and 26.61, difference 1.06); n = 2 each, so this is an indication, not an estimate of the variance.
- Queue joins per labelled person is a proxy (raw joins and links next to the labelled peak count), because the CAD labels carry no person ids.
- mot17_04 and mot17_09 are listed in `datasets/splits/test.txt` but have no queue labels and are excluded from queue metrics; the "4 test clips" are the CAD clips only.
- Scoring only annotated frames from 5 s, on 7 clips of one public dataset (CAD), not on a bank branch.
- The benchmark has no drawing or video writing, so the processing fps of the full `run_tracking` run is lower.
- The per-frame bootstrap is optimistic and not a basis for decisions.

### What this supports and does not support

Supports:
- The default queue-length error on held-out clips is about 1.6 people, with an honest 95 % interval of roughly 1.0 to 2.0 on 4 clips.
- Under the committed rule, YOLO11s and BoT-SORT (with or without ReID) are not better than YOLO11n + ByteTrack on this data in a way that survives the noise, and none of them runs in real time at the default settings on this machine.
- Much of the error at IoU 0.5 is localisation mismatch rather than missing or invented people.

Does not support:
- A claim that YOLO11s is worse (its point MAE is lower; the rule was simply not met on accuracy beyond noise and on speed) or that BoT-SORT never helps (it reduces stitching links, and C has fewer ID switches on MOT17-04, 39 against 47; on MOT17-09 C has 70 against 68 and D 91).
- Any claim about waiting-time accuracy, or about performance on queue footage other than the CAD clips.
- Any claim at a finer level than the intervals allow; differences under about 0.3 to 0.6 people cannot be resolved with 4 clips.

## Predictor accuracy on real call-centre queues (Anonymous Bank, 1999; protocol v1 + post-hoc v2 addendum)

**Result in one sentence: on the held-out test months the QueueVision prediction formula (queue length divided by service rate) is less accurate than all three simple baselines.** Mean absolute error (MAE) 96.8 s [91.4, 102.8] against 77.2 s for "last to enter service" (LES), 73.2 s for the recent mean and 61.2 s for a queue-length regression. It is biased high (+39.6 s), gives a prediction for only 87 % of calls, and its low-high range contains the actual wait for 22 % of calls. This is a measurement of the **formula on a phone queue**, reported as "predictor accuracy on real call-centre queues". It is not a measurement of QueueVision's waiting-time accuracy, and not of the cameras.

All numbers below come from `outputs/metrics/anonymous_bank_wait_eval.json` and `outputs/metrics/anonymous_bank_validation.json` (both local and gitignored; reproduce with the commands in the README), or from the commands named in the text.

### Method

- Data: "Anonymous Bank" Call-Center Data, Ilan Guedj and Avi Mandelbaum, Technion SEE Center (all calls of 1999, 12 monthly files, 444,448 rows). Input hashes are recorded in the eval JSON (`AnonymousBank.rar` SHA-256 `110527823a296e3163566f5d10d687e13859c60d963fd403d4623d89c7cadc4b`).
- Protocol: [docs/anonymous_bank_protocol.md](anonymous_bank_protocol.md). **Version 1 was committed in `f940045` before any prediction was scored**; the **version 2 addendum was committed in `aa112de` after the v1 results on validation and test were seen** and adds one post-hoc sensitivity (S1) without changing the v1 headline.
- Replay: each day of each queue is replayed on its own through the engine's own `ServiceRateEstimator` and `predict_wait` with `ServiceRateSettings()` defaults (window 300 s, min_services 2, min_history 60 s, basis service_start). At each call's `q_start` the queue length is the number of other calls queued at that moment and the service rate uses only service events strictly before it (no look-ahead). Engine code is not changed.
- Ground truth: actual wait = `q_exit - q_start` of AGENT calls. HANG calls left before service (censored): they enter no error metric; their number and how many hung up after a longer wait than predicted are reported.
- Primary queue: queued calls of types PS, PE, NE, NW, TT. Sensitivity queues: all types (including IN and AA), and PS + PE only.
- Baselines (same inputs): LES (wait of the last AGENT call to leave the queue before now, same day); recent mean (mean wait of the last 20 such calls, at least 5); queue-length regression `wait = a + b (queue length + 1)`, fitted on January-August primary AGENT calls (a = 19.36 s, b = 25.09 s per queue position) and then frozen.
- Split by time: January-August training/exploration, September-October validation (60 days), November-December test (61 days, the headline). Nothing was chosen on validation or test months.
- Uncertainty: 95 % percentile intervals from a day-level cluster bootstrap (10,000 resamples, seed 20261113), shown in square brackets. Calls within a day are correlated, so days are the unit. Paired differences use the same resampled days for both methods.
- Two choices of the implementer that the protocol does not state, labelled here as **interpretations**: (1) the engine's `estimate()` is called once per distinct event time, and a service that starts at time T counts for predictions after T, not at T (keeps "strictly before t"); this has no numeric effect on the results. (2) The paired MAE difference is computed on the calls that both methods cover (n reported with each), because the protocol does not say how to treat different coverage.

### Data quirks and exclusions (from the validation JSON)

| Item | Count |
|---|---|
| Rows parsed (12 files) / unparseable | 444,448 / 0 |
| Days | 363 |
| PHANTOM rows excluded ("a virtual call to be ignored") | 3,599 |
| Rows kept | 440,849 |
| Rows with spaces instead of a tab (parsed by splitting on whitespace) | 12 |
| Rows with a padded `type` value | 6 |
| Calls crossing midnight | 702 |
| Queued calls (`q_start` set or `q_time` > 0) | 260,013 (AGENT 196,007; HANG 64,006) |
| `q_time` vs `q_exit - q_start`, `ser_time` vs `ser_exit - ser_start` | 0 mismatches in 260,013 and 353,700 rows |
| Queued AGENT calls with no `ser_start` | 327 |
| HANG calls that carry service times | 1,113 |
| Served queued AGENT calls whose `ser_start` is after `q_exit` | 16 (of 195,680) |
| Type `AA` (not in the documentation; all HANG) | 5 |
| Primary queue: queued calls / AGENT / HANG | 249,590 / 189,640 / 59,950 |
| Primary queue: calls still queued at or after 24:00 of their date, not carried to the next day | 51 (4 more start after 24:00 of their date) |

### Results: test months (headline), primary queue, protocol v1

| Method | Coverage, % | MAE, s | RMSE, s | Median abs. error, s | Bias, s | Within 30 s, % | Within 60 s, % |
|---|---|---|---|---|---|---|---|
| Prediction formula | 87.0 [85.5, 88.3] | 96.8 [91.4, 102.8] | 156.4 [145.6, 167.8] | 57.7 [55.0, 61.0] | +39.6 [+36.2, +43.3] | 30.3 [28.9, 31.7] | 51.5 [49.7, 53.3] |
| Last to enter service (LES) | 99.8 [99.7, 99.8] | 77.2 [72.5, 82.0] | 114.9 [107.2, 122.9] | 50.0 [46.0, 53.0] | -14.0 [-15.6, -12.6] | 35.5 [33.6, 37.4] | 56.5 [54.1, 58.8] |
| Recent mean (last 20) | 99.1 [98.9, 99.2] | 73.2 [67.9, 78.6] | 105.0 [96.7, 113.4] | 51.6 [48.2, 55.2] | -4.4 [-5.0, -3.8] | 30.7 [28.7, 32.7] | 56.5 [53.5, 59.4] |
| Queue-length regression | 100.0 [100.0, 100.0] | 61.2 [58.0, 64.6] | 89.1 [82.2, 96.8] | 42.7 [41.4, 44.9] | +0.4 [-5.4, +5.7] | 33.5 [32.4, 34.6] | 63.9 [62.1, 65.7] |

Calls: 36,479 answered (AGENT) calls over 61 days; the formula had a prediction for 31,754. Range coverage of the formula (actual wait inside its low-high range): 22.0 [20.8, 23.2] %.

Paired MAE difference, formula minus baseline, on calls both cover (positive = formula worse): Last to enter service (LES) 17.0 [14.9, 19.2] s (n 31,754); Recent mean (last 20) 21.1 [19.0, 23.2] s (n 31,673); Queue-length regression 33.4 [30.4, 36.4] s (n 31,754).

HANG calls (left before service; in no error metric): 10,762; 9,839 had a prediction; 1,700 hung up after waiting longer than predicted.

### Results: validation months (September-October), primary queue, protocol v1

| Method | Coverage, % | MAE, s | RMSE, s | Median abs. error, s | Bias, s | Within 30 s, % | Within 60 s, % |
|---|---|---|---|---|---|---|---|
| Prediction formula | 76.3 [72.9, 79.1] | 85.0 [80.4, 89.6] | 136.7 [126.8, 147.2] | 50.0 [47.0, 52.6] | +35.7 [+33.1, +38.3] | 34.5 [33.0, 36.2] | 56.5 [54.8, 58.5] |
| Last to enter service (LES) | 99.6 [99.5, 99.7] | 61.0 [56.7, 64.8] | 92.2 [86.0, 97.8] | 38.0 [35.0, 41.0] | -9.9 [-11.5, -8.2] | 43.4 [41.2, 45.9] | 65.3 [63.0, 68.0] |
| Recent mean (last 20) | 98.3 [98.0, 98.6] | 54.6 [49.9, 58.7] | 79.0 [72.5, 84.6] | 37.9 [34.6, 41.0] | -2.3 [-2.8, -1.8] | 40.3 [37.4, 43.7] | 69.1 [65.7, 73.0] |
| Queue-length regression | 100.0 [100.0, 100.0] | 50.6 [48.0, 52.9] | 70.7 [66.2, 74.9] | 37.5 [36.4, 38.5] | +10.0 [+7.0, +13.0] | 37.9 [36.7, 39.4] | 71.4 [69.5, 73.6] |

Calls: 19,234 answered (AGENT) calls over 60 days; the formula had a prediction for 14,677. Range coverage of the formula (actual wait inside its low-high range): 17.5 [15.7, 19.1] %.

Paired MAE difference, formula minus baseline, on calls both cover (positive = formula worse): Last to enter service (LES) 20.2 [17.7, 22.7] s (n 14,677); Recent mean (last 20) 27.4 [24.3, 30.3] s (n 14,633); Queue-length regression 31.2 [28.4, 34.2] s (n 14,677).

HANG calls (left before service; in no error metric): 4,040; 3,270 had a prediction; 843 hung up after waiting longer than predicted.

The validation months show the same order: the formula is the worst of the four, and bias is positive (+35.7 s).

### Sensitivity queues (protocol v1)

MAE with 95 % intervals. The order (regression, then recent mean or LES, then the formula) is the same in every queue and split.

| Queue | Split | Days | Answered calls | Formula MAE, s | LES | Recent mean | Regression | Formula coverage, % | Formula bias, s |
|---|---|---|---|---|---|---|---|---|---|
| All types (incl. IN, AA) | validation | 60 | 20,760 | 88.7 [84.7, 92.8] | 68.1 [64.4, 71.5] | 59.1 [55.3, 62.5] | 55.5 [53.3, 57.4] | 76.6 [73.3, 79.5] | +35.8 [+33.0, +38.5] |
| All types (incl. IN, AA) | test | 61 | 38,666 | 103.0 [97.3, 109.2] | 84.3 [79.4, 89.3] | 77.0 [71.9, 82.3] | 66.9 [63.6, 70.3] | 87.6 [86.1, 88.9] | +41.8 [+38.0, +46.0] |
| PS + PE only | validation | 60 | 14,028 | 73.5 [69.7, 77.3] | 51.4 [47.7, 54.8] | 49.1 [44.4, 53.5] | 43.0 [40.7, 45.3] | 71.1 [67.7, 74.1] | +20.4 [+18.0, +23.0] |
| PS + PE only | test | 61 | 27,613 | 85.7 [80.6, 91.4] | 66.7 [62.0, 71.3] | 67.3 [62.0, 72.7] | 53.8 [49.6, 58.5] | 82.1 [80.2, 83.7] | +26.2 [+23.3, +29.4] |

### Exploration (January-August only; separate; not used to choose anything)

Service-rate window of the formula on the training months, primary queue, formula only. MAE is not comparable across windows without the coverage change: a short window gives predictions only when the queue is busy and fast.

| Window, s | Answered calls (Jan-Aug) | Covered | Coverage, % | MAE, s | Bias, s |
|---|---|---|---|---|---|
| 60 | 133,927 | 46,344 | 34.6 [33.5, 35.7] | 61.4 [59.0, 64.0] | -33.9 [-36.2, -31.6] |
| 120 | 133,927 | 86,097 | 64.3 [63.0, 65.6] | 74.1 [71.6, 76.8] | +4.2 [+2.6, +5.7] |
| 300 (default) | 133,927 | 116,768 | 87.2 [86.2, 88.1] | 97.1 [93.8, 100.6] | +38.5 [+36.4, +40.7] |
| 600 | 133,927 | 126,461 | 94.4 [93.9, 94.9] | 103.3 [100.2, 106.5] | +46.5 [+44.3, +48.7] |

### Post-hoc S1: "all service starts" (protocol v2 addendum; separate; post hoc)

**Why.** Review of the scoring code found that the version-1 service events counted only calls that queued. Agents also served calls that never queued: 25,255 of 61,673 AGENT calls of the primary types with a service start in November-December (41 %; stored as `unqueued_agent_share.test` in `outputs/metrics/anonymous_bank_wait_eval.json`: 25,255 unqueued of 61,673, share 0.4095). The v1 service rate therefore undercounts how fast the agents were working, and the formula over-predicts. The video engine counts every service start it sees at the service point, so S1 is closer to what the engine would see.

**Definition.** Identical to v1, except that service events are the `ser_start` of every AGENT call of the queue's types with a service start, queued or not, strictly before the prediction time. Queue length, ground truth, baselines, splits, metrics and bootstrap are unchanged.

**Status.** Both the v1 headline and S1 are correct executions of their definitions. S1 was chosen after the test months were seen, so it is a **post-hoc sensitivity** and **never replaces the v1 headline**. It is shown next to it.

| Method | Coverage, % | MAE, s | RMSE, s | Median abs. error, s | Bias, s | Within 30 s, % | Within 60 s, % |
|---|---|---|---|---|---|---|---|
| Prediction formula | 99.0 [98.7, 99.2] | 79.6 [73.9, 85.5] | 132.4 [120.9, 144.1] | 46.0 [43.0, 49.0] | +13.2 [+9.6, +16.8] | 36.4 [34.5, 38.3] | 59.2 [56.9, 61.4] |
| Last to enter service (LES) | 99.8 [99.7, 99.8] | 77.2 [72.5, 82.0] | 114.9 [107.2, 122.9] | 50.0 [46.0, 53.0] | -14.0 [-15.6, -12.6] | 35.5 [33.6, 37.4] | 56.5 [54.1, 58.8] |
| Recent mean (last 20) | 99.1 [98.9, 99.2] | 73.2 [67.9, 78.6] | 105.0 [96.7, 113.4] | 51.6 [48.2, 55.2] | -4.4 [-5.0, -3.8] | 30.7 [28.7, 32.7] | 56.5 [53.5, 59.4] |
| Queue-length regression | 100.0 [100.0, 100.0] | 61.2 [58.0, 64.6] | 89.1 [82.2, 96.8] | 42.7 [41.4, 44.9] | +0.4 [-5.4, +5.7] | 33.5 [32.4, 34.6] | 63.9 [62.1, 65.7] |

Calls: 36,479 answered (AGENT) calls over 61 days; the formula had a prediction for 36,106. Range coverage of the formula (actual wait inside its low-high range): 21.5 [20.3, 22.7] %.

Paired MAE difference, formula minus baseline, on calls both cover (positive = formula worse): Last to enter service (LES) 2.7 [0.9, 4.6] s (n 36,057); Recent mean (last 20) 6.8 [5.2, 8.6] s (n 35,838); Queue-length regression 18.5 [15.6, 21.5] s (n 36,106).

HANG calls (left before service; in no error metric): 10,762; 10,545 had a prediction; 2,268 hung up after waiting longer than predicted.

S1 validation months (post hoc):

| Method | Coverage, % | MAE, s | RMSE, s | Median abs. error, s | Bias, s | Within 30 s, % | Within 60 s, % |
|---|---|---|---|---|---|---|---|
| Prediction formula | 99.1 [98.8, 99.3] | 61.0 [55.9, 66.1] | 101.7 [89.8, 114.4] | 36.0 [33.7, 38.0] | +0.4 [-3.0, +3.8] | 43.8 [41.5, 46.3] | 68.2 [65.9, 70.9] |
| Last to enter service (LES) | 99.6 [99.5, 99.7] | 61.0 [56.7, 64.8] | 92.2 [86.0, 97.8] | 38.0 [35.0, 41.0] | -9.9 [-11.5, -8.2] | 43.4 [41.2, 45.9] | 65.3 [63.0, 68.0] |
| Recent mean (last 20) | 98.3 [98.0, 98.6] | 54.6 [49.9, 58.7] | 79.0 [72.5, 84.6] | 37.9 [34.6, 41.0] | -2.3 [-2.8, -1.8] | 40.3 [37.4, 43.7] | 69.1 [65.7, 73.0] |
| Queue-length regression | 100.0 [100.0, 100.0] | 50.6 [48.0, 52.9] | 70.7 [66.2, 74.9] | 37.5 [36.4, 38.5] | +10.0 [+7.0, +13.0] | 37.9 [36.7, 39.4] | 71.4 [69.5, 73.6] |

Calls: 19,234 answered (AGENT) calls over 60 days; the formula had a prediction for 19,058. Range coverage of the formula (actual wait inside its low-high range): 17.3 [15.9, 18.6] %.

Paired MAE difference, formula minus baseline, on calls both cover (positive = formula worse): Last to enter service (LES) 0.3 [-1.4, 2.3] s (n 19,002); Recent mean (last 20) 6.8 [4.8, 9.2] s (n 18,778); Queue-length regression 10.5 [7.4, 13.9] s (n 19,058).

HANG calls (left before service; in no error metric): 4,040; 3,959 had a prediction; 1,418 hung up after waiting longer than predicted.

Reading: counting all service starts removes most of the bias (+39.6 s to +13.2 s on test) and raises coverage to 99 %, but on the test months the formula is still worse than all three baselines (paired difference to LES 2.7 s [0.9, 4.6]); on validation it is level with LES (0.3 s [-1.4, 2.3]) and still worse than the other two. Range coverage stays low (21.5 % on test).

### Why the formula loses, and what limits the explanation

- v1 service events cover only queued calls (see S1); about 41 % of the served calls are invisible to the rate.
- The error depends strongly on queue length (v1, test, formula MAE and bias by queue length at the call's start): 0 waiting 46.5 s and -46.5 s (every error is an under-prediction), 1-2 waiting 65.9 s and +12.1 s, 3-5 waiting 111.4 s and +62.2 s, 6-10 waiting 174.4 s and +135.2 s, more than 10 waiting 346.1 s and +316.1 s. The regression on the same bands has MAE 34.7, 54.5, 73.6, 94.3 and 120.8 s. Full breakdowns (hour, queue-length band, priority, type, all methods) are in the JSON under `queues.primary.{validation,test}.breakdowns`.
- Priority customers jump the queue (priority credit of 1.5 minutes), so a call's wait is not set by the queue length at its start alone.
- **Abandonment limit:** a HANG call drains the queue but is not a service. The formula divides by the rate of services only, so when many callers hang up the queue shrinks without any service being counted and the formula over-predicts. This cannot be fixed by counting more services, which is why S1 still has +13.2 s bias. On test, 1,700 of 9,839 HANG calls with a prediction hung up after waiting longer than predicted (2,268 of 10,545 under S1).
- The causes above are the reviewer's finding and the checks listed; they were not tested one by one on the test months, and no variant was tuned on them.

### Little's law check (primary queue, all months, 360 days with queued primary calls)

For each hour of the day: time-average queue length divided by (arrival rate x mean wait of all queued calls). The ratio is 0.965 to 1.024 in every hour from 07:00 to 23:59 and 0.84 to 1.10 in hours 0 to 6 (hour 2: 0.84, hour 3: 1.10; those hours have very few calls). The logs are internally consistent: the queue lengths reconstructed from the log agree with the recorded waits.

### Figures (gitignored, local only)

`outputs/figures/anonymous_bank/`: `headline_mae_test.png`, `predicted_vs_actual_test.png`, `error_histogram_test.png`, `mae_by_hour_test.png`, `mae_by_queue_length_test.png`, `littles_law.png`, `exploration_windows_train.png`, `post_hoc_s1_mae_test.png`.

### Limits

- A phone queue, not people standing in line; priority customers get 1.5 minutes of credit, so service is not first come, first served; one call centre in 1999 with 8 regular agents.
- The documentation does not say whether call types share one queue; the primary queue assumes they share the regular agents.
- The formula assumes a steady service rate over the last 5 minutes. The event-driven replay calls `estimate()` at event times, not once per video frame, so the formula's low-high range (the min-max of recent rates) may differ from what the video engine would give; the range coverage here is a property of this replay.
- The queue lengths in the logs are exact; the video engine's are noisy counts. The logs say nothing about how good camera counts are.
- Intervals reflect day-to-day variation within one year at one centre, not variation between centres.
- S1 is post hoc; the 41 % figure is the November-December share, stored in the eval JSON as `unqueued_agent_share` (test 25,255 of 61,673; train 89,549 of 223,244 = 40 %; validation 33,537 of 52,755 = 64 %).

### What this supports, and what it does not

Supports: on this phone queue, the formula (queue length divided by the last five minutes' service rate) has a positive bias of about 40 s, is less accurate than a one-line baseline, and is not rescued by counting all service starts (post hoc, S1); a regression of wait on queue length does better on the same inputs.

Does not support: any claim about QueueVision's waiting-time accuracy on cameras (the cameras were not involved); any claim that the formula would work or fail at a bank branch, a shop or an airport; any claim about the video counts. Whether a different rate estimate would do better on camera data was not tested.

## Sprint 8: browser checklist for a person (PENDING, not run)

Status: **pending**. No real Chrome, real Safari or real phone check has been done by Claude. The only
browser runs so far are the automated Playwright e2e tests (Task 3, 2026-11-13: 12/12 passed, Chromium 6
and WebKit 6; WebKit is Playwright's engine, not Safari itself). The screenshots from Sprint 7 predate the
Sprint 7 fixes and the Sprint 8 dashboard changes, so they must be re-taken.

Setup: from the repository root run `.venv/bin/python -m api` (port 8000, binds 127.0.0.1 only), build the
dashboard (`cd dashboard && npm run build`), open `http://127.0.0.1:8000`. Use a clip with people (for example
`cad_seq25.mp4`) so there is something to see. Save screenshots to `outputs/s8_screens/` (gitignored), named
`<browser>_<page>.png`, for example `safari_live.png`.

Phone column: by default, check the phone layout with a device emulator on the same machine (desktop Chrome
device toolbar or Safari responsive design mode, 390 x 844, touch on). A real phone needs an explicit,
temporary team decision, because the API has no login: anyone on the same network could upload or delete
runs. If the team decides to do it, use a trusted network only, run
`QUEUEVISION_ALLOWED_ORIGINS=http://<LAN-IP>:8000 .venv/bin/python -m api --host <LAN-IP>` (see
`docs/api.md`), open `http://<LAN-IP>:8000` on the phone, and revert to the default 127.0.0.1 binding when done.

Fill the Result columns with PASS, FAIL (say what you saw) or N/A. Leave blank what was not checked.

| # | Page | What to look at | Chrome | Safari | Phone | Notes |
|---|---|---|---|---|---|---|
| 1 | Overview (`/`) | Loads without console errors. After load the line under "Overview" shows the full date (for example "Friday 13 November 2026") and "N runs processing" (or "1 run processing"); no flash, no hydration warning. The nav links are at least 44 px tall (top bar on desktop, bottom bar on phone) | | | | |
| 2 | Overview, lists | "Latest completed runs" (up to 3) and "Recent recorded runs" (up to 10) link to the run; a processing run shows a card whose name links to the Live page, and a "Watch live" link next to it in the "Recent recorded runs" table; the accuracy summary card is present, or says the accuracy data is not generated | | | | |
| 3 | Runs (`/runs`) | Upload form: Video file, Camera config, Every Nth frame, Blur heads, Process in real time, Start. The "All runs" table has columns Name, Camera, Status, Created, Frames processed (newest first; there is no sorting). The Created column is a clock time with the browser's zone (shows "NPT" only when the browser zone is Asia/Kathmandu, which means Nepal Time). Status chips are readable; no horizontal scroll on phone | | | | |
| 4 | Live (`/live?id=N`), real-time run | Upload with "Process in real time" ticked: the page opens on `/live`. The preview shows video with **heads blurred**; Queue length, Predicted wait, Service rate and People in frame update; the queue chart and Events list grow; "Last update HH:MM:SS" appears. **Cancel run** stops the run and the chip changes to Cancelled; "Open the recorded run" then appears | | | | |
| 5 | Live, congestion banner | When congestion is active a "Congestion alert" banner appears with its reasons and the chip reads "Live · congested". The note "Acknowledging records the time for this alert." is visible without hovering, and the **Acknowledge** button saves the acknowledgement and hides the banner; after a reload the banner stays hidden for that alert, and the Alerts tab of the run shows "Acknowledged HH:MM:SS NPT". (This note is on the Live page only.) | | | | |
| 6 | Live, offline | Stop the API while the Live page is open: the chip changes to **Offline**, a message "Connection lost. Reconnecting; values below are not current." appears, and the figures are dimmed with a "Last update" line. When the API returns the chip goes back to Live and the message goes away. No blank page | | | | |
| 7 | Recorded run (`/run?id=N`) | Pick a finished run. Header shows the status chip, "every Nth frame · heads blurred · fast or real time" and the camera; summary cards; the queue chart. The video is the browser's plain video control: press play and drag the scrubber to seek (there is no click-to-seek from the chart or lists). Heads are blurred. A run made with Blur heads off shows a note instead of the video. "Report (JSON)" and "Time series (CSV)" links work; Delete asks "Delete run N?" and Keep cancels it | | | | |
| 8 | Recorded run, tabs | Three tabs: **Waits**, **Events**, **Alerts**. They switch by click and by Left/Right arrows, Home and End, with focus and selection moving together. Waits lists Person, Joined, Service, Wait, Abandoned; Alerts lists Time, Event, Queue length, Reasons. Congestion rows read "Congestion started" and "Congestion ended" | | | | |
| 9 | Accuracy (`/accuracy`) | Headline cards (mean absolute error, bias, within one person) show numbers with their frame counts. A section whose source file is missing reads "Not available — <reason> (<path>)", never 0. The Waiting time card reads "Measured" with "N recorded clips", or "Not measured yet". The split buttons (All, Validation, Test) change the count-error charts; "Limits of these numbers" is listed | | | | |
| 10 | `/legacy` | The old test page loads; `/legacy?x=1` keeps the query string; `/legacy` redirects to `/legacy/` | | | | |
| 11 | Phone / emulator only | No horizontal scroll on any page in portrait (Overview, Runs, a run, Live, Accuracy); buttons and tabs are tappable; the Acknowledge button is easy to hit | | | | |
| 12 | Any | Keyboard: Tab order is logical and the focus ring is visible; the Acknowledge note is read together with the button | | | | |

Tester name, date, browser and OS versions, device: ______________________________

Anything that fails: write what you did, what you expected and what happened, and attach the screenshot.

## Fix pass, session 1 (2026-11-13): automated results

Branch `fix/structure`. Baselines are `main` at `b5bb0f9`; final numbers were run at the end of the session
(Python 3.11 `.venv`, Node 22 via nvm). The browser checklist above is still pending for a person.

| Command | Baseline | Final |
|---|---|---|
| `.venv/bin/pytest -q` | 454 passed | 469 passed |
| `.venv/bin/pytest -q -m "not integration"` | 443 passed, 11 deselected | 458 passed, 11 deselected |
| `ruff check .` / `ruff format --check .` | clean | clean (135 files formatted) |
| `npm run lint` / `typecheck` / `build` | clean | clean |
| `npm test` (Vitest) | 125 passed | 133 passed |
| `npm run e2e` | 12/12 | 14/14 (Chromium 7, WebKit 7), run by Task 4, not re-run for this table |

New tests: the golden test of the per-frame step (`tests/test_frame_processor_golden.py`), the migration and
concurrent-start tests (`tests/test_api_db.py`), the acknowledge route and route-order tests
(`tests/test_api.py`), Vitest for the Acknowledge button, and one Playwright test that acknowledges a real
alert on `cad_seq27`, reloads and finds it in the Alerts tab. Details: [fix_structure_report.md](fix_structure_report.md).

## Sprint 8: real-time pacing gap (2026-11-13)

Question: at stride 3 the Sprint 7 real-time run spent 12.6 s outside the pacing waits against 9.2 s for
the whole fast run. Where does the extra non-wait time go? Method: `scripts/measure_realtime.py --stages`
(timing wrappers applied from the script; no `ai_engine` or `api` code changed) on `cad_seq25.mp4`
(617 frames, 20.567 s, MPS, yolo11n), one throwaway fast run first so the model load is in no measured
run, then fast and real-time jobs alternating, 3 repeats per stride, one job at a time. Raw data:
`outputs/s8_pacing/` (gitignored).

| Stride, mode | Wall s (min / median / max) | Waited s | Non-wait s (min / median / max) |
|---|---|---|---|
| 1, fast | 25.914 / 26.062 / 26.704 | 0 | 25.914 / 26.062 / 26.704 |
| 1, real time | 26.172 / 26.183 / 26.256 | 0 (never waited) | 26.172 / 26.183 / 26.256 |
| 3, fast | 8.927 / 9.079 / 9.279 | 0 | 8.927 / 9.079 / 9.279 |
| 3, real time | 21.200 / 21.381 / 21.457 | 8.459 / 8.534 / 8.690 (205 waits) | 12.691 / 12.741 / 12.923 |

The gap reproduces: stride 3 real-time non-wait is about 3.7 s more than the fast run's total. Stage
totals, stride 3, repeat 1 (fast -> real time, seconds): tracker update 6.925 -> 9.385; head blur 0.414 ->
0.669; video write 0.591 -> 0.869; preview offer 0.131 -> 0.365 (renders 39 -> 79); metrics panel, zone,
people and service-point drawing, decode and queue analyzer together 0.46 -> 0.79. DB writes (0.005 s),
publish (0.000 s) and the on_frame work other than the wait and preview are negligible. Per-frame
detector+tracker update: median 32.9 ms (p90 35.7) fast against 45.7 ms (p90 51.6) in real time; the
204 updates that follow a wait are the slow ones, the 3 that do not (the first frames) match the fast
run at about 33 ms.

Run order reversed (real time first, then fast; stride 3, 3 repeats, same throwaway warm-up, raw data in
`outputs/s8_pacing/stages_rtfirst.json`):

| Stride 3, order real time first | Wall s (min / median / max) | Waited s | Non-wait s | Update ms median |
|---|---|---|---|---|
| real time | 21.361 / 21.625 / 21.731 | 7.889 / 8.808 / 8.894 | 12.468 / 12.923 / 13.737 | 44.2 to 45.6 |
| fast | 8.968 / 9.019 / 9.169 | 0 | 8.968 / 9.019 / 9.169 | 32.5 to 33.0 |

The gap holds with the order reversed (real-time non-wait about 3.9 s above the fast run), so it is not an
artefact of the fast run going first.

Cause (inferred, not measured): every stage, CPU-bound OpenCV drawing as well as MPS inference, runs
about 1.3 to 1.7 times slower once the worker sleeps about 40 ms between frames. Inference alone accounts
for roughly 2.5 s of the 3.7 s. The preview contributes about 0.2 s: its 5-per-second limit is by wall
clock, so a run that lasts twice as long encodes about twice as many previews (79 against 39). The
evidence is that a busy-wait removes the gap: the same real-time runs with the pacing wait replaced by a
busy-wait (diagnostic `--spin-wait`, not product behaviour) give non-wait 9.584 / 9.599 / 10.172 s (min / median / max) and a
per-frame update median of 34.1 to 34.4 ms, essentially the fast run's 32.4 to 32.7 ms (the fast run in
that batch has one slow outlier, 12.560 s). This is consistent with the machine slowing down while idle
between frames (for example a CPU/GPU power state); power, frequency and thermal counters were not
measured. The busy-wait also changes the CPU duty cycle, so it shows the slowdown depends on idling, not
which mechanism causes it. DB flush, publishing and the pacing logic do not explain it. No code defect
was found and nothing was changed. Effect on users: none beyond slightly slower inference; pacing
already skips a wait when the run is late, and real-time wall time stays within about 0.6 to 1.2 s of
the 20.567 s clip at stride 3. Stride 1 never waits because it processes at about 23.7 fps, below the clip's 30.

## Sprint 7, session 2 (2026-11-12): dashboard, preview, real-time option

Full write-up: [sprint7_session2_report.md](sprint7_session2_report.md). Machine, Python and
Node: MacBook Air M1, Python 3.11 `.venv`, Node v22.23.2, npm 10.9.8. Everything below was
run; items not run are marked pending.

### Automated tests: baselines vs final

| Command | Baseline (before any change, 2026-11-12) | Final (end of session 2) |
|---|---|---|
| `.venv/bin/pytest -q` | 403 passed | 440 passed (37 new Python tests; 35 in session 2 plus 2 in the final fix wave), 34.76 s |
| `.venv/bin/pytest -m "not integration" -q` | 392 passed, 11 deselected | 429 passed, 11 deselected, 21.67 s |
| `.venv/bin/ruff check .` and `ruff format --check .` | clean | clean |
| `cd dashboard && npm run lint` / `npm run typecheck` | n/a (no dashboard) | both clean |
| `npm test` (Vitest, jsdom, Testing Library) | n/a | 15 files, 114 tests passed |
| `npm run build` (static export) | n/a | succeeded; 7 static pages generated |

The existing tests pass unchanged except one deliberate change: the Sprint 4 test of the
served test page now requests `/legacy/` (the page moved; spec A5). New Python tests cover
real-time pacing (identical outputs with `realtime` on and off, no lag build-up, wait
interrupted by cancel; the cancel test was mutation-checked against `time.sleep`),
`FrameUpdate.image` / `people` and CLI output identity, the preview store and routes
(404 rules, MJPEG boundary format, blur of the head region even with `blur_heads` off, MJPEG
ending on shutdown), the accuracy export on small synthetic files, the Origin check, the
dashboard catch-all (including traversal and symlink escape) and `timeseries.csv`.
Vitest covers `WaitCard` (value and range, Unknown, Insufficient data), `StaleBanner`,
`useLiveSocket` (reconnect backoff and snapshot with a fake WebSocket), the API client,
formatting (including "NPT"), the Live, Run and Accuracy views and the accuracy card.
Not unit-tested: `QueueChart` geometry, `PreviewPanel` layout, `UploadForm`, `RunsTable`,
`WaitsTable` (checked in the browser instead). The Overview page has only a pre-load test (`app/page.test.tsx`).

CI: a `dashboard` job (Node 22: `npm ci`, generated-types staleness check, lint,
typecheck, Vitest, build) and an OpenAPI staleness step in `lint-and-unit` were added to
`.github/workflows/ci.yml`. They have **not been run on GitHub yet** (pending: the branch
is not pushed).

### Final fix wave (after the whole-branch review)

Fixed, each test-first where a test is named: `python -m api --port/--host` now reach the app settings (allowed origins); a blur-off run's video is not shown in the dashboard (controller ruling; upload option and API option kept); the Overview no longer flashes the build date or "0 runs processing"; `PreviewPanel` makes no request for a finished run; a preview encode error no longer fails a run; `pydantic==2.14.2` pinned in CI and `requirements.txt`; `ProcessingRunCard` shows a neutral chip during the first connect. Counts above include these.

### End-to-end (Playwright, real API, real model)

`cd dashboard && npm run build && npm run e2e`: **10 passed in 2.2 min** when measured at 3b3914c (Task 14b), and **10 passed in 1.9 min** re-run at the final head after the final fix wave,
Chromium 5 and WebKit 5, one run, no retries. Playwright 1.64.1; its bundled Chromium is
156.0.8078.4 (`browsers.json`). The API ran on port 8765 with a temporary data directory,
the real YOLO11n on `cad_seq25`. The five tests per browser:

1. Upload with real time on (`max_frames=150`) goes to the Live page, which updates; the run completes; the annotated video plays and seeks.
2. The upload form in the UI (file, camera, real time ticked, Start) opens `/live?id=`.
3. API crash (SIGKILL) while the Live page is open: the page shows Offline and "Last update", the API restarts, the page recovers without reload and shows Failed (a crash marks the run `failed`).
4. Graceful API stop (SIGTERM): Offline, restart, the page shows Cancelled without reload.
5. Phone viewport 390 x 844 on `/`, `/runs`, `/run`, `/live`, `/accuracy`: no horizontal scroll, controls reachable.

Finding: the first run of test 3 expected Failed after a graceful stop; the worker ends the
job `cancelled` then, so the assertion was split into two tests instead of weakened. The
e2e tests skip themselves when the clip, model or venv are missing, so CI does not run them.
The `/accuracy` phone check initially passed only because that e2e server had no
`accuracy.json`; the e2e server now writes a synthetic one (made-up values, used only for the
layout check).

### Browser check (controller, 2026-11-12)

Playwright Chromium and WebKit, desktop 1440 x 1000 and phone 390 x 844, time zone
Asia/Kathmandu, against the real API with real data (completed runs 1 and 2 of `cad_seq25`).
Screenshots are in `outputs/s7_screens/` (gitignored, derived from CAD, never committed; listed
in the session report). They were viewed. What was found and fixed (Task 14b):

| Finding | Cause | Status |
|---|---|---|
| `/accuracy` scrolled horizontally on a phone (scrollWidth 483) | Two visually hidden chart data tables (`sr-only`): a table ignores the 1 px width, so it stayed content-wide | Fixed; synthetic fully populated probe measured 718 before and 390 after in both engines |
| Times showed "GMT+5:45" instead of "NPT" | The zone name was mapped only when a zone was passed explicitly | Fixed, unit-tested |
| Live event feed not in time order | Events were shown in arrival order | Fixed: sorted by `timestamp_s`, ties newest arrival first |
| Live page opened after a run finished showed "No events yet" | Events came only from the socket | Fixed: history is loaded from `/api/videos/{id}/events` and merged |

Everything else checked looked right: Overview, Recorded runs and a run page (desktop and
phone, both engines); heads blurred in the annotated video; "5 of 11 joins"; "Unvalidated" tags; chart
band and markers; Live page (both engines, desktop and phone) with blurred preview, legend,
"Raw count N" caption, "Insufficient data", "Not the queue length", dashed alert line and the final
state with the recorded-run link. The screenshots were taken before the four fixes and were not re-taken; the fixes are verified by unit tests (each was written to fail first) and the final e2e run.

**Not done (pending, needs a person):** real Chrome (the Claude in Chrome extension was not
connected) and real Safari. WebKit through Playwright passed, which is not the same
as Safari. No real phone was used.

### Measurement: real-time run of `cad_seq25` (`scripts/measure_realtime.py`)

Source: `outputs/s7_realtime/summary.json` (gitignored). `cad_seq25.mp4`: 617 frames, 30 fps,
20.567 s, device `mps`, camera `cad_seq25`. Each pair is a normal run and a `realtime` run with
the same frame stride.

| | Stride 1 | Stride 3 |
|---|---|---|
| Frames processed (normal / real time) | 617 / 617 | 206 / 206 |
| Normal run wall time | 30.346 s (includes the model load) | 9.163 s |
| Real-time run wall time | 26.309 s | 21.392 s |
| Real-time wall minus clip duration | +5.742 s | +0.825 s |
| Pacing waits / total waited | 0 / 0.000 s | 205 / 8.785 s |
| Real-time wall minus time waited | 26.309 s | 12.607 s |
| Processing fps in the reports (normal / real time) | 25.67 / 25.45 | 25.22 / 18.11 |
| Pacing exercised | **no** | yes |
| Waits CSV, time series, events byte-identical to the normal run | yes (363, 1175, 10422 bytes) | yes (326, 1173, 7723 bytes) |

Reading these honestly:

* **Stride 1 never paced.** The detector processes about 25.5 frames a second, slower than the
  clip's 30, so the pacer never had to wait. The +5.742 s is the processing shortfall, not a
  pacing cost. This pair proves only that outputs are identical with the option on.
* **Stride 3 did pace** (205 waits). The real-time run finished 0.825 s after the clip's
  length. But its non-wait time (12.607 s) is 3.444 s longer than the whole normal run (9.163 s),
  and the wait counter does not explain that gap. The cause was **not measured** (possible
  suspects, unverified: per-frame callback and preview work, MPS variance). Figures vary from run to run
  (an earlier stride-3 run in the same session gave +0.769 s; the 9.316 s waited and
  9.645 s normal wall in that earlier run are superseded by the table).
* One run per pair; no repeats, so no variance estimate. Machine load from other macOS daemons affected an
  earlier e2e attempt (load average 40 to 120), so wall times are indicative.
* Real-time mode can only pace when the machine processes faster than the clip plays; at stride 1 on this M1 it does not (25.5 fps against 30). Stride 2 was not measured.

### Not run / pending

* Real Chrome and real Safari checks (see above).
* The `dashboard` CI job on GitHub.
* Waiting-time accuracy: still not measured; session 3 (the sheets are not filled). The dashboard shows "Not measured yet".

## Sprint 6 (2026-11-09)

Full write-up: [sprint6_report.md](sprint6_report.md).

| Item | Result |
|---|---|
| `pytest -q` | 403 passed (370 at end of Sprint 5); `ruff check .` / `ruff format --check .` clean |
| Error attribution (legacy, from 5 s, IoU 0.5) | reproduces raw bias +0.892 / +0.607 / +1.014 (all / val / test) exactly; per frame +2.10 counted-unmatched, +1.16 grace-period ghosts, −1.65 undetected, −0.77 still in dwell |
| imgsz 960 vs 640, smoothed MAE from 5 s | val 0.910 vs 1.176; test 1.723 vs 1.619 → not adopted |
| Grace 0.75 s + dwell 2.0 s (chosen on val) | val 0.984 vs 1.176; test 1.716 vs 1.619 → not adopted |
| Waiting-time accuracy on real video | still not measured: annotation kits generated, sheets not yet filled |

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
