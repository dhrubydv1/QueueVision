# Sprint 8 session 2: camera engine and API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start and stop a live camera session (webcam, RTSP or looped clip) from the API. One supervised worker process runs the queue engine and streams results into the existing tables, the existing Live page and the existing WebSocket.

**Architecture:** `ai_engine/live/` holds the camera sources and the live loop (`run_live`), which reuses `FrameProcessor` with no video writer. The new `worker/` package runs one camera per OS process and posts batches, previews, heartbeats and a final report to loopback-only, token-protected ingest routes. The API's `LiveSupervisor` spawns and watches the worker. The API stays the only database writer and republishes through `JobManager`'s existing pub/sub. Schema v3 adds `videos.kind` and `videos.source_kind`.

**Tech Stack:** Python 3.11, FastAPI, SQLite, OpenCV, Ultralytics YOLO11n + ByteTrack, stdlib `urllib.request` and `subprocess`. The dashboard is Next.js; only `RunDetail.tsx` changes. Tests use pytest, Vitest and Playwright.

**Spec:** [docs/superpowers/specs/2026-11-23-sprint8-session2-cameras-design.md](../specs/2026-11-23-sprint8-session2-cameras-design.md). It was derived from the brief's summary, not the team's text. The shared rules in [2026-11-13-sprint8-brief.md](../specs/2026-11-13-sprint8-brief.md) apply.

## Global Constraints

- Branch `sprint-8/cameras` from `main` `aba0024` (design commit `58ebb44`). Don't push until the user asks. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Baselines (Task 1 re-runs and records them): `pytest -q` 576; `pytest -q -m "not integration"` 565 passed / 11 deselected; `ruff check .` and `ruff format --check .` clean (166 files); dashboard `npm run lint`, `npm run typecheck`, `npm run build` clean; `npm test` 142; `npm run e2e` 14 (port 8765, local only, CAD clip required).
- Contracts must not break: the `run_tracking.py` CLI and its outputs; every existing `/api/*` route and response field; `/ws/videos/{id}` message types and fields (optional additions only); event schema 1.0; queue-logic defaults. **Do not tune anything.**
- Dependency direction: `api → ai_engine`, `worker → ai_engine`. `ai_engine` imports neither. `worker` never imports `api`; it talks HTTP only.
- No new Python or npm dependency. If one becomes unavoidable, pin it and record it in the report.
- An RTSP URL appears only in the worker's memory, read from the env var named in `live.rtsp_url_env`. It must never appear in the DB, logs, API responses, the dashboard, `argv` or error strings.
- A live session writes no image or video to disk. Its output folder holds only the time-series CSV and the metrics JSON. Previews are `frame` (default) or `boxes`, never `none`.
- One model job at a time. While a live session runs, `JobManager` starts no uploads, which stay queued. A session cannot start while an upload is processing (`409`).
- Server and ingest are loopback only. The ingest token is per run, passed in the child's env `QUEUEVISION_WORKER_TOKEN`, and compared with `hmac.compare_digest`.
- Unit tests are torch-free (fake source, clock, tracker, HTTP). Real-model tests are marked `integration`. Ruff clean.
- CAD material is never committed. Measurement output goes in `outputs/s11_live/`; add `outputs/s11_*` to `.gitignore` if it isn't covered.
- Every number in docs and reports comes from a command that was run. Whatever can't be run (webcam without permission) is marked **pending, needs a person**, with the exact command.

## Review Focus

1. **Webcam permission denied, or no camera at that index.** On macOS, OpenCV may open the device and then deliver no frames. The run must end `failed` with `camera_unavailable` within 10 s, not hang as `processing`. Test: Task 3 `test_no_first_frame_fails_with_camera_unavailable`.
2. **Stop pressed while the worker is still loading the model, or pressed twice.** The first stop ends the run (`completed`, `stopped_by: operator`, zero frames allowed). The second returns `409`. Test: Task 6 `test_stop_during_startup_and_twice`.
3. **The API restarts while a worker is running.** The worker exits by itself within 15 s, and the new API marks the old run `failed`. No orphan process is left. Tests: Task 4 `test_exits_when_api_unreachable`, Task 6 `test_startup_fails_leftover_live_run`.
4. **An upload is submitted during a live session.** It stays `queued` and starts after the session stops. Test: Task 6 `test_upload_waits_for_live_session`.
5. **Worker exception mid-run** (tracker or OpenCV error). The final status is `failed` with a fixed reason string. No traceback text or URL reaches the DB or the API. Test: Task 4 `test_engine_error_sends_failed_final_without_url`.

---

### Task 1: Baselines, schema v3 and run-record fields

**Files:**
- Modify: `api/db.py` (add `_migrate_2_to_3`, append `(3, _migrate_2_to_3)` to `MIGRATIONS`; `create_video` gains keyword-only `kind: str = "upload"` and `source_kind: str | None = None`)
- Modify: `api/models.py` (`Run.kind: Literal["upload", "live"] = "upload"`, `Run.source_kind: Literal["webcam", "rtsp", "clip"] | None = None`; `RunOptions.process_fps: float | None = None`)
- Modify: `dashboard/openapi.json`, `dashboard/lib/api-types.ts` (regenerate with the project's existing command; additive only)
- Test: `tests/test_db.py` (or the existing migration test file), `tests/test_api.py`

**Interfaces:**
- Produces: `db.create_video(conn, original_name, camera_id, options, *, kind="upload", source_kind=None) -> int`. Run records carry `kind` and `source_kind`, because `run_record` copies `Run.model_fields`.

- [ ] **Step 1:** Run every baseline command in Global Constraints. Save the raw output to `outputs/s11_baseline/` and write the counts at the top of `.superpowers/sdd/2026-11-23-sprint8-cameras/task-1-report.md`.
- [ ] **Step 2: Failing tests.**
  - `test_migrate_2_to_3_adds_kind_and_backs_up`: a v2 database with one row. After `init_db`, `schema_version == "3"`, the row has `kind == "upload"` and `source_kind is None`, and a `.bak-v2-*` file exists.
  - `test_create_video_live_kind`: `create_video(..., kind="live", source_kind="clip")` round-trips.
  - `test_run_record_has_kind`: `GET /api/videos/{id}` returns `"kind": "upload"` for an upload, and every pre-existing key is unchanged.
- [ ] **Step 3:** Implement the migration (`ALTER TABLE videos ADD COLUMN kind TEXT NOT NULL DEFAULT 'upload'`; `ADD COLUMN source_kind TEXT`) and the model fields. Regenerate the OpenAPI types.
- [ ] **Step 4:** `pytest -q -m "not integration"` passes, plus `npm run typecheck`.
- [ ] **Step 5:** Commit: "Schema v3: videos.kind and source_kind; additive run fields".

### Task 2: Camera `live:` settings, demo configs and camera listing

**Files:**
- Modify: `ai_engine/queue/camera_config.py` (new frozen `LiveSettings`; `CameraConfig.live: LiveSettings | None = None`; parse and validate a top-level `live` key)
- Create: `configs/cameras/webcam.yaml` (`live: source: webcam`; a placeholder queue zone `0.2,0.35 0.8,0.35 0.9,0.95 0.1,0.95` and a service line; `description` says "placeholder zone: redraw with draw_zone.py before the demo")
- Modify: `configs/cameras/cad_seq25.yaml` (add only `live: {source: clip, clip: datasets/raw/cad_seq25.mp4}`)
- Modify: `api/models.py` (`CameraInfo.live_source: Literal["webcam","rtsp","clip"] | None = None`, `CameraInfo.live_ready: bool = False`), `api/routes/cameras.py`
- Modify: `ai_engine/tools/draw_zone.py`, only if `--video 0` does not already open camera index 0 (use `parse_source`)
- Test: `tests/test_camera_config.py`, `tests/test_api.py`, `tests/test_draw_zone.py`

**Interfaces:**
- Produces:
  - `LiveSettings(source: str, webcam_index: int = 0, rtsp_url_env: str | None = None, clip: str | None = None, process_fps: float = 10.0, preview_privacy_mode: str = "frame", max_session_s: float = 7200.0)`.
  - `LiveSettings.clip_path(self) -> Path` resolves relative paths against `PROJECT_ROOT`.
  - `LIVE_SOURCES = ("webcam", "rtsp", "clip")`.

- [ ] **Step 1: Failing tests.**
  - `test_live_section_parsed` covers all three sources.
  - `test_live_absent_is_none` runs over every existing CAD config except `cad_seq25`.
  - `test_live_rejects` raises `CameraConfigError` for each of:
    - an unknown source;
    - `rtsp` without `rtsp_url_env`;
    - `rtsp_url_env` not matching `^[A-Z_][A-Z0-9_]*$`, for example a value that looks like a URL;
    - `clip` without `clip`;
    - `preview_privacy_mode: none`;
    - `process_fps` 0 or above 30;
    - `max_session_s` ≤ 0;
    - an unknown key inside `live`.
  - `test_cameras_lists_live_fields`:
    - `webcam` → `live_source "webcam"`, `live_ready true`.
    - `cad_seq25` → `"clip"`, ready when the file exists.
    - An rtsp camera whose env var is unset → `live_ready false`. Set the var to `rtsp://user:secret@host/x` → `true`, and the response body contains neither `secret` nor `rtsp://`.
    - A camera without `live` → `null` / `false`.
  - `test_draw_zone_accepts_webcam_index`: with `cv2.VideoCapture` monkeypatched, `read_frame("0")` opens index `0` (int).
- [ ] **Step 2:** Implement. `CameraConfig.to_dict()` includes `live` only when it is set, so existing reports are unchanged for configs without it.
- [ ] **Step 3:** Run `pytest -q -m "not integration"`. Commit: "Camera live settings, webcam and stand-in camera configs, live fields in /api/cameras".

### Task 3: `ai_engine/live/` — sources, shared messages and the live loop

**Files:**
- Create: `ai_engine/run_messages.py`. Move `SCHEMA_VERSION`, `progress_message`, `event_message` and `_timeseries_row` (renamed `timeseries_row`) here from `api/jobs.py`, unchanged. `api/jobs.py` re-imports them under the old names, so its behaviour and tests are unchanged.
- Create: `ai_engine/live/__init__.py`, `ai_engine/live/capture.py`, `ai_engine/live/loop.py`
- Test: `tests/test_live_capture.py`, `tests/test_live_loop.py`

**Interfaces:**
- Produces, in `capture.py`:
  - `class LiveSourceError(RuntimeError)` with a `.reason` attribute in `{"camera_unavailable", "stream_ended"}`. The message is the reason only, and never includes a URL.
  - `class LiveSource(Protocol)`:
    - `source_kind: str`
    - `loop_count: int`, which is 0 for anything but a clip
    - `open() -> None`
    - `read_latest(timeout_s: float) -> tuple[np.ndarray, float] | None`, returning the newest frame and its `time.monotonic()` capture time, or `None` on timeout
    - `close() -> None`
  - `WebcamSource(index: int)` and `RtspSource(url: str)`: a capture thread keeps only the newest frame.
  - `LoopedClipSource(path: Path, clock=time.monotonic)`: serves the frame at `floor(elapsed * fps) % frame_count`, increments `loop_count` at each wrap, and capture times keep increasing.
  - `open_source(live: LiveSettings, env: Mapping[str, str]) -> LiveSource`, which reads the RTSP URL from `env[live.rtsp_url_env]`.
- Produces, in `loop.py`:
  - `LiveOptions(output_dir: Path, process_fps: float = 10.0, preview_privacy_mode: str = "frame", max_session_s: float = 7200.0, first_frame_timeout_s: float = 10.0, frame_timeout_s: float = 10.0)`.
  - `run_live(source: LiveSource, tracker, camera: CameraConfig, options: LiveOptions, on_frame: Callable[[FrameUpdate], None] | None = None, should_stop: Callable[[], bool] | None = None, clock=time.monotonic, sleep=time.sleep) -> dict`.

Behaviour of `run_live`:
- `tracker.configure(process_fps)`, then warm up on the first frame's shape.
- Build `FrameProcessor(tracker, camera, privacy_mode=options.preview_privacy_mode)`. Draw once, in the preview mode, since there is no saved video.
- Frame `k` is processed no earlier than `start + k / process_fps`; sleep the difference, then `read_latest`. `timestamp_s = capture_time - first_capture_time`. `frame_index` counts processed frames.
- The `FrameUpdate` has `frames_total=None` and `image_privacy=preview_privacy_mode`.
- Each frame is added to `QueueTimeSeriesWriter(output_dir / "timeseries" / f"live{camera.camera_id}_queue.csv")` with the same arguments `RunWriters.add` uses, and to a `QueueSummary`.
- The loop ends on `should_stop()` (`stopped_by: "operator"`) or elapsed ≥ `max_session_s` (`"max_session"`). If no frame arrives within `first_frame_timeout_s`, or later within `frame_timeout_s`, it raises `LiveSourceError("camera_unavailable")`.
- The returned report comes from `performance.build_report` with these extras:
  - `kind: "live"`, `source_kind`, `stopped_by`, `duration_s`;
  - `loops: [{"loop": n, "timestamp_s": t}]`, appended whenever `source.loop_count` changes;
  - `configuration` (model path, confidence, image size, `process_fps`, `tracker.describe()`, `preview_privacy_mode`);
  - `outputs: {"timeseries": str(path), "metrics": str(metrics_path)}`;
  - `queue`, built exactly as `process_video` builds `report_extra["queue"]`.
- It is saved with `save_json` to `output_dir / "metrics" / f"live{camera_id}_metrics.json"`. No other files are written.

- [ ] **Step 1: Failing tests** (fake source, fake tracker from `tests/conftest.py`, fake clock and sleep):
  - `test_messages_moved_unchanged`: the `api.jobs` names are the same objects as the `ai_engine.run_messages` ones.
  - `test_looped_clip_time_increases_and_counts_loops`: synthetic 10-frame clip at 10 fps, fake clock advanced 2.5 s → `loop_count == 2`, and capture times strictly increase.
  - `test_latest_frame_only`: a fake capture yields 5 frames before the reader is asked; `read_latest` returns the 5th.
  - `test_run_live_paces_and_numbers`: fake source always ready, `process_fps` 10, stop after 1.0 s of fake time → 10 or 11 frames, `frame_index` 0..n-1, every update has `frames_total is None`.
  - `test_run_live_writes_only_csv_and_metrics`: afterwards `output_dir` holds exactly the CSV and the metrics JSON. No `.mp4`, `.jpg` or `.png` anywhere under it.
  - `test_run_live_report_fields`: `kind`, `source_kind`, `stopped_by == "operator"`, `loops`, `outputs.timeseries`, and `queue.camera_id`.
  - `test_max_session_stop`: `stopped_by == "max_session"`.
  - `test_no_first_frame_fails_with_camera_unavailable` (Review Focus 1): `read_latest` always returns `None` → `LiveSourceError` with `.reason == "camera_unavailable"` after 10 s of fake time.
- [ ] **Step 2:** Run the tests and confirm they fail. Implement. Run the tests and confirm they pass.
- [ ] **Step 3:** `pytest -q -m "not integration"`, so the existing `api/jobs.py` tests still pass. Commit: "ai_engine/live: camera sources and run_live; share message builders".

### Task 4: `worker/` package

**Files:**
- Create: `worker/__init__.py`, `worker/__main__.py` (CLI), `worker/client.py` (ingest HTTP client), `worker/session.py` (glue between `run_live` and the client), `worker/ctl.py` (demo command)
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `open_source`, `run_live`, `LiveOptions`, `LiveSourceError` (Task 3); `progress_message`, `event_message`, `timeseries_row` (Task 3); `load_camera_config` and `LiveSettings` (Task 2).
- The ingest protocol (Task 5 implements the server side; these names are fixed):
  - `POST {api}/api/ingest/{video_id}/batch` with JSON `{"events": [event_dict...], "timeseries": [row...], "progress": progress_message | null, "frames_processed": int, "captured_at_unix": float | null}` → `204`.
  - `POST .../preview` with body `image/jpeg`, at most 2 MB → `204`.
  - `POST .../heartbeat` with JSON `{"frames_processed": int, "processing_fps": float}` → `204`.
  - `POST .../final` with JSON `{"status": "completed" | "failed", "error": str | null, "report": dict | null}` → `204`.
  - Every request carries the header `X-Worker-Token`.
- Produces:
  - `IngestClient(api: str, video_id: int, token: str, opener=urllib.request.urlopen, clock=time.monotonic)` with `.batch(...)`, `.preview(jpeg: bytes)`, `.heartbeat(...)` and `.final(...)`. Each returns `bool` and never raises on network errors.
  - `.seconds_since_ok() -> float`.
  - `worker/__main__.py`: `python -m worker --video-id N --camera ID --api URL --output-dir PATH`. The token comes from env `QUEUEVISION_WORKER_TOKEN`; the process exits with code 2 if it is missing. The model comes from env `QUEUEVISION_MODEL` / `QUEUEVISION_DEVICE` with the defaults in `ai_engine.config`. The tracker is built as `api.jobs.default_tracker_factory` builds it, re-implemented with `ai_engine` imports only.
  - `worker/ctl.py`: `python -m worker.ctl start|stop|status <camera> [--api URL]` calls `POST /api/cameras/{id}/live`, `POST /api/videos/{id}/stop` and `GET /api/live`. It prints the video id, the Live page URL `/live?id=N`, or an error.

Behaviour:
- Events are buffered. Time-series rows are taken once per second of `timestamp_s` (the `JobManager` rule). A batch is sent each 1.0 s of clock time, along with the latest progress message (`video_id` = N).
- Previews are JPEG quality 80, at most one per 0.2 s.
- A heartbeat is sent when no batch has gone for 2 s.
- If `seconds_since_ok() > 15`, the session stops with no final message and exit code 3.
- SIGTERM sets a stop flag; the current frame finishes, the final message is sent (`completed`, `report`), and the process exits 0. A SIGTERM that arrives before `run_live` has started, for example while the model is loading, sends `completed` with `report: null` and exits 0.
- `captured_at_unix` converts the newest frame's monotonic capture time to wall time: `time.time() - (time.monotonic() - capture_time)`.
- A `LiveSourceError` sends `failed` with `error = reason` and exits 1. Any other exception sends `failed` with `error = "engine_error"` and exits 1. The traceback goes to the worker log with any `rtsp://…` text replaced by `rtsp://[redacted]`.

- [ ] **Step 1: Failing tests** (fake opener that records requests, fake clock, fake `run_live` or fake source with the conftest fake tracker):
  - `test_batches_once_per_second_with_progress_and_rows`
  - `test_preview_rate_limited`
  - `test_heartbeat_when_idle`
  - `test_exits_when_api_unreachable` (Review Focus 3): the opener raises `URLError`; after 15 s of fake time `main()` returns 3 and no final message was sent.
  - `test_sigterm_sends_completed_final`: the stop flag is set mid-run → the last request is `final` with `status == "completed"` and `report["stopped_by"] == "operator"`.
  - `test_source_error_sends_failed_reason`: `failed` with `camera_unavailable`.
  - `test_engine_error_sends_failed_final_without_url` (Review Focus 5): the fake tracker raises with a message containing `rtsp://user:secret@h/x` → final `error == "engine_error"`. No recorded request body, and no captured log line, contains `secret`.
  - `test_token_from_env_only`: without the env var → exit 2. `--help` lists no token option.
  - `test_ctl_start_prints_live_url` (fake opener).
- [ ] **Step 2:** Run the tests and confirm they fail. Implement. Run the tests and confirm they pass. Run `pytest -q -m "not integration"`. Commit: "worker package: live camera process with ingest client and demo command".

### Task 5: Ingest routes and the shared publish path

**Files:**
- Create: `api/routes/ingest.py`
- Modify: `api/jobs.py`. Add public `publish(video_id, message)` (same as `_publish`), `set_live_state(video_id, message)` and `clear_live_state(video_id)`. Add `hold_uploads() -> bool`, which returns `False` and holds nothing if a job is current, otherwise holds and returns `True`. Add `release_uploads()`. The worker loop's `wait_for` starts a pending job only when not held.
- Modify: `api/preview.py`. Add `PreviewStore.offer_jpeg(video_id: int, jpeg: bytes) -> None`, stored as already private with the same sequence numbering.
- Modify: `api/main.py` (include the router)
- Test: `tests/test_api_ingest.py`

**Interfaces:**
- Consumes: Task 4's protocol. `LiveSupervisor.verify(video_id, token) -> bool`, `LiveSupervisor.heartbeat(video_id, frames_processed, processing_fps)` and `LiveSupervisor.finished(video_id)` (Task 6). In this task, tests use a stub supervisor on `app.state.live`.
- Produces: the routes in Task 4's protocol.
  - Checks, in order: unknown id → `404`; client host not loopback (reuse `api.settings.is_loopback`) → `403`; token missing or wrong for that run → `401`; run not `processing` or `kind != "live"` → `409`; preview over 2 MB → `413`.
  - `batch`: `add_events`, `add_timeseries` and `set_progress` in one transaction. Then publish each `event_message`, and `set_live_state` plus publish the progress message. A batch also counts as a heartbeat (`supervisor.heartbeat`).
  - `final`: `set_report` and `set_status` in one transaction, as `JobManager._finish` does, then publish `status_message`, `clear_live_state`, `preview.drop` and `supervisor.finished`.
  - Ingest routes are excluded from the OpenAPI schema (`include_in_schema=False`), so the dashboard types don't change.

- [ ] **Step 1: Failing tests** (TestClient; for the loopback check, a test sets a non-loopback client via `TestClient(app, client=("203.0.113.5", 1))` or a header-free equivalent):
  - `test_ingest_requires_token`, `test_ingest_wrong_run_token_refused`, `test_ingest_non_loopback_refused`, `test_ingest_upload_run_refused` (an upload run → 409), `test_ingest_preview_size_limit`.
  - `test_batch_stores_events_waits_alerts_timeseries`: a `queue_join` plus `service_start` pair creates a waits row, and a congestion event creates an alerts row.
  - `test_batch_publishes_same_shapes_as_upload`: a WebSocket subscriber receives `event` and `progress` messages whose key sets equal those produced for an upload run in the existing WebSocket test.
  - `test_final_sets_status_and_report`
  - `test_uploads_held_while_held`: `hold_uploads()` → a submitted job stays `queued`; `release_uploads()` → it runs.
- [ ] **Step 2:** Run the tests and confirm they fail. Implement. Run the tests and confirm they pass. Run `pytest -q -m "not integration"`. Commit: "Loopback ingest routes for live workers; JobManager publish and upload hold".

### Task 6: Live supervisor and public routes

**Files:**
- Create: `api/live.py`, `tests/fake_worker.py` (speaks Task 4's protocol with no model; behaviour chosen by env `FAKE_WORKER_MODE` = `normal` | `slow_start` | `crash` | `silent`)
- Modify: `api/main.py` (create the supervisor; on lifespan exit call `shutdown()` before `jobs.stop()`), `api/deps.py` (`LiveDep`), `api/routes/videos.py` (`cancel` on a live run calls `supervisor.stop`; add `POST /api/videos/{id}/stop`), `api/routes/cameras.py` (`POST /api/cameras/{id}/live`, `GET /api/live`), `api/models.py` (`LiveSession` model)
- Test: `tests/test_api_live.py`. Its fixture runs the app with `uvicorn.Server` in a thread on a free loopback port, so a real subprocess can reach it.

**Interfaces:**
- Produces:
  - `LiveSupervisor(settings: ApiSettings, jobs: JobManager, worker_cmd: Sequence[str] = (sys.executable, "-m", "worker"), api_url: str | None = None, clock=time.monotonic, heartbeat_timeout_s: float = 10.0, stop_timeout_s: float = 15.0)`.
  - `.start(camera_id: str) -> int` raises `LiveStartError(status: int, reason: str)`:
    - `404` for an unknown camera;
    - `409` with `no_live_settings`, `already_live`, `upload_processing` or `rtsp_env_unset`.
  - `.stop(video_id: int) -> bool` (False when the run is not live or not processing), `.sessions() -> list[dict]`, `.verify`, `.heartbeat`, `.finished` and `.shutdown()`.
- Routes:
  - `POST /api/cameras/{id}/live` → `201` with a `Run` record (`kind "live"`).
  - `POST /api/videos/{id}/stop` → `200` with a `Run`, or `409` "run is not live".
  - `GET /api/live` → `list[LiveSession]` with fields `video_id`, `camera_id`, `source_kind`, `started_at`, `heartbeat_age_s`, `processing_fps`.
  - The new mutation routes go on `mutation_router`, which keeps its existing guards.

Behaviour of `start`, all under one lock:
1. Validate the camera.
2. `jobs.hold_uploads()` must return True, or the call fails with `upload_processing`.
3. `create_video(name=f"live:{camera_id}", camera_id, options={"realtime": True, "frame_stride": 1, "blur_heads": False, "privacy_mode": None, "preview_privacy_mode": live.preview_privacy_mode, "max_frames": None, "process_fps": live.process_fps}, kind="live", source_kind=live.source)`.
4. Set status to `processing` and create `outputs_dir/<id>/`.
5. `token = secrets.token_urlsafe(32)`.
6. `Popen([*worker_cmd, "--video-id", id, "--camera", camera_id, "--api", api_url, "--output-dir", out], env={**os.environ, "QUEUEVISION_WORKER_TOKEN": token}, stdout/stderr → outputs_dir/<id>/worker.log)` with no shell.

The watchdog thread runs every 1 s:
- If the process exited with the run still `processing` → `failed`, `worker_exited`.
- If there has been no heartbeat or batch for `heartbeat_timeout_s` (counted from the first heartbeat, or from start plus 60 s while the model loads) → `failed`, `no_heartbeat`, and the process is killed.
- Each of these releases the upload hold and publishes `status`.

`stop` sends SIGTERM, waits up to `stop_timeout_s` for `finished`, then sends SIGKILL and marks the run `failed` with `stop_timeout`. Ending a session releases the upload hold. `shutdown()` stops every session.

- [ ] **Step 1: Failing tests** (real `tests/fake_worker.py` subprocess against the threaded server):
  - `test_start_stream_stop_completes`: start → WebSocket shows progress → stop → `completed`, `report.stopped_by == "operator"`. The `GET /api/videos/{id}` record has `kind "live"`.
  - `test_start_refusals`: unknown camera 404; no `live` 409; second start 409 `already_live`; rtsp with the env var unset 409.
  - `test_concurrent_starts_only_one_wins`: two threads → exactly one `201`.
  - `test_stop_during_startup_and_twice` (Review Focus 2): `slow_start` mode → stop at once → `completed` with 0 frames → a second stop returns 409.
  - `test_cancel_on_live_run_stops_it`
  - `test_worker_crash_marks_failed`: `crash` → `failed` `worker_exited` within 3 s.
  - `test_silent_worker_marks_failed_and_killed`: `silent`, with `heartbeat_timeout_s` set low in the test → `failed` `no_heartbeat`, and the process is gone.
  - `test_upload_waits_for_live_session` (Review Focus 4): an upload stays `queued` during the session and completes after the stop.
  - `test_startup_fails_leftover_live_run` (Review Focus 3): a `processing` live row from before → `failed` after app start.
  - `test_shutdown_stops_workers`: after the app's lifespan ends, no fake worker pid is alive.
  - `test_rtsp_url_never_leaks`: the camera's `rtsp_url_env` points to an env var holding `rtsp://user:secret@203.0.113.9/s`, and the fake worker logs and posts nothing secret. Across start, `GET /api/live`, `GET /api/videos/{id}`, `GET /api/cameras`, the DB file bytes, captured API logs and the child's argv (`psutil` is not needed; check the `Popen.args` the supervisor keeps), `secret` never appears.
- [ ] **Step 2:** Run the tests and confirm they fail. Implement. Run the tests and confirm they pass. Run `pytest -q -m "not integration"`, then regenerate `openapi.json` and `api-types.ts` for `LiveSession` and the new routes. Commit: "Live supervisor and camera start/stop/list routes".

### Task 7: Run page note, Live e2e and the real-worker integration test

**Files:**
- Modify: `dashboard/components/RunDetail.tsx`. When `run.kind === "live"`:
  - replace the video player with a note reading exactly `Live session — no video recorded`;
  - make the options line read `live · <source_kind> · <process_fps> fps processed`, with no head-blur claim.
  - Leave everything else as it is, including the CSV link. `kind` missing or `upload` → unchanged.
- Modify: `dashboard/components/RunDetail.test.tsx`
- Create: `dashboard/e2e/live.spec.ts` (local only; skips like the existing spec without the CAD clip or `.venv`), using the existing `server.ts`
- Create: `tests/test_live_integration.py` (`@pytest.mark.integration`)

**Interfaces:**
- Consumes: `Run.kind`, `Run.source_kind`, `RunOptions.process_fps` (Tasks 1 and 6); the `POST /api/cameras/cad_seq25/live` and `POST /api/videos/{id}/stop` routes (Task 6).

- [ ] **Step 1: Failing Vitest:**
  - `shows the live note and no video for a live run`: the text is present and there is no `video` element.
  - `upload run unchanged`: an existing fixture renders as before.
  - `run without kind (older API) unchanged`
- [ ] **Step 2:** Implement and run `npm test`, `npm run lint` and `npm run typecheck`.
- [ ] **Step 3: e2e** `live camera session shows on Live page and as a recorded run`. Start `cad_seq25` through the API from the test. Open `/live?id=N`, and expect Queue length to match `/^\d+$/` within 30 s and the preview image to load. Stop it through the API. Open `/run?id=N`: the note is visible, no `video` element, and the "Time series (CSV)" link's URL returns 200. Run `npm run e2e`: 15 tests pass.
- [ ] **Step 4: Integration test** `test_real_worker_on_looped_clip`. Real `python -m worker` against the threaded API, `cad_seq25`, a 20 s session → `completed`, at least 150 frames processed, `timeseries` rows stored, the output folder holds only the CSV, the metrics JSON and `worker.log`, and the report has `loops`.
- [ ] **Step 5:** Commit: "Run page note for live sessions; live e2e and real-worker integration test".

### Task 8: Measurements, docs and the session report

**Files:**
- Create: `scripts/measure_live.py`. It starts a session through the API on a camera, samples every 1 s for its duration, and writes `outputs/s11_live/<name>.json`. It records:
  - from the worker's report: the update median and p90;
  - worker CPU, from `resource.getrusage(RUSAGE_CHILDREN)` after exit, or from the worker reporting its own `getrusage(RUSAGE_SELF)` in the final report (the builder picks one and records which);
  - Peak RSS for the worker (self-reported in the final report via `ru_maxrss`) and for the API (`ru_maxrss`);
  - capture-to-ingest latency (`receipt time.time() - captured_at_unix`, logged by the API per batch at DEBUG and summarised as median and p90);
  - batch count, mean events per batch, and mean rows per batch.
- Create: `docs/sprint8_session2_report.md`
- Modify: `docs/api.md`, `docs/architecture.md`, `docs/privacy.md`, `docs/security_review.md`, `docs/demo.md`, `docs/testing_report.md`, `README.md` (roadmap row 8 partly done, plus the run instructions)

- [ ] **Step 1: Measure** (one model job at a time, no other heavy jobs):
  - `cad_seq25` for 120 s, 3 repeats;
  - one 30-minute soak (RSS and update median for the first and last 60 s).
  - Save everything to `outputs/s11_live/`.
  - Webcam: run `python scripts/measure_live.py --camera webcam --seconds 120` if the camera permission is available. Otherwise record it as pending, with that exact command.
- [ ] **Step 2: Docs.**
  - `api.md`: every new route, the `kind`/`source_kind`/`process_fps` fields, the `live` camera section, and the `409` reasons.
  - `architecture.md`: the process diagram and data flow.
  - `privacy.md`: what a live session holds in memory and what it stores; no recording.
  - `security_review.md`: ingest token, loopback only, URL handling, remaining risks.
  - `demo.md`: the demo script, including the person's step of drawing the webcam zone with `draw_zone.py --video 0`, and starting and stopping with `python -m worker.ctl`.
  - `testing_report.md`: a session 2 section with the tests and what they prove, the measurements with their method and limits, and the statement that looped-clip queue numbers are not accuracy figures.
- [ ] **Step 3: Final commands.** Re-run every baseline command and save the output to `outputs/s11_final_commands/`.
- [ ] **Step 4: Session report** (`docs/sprint8_session2_report.md`), following the brief's FINAL REPORT list:
  - what was built, and the files;
  - dependency changes (expected: none);
  - a table of baselines against final results;
  - the measurements;
  - pending items for a person: drawing the webcam zone, the webcam run with permission, the real-browser check;
  - the builder's calls from the spec's "Left to the builder" list;
  - each design goal marked Completed, Partially completed or Not completed;
  - what session 3 needs.
- [ ] **Step 5:** Commit: "Docs, measurements and Sprint 8 session 2 report".
