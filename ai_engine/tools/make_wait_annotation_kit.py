"""Build a per-clip kit for a human to label per-person waiting times, and check filled sheets.

Make a kit (writes ``datasets/annotations/queue_events/<clip>/``)::

    python ai_engine/tools/make_wait_annotation_kit.py --video datasets/raw/cad_seq25.mp4 \\
        --camera-config configs/cameras/cad_seq25.yaml \\
        --tracks outputs/s5_tracks/cad/tracks/cad_seq25_mot.txt

Check a filled sheet (exit code 1 on errors), optionally against a second annotator::

    python ai_engine/tools/make_wait_annotation_kit.py --check sheet.csv [--compare other.csv]

The kit never contains time values in the ground-truth template: it is header + (optionally)
nothing else. Tracker output is offered only as a visual aid and as separate hints.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[2])

import cv2
import numpy as np

from ai_engine import config
from ai_engine.evaluation.replay import read_mot_tracks
from ai_engine.evaluation.wait_eval import (
    OBSERVED_COLUMNS,
    WaitRecord,
    match_by_join_time,
    read_observed_ground_truth,
)
from ai_engine.queue.camera_config import CameraConfigError, load_camera_config
from ai_engine.queue.zone import QueueZone
from ai_engine.video.transcode import reencode_h264
from ai_engine.video.video_loader import VideoLoader, VideoLoadError, VideoWriter, source_name
from ai_engine.visualization.metrics_overlay import draw_queue_zone, draw_service_point

QUEUE_EVENTS_DIR = config.PROJECT_ROOT / "datasets" / "annotations" / "queue_events"
HINT_COLUMNS = ("track_id", "first_seen_s", "first_in_zone_s", "last_seen_s")
TRACK_COLOR = (0, 165, 255)  # orange (BGR), distinct from the zone colours

README = """# Waiting-time labelling kit: {clip}

You will time how long each person waited, by hand. Nothing in this folder is an answer.

## Files

- `{clip}_review.mp4`: the clip with the frame number and time in seconds burned in at the top
  left, the queue zone and service point drawn.{tracks_line}
- `{clip}_waits_template.csv`: the sheet you fill in
  (header only by default; `--rows N` adds N empty rows with `clip_id` filled).
- `{clip}_track_hints.csv`: OPTIONAL tracker times, only to help you find people. Do NOT copy
  them as answers: they are the system's own estimates, and copying them makes the accuracy
  result meaningless. Use your own eyes and the burned-in time.

## How to label

1. Open `{clip}_review.mp4` in a player that can step single frames (VLC: press `e`; QuickTime:
   arrow keys; mpv: `.` and `,`).
2. Give each person you time your own number (1, 2, 3...). That is `track_id`. It is NOT the
   tracker's number from the video or the hints file.
3. For each person, step to the frame they happen to join the end of the waiting line, and
   copy the time from the burned-in `t=` stamp into `queue_join_seconds`. Do the same for
   `service_start_seconds` (reaches the counter/service point) and `service_complete_seconds`
   (visibly finished and leaves the counter; leave blank if not visible).
4. `observed_wait_seconds` = `service_start_seconds` - `queue_join_seconds`.
5. `annotation_confidence`: `high`, `medium` or `low`. Use `low` when unsure.
6. People who give up or are never served: leave `service_start_seconds` (and the two after
   it) blank. Skip people who are passing through or are staff.
7. Write `{clip}` in `clip_id` on every row you add.

Definitions (join, service start/complete, abandon) are in `datasets/metadata/annotation_guidelines.md`
sections 1-3. Refer to people only by number, never by name or description (`docs/privacy.md`).

## Check your sheet

    python ai_engine/tools/make_wait_annotation_kit.py --check <your filled csv>

Fix every ERROR it prints. Times are video seconds from frame 0 (frame / fps, fps = {fps:g}).

## Second annotator

Have a second person label this clip independently (do not share sheets first), then:

    python ai_engine/tools/make_wait_annotation_kit.py --check a.csv --compare b.csv

People are matched by queue-join time. Discuss disagreements; keep both files.

This folder holds stills/video of real people and is git-ignored. Do not share it outside the project.
"""


def write_template(path: Path, clip_id: str, rows: int = 0) -> None:
    """Header, plus ``rows`` empty rows with only ``clip_id`` set. Never any time value."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(OBSERVED_COLUMNS)
        for _ in range(rows):
            writer.writerow([clip_id] + [""] * (len(OBSERVED_COLUMNS) - 1))


def _burn_in(frame, index: int, fps: float) -> None:
    text = f"frame {index}   t={index / fps:.2f}s"
    scale, thickness = max(frame.shape[1] / 640, 0.8), 2
    (w, h), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale * 0.9, thickness)
    cv2.rectangle(frame, (0, 0), (w + 16, h + base + 14), (0, 0, 0), -1)
    cv2.putText(
        frame,
        text,
        (8, h + 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale * 0.9,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def make_wait_kit(
    video: Path,
    camera_config: Path | None = None,
    tracks: Path | None = None,
    output_dir: Path = QUEUE_EVENTS_DIR,
    force: bool = False,
    h264: bool = True,
    rows: int = 0,
) -> dict:
    """Write the review video, blank template, hints (with tracks) and README; return a summary."""
    clip = source_name(video)
    kit = output_dir / clip
    template = kit / f"{clip}_waits_template.csv"
    if template.exists() and not force:
        raise FileExistsError(f"{template} exists (it may be a filled sheet); use --force")
    camera = load_camera_config(camera_config) if camera_config else None
    zone = QueueZone(camera.queue_zone) if camera else None
    service = camera.service_zone or camera.service_line if camera else None
    by_frame = read_mot_tracks(tracks) if tracks else {}
    kit.mkdir(parents=True, exist_ok=True)
    review = kit / f"{clip}_review.mp4"

    seen: dict[int, list[float | None]] = {}  # id -> [first_seen, first_in_zone, last_seen]
    frames = 0
    with VideoLoader(video) as loader:
        fps = loader.metadata.fps
        if fps <= 0:
            raise VideoLoadError("Video reports no FPS; cannot map frames to times")
        size = (loader.metadata.width, loader.metadata.height)
        zone_px = zone.to_pixels(size) if zone else None
        service_px = None
        if service:
            service_px = np.round(np.array(service, dtype=float) * size).astype(np.int32)
        with VideoWriter(review, fps, size) as writer:
            for index, frame in loader:
                if zone_px is not None:
                    draw_queue_zone(frame, zone_px)
                if service_px is not None:
                    draw_service_point(frame, service_px)
                for det in by_frame.get(index, []):
                    x1, y1, x2, y2 = det.bbox
                    cv2.rectangle(frame, (x1, y1), (x2, y2), TRACK_COLOR, 1)
                    cv2.putText(
                        frame,
                        f"sys{det.track_id}",
                        (x1 + 2, max(y1 - 4, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        TRACK_COLOR,
                        1,
                        cv2.LINE_AA,
                    )
                    time_s = index / fps
                    entry = seen.setdefault(det.track_id, [time_s, None, time_s])
                    entry[2] = time_s
                    foot = ((x1 + x2) / 2, y2)
                    if entry[1] is None and zone is not None and zone.contains(foot, size):
                        entry[1] = time_s
                _burn_in(frame, index, fps)
                writer.write(frame)
                frames += 1
    if h264:
        reencode_h264(review)

    write_template(template, clip, rows)
    hints = kit / f"{clip}_track_hints.csv"
    if tracks:
        with hints.open("w", newline="", encoding="utf-8") as handle:
            writer_csv = csv.writer(handle)
            writer_csv.writerow(HINT_COLUMNS)
            for track_id in sorted(seen):
                first, in_zone, last = seen[track_id]
                if in_zone is None:
                    continue  # only people who entered the queue zone
                writer_csv.writerow([track_id, f"{first:.2f}", f"{in_zone:.2f}", f"{last:.2f}"])
    tracks_line = (
        " Orange boxes labelled `sysN` are tracker IDs: a visual aid only, not your person numbers."
        if tracks
        else ""
    )
    (kit / "README.md").write_text(
        README.format(clip=clip, fps=fps, tracks_line=tracks_line), encoding="utf-8"
    )
    return {"kit": kit, "frames": frames, "fps": fps, "hints": hints if tracks else None}


# ------------------------------------------------------------------------------ checker


def check_sheet(
    path: Path, compare: Path | None = None, tolerance_s: float = 3.0
) -> tuple[list[str], list[str]]:
    """Validate a filled sheet with wait_eval's reader; return ``(errors, notes)``."""
    errors: list[str] = []
    notes: list[str] = []
    try:
        truth = read_observed_ground_truth(path)
    except (ValueError, OSError) as error:
        return [str(error)], notes
    fatal = {
        "invalid_confidence": "annotation_confidence must be high, medium or low",
        "unparseable_number": "a time is not a number",
        "missing_join": "queue_join_seconds is blank",
        "service_before_join": "service_start_seconds is before queue_join_seconds",
        "missing_observed_wait": "observed_wait_seconds is blank although service_start is given",
    }
    for reason, message in fatal.items():
        if truth.excluded.get(reason):
            errors.append(f"{truth.excluded[reason]} row(s): {message} ({reason})")
    for item in truth.inconsistent:
        errors.append(
            f"row {item['row']} track {item['track_id']}: observed_wait_seconds "
            f"{item['observed_wait_seconds']} != service_start - queue_join "
            f"{item['service_start_minus_join']}"
        )
    # Row-level checks the reader does not do.
    seen_ids: set[tuple[str, str]] = set()
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            key = ((row["clip_id"] or "").strip(), (row["track_id"] or "").strip())
            if not key[1]:
                errors.append(f"row {number}: track_id is blank")
            elif key in seen_ids:
                errors.append(f"row {number}: duplicate track_id {key[1]} for clip {key[0] or '(blank)'}")
            seen_ids.add(key)
            try:
                start, done = (
                    (row["service_start_seconds"] or "").strip(),
                    (row["service_complete_seconds"] or "").strip(),
                )
                if start and done and float(done) < float(start):
                    errors.append(f"row {number}: service_complete_seconds is before service_start_seconds")
            except ValueError:
                pass  # already counted by the reader
    notes.append(
        f"{truth.rows_read} rows read, {len(truth.valid)} complete waits; "
        f"{truth.excluded.get('not_served', 0)} not served (abandon/unserved, allowed)"
    )
    if compare is not None:
        try:
            other = read_observed_ground_truth(compare)
        except (ValueError, OSError) as error:
            errors.append(f"--compare: {error}")
            return errors, notes
        as_records = [WaitRecord(o.track_id, o.join_s, o.service_start_s, False) for o in other.valid]
        matches, only_a, only_b = match_by_join_time(truth.valid, as_records, tolerance_s)
        notes.append(
            f"compare: {len(matches)} people matched by join time (+/-{tolerance_s:g}s), "
            f"{len(only_a)} only in first, {len(only_b)} only in second"
        )
        diffs = [abs(a.observed_wait_s - (b.wait_s or 0.0)) for a, b in matches]
        if diffs:
            notes.append(
                f"compare: mean abs wait difference {sum(diffs) / len(diffs):.2f}s, max {max(diffs):.2f}s"
            )
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make or check a waiting-time labelling kit.")
    parser.add_argument("--video", type=Path)
    parser.add_argument("--camera-config", type=Path)
    parser.add_argument("--tracks", type=Path, help="MOT file: tracker boxes (visual aid) and hints")
    parser.add_argument("--output-dir", type=Path, default=QUEUE_EVENTS_DIR)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing template")
    parser.add_argument("--rows", type=int, default=0, help="Empty template rows with clip_id filled")
    parser.add_argument("--no-h264", action="store_true", help="Keep the mp4v video")
    parser.add_argument("--check", type=Path, help="Validate a filled sheet instead of making a kit")
    parser.add_argument("--compare", type=Path, help="With --check: a second annotator's sheet")
    args = parser.parse_args(argv)
    if args.check:
        errors, notes = check_sheet(args.check, args.compare)
        for note in notes:
            print(note)
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print("FAILED" if errors else "OK")
        return 1 if errors else 0
    if not args.video:
        parser.error("--video is required unless --check is given")
    try:
        summary = make_wait_kit(
            args.video,
            args.camera_config,
            args.tracks,
            args.output_dir,
            args.force,
            not args.no_h264,
            args.rows,
        )
    except (VideoLoadError, CameraConfigError, FileExistsError, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Wrote kit to {summary['kit']} ({summary['frames']} frames at {summary['fps']:g} fps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
