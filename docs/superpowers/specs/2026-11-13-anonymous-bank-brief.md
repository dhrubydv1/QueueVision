# Score the waiting-time predictor on real queue logs (Anonymous Bank) — brief, recorded as the spec

Recorded from the team's prompt (13 Nov 2026). Branch `data/anonymous-bank` from `main` (`b5bb0f9`). Fix-pass session 2 has not landed, so a minimal day-level bootstrap is added here (and said so).

## Goal
Test the waiting-time predictor on its own (queue length ÷ service rate, `ai_engine/queue/waiting_time.py`) against real waits from the "Anonymous Bank" call-centre logs (Israeli bank call centre, all of 1999). Report it everywhere as "predictor accuracy on real call-centre queues", never "QueueVision waiting-time accuracy".

## Shared rules
1. Inspect first (waiting_time.py, camera_config.py service-rate settings `window_seconds` 300 / `min_services` 2, lifecycle/event classes, event_schema.md, wait_eval.py, evaluate_waiting.py, export_accuracy.py, datasets README/metadata, dataset_licences.md, testing_report.md). Record baselines.
2. Never fabricate results. If the download fails, stop and report; do not substitute other data.
3. Do not change the predictor or its defaults. Explore other settings only on training months, as exploration; a default changes only via an adoption rule, never on test months.
4. Data rights: "The data is free for use"; notify Prof. Avi Mandelbaum (avim@ie.technion.ac.il) and acknowledge the source. Redistribution not addressed and the repo is public: never commit raw or converted data; keep it gitignored. Do not send email; the team does that.
5. Torch-free unit tests; ruff clean; pinned deps; one PR into `main`; don't push to `main`.

## 1. Register and download
- Licensing register + `docs/dataset_licences.md` entry: "Anonymous Bank call-centre data", Ilan Guedj and Avi Mandelbaum (documentation 9 Feb 2000), source https://see-center.iem.technion.ac.il/databases/AnonymousBank/, quoted terms (`terms_verified=yes` only after reading the documentation), redistribution "not stated — do not redistribute", citation and notify-and-acknowledge request; a "team action: email Prof. Mandelbaum" line.
- `scripts/fetch_anonymous_bank.py`: documentation PDF + data into `datasets/raw/anonymous_bank/` (gitignored), from Technion if offered directly, else the University of Bern mirror; record source URL, size, SHA-256 per file in `datasets/metadata/anonymous_bank_files.csv` (committed, no data); re-runs skip files whose hash matches.
- If both copies exist, compare one month; if they differ, use Technion's and report the difference.

## 2. Parse and validate (`ai_engine/data/anonymous_bank.py`)
17 fields: `vru+line, call_id, customer_id, priority, type, date (YYMMDD), vru_entry, vru_exit, vru_time, q_start, q_exit, q_time, outcome (AGENT|HANG|PHANTOM), ser_start, ser_exit, ser_time, server`. hh:mm:ss times; `q_start`/`ser_start` `00:00:00` when never queued / never served. Delimiter undocumented — parse what is found; tests on a hand-made or synthesised fixture (a few rows only).
Quirks with counts: exclude PHANTOM; midnight crossing; `q_time` vs `q_exit−q_start` and `ser_time` vs `ser_exit−ser_start` mismatches (decide in writing before scoring which to use); drop `customer_id`; priority 2 gets 1.5 min credit (not FCFS); types PS, PE, IN (5 separate positions), NE, NW, TT; 8 agents; weekdays 07:00–24:00; closed Fri 14:00 to Sat ~20:00.
Per month a tidy table (gitignored): `call_key, date, type, priority, queued, q_start_ts, q_exit_ts, ser_start_ts, ser_exit_ts, outcome, server`, durations. Validation report `outputs/metrics/anonymous_bank_validation.json`: rows per month, exclusions by reason, mismatches, HANG share among queued, calls per type and priority, hours covered.

## 3. Protocol before scoring
`docs/anonymous_bank_protocol.md`, committed on its own before any scoring. Defaults: primary queue = queued calls of PS, PE, NE, NW, TT with outcome AGENT or HANG (IN excluded); sensitivity (a) all types, (b) PS+PE only. Prediction moment = each call's `q_start`. Inputs (no look-ahead): queue length = calls in the queue with `q_start ≤ t < q_exit`, excluding the arriving call; service events = `ser_start` of same-queue calls before `t`. Ground truth = `q_time` (or `q_exit − q_start`) for AGENT; HANG censored (excluded from error metrics; count reported; and how many hung up after waiting longer than their prediction). Split by time: Jan–Aug training/exploration, Sep–Oct validation, Nov–Dec test (headline). Day-level cluster bootstrap (10,000 resamples, fixed seed) for every metric.

## 4. Replay (`scripts/score_anonymous_bank.py`)
Per day, feed events in time order into the engine's own service-rate and prediction code through public interfaces (session seconds since 00:00 or since opening; document which): `queue_join` at `q_start`, `service_start` at `ser_start`, `queue_abandon` at `q_exit` for HANG, `service_complete` at `ser_exit`; engine default service-rate settings. Thinnest adapter possible, with a test proving it matches the engine on the same events. Record per call at `q_start`: queue length, service rate + status, predicted wait, low/high, prediction status. Baselines (same inputs, no look-ahead): LES (queue time of the most recent caller to start service); recent mean (mean `q_time` of the last 20 served); optional queue-length regression fitted on Jan–Aug. Optional exploration: windows 60/120/600 s, training months only.

## 5. Metrics
For the predictor and each baseline, validation and test (headline test): coverage; MAE, RMSE, median absolute error, bias (predicted − actual) in seconds with day-bootstrap 95% intervals; share within ±30 s and ±60 s; range coverage (actual inside low–high); breakdowns by hour, queue-length band (0, 1–2, 3–5, 6–10, >10), priority (regular vs 2), type; Little's law check per hour; PNG plots under `outputs/figures/anonymous_bank/` (gitignored): predicted vs actual hexbin, error by queue-length band, error by hour.

## 6. Into the project
`outputs/metrics/anonymous_bank_wait_eval.json` (every number, protocol version, file hashes, bootstrap seed). `scripts/export_accuracy.py` + `/api/accuracy`: section `predictor_on_call_centre_logs` (headline test metrics with intervals, coverage, baselines, n calls, n days, source, citation). Accuracy page card "Waiting-time predictor on real call-centre queues" with caveat "Phone queue, 1999, Israel; measures the prediction formula, not the cameras."; the camera "Waiting time" card unchanged. `docs/testing_report.md` (method, protocol link, quirks, exclusions, results, limits, what it does/doesn't support). README: fetch + score commands and acknowledgement "Call-centre data: 'Anonymous Bank' Call-Center Data, Ilan Guedj and Avi Mandelbaum, Technion SEE Center."

## 7. Tests
Synthetic fixtures only: parser on whitespace/tab layouts, midnight crossing, PHANTOM exclusion, mismatch detection, population filter, queue-length reconstruction (simultaneous events, ordering), no look-ahead (shuffling future events doesn't change a prediction), event mapping into the engine, LES and recent-mean, HANG censoring, day bootstrap with fixed seed, export section shape, Vitest for the new card.

## 8. Report
Built; files; deps; baselines vs final; validation counts; protocol decisions and their commit (before the scoring commit); results (test headline, validation, sensitivity, exploration separated); figures; limits; team actions (email Prof. Mandelbaum; card wording). Each section Completed / Partially / Not completed.
