# Sprint 7 Session 2: API additions and dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time pacing, a head-blurred live preview, an accuracy endpoint, a WebSocket Origin check and dashboard serving to the FastAPI app, and build the static Next.js dashboard (Overview, Live, Recorded runs, Accuracy) on top of them.

**Architecture:** Python changes stay inside `api/` except one backward-compatible hook in `ai_engine/run_tracking.py` (optional image + boxes on `FrameUpdate`). The dashboard is a separate `dashboard/` Next.js app exported to static files, served by FastAPI at `/` in demo mode and by `next dev` (with `/api` rewrites) in development; it talks to the API only over HTTP and WebSocket.

**Tech Stack:** Python 3.11, FastAPI, OpenCV, pytest; Node 22 LTS (nvm, v22.23.2 installed), Next.js App Router, TypeScript strict, Tailwind CSS, TanStack Query, Recharts, Vitest + Testing Library, Playwright, openapi-typescript.

**Spec:** `docs/superpowers/specs/2026-11-12-sprint7-dashboard-design.md` (sections A–E and the shared rules). Executors read both.

## Global Constraints

- Branch `sprint-7/dashboard` (from the tip of PR #6, by team decision). Never push to `main`; one PR for the branch.
- Baselines before any change (recorded 2026-11-12 on this branch): `pytest -q` 403 passed; `pytest -m "not integration"` 392 passed, 11 deselected.
- Do not change: `run_tracking.py` CLI behaviour and output files; existing `/api/*` routes and response shapes (adding optional fields is allowed); `/ws/videos/{id}` message types and fields; event schema 1.0; queue-logic defaults. No tuning.
- `ai_engine` imports nothing from `api`. Unit tests torch-free; real-model tests marked `integration`. `ruff check .` and `ruff format --check .` clean.
- No database schema change.
- Python deps pinned in `requirements*.txt`; npm deps exact (`npm install -E`), `package-lock.json` committed. Pick each npm package's newest stable version published at least two weeks before install; record versions in README.
- Heads blurred in every image the dashboard can show. Never commit CAD frames, videos or screenshots; screenshots go to `outputs/s7_screens/` (gitignored by `outputs/**`).
- API stays bound to 127.0.0.1.
- Never fabricate a number: docs, dashboard and reports show only values from runs or files on disk; anything not run is "pending" with the reason.
- UI copy that is fixed by the spec (exact strings): "Unknown", "Insufficient data", "Unvalidated", "Not the queue length", "Raw count N · smoothed over 2 s" (2 = the camera's smoothing window), "No runs yet — upload a video", "Accuracy data not generated — run scripts/export_accuracy.py", "Not measured yet", status chip words "Live" / "Live · congested" / "Offline", event labels "Joined the queue" / "Service started" / "Service completed" / "Left without service" / "Left the area". No emoji.
- Design tokens (section D) are defined once in `dashboard/app/globals.css` as Tailwind theme variables and used everywhere by name.

## Review Focus

1. **Run ends while an MJPEG client is connected** — the stream must end promptly (not hang the server thread or the worker), and `preview.jpg` must then 404. Test in Task 3 (`test_mjpeg_ends_when_run_ends`).
2. **Cancel during a long real-time wait** (e.g. a 30 s clip, cancel at 2 s) — the job must stop within ~0.5 s, not after the clip's remaining duration. Test in Task 1 (`test_realtime_wait_interrupted_by_cancel`).
3. **Old runs whose `options` JSON has no `realtime` key** — list and detail must still validate and report `realtime: false`. Test in Task 1 (`test_old_run_without_realtime_option`).
4. **Path traversal or unknown routes on the dashboard catch-all** (`/../api/main.py`, `/%2e%2e/`, `/nope`) — never serve files outside `dashboard/out`, unknown paths get the export's `404.html` with status 404, and `/api/*` and `/ws/*` are never shadowed. Test in Task 6 (`test_dashboard_catch_all_safety`).
5. **Live page opened on a finished or unknown run id** — the snapshot arrives then the server closes (normal, or 4404): the page shows the final state / "Run not found" instead of reconnecting forever. Test in Task 8 (`useLiveSocket` "does not reconnect after a final status or 4404").

---

### Task 1: Real-time processing option (A1)

**Files:**
- Modify: `api/models.py` (`RunOptions`), `api/main.py` (`upload`), `api/jobs.py` (`JobManager`)
- Test: `tests/test_api_realtime.py`

**Interfaces:**
- Produces: `RunOptions.realtime: bool = False` (so `options.realtime` is in every run record; old rows default to false). Form field `realtime: bool = False` on `POST /api/videos`, stored as `options["realtime"]`.
- Produces: `JobManager(settings, tracker_factory, clock: Callable[[], float] = time.monotonic, wait: Callable[[float], object] | None = None)`. `wait(seconds)` defaults to `self._interrupt.wait(seconds)` where `self._interrupt: threading.Event` is set by `cancel()` of the current job and by `stop()`, and cleared at the start of each job.

- [ ] **Step 1: Write failing tests** in `tests/test_api_realtime.py` (reuse fixtures/helpers from `tests/test_api.py` and `tests/test_api_jobs.py`):
  - `test_realtime_option_stored_and_exposed`: upload with `realtime="true"` → response and `GET /api/videos/{id}` have `options["realtime"] is True`; default upload → `False`; `realtime="maybe"` → 422.
  - `test_old_run_without_realtime_option`: insert a row via `db.create_video(conn, "a.mp4", "walker", {"frame_stride": 1, "blur_heads": True, "max_frames": None})` → `GET /api/videos` and `/api/videos/{id}` return 200 with `options["realtime"] is False`.
  - `test_realtime_outputs_identical`: drive `JobManager` directly with a fake clock (`FakeClock.now`, and a `wait(seconds)` that advances the clock by `seconds`), `WalkerTracker`, the 60-frame synthetic clip; run once with `realtime` False and once True; assert the waits CSV, timeseries CSV and events JSONL files under each run's output dir are byte-identical, and the DB `list_events` / `list_timeseries` / `list_waits` rows match (ignoring `video_id`).
  - `test_realtime_paces_to_video_time`: with the fake clock, the realtime run's recorded wait calls sum to ≈ the clip's last `timestamp_s` (within one frame), and the non-realtime run makes no wait calls.
  - `test_realtime_never_accumulates_lag`: fake tracker whose `update` advances the fake clock by 2 frame-durations per frame → no wait call has a positive argument after the run falls behind (wait is skipped, never negative).
  - `test_realtime_wait_interrupted_by_cancel`: real clock, default `wait`, a 30 s synthetic clip (30 fps × 900 frames, small frames), realtime on; after the run reaches `processing`, `POST /cancel`; the run is `cancelled` within 2 s of the cancel.

- [ ] **Step 2: Run** `pytest tests/test_api_realtime.py -v` — expect failures (unknown field / missing kwargs).

- [ ] **Step 3: Implement.** In `_process.on_frame`, after the existing handling: if `options.get("realtime")`, set `start = clock() - update.timestamp_s` on the first frame, then `delay = start + update.timestamp_s - clock()`; call `wait(delay)` only when `delay > 0`. Cancel/stop set `_interrupt` in addition to the existing events.

- [ ] **Step 4: Run** `pytest tests/test_api_realtime.py tests/test_api.py tests/test_api_jobs.py -q` — all pass.

- [ ] **Step 5: Commit** `git commit -m "Add real-time pacing option to video runs"`

### Task 2: Frame image on `FrameUpdate` (A2, engine part)

**Files:**
- Modify: `ai_engine/run_tracking.py` (`FrameUpdate`, `process_video`)
- Test: `tests/test_pipeline.py` (add tests)

**Interfaces:**
- Produces: `FrameUpdate.image: np.ndarray | None = None` (the frame after all drawing, the same array that was written; valid only during the callback — callers copy) and `FrameUpdate.people: tuple[PersonDetection, ...] | None = None`. New keyword `process_video(..., frame_callback_wants_image: bool = False)`; when False both fields stay `None`.

- [ ] **Step 1: Write failing tests:**
  - `test_frame_update_image_only_when_requested`: synthetic clip + `FakeTracker`; default call → every update has `image is None and people is None`; with `frame_callback_wants_image=True` → `image.shape == (h, w, 3)` and `people[0].bbox == (60, 30, 100, 110)`.
  - `test_cli_outputs_identical_with_image_callback`: run `process_video` twice (camera config from `tests/test_api_jobs.CAMERA_YAML`, separate output dirs), once with no callback and once with an `on_frame` that requests images; the annotated MP4, timeseries, zone events, waits, alerts and events JSONL files are byte-identical; metrics JSON equal after removing `generated_at`, `total_runtime_seconds`, FPS fields and `outputs` paths.
- [ ] **Step 2: Run** them — fail.
- [ ] **Step 3: Implement** the two defaulted fields (keep the dataclass frozen; `people` as a tuple) and pass them only when the flag is set.
- [ ] **Step 4: Run** `pytest tests/test_pipeline.py -q` — pass.
- [ ] **Step 5: Commit** `git commit -m "Let process_video callers request the annotated frame"`

### Task 3: Live preview store and routes (A2, API part)

**Files:**
- Create: `api/preview.py`
- Modify: `api/jobs.py` (wire the store), `api/main.py` (routes)
- Test: `tests/test_api_preview.py`

**Interfaces:**
- Consumes: Task 2's `FrameUpdate.image` / `.people`; `ai_engine.visualization.privacy.blur_heads`.
- Produces in `api/preview.py`:
  - `PREVIEW_MAX_WIDTH = 960`, `PREVIEW_JPEG_QUALITY = 70`, `PREVIEW_MIN_INTERVAL_S = 0.2`, `MJPEG_BOUNDARY = "frame"`.
  - `render_preview(image: np.ndarray, people: Sequence[PersonDetection]) -> np.ndarray` — blur heads on a **copy** (never mutates the input), then downscale with `INTER_AREA` to width 960 if wider.
  - `class PreviewStore` (thread-safe): `offer(video_id: int, image, people, now: float) -> bool` (renders + encodes only if ≥ 0.2 s since the last encode for that run; returns whether it encoded); `latest(video_id) -> tuple[int, bytes] | None` (sequence number, JPEG); `drop(video_id) -> None`.
  - `mjpeg_part(jpeg: bytes) -> bytes` — `b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: N\r\n\r\n" + jpeg + b"\r\n"`.
- `JobManager` owns `self.preview = PreviewStore()`, calls `process_video(..., frame_callback_wants_image=True)`, calls `self.preview.offer(video_id, update.image, update.people, clock())` in `on_frame`, and `self.preview.drop(video_id)` in a `finally` around the processing.
- Routes: `GET /api/videos/{id}/preview.jpg` (`image/jpeg`, `Cache-Control: no-store`; 404 `"no preview: run is not processing"` or `"no preview frame yet"`), `GET /api/videos/{id}/preview.mjpg` (`StreamingResponse`, `multipart/x-mixed-replace; boundary=frame`; async generator polls `latest()` every 0.1 s, yields a part when the sequence number changes, stops when the run is no longer processing or `await request.is_disconnected()`; same 404 rules at start). Both 404 for unknown ids like other routes.

- [ ] **Step 1: Write failing tests:**
  - `test_render_preview_blurs_heads_only`: frame of random noise (seed 0) 200×160, person box (60, 30, 100, 110), head fraction 0.2 → the region `[30:46, 60:100]` differs from the input; every pixel outside that region is equal; the input array is unchanged.
  - `test_render_preview_downscales_wide_frames`: 1920×1080 input → output width 960, height 540.
  - `test_store_rate_limit`: offers at now = 0.0, 0.1, 0.25 → encoded True, False, True; `latest` sequence increments only on encode; `drop` → `latest is None`.
  - `test_preview_routes_404_rules`: unknown id → 404; completed run → 404 for both routes; queued run → 404.
  - `test_preview_jpg_while_processing`: `GatedTracker` (from `tests/test_api_jobs.py`) holds the run in `processing` after a frame → `preview.jpg` 200, body starts with `b"\xff\xd8"`, decodes with `cv2.imdecode`.
  - `test_mjpeg_boundary_format`: stream while processing; the first chunk matches `mjpeg_part` framing (`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: <n>\r\n\r\n` and `n` equals the JPEG length) and the response content type is `multipart/x-mixed-replace; boundary=frame`.
  - `test_mjpeg_ends_when_run_ends`: open the stream, release the gate so the run completes; the stream iterator finishes within 3 s; then `preview.jpg` → 404.
  - `test_preview_blurred_even_when_blur_off`: upload with `blur_heads="false"`; `PreviewStore.offer` is called with an image whose head region still contains the sharp pattern, and the stored JPEG decoded differs from that region (spy on `render_preview` or compare to an unblurred encode).
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement** `api/preview.py`, the wiring and the routes.
- [ ] **Step 4: Run** `pytest tests/test_api_preview.py tests/test_api.py tests/test_api_jobs.py tests/test_api_realtime.py -q` — pass.
- [ ] **Step 5: Commit** `git commit -m "Add head-blurred live preview (JPEG and MJPEG) for processing runs"`

### Task 4: Accuracy export and `GET /api/accuracy` (A3)

**Files:**
- Create: `scripts/export_accuracy.py`
- Modify: `api/main.py` (route), `api/settings.py` (`accuracy_path` property = `data_dir / "accuracy.json"`)
- Test: `tests/test_export_accuracy.py`

**Interfaces:**
- Produces: `build_accuracy(outputs_dir: Path, splits_dir: Path, now: datetime) -> dict` and CLI `python scripts/export_accuracy.py [--outputs outputs] [--splits datasets/splits] [--out data/accuracy.json]`.
- Produces the JSON shape (the dashboard's `lib/accuracy-types.ts` mirrors it exactly):

```text
{ schema: "accuracy-1", generated_at, missing_sources: [{section, path, reason}],
  queue_length: null | { source, protocol: {series:"smoothed", window:"from5s", tolerance_seconds, from_seconds},
      per_clip: [{clip, split, mae, rmse, bias, within_one_rate, frames}],
      pooled: {val:{…same metrics…, clips:[…]}, test:{…}} },
  count_errors: null | { source, iou: 0.5, window: "from5s",
      per_split: {all|val|test: {frames, categories: [{key, label, direction:"over"|"under"|"correct"|"other", total, per_frame}]}} },
  changes_not_adopted: null | [{ name, source, sprint, val_mae, test_mae, baseline_val_mae, baseline_test_mae }],
  waiting_time: null | { source, per_clip: [...], pooled: {...} },
  limits: [string] }
```

- Sources (fixed): queue length = `outputs/s6_analysis/queue_640.json`, variant `as_configured`, key `smoothed_from5s`, `frames` = `matched`; splits from `datasets/splits/{val,test}.txt` (lines not starting with `#`). Count errors = `outputs/s6_analysis/errors_640.json`, `as_configured`, `pooled.<split>.iou_0.5.from5s` totals and `mean_per_frame`; category labels and directions: `tp` "Counted correctly" correct; `fp_unmatched` "Counted, no matching labelled box" over; `counted_not_visible` "Counted while out of view (grace)" over; `fp_label:walking` "Counted, labelled walking" over; `fn_no_detection` "Labelled queuer not detected" under; `fn_candidate_dwell` "Still in the dwell time" under; `fn_foot_outside_zone` + `gt_queueing_outside_zone` → "Labelled queuer outside the zone" other (summed); skip the `fp_unmatched_*overlap*` sub-split keys and zero-total `fn_candidate_speed`/`fn_other`. Changes not adopted = "Detector input 960 px" (val/test `smoothed_from5s.mae` of `queue_960.json` vs `queue_640.json`) and the non-baseline variant of `outputs/s6_sweep/chosen_all.json` (name built from the variant key, e.g. "grace 0.75 s, dwell 2.0 s"), with `baseline_*` from that file's `as_configured`. Waiting time = every `outputs/metrics/*_wait_eval.json` if any exist (pass through their metrics per clip; pooled only if the files carry it), else `null` with a `missing_sources` entry reason `"not measured yet (session 3)"`.
- `limits` (text, no typed metrics): clip counts derived from the split files, e.g. f"Queue length is measured on {n_val} val and {n_test} test CAD clips only"; "CAD frame rate is assumed to be 30 fps"; "CAD boxes are looser than the detector's, which inflates the matched-box error categories"; "Waiting-time accuracy is not measured yet" when `waiting_time` is null.
- Route: `GET /api/accuracy` → the file's JSON; 404 `"accuracy data not generated; run python scripts/export_accuracy.py"` when absent; 500-safe on invalid JSON (log, 500 `"accuracy data unreadable"`).

- [ ] **Step 1: Write failing tests** with tiny synthetic files in `tmp_path` mirroring the real key layout (two clips: one val, one test):
  - `test_build_accuracy_reads_numbers_from_files`: per-clip and pooled metrics equal the synthetic input values; each section's `source` names its file.
  - `test_missing_sources_null_sections`: no `errors_640.json` → `count_errors is None` and a `missing_sources` entry for it; other sections still built.
  - `test_waiting_time_absent_then_present`: no wait evals → `waiting_time is None` and limit "Waiting-time accuracy is not measured yet"; with a synthetic `cad_seq25_wait_eval.json` → section present with its numbers.
  - `test_no_typed_numbers`: with all sources missing, the output contains no numeric values except in `generated_at` (walk the dict).
  - `test_accuracy_route`: no file → 404 with the message; written file → 200 equal content; invalid JSON → 500.
- [ ] **Step 2: Run** — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** tests; then run for real: `.venv/bin/python scripts/export_accuracy.py` and check `data/accuracy.json` test pooled MAE is 1.6193, bias 0.9596, within ±1 0.5368 (values from `queue_640.json`).
- [ ] **Step 5: Commit** `git commit -m "Export measured accuracy for the dashboard and serve it at /api/accuracy"`

### Task 5: WebSocket Origin check (A4)

**Files:**
- Modify: `api/settings.py`, `api/main.py` (`live`), `docs/security_review.md`
- Test: `tests/test_api_security.py` (add)

**Interfaces:**
- Produces: `ApiSettings.allowed_origins: tuple[str, ...] | None = None` and `ApiSettings.effective_allowed_origins() -> frozenset[str]`: when `None`, `{f"http://{host}:{port}", f"http://localhost:{port}" if host is loopback, "http://localhost:3000", "http://127.0.0.1:3000"}` (the 127.0.0.1/localhost twins are my call: browsers send whichever name was typed). `QUEUEVISION_ALLOWED_ORIGINS` = comma-separated, trimmed, trailing `/` stripped, empty items ignored. Comparison is exact on scheme://host[:port].
- In `live`: before `accept`, read `websocket.headers.get("origin")`; if present and not allowed → `await websocket.close(code=1008)`; return. No `Origin` → allowed.

- [ ] **Step 1: Write failing tests:** `test_ws_origin_allowed_default` (`Origin: http://localhost:3000` → snapshot received); `test_ws_origin_rejected` (`Origin: http://evil.example` → `WebSocketDisconnect` with `code == 1008`, no snapshot); `test_ws_no_origin_allowed`; `test_allowed_origins_env_parsing` (`" http://a:1/, ,http://b:2"` → `{"http://a:1", "http://b:2"}`, default not added).
- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** `pytest tests/test_api_security.py tests/test_api.py -q` — pass.
- [ ] **Step 5:** Add an "Origin check (Sprint 7)" item to `docs/security_review.md` (what it blocks: cross-site WebSocket reads of live data from a page the user visits; what it doesn't: non-browser clients, which can send any Origin; still loopback-only, no login). **Commit** `git commit -m "Reject WebSocket connections from unlisted browser origins"`

### Task 6: Serve the dashboard export at `/`, legacy page at `/legacy` (A5)

**Files:**
- Modify: `api/main.py` (replace the static mount), `api/settings.py` (`dashboard_dir: Path`, env `QUEUEVISION_DASHBOARD_DIR`, default `PROJECT_ROOT / "dashboard" / "out"`), `tests/test_api.py:292` and `tests/test_api_security.py:329` only if the route order they rely on changes (explain in the commit message)
- Test: `tests/test_api_dashboard.py`

**Interfaces:**
- `/legacy` → `StaticFiles(directory=api/static, html=True)` mounted at `/legacy` (the page uses absolute `/api` and `/ws` paths, so it works unchanged).
- Catch-all `GET /{path:path}` registered last (after the upload router): resolve `dashboard_dir / path` with `.resolve()` and require it stays under `dashboard_dir.resolve()`; serve, in order, the file itself, `path + ".html"`, `path/index.html`; else `404.html` with status 404 (or plain 404). Paths starting with `api/` or `ws/` → plain JSON 404 (never served as HTML). When `dashboard_dir` does not exist: `/` returns a short HTML page: "Dashboard not built. Run: cd dashboard && npm ci && npm run build — then reload. The old test page is at /legacy."

- [ ] **Step 1: Write failing tests** (temporary `dashboard_dir` with `index.html`, `live.html`, `404.html`, `_next/static/x.js`):
  - `test_dashboard_routes`: `/` → index; `/live?id=3` → live.html; `/_next/static/x.js` → JS content type.
  - `test_dashboard_catch_all_safety`: `/../api/main.py`, `/%2e%2e/%2e%2e/etc/passwd`, `/nope` → 404 and never file content from outside; `/api/health` still JSON 200; `/api/nope` → JSON 404.
  - `test_dashboard_missing_shows_build_message`: no dir → `/` 200 HTML containing "npm run build" and "/legacy".
  - `test_legacy_page_served`: `/legacy/` → the old page (contains "QueueVision").
- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** full `pytest -q` — pass.
- [ ] **Step 5: Commit** `git commit -m "Serve the dashboard export at / and move the test page to /legacy"`

### Task 7: Dashboard scaffold, tokens, API types and CI (B1, D)

**Files:**
- Create: `dashboard/` (`package.json`, `package-lock.json`, `next.config.ts`, `tsconfig.json`, `eslint.config.mjs`, `postcss.config.mjs`, `vitest.config.ts`, `vitest.setup.ts`, `playwright.config.ts`, `app/layout.tsx`, `app/globals.css`, `app/page.tsx` placeholder, `app/not-found.tsx`, `.gitignore`), `scripts/export_openapi.py`, `dashboard/openapi.json`, `dashboard/lib/api-types.ts`
- Modify: `.github/workflows/ci.yml` (new `dashboard` job; openapi staleness step in `lint-and-unit`), `api/models.py` + `/api/cameras` (additive `CameraInfo.max_queue_length: float | None = None`, `CameraInfo.smoothing_window_s: float | None = None`, needed for the alert line and "smoothed over N s")
- Test: `tests/test_api.py` (cameras test asserts the two new fields for `walker.yaml`)

**Interfaces:**
- `next.config.ts`: `output: "export"`, `images: { unoptimized: true }`, `trailingSlash: false`; `rewrites()` only when `NODE_ENV === "development"` (`/api/:path*` → `http://127.0.0.1:8000/api/:path*`; rewrites are unsupported in export builds, so they must not be active there).
- npm scripts: `dev` (`next dev -p 3000`), `build`, `lint`, `typecheck` (`tsc --noEmit`), `test` (`vitest run`), `e2e` (`playwright test`), `gen:api` (`openapi-typescript openapi.json -o lib/api-types.ts`), `gen:api:live` (same from `http://127.0.0.1:8000/openapi.json`).
- `scripts/export_openapi.py` writes `create_app(ApiSettings(...tmp...)).openapi()` as sorted, indented JSON to `dashboard/openapi.json`. Deviation from the spec (my call): types are generated from this committed file rather than a running server, so CI can check staleness without starting the API; `gen:api:live` keeps the spec's live path.
- CI: `lint-and-unit` runs `python scripts/export_openapi.py && git diff --exit-code dashboard/openapi.json`; new `dashboard` job (Node 22, `working-directory: dashboard`): `npm ci`, `npm run gen:api && git diff --exit-code lib/api-types.ts`, `lint`, `typecheck`, `test`, `build`.
- `globals.css`: Tailwind `@theme` tokens for every section-D colour (names: `page`, `surface`, `border`, `border-input`, `text`, `text-2`, `text-3`, `primary`, `primary-hover`, `primary-soft`, `warn`, `warn-soft`, `warn-border`, `warn-text`, `ok`, `ok-soft`, `chip`, `danger`, `bar-val`, `bar-test`), radii, and `font-variant-numeric: tabular-nums` on numbers. Fonts via `next/font/google` (Inter 400/500/600, Space Grotesk 500/600; self-hosted at build).
- `lib/contrast.test.ts`: computes WCAG contrast for every text/background pair used (text, text-2, text-3 on page and surface; primary on surface and primary-soft; white on primary; warn-text on warn-soft; ok on ok-soft; danger on surface; text on chip) and asserts ≥ 4.5. If a spec pair fails, report it rather than silently changing the colour.

- [ ] **Step 1:** Scaffold with exact versions; add the files above; placeholder page renders "QueueVision".
- [ ] **Step 2:** Write `lib/contrast.test.ts`; run `npm test` — record any failing pair.
- [ ] **Step 3:** Add `scripts/export_openapi.py`, the camera fields (test first), regenerate `openapi.json` and `lib/api-types.ts`.
- [ ] **Step 4: Verify** `npm run lint && npm run typecheck && npm test && npm run build` succeed and `dashboard/out/index.html` exists; `pytest -q` passes.
- [ ] **Step 5: Commit** `git commit -m "Scaffold the Next.js dashboard with design tokens, generated API types and CI"`

### Task 8: Shared client code (B2: data layer)

**Files:**
- Create: `dashboard/lib/api.ts`, `dashboard/lib/live-types.ts`, `dashboard/lib/accuracy-types.ts`, `dashboard/lib/useLiveSocket.ts`, `dashboard/lib/format.ts`, `dashboard/app/providers.tsx` (QueryClientProvider)
- Test: `dashboard/lib/useLiveSocket.test.ts`, `dashboard/lib/format.test.ts`, `dashboard/lib/api.test.ts`

**Interfaces:**
- `lib/api.ts`: `class ApiError extends Error { status: number }`; `apiGet<T>(path: string): Promise<T>` (message = response `detail` when JSON, else `"Request failed (STATUS)"`, network failure → `"Cannot reach the QueueVision API"`); typed wrappers `listRuns`, `getRun(id)`, `getTimeseries(id)`, `getEvents(id)`, `getWaits(id)`, `getAlerts(id)`, `getCameras()`, `getAccuracy()`, `uploadVideo(form: FormData)`, `cancelRun(id)`, `deleteRun(id)`; URL helpers `previewMjpegUrl(id)`, `videoUrl(id)`, `reportUrl(id)`, `timeseriesCsvUrl(id)`. Types from `lib/api-types.ts`.
- `lib/live-types.ts`: `SnapshotMessage`, `ProgressMessage`, `EventMessage`, `StatusMessage`, `LiveMessage` union — fields exactly as `docs/api.md` § WebSocket and `api/jobs.py` (`progress_message`, `event_message`, `status_message`).
- `lib/useLiveSocket.ts`: `useLiveSocket(videoId: number | null, opts?: { wsBase?: string; WebSocketImpl?: typeof WebSocket; now?: () => number }) => { connectionState: "connecting" | "open" | "closed" | "ended" | "not_found"; lastMessageAt: number | null; run: Run | null; progress: ProgressMessage | null; events: EventMessage["event"][]; status: StatusMessage | null }`. Base URL: `NEXT_PUBLIC_API_WS` if set, else same origin (`ws(s)://location.host`) when not on port 3000, else `ws://127.0.0.1:8000`. Backoff 1, 2, 4, 8, 15, 15 … s, reset after a successful open. Every `snapshot` replaces `run` and `progress`; `events` keep accumulating across reconnects, deduplicated by `event_type + timestamp_s + (person_key ?? track_id)`. After a `status` in a final state, or close code 4404 → `ended` / `not_found`, no reconnect.
- `lib/format.ts`: `formatClock(iso: string | number, tz?: string): string` → `"HH:MM:SS NPT"` style using `Intl.DateTimeFormat` with `timeZoneName: "short"`, mapping the `Asia/Kathmandu` short name (`GMT+5:45`) to `"NPT"`; `formatSeconds(s: number | null): string` (`"1 min 05 s"`, `"42 s"`); `formatNumber(n, digits)`. No other computation.

- [ ] **Step 1: Write failing tests** (fake WebSocket class with `open()`, `message(obj)`, `close(code)` helpers; fake timers):
  - reconnects after close with delays 1 s, 2 s, 4 s (advance timers; count constructions);
  - applies the snapshot on each reconnect (second snapshot replaces `run`);
  - "does not reconnect after a final status or 4404" (`ended`, `not_found`);
  - `lastMessageAt` updates on every message;
  - `formatClock("2026-11-12T06:15:00Z", "Asia/Kathmandu")` → `"12:00:00 NPT"`; `apiGet` turns `{detail:"x"}` 404 into `ApiError("x", 404)`.
- [ ] **Step 2: Run** `npm test` — fail. **Step 3: Implement.** **Step 4: Run** `npm test && npm run typecheck` — pass.
- [ ] **Step 5: Commit** `git commit -m "Add typed API client, live WebSocket hook and formatting helpers"`

### Task 9: Shared components and app shell (B2 components, D)

**Files:**
- Create: `dashboard/components/` — `AppHeader.tsx` (desktop nav + phone bottom bar), `StatusChip.tsx`, `MetricCard.tsx`, `QueueLengthCard.tsx`, `WaitCard.tsx`, `StaleBanner.tsx`, `AlertBanner.tsx`, `QueueChart.tsx`, `EventFeed.tsx`, `PreviewPanel.tsx`, `RunsTable.tsx`, `WaitsTable.tsx`, `UploadForm.tsx`, `EmptyState.tsx`, `icons.tsx` (inline SVG); modify `app/layout.tsx`
- Test: `dashboard/components/WaitCard.test.tsx`, `StaleBanner.test.tsx`, `StatusChip.test.tsx`, `EventFeed.test.tsx`

**Interfaces (props):**
- `StatusChip({ status: "live" | "congested" | "offline" | "queued" | "processing" | "completed" | "failed" | "cancelled" })` — words always shown: Live, Live · congested, Offline, Queued, Processing, Completed, Failed, Cancelled.
- `WaitCard({ label: string; valueS: number | null; lowS: number | null; highS: number | null; status: string | null; unvalidated: boolean })` — engine statuses (from `ai_engine/queue/waiting_time.py`): prediction `"ok" | "unknown"`, service rate `"ok" | "insufficient_data"`. `"unknown"` → "Unknown", `"insufficient_data"` → "Insufficient data", `null` (no queue yet) → "Unknown"; never show 0 for these. `"ok"` → value + "range LOW–HIGH"; "Unvalidated" tag when `unvalidated`. The Live page's Service rate card uses the same mapping.
- `StaleBanner({ lastMessageAt: number | null; connectionState; now?: () => number; children })` — stale when closed or no message for 5 s; greys children (`aria-disabled`, reduced opacity) and shows "Last update HH:MM:SS".
- `AlertBanner({ reasons: string[] })` — Acknowledge `<button>` hides it locally; `title`/tooltip "Hides this banner on this screen only; nothing is recorded".
- `QueueChart({ points: {t: number; smoothed: number | null; raw?: number | null}[]; alertLimit?: number | null; markers?: {t: number; kind: "service_start"}[]; dwellBandS?: number; compact?: boolean })` — Recharts; smoothed solid line, raw dotted, alert limit dashed, service starts as triangle markers, optional shaded band 0–5 s labelled "first 5 s: dwell and smoothing"; `compact` = sparkline.
- `EventFeed({ events })` — labels per Global Constraints mapping: `queue_join`, `service_start`, `service_complete`, `queue_abandon`, `queue_exit`+`zone_exit` → "Left the area"; `zone_enter`, `congestion_*` shown as "Entered the area" / "Congestion started" / "Congestion ended"; unknown types ignored; each kind has a distinct marker shape (circle, triangle, square, diamond, ring).
- `PreviewPanel({ videoId: number; active: boolean })` — 3:2 box; `<img src=previewMjpegUrl>`; on `error` retries after 2 s while `active`; placeholder "No preview frame yet"; legend matches what `ai_engine/visualization/metrics_overlay.py` actually draws (the spec's legend says "dashed yellow" and "service line = blue", but the engine draws solid boxes, a magenta service point and a blue queue zone, and changing the drawing would change CLI output, which rule 3 forbids): In queue = orange box (`#FF8C00`), Joining (dwell) = yellow box (`#FFE600`), Not in queue = green box (`#00C800`), At the service point / service line = magenta (`#FF00FF`), Queue zone = blue outline (`#00A0FF`). Swatches are bordered so they are not colour-only (text label beside each).
- `UploadForm({ cameras; onStarted(run) })` — `<label>`ed file, camera select, "Every Nth frame" number 1–10, "Blur heads" checkbox checked, "Process in real time" checkbox, Start `<button>`; shows the API's error message.
- Layout: max width 1360 px, 24 px padding, cards 12 px radius / 20 px padding / 1 px border, no shadows; controls 40 px (44 px below 640 px); two-column grids collapse below 900 px; tables in `overflow-x-auto` wrappers; header with logo mark, "QueueVision", nav (Overview `/`, Live camera `/live`, Recorded runs `/runs`, Accuracy `/accuracy`; active on primary-soft), "localhost" chip; phone bottom bar with four icon+label links.

- [ ] **Step 1: Write failing tests:** WaitCard (value + range; "Unknown"; "Insufficient data"; no "0" rendered for those; "Unvalidated" present); StaleBanner (fresh → no banner; 5 s old → "Last update" text; closed → banner); StatusChip renders words for every status; EventFeed maps the five labels and ignores an unknown type.
- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** `npm test && npm run lint && npm run typecheck` — pass.
- [ ] **Step 5: Commit** `git commit -m "Add dashboard shell and shared components"`

### Task 10: Overview page `/`

**Files:** Modify `dashboard/app/page.tsx`; create `dashboard/components/ProcessingRunCard.tsx`, `AccuracySummaryCard.tsx`
**Interfaces:** consumes `listRuns`, `getAccuracy`, `useLiveSocket`, Task 9 components. Data rules: "runs processing" = runs with `status === "processing"`; each gets a `ProcessingRunCard` (own `useLiveSocket`; sparkline from received progress `queue_length` values; "updated HH:MM:SS"; links to `/live?id=`). Latest completed runs card: 3 most recent `completed`. Recent recorded runs: `RunsTable` of the 10 most recent. Accuracy card: test pooled MAE, bias, within ±1 (as %) from `/api/accuracy` with frame counts; waiting time "Not measured yet" when `waiting_time` is null; 404 → "Accuracy data not generated — run scripts/export_accuracy.py". "Upload a video" button links to `/runs#upload`. `listRuns` refetches every 5 s.
- [ ] Steps: component test for `AccuracySummaryCard` (renders the numbers given, and the not-generated message on `ApiError` 404) → fail → implement page → `npm test && npm run build` → commit `"Add Overview page"`.

### Task 11: Live page `/live?id=`

**Files:** Create `dashboard/app/live/page.tsx` (wrap the `useSearchParams` reader in `<Suspense>` — required for static export), `dashboard/components/LiveRunView.tsx`
**Interfaces:** consumes `useLiveSocket`, `getTimeseries` (initial chart history, then append progress points), `getCameras` (alert limit = `max_queue_length`, smoothing window), `cancelRun`, Task 9 components. Without `id`: pick the newest `processing` run and replace the URL; none → "No run is processing — upload a video" with a link to `/runs`. Chip: Offline when socket not open, Live · congested when `alert_active`, else Live; final run → its final status chip. Right column per spec (Queue length 64 px + "Raw count N · smoothed over W s"; Predicted wait `WaitCard` with Unvalidated; Service rate (per min, or the engine's status words); People in frame + "Not the queue length"). Cancel button disabled unless processing; shows API error text.
- [ ] Steps: component test for `LiveRunView` with a mocked hook (alert banner appears with reasons when `alert_active`; "Not the queue length" present; raw count line) → fail → implement → `npm test && npm run build` → commit `"Add Live run page"`.

### Task 12: Recorded runs `/runs` and `/run?id=`

**Files:** Create `dashboard/app/runs/page.tsx` (UploadForm with `id="upload"` + RunsTable of all runs), `dashboard/app/run/page.tsx` (Suspense), `dashboard/components/RunDetail.tsx`
**Interfaces:** after upload, go to `/live?id=` when `realtime` was ticked, else `/run?id=`. Run detail: header (status chip, options summary "every Nth frame · heads blurred/not · real time/fast"), links Report (JSON) → `/api/videos/{id}/report` and Time series (CSV) → new additive route `GET /api/videos/{id}/timeseries.csv` (added in this task, test first in `tests/test_api.py`: serves the run's `outputs.timeseries` file as `text/csv` with `Content-Disposition: attachment`; 404 when the run has no report or the file is missing; add it to `lib/api.ts` as `timeseriesCsvUrl`). Delete `<button>` with an inline confirm step (no browser `confirm()` dialog). `<video controls src=videoUrl>` (seeking works via existing Range support); summary cards from `run.summary` fields only (joined, service started, left without service and its % only if the summary provides it, congestion alerts, measured wait mean/median/P90 with Unvalidated) — read the field names from a real report's `queue` section; where a figure is absent show "Not reported". Chart: timeseries + `service_start` markers from events + 5 s band. Tabs Waits / Events / Alerts with counts (`role="tablist"`).
- [ ] Steps: component test for `RunDetail` summary cards (values from a fixture report; "Not reported" when a field is absent) → fail → implement → `npm test && npm run build` → commit `"Add Recorded runs pages"`.

### Task 13: Accuracy page `/accuracy`

**Files:** Create `dashboard/app/accuracy/page.tsx`, `dashboard/components/AccuracyView.tsx`
**Interfaces:** consumes `getAccuracy` / `accuracy-types.ts`. Headline cards (test pooled MAE, bias, within ±1, waiting time or "Not measured yet"), each with frames/sample size. Error by clip: horizontal bars of per-clip MAE, val `bar-val`, test `bar-test`, legend. Where the count goes wrong: bars of `per_frame` for `over` and `under` categories of the `test` split (toggle all/val/test), grouped Over-count / Under-count. Changes tried and not adopted: table only when `changes_not_adopted` is non-null. Limits list from `limits`. Null sections show which source is missing (from `missing_sources`).
- [ ] Steps: component test (renders table only when present; per-clip bars count equals clips; missing section message) → fail → implement → `npm test && npm run build` → commit `"Add Accuracy page"`.

### Task 14: End-to-end tests, browser checks and measurements (C)

**Files:** Create `dashboard/e2e/dashboard.spec.ts`, `dashboard/e2e/server.ts` (spawns/stops `python -m api` with a temp `QUEUEVISION_DATA_DIR`, `QUEUEVISION_DASHBOARD_DIR=dashboard/out`, port 8765, waits for `/api/health`); `scripts/measure_realtime.py`
**Interfaces:** Playwright uses the real model (no fake-tracker mode exists) on `datasets/raw/cad_seq25.mp4` with `max_frames=150` and `camera_id=cad_seq25`; tests skip with a clear message if the clip is absent (CAD is not in CI). Same-origin serving (dashboard built first). Chromium and WebKit projects.
- [ ] **Step 1:** `upload in real time → Live updates → completion → video plays and seeks`: upload via `/runs` with real time ticked; Live page shows a queue length within 15 s and the preview `<img>` loads; wait for Completed; `/run?id=` video `readyState >= 1`, set `currentTime = 3`, expect `seeked`.
- [ ] **Step 2:** `API restart → offline → recovers without reload`: on the Live page of a running real-time run, stop the server process, expect "Offline" chip and "Last update"; restart it (same data dir; the interrupted run becomes failed per `mark_interrupted`); expect the socket to reconnect and the page to show the run's final status without `page.reload()`.
- [ ] **Step 3:** `phone 390×844`: for each page, `document.documentElement.scrollWidth <= 390`, bottom nav visible, every button/link/input in the viewport after scrolling (`toBeInViewport` after `scrollIntoViewIfNeeded`).
- [ ] **Step 4:** Run `npm run build && npm run e2e`; record results.
- [ ] **Step 5:** Manual check in Chrome (Claude in Chrome) of all four pages at desktop and phone width; screenshots to `outputs/s7_screens/`; look at each. Safari: run the WebKit Playwright project and record it as WebKit, and mark a real Safari check "pending: needs a person" unless one is done.
- [ ] **Step 6:** `scripts/measure_realtime.py --video datasets/raw/cad_seq25.mp4 --camera cad_seq25`: runs the API job path twice in-process (realtime off/on, real tracker, MPS), records wall time of each, clip duration, per-frame overhead = (realtime wall − clip duration) / frames when positive, and asserts waits CSV / timeseries CSV / events JSONL byte-equal; prints a JSON summary saved to `outputs/s7_realtime/summary.json`.
- [ ] **Step 7: Commit** `git commit -m "Add Playwright end-to-end tests and real-time measurement script"`

### Task 15: Docs and session report (E)

**Files:** Modify `README.md`, `docs/api.md`, `docs/architecture.md`, `docs/security_review.md`, `docs/testing_report.md`; create `docs/sprint7_session2_report.md`; add `npm run demo`-style one-command start (`scripts/demo.sh`: build dashboard if `out/` missing, export accuracy, start `python -m api`).
- [ ] **Step 1:** `docs/api.md`: `realtime` field, `preview.jpg`/`preview.mjpg`, `/api/accuracy`, Origin check, `/legacy`, new optional `CameraInfo` fields — examples copied from a real run's responses.
- [ ] **Step 2:** README (build, dev, demo command, pinned versions incl. Node), architecture (dashboard, serving, preview data flow), testing report (baselines vs final counts, Vitest/Playwright results, browser checks, measurements from Task 14).
- [ ] **Step 3:** Session report per spec E: each item in A–C marked Completed / Partially completed / Not completed, screenshot paths, issues, what session 3 needs.
- [ ] **Step 4: Verify** `pytest -q`, `ruff check .`, `ruff format --check .`, `cd dashboard && npm run lint && npm run typecheck && npm test && npm run build` all pass. **Commit** `git commit -m "Document Sprint 7 session 2"`; push branch; open PR against `sprint-6/error-analysis-and-labelling`.
