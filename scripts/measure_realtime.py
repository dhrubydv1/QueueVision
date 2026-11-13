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
    args = parser.parse_args(argv)

    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        print(f"cannot open {args.video}", file=sys.stderr)
        return 2
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    duration = count / fps

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
