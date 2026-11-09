# Sprint 6 Report (2026-11-09)

Branch `sprint-6/error-analysis-and-labelling`, built on Sprint 5
(`sprint-5/accuracy-and-validation`, PR #5). Every number comes from a command
that was run; outputs are under `outputs/s6_*` (git-ignored).

**Headline: Sprint 6 explains where the queue-count error comes from, but the
two changes it tested (detector input 960 px; shorter grace and dwell) did not
generalize from the tuning clips to the held-out clips. Defaults are unchanged.**
It also prepared the tools a human needs to label waiting times, and recorded
the licence position of every planned dataset.

## 1. Where the count error comes from

New `ai_engine/analyze_queue_errors.py` (`ai_engine/evaluation/count_errors.py`):
* At every annotated CAD frame, it matches the tracks the system counts against
  CAD's own per-person boxes and action labels. These are now exported by
  `prepare_cad.py --person-boxes-only` as `cad_seqNN_person_boxes.csv`.
* Each counted track and each missed queuer is put in exactly one category.
* The code asserts that the categories add up to the raw count and to the
  labelled count.
* It reproduces the Sprint 5 raw bias exactly (+0.892 all, +0.607 val, +1.014
  test, replay protocol).

Category totals, legacy config, frames from 5 s, IoU 0.5 (`outputs/s6_analysis/errors_640.txt`):

| Group | frames | counted | labelled queueing | correct | counted, walking label | counted, no matching box | counted, not visible (grace) | missed: no detection | missed: still in dwell | labelled queuer outside zone |
|---|---|---|---|---|---|---|---|---|---|---|
| all clips | 407 | 3339 | 2984 | 1997 | 16 | 853 | 473 | 673 | 312 | 9 |
| val | 122 | 761 | 686 | 471 | 4 | 189 | 97 | 154 | 61 | 8 |
| test | 285 | 2578 | 2298 | 1526 | 12 | 664 | 376 | 519 | 251 | 1 |

Reading it:

* **The net bias hides much larger errors in both directions.** Per frame:
  * Over-count: about 2.10 counted boxes with no matching labelled box, plus
    1.16 tracks counted while out of view during the 1.5 s grace.
  * Under-count: 1.65 labelled queuers never detected, plus 0.77 still in the
    3 s dwell.
* **Box alignment explains much of the "no match".** At IoU 0.3, "counted, no
  matching box" falls from 853 to 381 and "no detection" from 673 to 85. CAD's
  boxes are looser than the detector's.
* **The remaining real causes** are grace-period ghosts, duplicate boxes on one
  person, and people not yet past the dwell time.
* **Not causes:** the zones (9 labelled queuers outside them) and walkers being
  counted (16).
* **Spot checks** (3 worst val frames, `outputs/s6_analysis/frames/`, not
  committed) show the same picture: ghosts held by the grace period, two boxes
  on one person, and an unannotated person at the counter.

## 2. Detector input size 960 vs 640

Same tracker and configs; tracks saved at 960 and replayed
(`outputs/s6_analysis/queue_{640,960}.txt`). Smoothed MAE from 5 s:

| Split | 640 | 960 |
|---|---|---|
| val (decision) | 1.176 | 0.910 |
| test (confirmation) | 1.619 | 1.723 |

* **Clips:** 960 is better on all 3 val clips but worse on 3 of the 4 test
  clips (flat on seq27).
* **Speed:** inference FPS fell by 2–47% per clip. The machine load was not
  controlled, so treat these speed figures as rough.
* **Decision: not adopted.**

## 3. Grace and dwell, tuned on val only

* **Sweep:** 12 combinations (grace 0.5/0.75/1.0/1.5 s × dwell 2.0/2.5/3.0 s)
  on the val clips only (`outputs/s6_sweep/val_sweep.txt`).
* **Selection rule, fixed in advance:** lowest val smoothed MAE from 5 s. That
  chose grace 0.75 s, dwell 2.0 s. It was then scored on test once.

| Split | shipped (1.5 s / 3.0 s) | grace 0.75 / dwell 2.0 |
|---|---|---|
| val, smoothed MAE | 1.176 | 0.984 |
| test, smoothed MAE | 1.619 | 1.716 |
| test, bias | +0.960 | +1.126 |

**Not adopted.** Twice now, a gain on the three val clips (few people, short)
has failed on the four denser test clips. The val split is too small and too
different from test to tune on; more labelled clips are needed first.

## 4. Dataset licences (checked 2026-11-09, `docs/dataset_licences.md`)

| Dataset | Terms | Consequence |
|---|---|---|
| MOT17 | Explicit: CC BY-NC-SA 3.0 (motchallenge.net); some sequences may carry source conditions | non-commercial, attribution, share-alike |
| Collective Activity | None stated (citation requested) | local academic use only; ask the authors before sharing anything derived |
| MIT Traffic | None stated | as above; not acquired |
| CUHK Square | None stated | as above; not acquired |

All current evaluation data (CAD) has **no stated licence**. Nothing derived
from it may be published without the authors' permission.

## 5. Waiting-time annotation kit

`ai_engine/tools/make_wait_annotation_kit.py` produces, per clip:
* a review video with frame number and time burned in, zones drawn, and
  tracker IDs as a visual aid;
* a blank ground-truth sheet in the Sprint 5 format;
* a separate tracker-hints file, kept apart so annotators are not steered
  toward the system's answers;
* annotator instructions;
* a checker (`--check`, `--compare` for two annotators).

Kits were generated for cad_seq25, 26 and 41 under
`datasets/annotations/queue_events/` (git-ignored). **Waiting-time accuracy is
still unmeasured until a person fills these sheets.**

## 6. Tests

`pytest -q`: 403 passed (370 at the end of Sprint 5). `ruff check .` and
`ruff format --check .` are clean. New: `tests/test_count_errors.py` (14),
`tests/test_wait_annotation_kit.py` (15), 4 in `tests/test_prepare_cad.py`.
The Sprint 5 replay correctness gate still passes.

## 7. Recommended next sprint

1. **Label waiting times** on the three kits, double-annotating one clip, then
   run `evaluate_waiting.py`. This is the main open accuracy question.
2. **More labelled queue clips** before any further tuning, so that val and test
   are each large enough and similar in crowd density. Re-split by a rule fixed
   in advance.
3. **Write to the CAD authors** about use of their data (questions in
   `docs/dataset_licences.md`).
4. **Targeted fixes to test once there is more data:** duplicate-box suppression
   among counted tracks; ending grace early when a nearby new track appears.
   Both address error categories measured above.
