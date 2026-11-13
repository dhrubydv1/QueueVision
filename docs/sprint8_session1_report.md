# Sprint 8, session 1 report (2026-11-13)

Branch `sprint-8/hardening`, started from the tip of PR #7 (`160dcea`) at the user's choice, because #5 to #7
are not merged yet (`main` is at Sprint 4, `dd758b2`). Its PR targets `sprint-7/dashboard` until they merge.

## What was built

Session 1a was verification only. Session 1b closed the Sprint 7 deferred items: API path containment, Origin
edge cases, `stop()` under the lock, `/legacy` and HTML caching, accuracy-export naming, dashboard
accessibility and correctness fixes, a pacing investigation, and a person-run browser checklist.

## Session 1a: merge readiness (nothing fixed)

| PR | Tip | `pytest -q` | CI |
|---|---|---|---|
| #5 | `4935e5f` | 370 passed | green |
| #6 | `b0f74a5` | 403 passed | green |
| #7 | `160dcea` | 440 passed; Vitest 114; e2e 10/10 | green, including the first run of the `dashboard` job |

The `dashboard` job passed twice. There was no font-network problem (`next/font/google`), so no job change
was needed. No genuine failure was found on any of the three branches, so nothing was changed in 1a.

**Merge order, for the team (Claude does not merge):** #5, then #6, then #7, each with a merge commit,
retargeting each PR to `main` after the previous merge. Then open the `sprint-8/hardening` PR against `main`
(or against `sprint-7/dashboard` first, if it is opened before the merges, and retarget it).

## Session 1b: items

| Item | Status | Detail |
|---|---|---|
| Report-path containment (`/api/videos/{id}/video`, `timeseries.csv`) | Completed | `_contained()` in `api/main.py` resolves the path, requires it under the data directory, logs a warning, returns the existing 404. Tests: outside absolute path and `../` path refused; a normal `data/outputs/<id>/` file still served. |
| Origin edge cases | Completed | `""`, `null`, comma-only env value, bracketed IPv6 own origin, case-insensitive comparison (`ApiSettings.origin_allowed`). Look-alikes `localhost:30000` and `localhost.evil:3000` still rejected. Docs note a refused WebSocket appears as HTTP 403. Also trims whitespace in the header. |
| `JobManager.stop()` under the lock | Completed (race not reproduced) | `_stop` and `_interrupt` are now set under `_cond`. The new test `test_stop_interrupts_long_pacing_wait` passed before the change: it is a smoke pin, not a regression test, because the race is narrow and not deterministic. The change was applied as specified. |
| `/legacy` redirect, HEAD, cache headers | Completed | Redirect keeps the query string (307), `HEAD /legacy` handled, `Cache-Control: no-cache` on dashboard `.html`, `404.html` and the not-built page; `_next/static` is left cacheable. Tests added (red then green). |
| `export_accuracy.py` wait-eval naming | Completed | Clip name has `_observed` stripped and each wait evaluation carries `kind` (`predicted` or `observed`), sorted by (clip, kind); unreadable or malformed files go to `missing_sources`. Naming documented in `evaluate_waiting.py`. Dashboard `accuracy-types.ts` gains `kind`. Limits: clip ids ending in `_observed` are reserved; a pooled observed entry would be keyed `<clip>_observed`; neither writer emits `pooled` today. |
| Typecheck on a fresh checkout | Not reproduced, pinned | Plain `tsc --noEmit` also passed on a fresh checkout. For robustness `typecheck` is now `next typegen && tsc --noEmit` (verified with `npm ci` in a temporary worktree, with `next-env.d.ts` and `.next` removed). |
| Desktop nav links at least 44 px | Completed | `min-h-11` on desktop links; class-check test. |
| Run-page tabs, arrow keys | Completed | Roving tabindex, Left/Right with wrap, Home/End, focus follows selection. Limit: all tabs carry `aria-controls` pointing at the one panel (the APG prefers the selected tab only). |
| AlertBanner explanation | Completed | The hover-only tooltip is replaced by a visible note linked with `aria-describedby`. Limit: the note id is hard-coded. |
| Alerts tab unknown event labels | Completed | "Congestion started", "Congestion ended", otherwise the raw type with underscores as spaces. |
| `eventKey` collisions | Completed | Key includes event type, timestamp, frame index, person key, track id and track ids. The same event arriving twice (history and socket) still dedupes (tested). |
| Overview hydration mismatch | Not reproduced | The date and processing count were already gated until after mount. No app change; a Playwright test now checks the served HTML has no "N runs processing" or current year and that the page loads with no hydration errors. |
| Real-time pacing gap | Completed (documented, not a bug) | See measurements. No product code changed; `scripts/measure_realtime.py` gained a `--stages` mode. Cause is inferred, not measured. |
| Real Chrome, Safari, phone check | Not completed (needs a person) | Checklist ready in `docs/testing_report.md` ("Sprint 8: browser checklist"); results blank. |

## Files

Created: `docs/sprint8_session1_report.md`. Modified: `api/main.py`, `api/settings.py`, `api/jobs.py`,
`ai_engine/evaluate_waiting.py`, `scripts/export_accuracy.py`, `scripts/measure_realtime.py`,
`dashboard/package.json`, `dashboard/components/{AlertBanner,AppHeader,RunDetail}.tsx`,
`dashboard/lib/{accuracy-types,live-types}.ts`, `dashboard/e2e/dashboard.spec.ts`, their tests
(`tests/test_api*.py`, `tests/test_export_accuracy.py`, dashboard `*.test.tsx`/`*.test.ts`),
`docs/security_review.md`, `docs/testing_report.md`. Added earlier: the Sprint 8 brief and plan under
`docs/superpowers/`.

Dependency changes: none (`requirements.txt` and `dashboard/package.json` dependencies unchanged; only the
`typecheck` script changed).

## Baselines versus final

| Command | Baseline (`160dcea`) | Final |
|---|---|---|
| `.venv/bin/pytest -q` | 440 passed | 452 passed |
| `.venv/bin/pytest -q -m "not integration"` | 429 passed, 11 deselected | 441 passed, 11 deselected |
| `ruff check` / `ruff format --check` | clean | clean (121 files formatted) |
| `npm run lint` | clean | clean |
| `npm run typecheck` | clean | clean |
| `npm test` (Vitest) | 114 passed | 124 passed (18 files) |
| `npm run build` | clean | clean |
| e2e (Playwright) | 10/10 (Chromium 5, WebKit 5) | 12/12 (Chromium 6, WebKit 6), run by Task 3, not re-run for this report |

## Measurements: real-time pacing gap

Question: at stride 3, why did the real-time run spend 12.6 s outside the waits against 9.2 s for the whole
fast run? Method: `scripts/measure_realtime.py --stages` (timers wrapped around each stage from the script;
no engine or API change), `cad_seq25.mp4` (617 frames, 20.567 s, MPS, yolo11n), one throwaway fast run first
so model load is excluded, then fast and real-time jobs alternating, 3 repeats per configuration, one job at
a time. Min / median / max, seconds:

| Stride, mode | Wall | Waited | Non-wait |
|---|---|---|---|
| 1, fast | 25.914 / 26.062 / 26.704 | 0 | same as wall |
| 1, real time | 26.172 / 26.183 / 26.256 | 0 (never waited) | same as wall |
| 3, fast | 8.927 / 9.079 / 9.279 | 0 | same as wall |
| 3, real time | 21.200 / 21.381 / 21.457 | 8.459 / 8.534 / 8.690 | 12.691 / 12.741 / 12.923 |
| 3, real time first (order reversed) | 21.361 / 21.625 / 21.731 | 7.889 / 8.808 / 8.894 | 12.468 / 12.923 / 13.737 |

The gap reproduces (about 3.7 s, 3.9 s with the order reversed). Per-frame detector update: median about 33
ms fast against about 45 ms in real time. With a busy-wait diagnostic replacing the sleep (not product
behaviour), non-wait time falls to 9.6 to 10.2 s and the update median to about 34 ms. **Cause, inferred:**
the machine slows down while idle between frames (for example a power state); counters were not measured,
and the busy-wait also changes CPU duty cycle. No defect was found. Effect on users: real-time wall time
stays about 0.6 to 1.2 s over the clip. Caveat: one fast outlier (12.560 s) in the busy-wait batch, shown as
is. Full tables and stage breakdown: `docs/testing_report.md`.

## Pending: needs a person

- Real Chrome, real Safari (desktop) and a real phone check of every page, and re-taking the screenshots
  into `outputs/s8_screens/` (gitignored). Checklist with blank result columns: `docs/testing_report.md`,
  section "Sprint 8: browser checklist for a person". Playwright WebKit is not Safari.
- The merges of #5, #6, #7 (team action).

## Issues, remaining limits

- The `stop()` race is not reproducible; its test cannot fail reliably.
- Clip ids ending `_observed` are reserved by the export naming (not yet documented outside the script).
- Tabs and the alert note have the minor accessibility limits listed above.
- Pacing cause is inference from one experiment on one machine and clip.

## What session 2 needs

- #5, #6 and #7 merged into `main` in order, then this branch rebased or retargeted to `main`.
- The browser checklist filled (or an explicit decision to defer it).
- The team's Sprint 8 prompt text for session 2 (camera engine and API: `kind='live'` runs, worker processes,
  schema v2, `ai_engine/live/` capture and pacing, `worker/` package, ingest and supervisor routes, recording
  off by default); the brief records it only in summary.
- Awareness that real-time pacing carries a roughly 40 percent per-frame slowdown when idle between frames on
  this machine; live-camera pacing and its measurements should account for it (for example by measuring with
  and without the wait).
