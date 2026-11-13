# Anonymous Bank: evaluation protocol for the waiting-time predictor

Protocol version: **1** (written 13 Nov 2026, before any prediction was scored).

This protocol fixes, in advance, how QueueVision's waiting-time **predictor** (queue length ÷
service rate, `ai_engine/queue/waiting_time.py`) is scored against real waits in the
"Anonymous Bank" call-centre logs (Ilan Guedj and Avi Mandelbaum, Technion SEE Center; all calls
of 1999). It measures the prediction formula on a phone queue, **not** QueueVision's cameras.
Results are reported as "predictor accuracy on real call-centre queues", never as "QueueVision
waiting-time accuracy".

Nothing below may be changed after scoring starts. A change needs a new protocol version,
committed before the re-scoring, with the reason.

## Data facts this protocol relies on

From `outputs/metrics/anonymous_bank_validation.json` (parser in `ai_engine/data/anonymous_bank.py`):

- 444,448 rows in 12 monthly files; 363 days; 0 unparseable rows. 3,599 `PHANTOM` rows are
  excluded ("a virtual call to be ignored"). 12 rows use spaces instead of a tab and 6 have a
  padded `type` value; splitting on whitespace parses all of them.
- `q_time` equals `q_exit − q_start` in every queued row and `ser_time` equals
  `ser_exit − ser_start` in every served row (0 mismatches). **Decision: the timestamps are used;
  the duration fields agree with them.**
- For served queued calls, `ser_start` is usually 1 s **before** `q_exit` and ranges from 5,131 s
  before to 310 s after it. So `q_exit`, not `ser_start`, is the moment a caller's wait is known.
- 327 `AGENT` calls queued but have no `ser_start`; 1,113 `HANG` calls have service times.
- Type `AA` (5 calls, all `HANG`) is not in the documentation.
- **Queued** means `q_start` is not `0:00:00` **or** `q_time` > 0. The documentation's
  `0:00:00` sentinel misses one `AGENT` call that genuinely queued from exactly midnight
  (`q_time` 27 s); with this rule the queued count is 260,013 instead of the validation report's
  260,012. The parser is updated to this rule before scoring.

## Queue definitions

- **Primary queue:** queued calls (`q_start` set) of types **PS, PE, NE, NW, TT** with outcome
  `AGENT` or `HANG`. `IN` is excluded (internet consulting, 5 separate agents in another room).
  `AA` is excluded (undocumented).
- **Sensitivity (a), all types:** every queued `AGENT`/`HANG` call of every type, including IN and AA.
- **Sensitivity (b), PS+PE only.**
- The documentation does not say whether types share one queue; the primary definition assumes
  they share the 8 regular agents. This is a limit, reported as such.

## Time base

- Each call belongs to its `date` (the date in its record). Times are seconds since 00:00 of that
  date; an exit earlier than its start is the next day (+86,400 s), as the parser does.
- Each day is replayed on its own, from 0 s. Calls of the previous date that are still queued after
  midnight are not carried into the next date's replay (the centre is staffed until 24:00, so
  such calls are rare; their count is reported).

## Prediction moment and inputs (no look-ahead)

For each call *c* in the queue, the prediction is made at *t* = `q_start(c)`.

- **Order of events at equal times:** calls are ordered by (`q_start`, `vru_entry`, `call_key`).
- **Queue length** = number of *other* calls in the same queue with `q_start` before *t* (or equal
  to *t* and earlier in the order above) and `q_exit` after *t*. Exact counts from the log; no
  smoothing (the video engine median-smooths its noisy counts; the logs need none).
- **Service events** for the rate = `ser_start` of `AGENT` calls in the same queue with `ser_start`
  set and `ser_start` < *t* (strictly). `HANG` calls are never service events, even when they
  carry service times.

## The predictor (unchanged engine code)

- One `ServiceRateEstimator(ServiceRateSettings(), start_s=0.0)` per day per queue definition, with
  the engine defaults: method `window`, `window_seconds` 300, `min_services` 2,
  `min_history_seconds` 60, basis `service_start`.
- `add_service(ser_start)` for every service event in time order.
- `estimate(now)` is called at **every event time of that day's queue** (each `q_start`, `q_exit`,
  `ser_start`), in time order, including each prediction moment. The engine calls `estimate` once
  per processed video frame; the low–high range is the min–max of the "ok" rates seen in the last
  300 s, so it depends on how often `estimate` is called. Event-driven sampling is used here and
  stated as a difference from the video engine.
- `predict_wait(queue_length, rate)` at *t* gives predicted wait, low, high and status. A call
  gets a usable prediction only when status is `ok`.
- The adapter only calls these public functions; a test shows that the same events give the same
  output as `QueueAnalyzer`'s use of them.

## Ground truth and censoring

- **Actual wait** = `q_exit − q_start` for `AGENT` calls in the queue.
- `HANG` calls are **censored** (they left before service): excluded from every error metric.
  Reported: their number, and how many hung up after waiting longer than their predicted wait.

## Baselines (same inputs, no look-ahead)

1. **Last to enter service (LES):** the wait (`q_exit − q_start`) of the most recent `AGENT` call
   in the queue whose `q_exit` < *t*, on the same day. No such call → no prediction.
2. **Recent mean:** mean wait of the last 20 `AGENT` calls in the queue with `q_exit` < *t*, on the
   same day; at least 5 needed, else no prediction.
3. **Queue-length regression:** wait = *a* + *b*·(queue length + 1), ordinary least squares fitted
   on the training months' `AGENT` calls of the primary queue; *a* and *b* are reported and then
   frozen.

## Split (by time, no shuffling)

- **Training / exploration:** January–August.
- **Validation:** September–October.
- **Test (headline):** November–December.
- Nothing is chosen on validation or test months. Exploration (service-rate windows 60, 120 and
  600 s) runs on training months only and is reported separately as exploration.

## Metrics

For the predictor and each baseline, on validation and test (headline: test), primary queue:

- **Coverage:** share of the queue's `AGENT` calls with a usable prediction.
- On covered `AGENT` calls: MAE, RMSE, median absolute error, bias (predicted − actual), in
  seconds; share within ±30 s and ±60 s.
- **Range coverage** (predictor only): share of actual waits inside [low, high].
- Breakdowns: hour of day of *t*; queue-length band 0, 1–2, 3–5, 6–10, >10; priority (regular 0/1
  versus 2); type.
- **Little's law check** per hour of day over all months: time-average queue length versus
  (queued arrivals per second) × (mean `q_exit − q_start` of all queued calls, `AGENT` and `HANG`).
- Sensitivity queues (a) and (b): the same headline metrics, validation and test.

## Uncertainty

- **Day-level cluster bootstrap:** resample days with replacement within the split, 10,000
  resamples, seed **20261113**; 95 % percentile intervals for every reported metric. Calls within a
  day are correlated, so days are the unit.
- Paired comparison predictor versus each baseline: the same resampled days for both, interval
  of the difference in MAE.

## What is reported

`outputs/metrics/anonymous_bank_wait_eval.json` (gitignored) holds every number, this protocol's
version, the input file hashes and the seed. The project shows the test-month headline with
intervals, coverage and the baselines, with the caveat "Phone queue, 1999, Israel; measures the
prediction formula, not the cameras."

## Limits stated in advance

A phone queue, not people standing in line; priority customers get 1.5 minutes of credit, so the
queue is not first come, first served; one call centre in 1999 with 8 regular agents; the formula
assumes a steady service rate over the last 5 minutes; whether call types share one queue is not
documented.
