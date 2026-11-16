# Anonymous Bank report: predictor accuracy on real call-centre queues

Branch `data/anonymous-bank` (from `main` `b5bb0f9`). Not pushed. Full numbers and method:
[testing_report.md](testing_report.md); pre-registered [protocol](anonymous_bank_protocol.md).

## Headline, stated plainly

The result is **unfavourable to the formula**. On the held-out test months (November-December 1999, 61 days,
36,479 answered calls, primary queue) the QueueVision prediction formula (queue length divided by service
rate) has a mean absolute error of **96.8 s** [91.4, 102.8], against 77.2 s for "last to enter service" (LES),
73.2 s for the recent mean and 61.2 s for a queue-length regression. It is biased high (+39.6 s), gives a
prediction for 87 % of calls, and its low-high range holds the actual wait for 22 % of calls. This measures
the formula on a phone queue; it says nothing about the cameras. Wording used everywhere: "predictor accuracy
on real call-centre queues", never "QueueVision waiting-time accuracy".

Cause found in review: the protocol-v1 service events counted only calls that queued, but 41 % of the AGENT
calls of these types (25,255 of 61,673 in November-December) were served without queueing, so the service rate
was undercounted. A post-hoc sensitivity, **S1** ("all service starts", protocol v2 addendum `aa112de`), gives
MAE 79.6 s [73.9, 85.5], bias +13.2 s, coverage 99 %: better, but still worse than every baseline on test. A
second limit stays: an abandoning (HANG) caller drains the queue but is not a service, so the formula
over-predicts when many hang up. Both the v1 headline and S1 are correct executions of their definitions; S1
was chosen after the test months were seen and **never replaces the headline**.

Two choices by the implementer that the protocol does not state are labelled **interpretations**: one
`estimate()` per distinct event time (no numeric effect), and paired MAE differences on the calls both methods
cover.

## Sections

| Section | Status | Notes |
|---|---|---|
| Data acquisition (fetch script, hashes, licence register) | Completed | Technion RAR and PDF, SHA-256 pinned; Bern mirror hashes pinned; commits `51d967d`, `fdd0391`, `bdbd60f`, `0941d14`. Data not committed. |
| Parser and validation | Completed | 444,448 rows, 0 unparseable, 3,599 PHANTOM excluded, 12 space-for-tab rows and 6 padded types handled; 0 duration mismatches; queued rule fixed in the protocol before scoring (`1ba3c44`). |
| Protocol v1 (before scoring) | Completed | `f940045`, committed before any prediction was scored. |
| Replay adapter, baselines, bootstrap, metrics, scoring script | Completed | Engine code unchanged; day-level cluster bootstrap, 10,000 resamples, seed 20261113. |
| Headline result (test months) | Completed | Formula loses to all three baselines (above). |
| Validation, sensitivity queues, exploration | Completed | Same order in every queue and split; exploration on January-August only. |
| Little's law check | Completed | Ratio 0.965 to 1.024 in hours 07-23; logs are internally consistent. |
| Post-hoc S1 | Completed (post hoc) | Protocol v2 addendum `aa112de`; scored under its own key; reported separately. |
| Export, API, dashboard card | Completed | Section `predictor_on_call_centre_logs` in `/api/accuracy`; card "Waiting-time predictor on real call-centre queues" with the formula next to the three baselines and a one-line S1 note; the camera "Waiting time" card is unchanged. |
| Testing report and README | Completed | Section in `docs/testing_report.md`; fetch and score commands and the acknowledgement in the README. |
| Explaining the cause | Partially completed | The undercounted service rate is measured (S1 removes most of the bias); the abandonment effect is argued, not isolated. The causes were not tested one by one on the test months. |
| Real-bank or camera validation of the formula | Not completed | Out of scope: no camera footage of a queue with ground-truth waits was used. |
| End-to-end browser tests | Not completed | Playwright e2e was not re-run for this task (last run 12/12 before this branch). |
| Contact with the data owners | Not completed | Needs a person (see team actions). |

## Files

New or changed (this branch): `docs/anonymous_bank_protocol.md`, `docs/anonymous_bank_report.md`,
`docs/testing_report.md`, `docs/dataset_licences.md`, `README.md`, `datasets/metadata/anonymous_bank_files.csv`,
`datasets/metadata/dataset_licensing_register.csv`, `scripts/fetch_anonymous_bank.py`,
`scripts/validate_anonymous_bank.py`, `scripts/score_anonymous_bank.py`, `scripts/export_accuracy.py`,
`ai_engine/data/anonymous_bank.py`, `ai_engine/data/anonymous_bank_replay.py`,
`ai_engine/evaluation/anonymous_bank_metrics.py`, `ai_engine/evaluation/day_bootstrap.py`,
`dashboard/lib/accuracy-types.ts`, `dashboard/components/AccuracyView.tsx`, tests for each Python module and for
the card. Local and gitignored: `outputs/metrics/anonymous_bank_wait_eval.json`,
`outputs/metrics/anonymous_bank_validation.json`, `outputs/figures/anonymous_bank/*.png`, `data/accuracy.json`,
raw and tidy data.

## Dependencies

One new pinned development dependency: `matplotlib==3.11.2` (`requirements-dev.txt`, for the figures). No new
runtime dependency; the parser uses the standard library.

## Baselines versus final

| Command | Baseline (`main` `b5bb0f9`) | Final |
|---|---|---|
| `pytest -q` | 454 passed | 495 passed |
| `pytest -q -m "not integration"` | 443 passed, 11 deselected | 484 passed, 11 deselected |
| `ruff check .` | clean | clean |
| `ruff format --check .` | not recorded in the baseline | 9 files would be reformatted, all added in earlier tasks of this branch (listed below); not changed in this task |
| Dashboard `npm run lint` | not recorded | clean |
| Dashboard `npm run typecheck` | not recorded | clean |
| Dashboard `npm test` (Vitest) | 125 passed | 127 passed (2 new card tests) |
| Dashboard `npm run build` | not recorded | succeeded |
| Playwright e2e | 12/12 passed | **not re-run** |

Files that `ruff format --check` would reformat: `ai_engine/data/anonymous_bank.py`,
`ai_engine/data/anonymous_bank_replay.py`, `ai_engine/evaluation/anonymous_bank_metrics.py`,
`ai_engine/evaluation/day_bootstrap.py`, `scripts/score_anonymous_bank.py`, `scripts/validate_anonymous_bank.py`,
`tests/test_anonymous_bank.py`, `tests/test_anonymous_bank_metrics.py`, `tests/test_anonymous_bank_replay.py`.
CI runs `ruff format --check .`, so these need one formatting commit (`ruff format` on those files) before merging.

## Validation counts

See the table "Data quirks and exclusions" in [testing_report.md](testing_report.md): 444,448 rows, 363 days,
3,599 PHANTOM, 260,013 queued calls (196,007 AGENT, 64,006 HANG), 327 queued AGENT calls without a service start,
1,113 HANG calls with service times, 702 midnight crossings, 5 `AA` calls.

## Protocol decisions and commits

- Protocol v1 `f940045`, before any scoring; scoring commits come after it (`bd06eaf` and later).
- Queued = `q_start` set **or** `q_time` > 0 (one genuine midnight call); fixed in the protocol before scoring.
- Protocol v2 addendum `aa112de` (post-hoc S1), committed before S1 was scored; the v1 headline is unchanged.
  Cost if this was the wrong call: readers may prefer S1 as the headline; it is labelled post hoc for that reason.

## Results

Test (headline), validation, sensitivity queues, exploration (separate) and S1 (separate, post hoc) are in
[testing_report.md](testing_report.md). Figures (local, gitignored): `outputs/figures/anonymous_bank/`.

## Limits

A phone queue, not people in a line; priority customers jump the queue; one centre, one year, 8 regular agents;
unknown whether call types share a queue; event-driven replay differs from per-frame video sampling; HANG
calls are censored and excluded from error metrics; intervals cover day-to-day variation only.

## Team actions

1. Email Prof. Mandelbaum (Technion SEE Center) to tell him the data is used, share the result, and
   ask whether the call types share one queue and whether `ser_start` is recorded for unqueued calls the way we
   assume. Not sent by Claude.
2. Decide the wording of the dashboard card ("Waiting-time predictor on real call-centre queues"; caveat "Phone
   queue, 1999, Israel; measures the prediction formula, not the cameras."), and whether showing an unfavourable
   result there is wanted.
3. Decide whether to re-examine the service-rate estimate (for example counting all service starts, which S1
   suggests) as a new, pre-registered protocol version on new data, rather than reusing the test months.
4. Run `ruff format` on the nine files listed above and re-run the Playwright e2e tests before merging.
