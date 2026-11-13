"""Measure real-time pacing: run the API job path twice in-process and compare the results.

Run 1 processes the clip as fast as the machine allows; run 2 paces it to video time
(the "Process in real time" option). The script records both wall times and checks that
the waits CSV, time-series CSV and event log are byte-identical, since pacing may only
delay frames, never change what they produce. All numbers come from this run.

    python scripts/measure_realtime.py --video datasets/raw/cad_seq25.mp4 --camera cad_seq25
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api import db  # noqa: E402
from api.jobs import JobManager, default_tracker_factory  # noqa: E402
from api.settings import ApiSettings  # noqa: E402


class StageTimer:
    """Per-stage wall-time totals gathered by wrapping functions from outside (no hot-path changes)."""

    def __init__(self) -> None:
        self.totals: dict[str, float] = {}
        self.counts: dict[str, int] = {}
        self.samples: dict[str, list[float]] = {}
        self.after_wait = False  # set by the wait wrapper; consumed by the next tracker.update

    def reset(self) -> None:
        self.totals.clear()
        self.counts.clear()
        self.samples.clear()
        self.after_wait = False

    def add(self, name: str, seconds: float, keep: bool = False) -> None:
        self.totals[name] = self.totals.get(name, 0.0) + seconds
        self.counts[name] = self.counts.get(name, 0) + 1
        if keep:
            self.samples.setdefault(name, []).append(seconds)

    def wrap(self, fn, name: str, keep: bool = False):
        def timed(*args, **kwargs):
            began = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                self.add(name, time.perf_counter() - began, keep)

        return timed


def install_stage_timers(timer: StageTimer) -> None:
    """Monkeypatch the pipeline pieces with timing wrappers (only for this process)."""
    from ai_engine import run_tracking
    from ai_engine.queue.analyzer import QueueAnalyzer
    from ai_engine.tracking.tracker import PersonTracker
    from ai_engine.video.video_loader import VideoLoader, VideoWriter
    from api import jobs, preview

    if getattr(PersonTracker.update, "_stage_timed", False):
        return  # already installed in this process

    original_update = PersonTracker.update

    def update(self, frame):
        began = time.perf_counter()
        try:
            return original_update(self, frame)
        finally:
            took = time.perf_counter() - began
            timer.add("tracker_update", took, keep=True)
            if timer.after_wait:
                timer.after_wait = False
                timer.add("tracker_update_after_wait", took, keep=True)
            else:
                timer.add("tracker_update_no_wait", took, keep=True)

    update._stage_timed = True  # type: ignore[attr-defined]
    PersonTracker.update = update
    QueueAnalyzer.update = timer.wrap(QueueAnalyzer.update, "queue_analyzer_update")
    VideoLoader.read = timer.wrap(VideoLoader.read, "decode_read")
    VideoLoader.skip = timer.wrap(VideoLoader.skip, "decode_skip")
    VideoWriter.write = timer.wrap(VideoWriter.write, "video_write")
    for name in (
        "blur_heads",
        "draw_queue_zone",
        "draw_service_point",
        "draw_people",
        "draw_alert_banner",
        "draw_metrics_panel",
    ):
        setattr(run_tracking, name, timer.wrap(getattr(run_tracking, name), f"draw:{name}"))
    preview.PreviewStore.offer = timer.wrap(preview.PreviewStore.offer, "preview_offer", keep=True)
    original_render = preview.render_preview
    preview.render_preview = timer.wrap(original_render, "preview_render")  # called only on encodes
    original_process = jobs.process_video

    def process_video(*args, on_frame=None, **kwargs):
        return original_process(*args, on_frame=timer.wrap(on_frame, "on_frame_total"), **kwargs)

    jobs.process_video = process_video
    jobs.JobManager._publish = timer.wrap(jobs.JobManager._publish, "publish")
    for fname in ("add_events", "add_timeseries", "set_progress"):
        setattr(db, fname, timer.wrap(getattr(db, fname), f"db:{fname}"))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def spread(values: list[float]) -> dict:
    return {"min": min(values), "median": statistics.median(values), "max": max(values), "all": values}


def run_stages(args, fps: float, count: int) -> int:
    """Repeat fast/real-time runs per stride with per-stage timing; write raw results to a folder."""
    timer = StageTimer()
    waits: list[float] = []
    out_dir: Path = args.stages_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="qv_pacing_") as tmp:
        settings = ApiSettings(data_dir=Path(tmp), camera_dir=PROJECT_ROOT / "configs" / "cameras")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = db.connect(settings.db_path)
        db.init_db(conn)
        conn.close()

        def counting_wait(delay: float) -> object:
            began = time.monotonic()
            if args.spin_wait:  # diagnostic: keep the core busy instead of sleeping
                deadline = began + delay
                while time.monotonic() < deadline:
                    pass
                result = False
            else:
                result = manager._interrupt.wait(delay)
            waits.append(time.monotonic() - began)
            timer.add("pacing_wait", waits[-1])
            timer.after_wait = True
            return result

        manager = JobManager(settings, default_tracker_factory(settings), wait=counting_wait)
        manager.start()
        try:
            install_stage_timers(timer)
            # Throwaway run: loads the detector so no measured run includes the model load.
            run_job(settings, manager, args.video, args.camera, realtime=False, stride=1)
            for stride in args.strides:
                for rep in range(1, args.repeats + 1):
                    for realtime in (False, True) if not args.realtime_first else (True, False):
                        timer.reset()
                        waits.clear()
                        job = run_job(settings, manager, args.video, args.camera, realtime, stride)
                        waited = sum(waits)
                        updates = timer.samples.get("tracker_update", [])
                        record = {
                            "stride": stride,
                            "rep": rep,
                            "realtime": realtime,
                            "wall_s": job["wall_s"],
                            "waited_s": waited,
                            "wait_calls": len(waits),
                            "non_wait_s": job["wall_s"] - waited,
                            "frames": len(updates),
                            "stage_totals_s": dict(timer.totals),
                            "stage_counts": dict(timer.counts),
                            "update_ms": {
                                k: {
                                    "n": len(timer.samples.get(k, [])),
                                    "median": 1000 * statistics.median(timer.samples[k]),
                                    "p90": 1000 * percentile(timer.samples[k], 0.9),
                                }
                                for k in (
                                    "tracker_update",
                                    "tracker_update_no_wait",
                                    "tracker_update_after_wait",
                                )
                                if timer.samples.get(k)
                            },
                            "preview_offer_ms_median_encoded": 1000
                            * statistics.median(
                                [x for x in timer.samples.get("preview_offer", []) if x > 0.002] or [0]
                            ),
                        }
                        runs.append(record)
                        mode = "RT  " if realtime else "fast"
                        print(
                            f"stride {stride} rep {rep} {mode} wall {record['wall_s']:.3f} "
                            f"waited {waited:.3f} non-wait {record['non_wait_s']:.3f} "
                            f"frames {record['frames']}",
                            flush=True,
                        )
        finally:
            manager.stop()

    summary: dict = {
        "video": args.video.name,
        "clip_fps": fps,
        "clip_frames": count,
        "runs": runs,
        "spread": {},
    }
    for stride in args.strides:
        for realtime in (False, True):
            sel = [r for r in runs if r["stride"] == stride and r["realtime"] == realtime]
            key = f"stride_{stride}_{'realtime' if realtime else 'fast'}"
            summary["spread"][key] = {
                m: spread([r[m] for r in sel]) for m in ("wall_s", "waited_s", "non_wait_s")
            }
    (out_dir / args.stages_file).write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["spread"], indent=2))
    return 0


COMPARED = {"waits": "*_waits.csv", "timeseries": "*_queue.csv", "events": "*_events.jsonl"}


def run_job(
    settings: ApiSettings, manager: JobManager, source: Path, camera: str, realtime: bool, stride: int
) -> dict:
    """Create one run in the uploads layout the API uses, process it, return timing and record."""
    conn = db.connect(settings.db_path)
    try:
        options = {"frame_stride": stride, "blur_heads": True, "max_frames": None, "realtime": realtime}
        video_id = db.create_video(conn, source.name, camera, options)
        folder = settings.uploads_dir / str(video_id)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / f"video{video_id}{source.suffix.lower()}")
    finally:
        conn.close()
    started = time.monotonic()
    manager.submit(video_id)
    if not manager.wait_idle(3600.0):
        raise RuntimeError("job did not finish within an hour")
    wall = time.monotonic() - started
    conn = db.connect(settings.db_path)
    try:
        record = db.get_video(conn, video_id)
    finally:
        conn.close()
    if record is None or record["status"] != "completed":
        raise RuntimeError(
            f"run {video_id} did not complete: {record and record['status']} {record and record['error']}"
        )
    return {"video_id": video_id, "wall_s": wall, "record": record}


def output_file(settings: ApiSettings, video_id: int, pattern: str) -> Path:
    matches = sorted((settings.outputs_dir / str(video_id)).rglob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern} for run {video_id}, found {len(matches)}")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--camera", required=True)
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "outputs" / "s7_realtime" / "summary.json"
    )
    parser.add_argument("--stages", action="store_true", help="per-stage timing mode (see run_stages)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--strides", type=int, nargs="+", default=[1, 3])
    parser.add_argument(
        "--realtime-first", action="store_true", help="run the real-time job before the fast one"
    )
    parser.add_argument(
        "--spin-wait", action="store_true", help="diagnostic: busy-wait instead of sleeping in pacing waits"
    )
    parser.add_argument("--stages-file", default="stages.json")
    parser.add_argument("--stages-dir", type=Path, default=PROJECT_ROOT / "outputs" / "s8_pacing")
    args = parser.parse_args(argv)

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        print(f"cannot open {args.video}", file=sys.stderr)
        return 2
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    duration = count / fps
    if args.stages:
        return run_stages(args, fps, count)

    waits: list[float] = []  # seconds actually spent in each real-time pacing wait

    def run_pair(settings, manager, stride: int, first: bool) -> dict:
        waits.clear()
        fast = run_job(settings, manager, args.video, args.camera, realtime=False, stride=stride)
        waits.clear()
        paced = run_job(settings, manager, args.video, args.camera, realtime=True, stride=stride)
        calls, waited = len(waits), sum(waits)
        equal = {}
        for name, pattern in COMPARED.items():
            a = output_file(settings, fast["video_id"], pattern).read_bytes()
            b = output_file(settings, paced["video_id"], pattern).read_bytes()
            equal[name] = {"byte_equal": a == b, "bytes": len(a)}
        reports = {k: v["record"]["report"] for k, v in (("fast", fast), ("paced", paced))}
        frames = reports["paced"]["frames_processed"]
        fast_fps = reports["fast"]["performance"]["average_processing_fps"]
        paced_fps = reports["paced"]["performance"]["average_processing_fps"]
        gap = paced["wall_s"] - duration
        note = (
            f"Real-time run: {calls} pacing waits, {waited:.3f} s waited in total; real-time wall "
            f"{paced['wall_s']:.3f} s vs clip {duration:.3f} s ({gap:+.3f} s); normal run "
            f"{fast['wall_s']:.3f} s. Processing fps from the reports: normal {fast_fps}, real time "
            f"{paced_fps}."
        )
        if calls == 0:
            note += " The pacer never waited, so this pair does not exercise pacing."
        else:
            extra = paced["wall_s"] - waited - fast["wall_s"]
            note += (
                f" Real-time wall minus time waited is {paced['wall_s'] - waited:.3f} s against "
                f"{fast['wall_s']:.3f} s for the normal run; the {extra:+.3f} s "
                "difference is not explained by the wait counter (cause not measured)."
            )
        if first:
            note += (
                " The normal run is the first user of the detector, so its wall time includes the model load."
            )
        return {
            "frame_stride": stride,
            "note": note,
            "frames_processed": {k: r["frames_processed"] for k, r in reports.items()},
            "fast_wall_s": round(fast["wall_s"], 3),
            "realtime_wall_s": round(paced["wall_s"], 3),
            "non_wait_s": round(paced["wall_s"] - waited, 3),
            "wait_calls": calls,
            "total_waited_s": round(waited, 3),
            "report_processing_fps": {"normal": fast_fps, "realtime": paced_fps},
            "realtime_wall_minus_clip_duration_s": round(gap, 3),
            "realtime_wall_minus_clip_per_frame_ms": round(1000 * gap / frames, 3) if frames else None,
            "pacing_exercised": calls > 0,
            "outputs_identical": equal,
            "all_outputs_identical": all(v["byte_equal"] for v in equal.values()),
        }

    with tempfile.TemporaryDirectory(prefix="qv_realtime_") as tmp:
        settings = ApiSettings(data_dir=Path(tmp), camera_dir=PROJECT_ROOT / "configs" / "cameras")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = db.connect(settings.db_path)
        db.init_db(conn)
        conn.close()

        def counting_wait(delay: float) -> object:
            """The manager's real interruptible Event wait, with the time it blocked recorded."""
            began = time.monotonic()
            result = manager._interrupt.wait(delay)
            waits.append(time.monotonic() - began)
            return result

        manager = JobManager(settings, default_tracker_factory(settings), wait=counting_wait)
        manager.start()
        try:
            # Both runs of the first pair share one loaded detector and the normal run is its
            # first user, so that wall time includes the model load.
            stride1 = run_pair(settings, manager, 1, first=True)
            stride3 = run_pair(settings, manager, 3, first=False)
        finally:
            manager.stop()

        summary = {
            "video": args.video.name,
            "camera": args.camera,
            "device": manager.loaded_device,
            "clip_fps": round(fps, 3),
            "clip_frames": count,
            "clip_duration_s": round(duration, 3),
            "pairs": {"stride_1": stride1, "stride_3": stride3},
            "all_outputs_identical": stride1["all_outputs_identical"] and stride3["all_outputs_identical"],
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["all_outputs_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
