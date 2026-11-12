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


def run_job(settings: ApiSettings, manager: JobManager, source: Path, camera: str, realtime: bool) -> dict:
    """Create one run in the uploads layout the API uses, process it, return timing and record."""
    conn = db.connect(settings.db_path)
    try:
        options = {"frame_stride": 1, "blur_heads": True, "max_frames": None, "realtime": realtime}
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

    with tempfile.TemporaryDirectory(prefix="qv_realtime_") as tmp:
        settings = ApiSettings(data_dir=Path(tmp), camera_dir=PROJECT_ROOT / "configs" / "cameras")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = db.connect(settings.db_path)
        db.init_db(conn)
        conn.close()
        manager = JobManager(settings, default_tracker_factory(settings))
        manager.start()
        try:
            # Untimed pass would hide model load from run 1; instead both runs share one loaded
            # detector and run 1 is the first user, so its wall time includes the model load.
            fast = run_job(settings, manager, args.video, args.camera, realtime=False)
            paced = run_job(settings, manager, args.video, args.camera, realtime=True)
        finally:
            manager.stop()

        equal = {}
        for name, pattern in COMPARED.items():
            a = output_file(settings, fast["video_id"], pattern).read_bytes()
            b = output_file(settings, paced["video_id"], pattern).read_bytes()
            equal[name] = {"byte_equal": a == b, "bytes": len(a)}
        reports = {k: v["record"]["report"] for k, v in (("fast", fast), ("paced", paced))}
        frames = reports["paced"]["frames_processed"]
        overhead = paced["wall_s"] - duration
        summary = {
            "video": args.video.name,
            "camera": args.camera,
            "device": manager.loaded_device,
            "clip_fps": round(fps, 3),
            "clip_frames": count,
            "clip_duration_s": round(duration, 3),
            "frames_processed": {k: r["frames_processed"] for k, r in reports.items()},
            "normal_run": {
                "wall_s": round(fast["wall_s"], 3),
                "includes_model_load": True,
                "report_processing_fps": reports["fast"]["performance"]["average_processing_fps"],
            },
            "realtime_run": {
                "wall_s": round(paced["wall_s"], 3),
                "report_processing_fps": reports["paced"]["performance"]["average_processing_fps"],
            },
            "realtime_wall_minus_clip_duration_s": round(overhead, 3),
            "per_frame_overhead_ms": round(1000 * overhead / frames, 3) if overhead > 0 and frames else None,
            "per_frame_overhead_note": (
                "null when the real-time wall time did not exceed the clip duration. Pacing only waits "
                "when processing is faster than the clip; when it is slower (processing fps below clip "
                "fps) the real-time run never waits, so this overhead is the processing shortfall, "
                "not a cost of pacing."
            ),
            "processing_faster_than_clip": reports["paced"]["performance"]["average_processing_fps"] > fps,
            "outputs_identical": equal,
            "all_outputs_identical": all(v["byte_equal"] for v in equal.values()),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["all_outputs_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
