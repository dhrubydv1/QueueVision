# Anonymous Bank: score the waiting-time predictor on real call-centre logs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Measure the engine's own wait predictor (queue length ÷ service rate) against real 1999 call-centre waits, with baselines and day-level bootstrap intervals, under a protocol committed before scoring.

**Spec:** `docs/superpowers/specs/2026-11-13-anonymous-bank-brief.md`.

## Global Constraints

- Branch `data/anonymous-bank` from `main` `b5bb0f9`. Baselines: `pytest -q` 454; `-m "not integration"` 443/11; ruff clean; Vitest 125; e2e 12/12.
- Data facts (controller inspection, 13 Nov 2026): Technion offers `Data/AnonymousBank.rar` (10,625,021 B, SHA-256 `110527823a296e3163566f5d10d687e13859c60d963fd403d4623d89c7cadc4b`) and `Data/AnonymousBank.pdf` (35,223 B, `77816db694fdee313a7d612e22199d87632ddca5a82f17c3258bd6799ff6e045`); the RAR holds `January1999.txt` … `December1999.txt`. `bsdtar` (libarchive 3.7.4, `/usr/bin/bsdtar`) extracts it. Files are tab-separated, CRLF, one header row, 17 fields on every row; times like `0:00:31` (hour not zero-padded). Already downloaded and extracted under `datasets/raw/anonymous_bank/technion/` (gitignored by `datasets/raw/*`). Bern mirror: documentation PDF + 12 `*.txt_ger.zip` files linked from its index page.
- Never commit raw or converted data, extracted text, or figures (all gitignored). Fixtures are synthetic. Never send email.
- Do not change `ai_engine/queue/waiting_time.py` or any default. Drive `ServiceRateEstimator(settings, start_s)`, `add_service(t)`, `estimate(now)` and `predict_wait(queue_length, rate)` with `ServiceRateSettings()` defaults (window 300 s, min_services 2, min_history 60 s, basis service_start).
- Wording: "predictor accuracy on real call-centre queues", never "QueueVision waiting-time accuracy".
- Archives are untrusted data: extract into their own folder; run Python that reads them with `-I`; never execute anything from them.
- Unit tests torch-free; ruff clean; pinned deps (no new dependency unless needed — prefer stdlib csv over pandas/pyarrow; matplotlib only if already installed, else ask); commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; don't push.

## Review Focus

1. No look-ahead anywhere: queue length, service events and baselines at `q_start` use only events strictly before (or at, with a stated tie rule) that moment.
2. The adapter calls the engine's code, not a copy, and a test pins equality with the engine on identical events.
3. HANG calls never enter error metrics; their count and "hung up after waiting longer than predicted" are reported.
4. Protocol commit precedes any scoring output or commit; test months are never used to choose anything.
5. No CAD or call-centre data, derived rows or figures committed.

---

### Task 1: Register the dataset and the fetch script
**Files:** `datasets/metadata/dataset_licensing_register.csv`, `docs/dataset_licences.md`, `scripts/fetch_anonymous_bank.py`, `datasets/metadata/anonymous_bank_files.csv` (committed; URLs, sizes, hashes only), `.gitignore` (confirm `datasets/raw/*` covers it; add `outputs/figures/` if not ignored), `tests/test_fetch_anonymous_bank.py`.
- Fetch: Technion `Data/AnonymousBank.rar` + `Data/AnonymousBank.pdf` into `datasets/raw/anonymous_bank/technion/`, extract with `bsdtar` into `…/technion/extracted/`; also Bern's PDF + 12 zips into `…/bern/` (extract each into `…/bern/extracted/`). Record each downloaded file's source URL, size, SHA-256 in the CSV. Re-run skips files whose hash matches the CSV. Network/HTTP errors stop with a clear message (no partial substitution). Tests with a fake downloader (no network).
- Compare one month (January) Technion vs Bern after normalising line endings: identical rows or report differences (counts and first differing lines) in the task report; Technion wins.
- Register entries per spec §1 with the quoted terms (read from the PDF: "The data is free for use. If used, please notify Avi Mandelbaum, at avim@ie.technion.ac.il and do acknowledge the data source in any of your work."), redistribution "not stated — do not redistribute", `terms_verified=yes`, team action line.

### Task 2: Parser and validation
**Files:** `ai_engine/data/anonymous_bank.py`, `scripts/validate_anonymous_bank.py` (or a CLI in the module), `tests/test_anonymous_bank.py`, output `outputs/metrics/anonymous_bank_validation.json` (gitignored) and per-month tidy CSVs under `datasets/raw/anonymous_bank/tidy/`.
- `parse_month(path) -> Iterator[CallRecord]` handling tab or whitespace delimiters, CRLF, unpadded hours; `CallRecord` with `call_key` (`vru_line:call_id:date`), `date`, `type`, `priority`, `queued` (`q_start != 0:00:00`), timestamps as seconds since that day's 00:00 with midnight-crossing (an exit earlier than its start → +86 400 s), `outcome`, `server`, the three duration fields; `customer_id` dropped at parse time.
- Validation JSON: rows per month, exclusions by reason (PHANTOM, unparseable), q_time and ser_time mismatches (count, distribution of differences), midnight crossings, HANG share among queued calls, calls per type and per priority, queued calls per hour of day, days per month. Report every count in the task report.

### Task 3 (controller): Protocol, committed alone
Written by the controller from the validation results; commit hash recorded before any scoring.

### Task 4: Replay, baselines, metrics, bootstrap, figures
**Files:** `ai_engine/data/anonymous_bank_replay.py` (adapter + population + baselines), `ai_engine/evaluation/day_bootstrap.py` (minimal day-level cluster bootstrap), `scripts/score_anonymous_bank.py`, tests; outputs `outputs/metrics/anonymous_bank_wait_eval.json`, `outputs/figures/anonymous_bank/*.png` (gitignored).
- Exactly per the committed protocol `docs/anonymous_bank_protocol.md`.

### Task 5: Export, API, dashboard card, docs, report
**Files:** `scripts/export_accuracy.py` (+ tests), `dashboard/lib/accuracy-types.ts`, `dashboard/components/AccuracyView.tsx` (+ Vitest), `docs/testing_report.md`, `README.md`, `docs/anonymous_bank_report.md` (new).
