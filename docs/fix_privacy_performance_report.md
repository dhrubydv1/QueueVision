# Fix pass, session 3: privacy that does not depend on detection, and the pacing slowdown (`fix/privacy-performance`)

Branch `fix/privacy-performance` from `main` at `b68fb47` (#11 merged). Plan:
[superpowers/plans/2026-11-17-fix-privacy-performance.md](superpowers/plans/2026-11-17-fix-privacy-performance.md); brief recorded in
[superpowers/specs/2026-11-13-fix-pass-brief.md](superpowers/specs/2026-11-13-fix-pass-brief.md) (session 3). Method, test list,
pacing numbers and limits are in [testing_report.md](testing_report.md) (section "Fix pass, session 3"); privacy modes are in
[privacy.md](privacy.md). **The pacing adoption rule was written into the plan and committed (`81b429a`) before any pacing run.**

## Outcome in short

- **Privacy.** A new `frame` privacy mode pixelates the whole frame before zone, boxes, IDs and panel are drawn, so a
  person the detector missed is hidden too. Live previews (`preview.jpg`, `preview.mjpg`) now default to `frame`; this
  is the one intended default change. Saved videos keep the old behaviour (`boxes` = head blur when blurring is on).
- **Pacing.** No pacing change was adopted. Of the three remedies, only the warm-up (c) met the speed criterion, and it
  used 2.59 times the CPU of paced-as-now (limit 1.10). `caffeinate` (b) and spin-tail (d) did not close the gap.

## What changed, by task

| Task | Brief item | Commits | Status |
|---|---|---|---|
| 0 | Brief and plan, pacing rule fixed before measuring | `81b429a` | Completed |
| 1 | `frame` privacy mode in the engine | `e4644ce` | Completed |
| 2 | Plumbing: CLI, run options, API, preview, dashboard | `dca629c`, `c219f55` (review fix round 1) | Completed |
| 3 | Pacing experiments and decision | `f9e954d`, `64f8639` (review fix: after-wait / no-wait split, git commit in `decision.json`) | Completed |
| 4 | Adopt the passing remedy (conditional) | none | Skipped: `decision.json` `passing` is empty |
| 5 | Docs and this report | the commit that adds this file | Completed; items needing a person are under Pending |

Also on the branch: `47f1410` (roadmap update through Sprint 7).

**Task 1.** `ai_engine/visualization/privacy.py`: `pixelate_frame(frame, block=None)` (in place; INTER_AREA down,
INTER_NEAREST up; default block `max(8, round(width / 40))` px) and `PRIVACY_MODES = ("none", "boxes", "frame")`.
`FrameProcessor` takes `privacy_mode` (default `none`; `blur_heads=True` maps to `boxes`; conflicting arguments raise
`ValueError`) and can render a second output in another mode from a clean copy of the undrawn frame.
`tests/test_privacy_frame_mode.py` (torch-free) proves that an unreported high-contrast patch loses all block variance
in `frame` mode, that overlays stay sharp, and that `boxes` is pixel-identical to the old `blur_heads` path.

**Task 2.** `run_tracking.py --privacy-mode {none,boxes,frame}` (default `none`; `--blur-heads` still means `boxes`;
both must agree). The metrics JSON gains `privacy_mode` (additive). `RunOptions` gains `privacy_mode` (video) and
`preview_privacy_mode` (`boxes`|`frame`, default `frame`, never `none`); the frame is only copied when the two modes
differ. The upload form gains the fields `privacy_mode` (default `boxes`) and `preview_privacy_mode` (default `frame`).
`blur_heads=false` still means a saved video with no blur, and `options.privacy_mode` then reports `none`. Runs made
before this change return `null`. `api/preview.render_preview` no longer blurs an already privacy-rendered image
again, but keeps a fallback so an image without privacy is never served. The dashboard `UploadForm` has two selects.
Review fix round 1: the golden test now also runs the default `frame` preview against the frozen reference; the
privacy tests are stronger; the option modes are reported as applied; docs reworded.

**Task 3.** `scripts/measure_realtime.py --stages --variant {baseline,warmup,spin-tail}` with per-run
`resource.getrusage` CPU time, a per-run after-wait / no-wait update split (added in review, after the runs), and the
driver `scripts/run_pacing_experiments.py` (b = baseline under `caffeinate -i`, rotated order, 20 s pauses, git commit
recorded). The stage timers pointed at names that had moved to `ai_engine/frame_processor.py` in session 1, so
`--stages` was broken on `main`; they were re-pointed (script only). `tests/test_pacing_variants.py` (torch-free) uses a
fake clock to check spin-tail timing, that the warm-up runs once per paced frame, and that the CPU fields are present.
No product code changed.

## What Sprint 8 live cameras should expect

From `outputs/s10_pacing/decision.json` (stride 3, `cad_seq25`, YOLO11n on MPS, 8 GB M1, **on battery with load about
4.8**, n = 3; compare within this session only):

| Quantity | Unpaced (fast) | Paced as now (a) |
|---|---|---|
| Median detector-update time per processed frame | 32.3 ms | 40.2 ms (+7.9 ms, about +24 %) |
| Process CPU time for the 20.6 s clip | 6.40 s | 8.66 s |
| Wall time for the 20.6 s clip | 10.1 s | 21.6 s (paced to real time; 8.6 s of it waiting) |

- **Pacing cost.** A paced live run pays about 8 ms more per detector update than an unpaced one on this machine and
  clip. It still keeps up with the 30 fps clip at stride 3 (12.98 s of non-wait work over 20.6 s of video). Sprint 8
  measured a gap of about 12 ms on AC power, so expect 8 to 12 ms.
- **No fix for the gap yet.** `caffeinate -i` and spin-waiting change nothing measurable. A full-size warm-up pass
  after each wait costs about 60 ms of CPU per call (2.6 times the run's CPU) and is not worth it. Its apparent speed-up
  is confounded: about half of its frames had no wait before them. The open, **untested** follow-up is a much cheaper
  warm-up, measured with the new after-wait / no-wait split.
- **Privacy defaults for live previews.** Previews pixelate the whole frame (`preview_privacy_mode=frame`). People
  the detector misses are hidden, and so is scene detail: the queue shows as coarse blocks, with boxes, IDs, zone and
  panel drawn sharp on top. `boxes` can be chosen for a sharper preview, which blurs only detected heads. A preview with
  no privacy cannot be requested. Saved videos default to `boxes`.
- **Not measured.** The cost of the second, preview-mode render per encoded preview was not timed. The pacing runs
  report detector-update time and CPU, not preview encoding.

## Files

Added: `docs/fix_privacy_performance_report.md`, `docs/superpowers/plans/2026-11-17-fix-privacy-performance.md`,
`scripts/run_pacing_experiments.py`, `tests/test_privacy_frame_mode.py`, `tests/test_pacing_variants.py`.
Changed: `ai_engine/frame_processor.py`, `ai_engine/run_tracking.py`, `ai_engine/visualization/privacy.py`,
`api/jobs.py`, `api/models.py`, `api/preview.py`, `api/routes/videos.py`, `scripts/measure_realtime.py`,
`dashboard/components/UploadForm.tsx`, `dashboard/components/UploadForm.test.tsx`, `dashboard/lib/api-types.ts`,
`dashboard/openapi.json`, `tests/conftest.py`, `tests/test_api.py`, `tests/test_api_preview.py`,
`tests/test_frame_processor_golden.py`, `tests/test_pipeline.py`, `README.md`, `docs/api.md`, `docs/architecture.md`,
`docs/demo.md`, `docs/privacy.md`, `docs/security_review.md`, `docs/testing_report.md`, the session brief.
Not committed, by design: `outputs/s10_pacing/`, `outputs/s10_final_commands/` (gitignored), and any CAD frames.

## Dependency changes

None: no requirements file, `pyproject.toml`, `package.json` or lockfile is changed on the branch.

## Baselines vs final

The Python baselines come from Task 1 (`.superpowers/sdd/2026-11-17-fix-privacy-performance/task-1-report.md`, on
`81b429a`). The dashboard baselines are the session 2 finals named in the plan's global constraints. The final runs
were made on the final tree, and their raw output is in `outputs/s10_final_commands/` (local, gitignored).

| Command | Baseline | Final |
|---|---|---|
| `.venv/bin/python -m pytest -q` | 544 passed | 576 passed (`pytest.txt`) |
| `.venv/bin/python -m pytest -q -m "not integration"` | 533 passed, 11 deselected | 565 passed, 11 deselected (`pytest_not_integration.txt`) |
| `.venv/bin/ruff check .` | clean | All checks passed (`ruff_check.txt`) |
| `.venv/bin/ruff format --check .` | 163 files already formatted | 166 files already formatted (`ruff_format.txt`) |
| dashboard `npm run lint` | clean | clean (`dash_lint.txt`) |
| dashboard `npm run typecheck` | clean | clean (`dash_typecheck.txt`) |
| dashboard `npm run build` | success | success (`dash_build.txt`) |
| dashboard Vitest | 140 passed | **First run failed**: 2 timeouts plus 1 worker start failure, 139 of 141 passed. The run took 2 h 34 min at 0 % CPU, so the machine was starved or asleep (`dash_vitest.txt`, `dash_vitest_attempt{1,2}_worker_timeouts.txt`). **Re-run on an idle machine: 142 passed, 19 files** (`dash_vitest_rerun.txt`) |
| dashboard `npm run e2e` (chromium + webkit) | 14 passed | **First run failed**: 2 h 34 min at 1 % CPU, with "API did not become healthy within 60 s" and a browser launch timeout (`dash_e2e.txt`). **Re-run: 13 passed, 1 failed** (`dash_e2e_rerun.txt`): in WebKit, "upload in real time, Live updates, completion, video plays and seeks" still showed Queue length "—" after its 15 s limit. Chromium passed the same test. Run alone in WebKit, it **passed 2 of 2** (`dash_e2e_webkit_rerun.txt`, `--repeat-each=2`). Treated as a timing flake under full-suite load, not a fixed or proven-harmless issue |

Net change in tests: Python 544 to 576; Vitest 140 to 142. The e2e suite uses port 8765 only.

## Rulings made during the session

- The preview privacy mode is limited to `boxes` or `frame`, never `none`, because shared rule 5 requires heads to be
  blurred in everything the dashboard shows. Cost if wrong: a client cannot get an unblurred preview (intended).
- The brief's "most of the gap" and "not much CPU" were defined as at least two thirds of the gap closed and at most
  10 % more CPU. This was fixed in the plan before measuring.
- `frame` uses pixelation with a block of `max(8, width / 40)`. The block scales with frame width, not with person
  size, so very close faces may stay partly recognisable. This is documented in `privacy.md` and left for a person's
  visual check.
- No new pacing runs after review. Adding a cheaper warm-up variant after seeing the results would be post-hoc
  against the pre-registered rule. The c result is labelled limited, and the cheap warm-up is named as a Sprint 8
  follow-up.
- A scratch report commit (`21235e1`) was dropped from the branch during Task 3 review. The file was kept on disk and
  is untracked.

## Pending (needs a person)

- **Look at `frame` pixelation on real close faces.** So far it has been tested only on synthetic patterns and a noise
  clip.
- **Real-browser check.** Real Chrome, real Safari and a real phone, following [demo.md](demo.md) and the Sprint 8
  checklist in [testing_report.md](testing_report.md), including the new upload selects and the pixelated Live preview.
- **WebKit e2e flake.** The "upload in real time" test missed its 15 s first-update limit once under full-suite load
  (see the table above). If it recurs, look at first-update latency in WebKit with the `frame` preview, or raise that
  timeout.
- **A cheaper warm-up.** Not tested. Measure it under a new, pre-registered rule using `update_split`, ideally on AC
  power.
- **Cosmetic doc leftovers from the Task 2 review.** A few older `testing_report.md` rows and `architecture.md`
  passages still describe the recorded-run video as "heads blurred". This is accurate for the default `boxes` mode only.
- Minor, deferred: the wrapper test in `tests/test_pacing_variants.py` copies the branching of the code it tests.
