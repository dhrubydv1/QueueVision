# Fix pass session 3: privacy that does not depend on detection, and the pacing slowdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a `frame` privacy mode (whole frame pixelated before overlays) used by default for previews, and measure the ~40 % per-frame slowdown under real-time pacing against three candidate remedies, adopting one only under a rule fixed before measuring.

**Spec:** `docs/superpowers/specs/2026-11-13-fix-pass-brief.md` (SHARED RULES, SESSION 3).

## Global Constraints

- Branch `fix/privacy-performance` from `main` `b68fb47`. Baselines (record in Task 1's report, re-run): pytest 544; `-m "not integration"` 533 passed / 11 deselected; ruff check + format clean; dashboard lint, typecheck, Vitest 140, build; e2e 14/14 (port 8765).
- Never fabricate. Every number from a command run or a named file; what cannot be run is pending with the reason.
- No silent behaviour changes (shared rule 3). The spec itself changes one default: previews (`preview.jpg`, `preview.mjpg`) become `frame`. Everything else is additive: existing `/api/*` fields, the `blur_heads` upload field and `--blur-heads` CLI flag keep their meaning; `run_tracking.py` default output (no blur) is unchanged; saved videos default to `boxes` when blurring is on.
- Rule 5: heads blurred in everything the dashboard shows — the preview privacy mode may be `boxes` or `frame`, never off.
- CAD has no stated licence: never commit CAD frames, videos or screenshots; outputs under `outputs/s10_*` (gitignored — check `.gitignore`).
- One heavy (model) job at a time on this 8 GB M1; use `caffeinate -i` only where the experiment calls for it (variant b) — the other variants must run without it. Don't touch ports 8001 (demo) or 8770; 8000 is free but leave it alone.
- Unit tests torch-free; real-model tests marked `integration`; ruff clean; deps pinned (no new dependency expected); npm exact with lockfile; commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; don't push.

### Pacing adoption rule (fixed here, before any measurement)

Measured at stride 3 on the Sprint 8 clip and camera (see `docs/testing_report.md`, Sprint 8 pacing section). Gap = median per-frame detector-update time of (a) paced-as-now minus that of the unpaced ("fast") reference measured in the same session. A remedy (b, c or d) is adopted as a product change only if **both** hold, on medians over its 3 repeats:
1. It closes at least two thirds of the gap (its paced update median ≤ fast median + gap / 3).
2. Its process CPU time (user + system from `resource.getrusage(RUSAGE_SELF)`, over the run) is at most 10 % above (a)'s.
If several pass, the one with the lowest CPU time is adopted. Otherwise nothing is adopted and the findings are documented. (b) `caffeinate` is an OS-level wrapper: if it passes, the product change is to document/offer it for live runs, not to spawn it silently.

## Review Focus

1. In `frame` mode a person the detector did not report is unrecognisable in the output, while zone, boxes, IDs and panel text stay sharp.
2. Preview images are produced in their own privacy mode even when the saved video uses another (no double blur of overlays, no leak of an unblurred frame to the preview).
3. `boxes` mode output is pixel-identical to today's `blur_heads` output; default CLI and `/api` outputs unchanged apart from the preview default.
4. The pacing variants differ only in the one thing named; order alternates; each reports wall, waited, non-wait, update median and CPU time with min/median/max.
5. The adoption decision follows the rule above exactly.

---

### Task 1: `frame` privacy mode in the engine
Baselines first. `ai_engine/visualization/privacy.py`: `pixelate_frame(frame, block: int | None = None) -> np.ndarray` (in place; downscale with INTER_AREA and back up with INTER_NEAREST; default block = `max(8, round(width / 40))` px) and a `PRIVACY_MODES = ("none", "boxes", "frame")` constant. `FrameProcessor`: `privacy_mode` keyword (default `"none"`; existing `blur_heads=True` maps to `"boxes"`, conflicting combinations raise `ValueError`); in `draw`, `frame` pixelates the whole frame **before** zone, service point, people, banner and panel. Add a way to render a second output in a different mode from the same undrawn frame (e.g. `draw(result, total_frames, privacy_mode=...)` on a copy taken before drawing) for Task 2's preview. Tests (torch-free, synthetic frames): an unreported high-contrast "person" patch loses its detail in `frame` mode (e.g. per-block variance zero / high-frequency energy removed); overlay pixels (a drawn box edge, panel text region) equal those drawn on an unblurred frame; `boxes` output is pixel-identical to the current `blur_heads` path; `none` unchanged.

### Task 2: Plumbing — CLI, run options, API, preview, dashboard
`run_tracking.py`: `--privacy-mode {none,boxes,frame}` for the saved video (default none; `--blur-heads` keeps meaning `boxes`; both together must agree or error); metrics JSON records `privacy_mode` additively (existing keys unchanged). `process_video` / `RunOptions`: `privacy_mode` (video) and `preview_privacy_mode` (`boxes`|`frame`, default `frame`); when the callback wants an image, the update's image is rendered in the preview mode from a copy of the undrawn frame (only copy when the modes differ). API: upload `Form` fields `privacy_mode` (`boxes`|`frame`, default `boxes`; `blur_heads=false` still means no blur on the saved video — define and document the combination) and `preview_privacy_mode` (`boxes`|`frame`, default `frame`); stored in run options, returned additively. `api/preview.render_preview` stops re-blurring an already privacy-rendered image (keep a safety fallback so an image without privacy applied is never served). Dashboard `UploadForm`: a select for the saved-video privacy mode and one for the preview (defaults as above), types updated, Vitest. Tests for each layer; the e2e preview test still passes. Docs: `docs/privacy.md` (modes, defaults, trade-off: `frame` hides missed people but also hides scene detail; `boxes` keeps detail but misses undetected people), `docs/api.md`.

### Task 3: Pacing experiments
Extend `scripts/measure_realtime.py --stages` with `--variant {baseline,warmup,spin-tail}` (c: one dummy MPS inference just before each paced frame; d: sleep until ~2 ms before the deadline then spin) and per-run `resource.getrusage` CPU time (user, system). (b) is the baseline variant run under `caffeinate -i` by the driver (record that). Add an unpaced fast reference. Run fast reference, a, b, c, d — 3 repeats each, order alternating/rotated between rounds, stride 3, Sprint 8 clip and camera, one job at a time — into `outputs/s10_pacing/`. Report per variant: wall, waited, non-wait, update median, CPU time, each min/median/max. Apply the pacing adoption rule above exactly; write `outputs/s10_pacing/decision.json` (inputs, gap, each criterion, decision, source files). Tests (torch-free) for the new variant logic (spin-tail timing with a fake clock; warm-up called once per paced frame; CPU fields present). Do not change product code here.

### Task 4 (conditional): Adopt the passing remedy
Only if Task 3's decision adopts one: implement it in the product pacing path (`api/jobs.py`) behind the smallest change, with tests, and re-measure once to confirm. If nothing passes, this task is skipped and recorded as such.

### Task 5: Docs and session report
`docs/testing_report.md` (privacy-mode tests; pacing method, numbers, decision, limits), `docs/privacy.md` final, a "What Sprint 8 live cameras should expect" section (pacing cost per frame, CPU, privacy defaults for live previews), and `docs/fix_privacy_performance_report.md` per shared rule 7 (baselines vs final for every command, saved under `outputs/s10_final_commands/`).
