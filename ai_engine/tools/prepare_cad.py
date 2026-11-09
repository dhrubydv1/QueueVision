"""Convert Collective Activity Dataset sequences to MP4 clips and queue-count CSVs.

Example::

    python ai_engine/tools/prepare_cad.py --source datasets/raw/cad.zip --sequences 10 25

For each sequence writes ``<video-dir>/cad_seqNN.mp4``, ``<annotation-dir>/cad_seqNN_queue_counts.csv``
(columns ``time_s, frame, queue_count``, as read by ``evaluate_queue.py``) and ``cad_seqNN.json``, plus
``cad_seqNN_person_boxes.csv`` (columns ``time_s, frame, x, y, w, h, action, action_name, pose``:
one row per annotated person, boxes in the 720x480 video pixel space, same frame and time
convention as the queue-count CSV; ``--person-boxes-only`` writes just that file).
The dataset does not state its frame rate, so ``--fps`` (default 30) is an assumption, recorded as
``fps_assumed`` in the JSON. Queueing is annotation action 4. The zip is untrusted: only
``seqNN/frameN.jpg`` and ``seqNN/annotations.txt`` members are read, in memory, never extracted.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[2])

import cv2
import numpy as np

from ai_engine import config
from ai_engine.video.transcode import reencode_h264
from ai_engine.video.video_loader import VideoLoadError, VideoWriter

QUEUEING_SEQUENCES = (10, 25, 26, 27, 39, 40, 41)
QUEUEING_ACTION = 4
DEFAULT_FPS = 30.0  # assumption: the dataset does not state its frame rate
VIDEO_DIR = config.PROJECT_ROOT / "datasets" / "raw"
ANNOTATION_DIR = config.PROJECT_ROOT / "datasets" / "annotations" / "cad"
CSV_COLUMNS = ("time_s", "frame", "queue_count")
PERSON_BOX_COLUMNS = ("time_s", "frame", "x", "y", "w", "h", "action", "action_name", "pose")
ACTION_NAMES = {1: "NA", 2: "crossing", 3: "waiting", 4: "queueing", 5: "walking", 6: "talking"}


def read_annotations(text: str) -> dict[int, list[int]]:
    """Parse ``frame x y w h action pose`` rows into frame number -> action ids."""
    actions: dict[int, list[int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 6:
            raise ValueError(f"Malformed annotation row: {line!r}")
        actions.setdefault(int(parts[0]), []).append(int(parts[5]))
    return actions


def read_person_boxes(text: str) -> dict[int, list[tuple[int, int, int, int, int, int]]]:
    """Parse rows into frame number -> ``(x, y, w, h, action, pose)`` per person, in file order."""
    boxes: dict[int, list[tuple[int, int, int, int, int, int]]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 7:
            raise ValueError(f"Malformed annotation row: {line!r}")
        x, y, w, h, action, pose = (int(v) for v in parts[1:7])
        boxes.setdefault(int(parts[0]), []).append((x, y, w, h, action, pose))
    return boxes


def person_box_rows(
    boxes: dict[int, list[tuple[int, int, int, int, int, int]]], fps: float
) -> list[tuple[str, int, int, int, int, int, int, str, int]]:
    """CSV rows; ``frame`` and ``time_s`` follow ``queue_counts`` (index = frame number - 1)."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    rows = []
    for frame in sorted(boxes):
        index = frame - 1
        for x, y, w, h, action, pose in boxes[frame]:
            name = ACTION_NAMES.get(action, f"action_{action}")
            rows.append((f"{index / fps:.3f}", index, x, y, w, h, action, name, pose))
    return rows


def write_person_boxes(path: Path, text: str, fps: float) -> Path:
    """Write the person-box CSV for one sequence's annotation text."""
    rows = person_box_rows(read_person_boxes(text), fps)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(PERSON_BOX_COLUMNS)
        out.writerows(rows)
    return path


def queue_counts(annotations: dict[int, list[int]], fps: float) -> list[tuple[float, int, int]]:
    """Return ``(time_s, frame_index, queue_count)`` per annotated frame, sorted by time."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    rows = []
    for frame in sorted(annotations):
        index = frame - 1
        count = sum(1 for action in annotations[frame] if action == QUEUEING_ACTION)
        rows.append((index / fps, index, count))
    return rows


def _read_source(source: Path, sequence: int, stack: ExitStack) -> tuple[str, list[tuple[int, object]]]:
    """Return the annotation text and ordered ``(frame_number, reader)`` pairs for a sequence."""
    prefix = f"seq{sequence:02d}"
    frame_re = re.compile(rf"^{prefix}/frame(\d+)\.jpg$")
    annotation_name = f"{prefix}/annotations.txt"
    if source.is_dir():
        names = [f"{prefix}/{path.name}" for path in (source / prefix).glob("*") if path.is_file()]

        def read(name: str) -> bytes:
            return (source / name).read_bytes()

    else:
        archive = stack.enter_context(zipfile.ZipFile(source))
        names = archive.namelist()
        read = archive.read
    frames = sorted(
        (int(match.group(1)), name) for name in names if (match := frame_re.match(name)) is not None
    )
    if not frames or annotation_name not in names:
        raise ValueError(f"Sequence {sequence} (frames and annotations.txt) not found in {source}")
    text = read(annotation_name).decode("utf-8", errors="replace")
    return text, [(number, (lambda n=name: read(n))) for number, name in frames]


def _frames(readers: list[tuple[int, object]]) -> Iterator[np.ndarray]:
    for number, reader in readers:
        frame = cv2.imdecode(np.frombuffer(reader(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"Frame {number} is not a readable JPEG")
        yield frame


def prepare_person_boxes(
    source: Path, sequence: int, fps: float, annotation_dir: Path, force: bool = False
) -> Path:
    """Write only ``cad_seqNN_person_boxes.csv``; no video, count CSV or JSON is touched."""
    path = annotation_dir / f"cad_seq{sequence:02d}_person_boxes.csv"
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; use --force")
    with ExitStack() as stack:
        text, _ = _read_source(source, sequence, stack)
    return write_person_boxes(path, text, fps)


def prepare_sequence(
    source: Path,
    sequence: int,
    fps: float,
    video_dir: Path,
    annotation_dir: Path,
    force: bool = False,
) -> tuple[Path, Path]:
    """Write the video, queue-count CSV and metadata JSON for one sequence."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    video_path = video_dir / f"cad_seq{sequence:02d}.mp4"
    csv_path = annotation_dir / f"cad_seq{sequence:02d}_queue_counts.csv"
    json_path = annotation_dir / f"cad_seq{sequence:02d}.json"
    if not force:
        for path in (video_path, csv_path, json_path):
            if path.exists():
                raise FileExistsError(f"{path} exists; use --force")
    writer: VideoWriter | None = None
    with ExitStack() as stack:
        text, readers = _read_source(source, sequence, stack)
        rows = queue_counts(read_annotations(text), fps)
        write_person_boxes(annotation_dir / f"cad_seq{sequence:02d}_person_boxes.csv", text, fps)
        try:
            for frame in _frames(readers):
                if writer is None:
                    height, width = frame.shape[:2]
                    writer = VideoWriter(video_path, fps, (width, height))
                writer.write(frame)
        finally:
            if writer is not None:
                writer.release()
    reencode_h264(video_path)

    annotation_dir.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(CSV_COLUMNS)
        out.writerows((f"{time_s:.3f}", index, count) for time_s, index, count in rows)
    metadata = {
        "sequence": sequence,
        "fps": fps,
        "fps_assumed": True,
        "frames": len(readers),
        "source": source.name,
    }
    json_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return video_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare Collective Activity Dataset sequences.")
    parser.add_argument("--source", type=Path, required=True, help="CAD zip or extracted folder")
    parser.add_argument("--sequences", type=int, nargs="+", default=list(QUEUEING_SEQUENCES))
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Assumed frame rate (default 30)")
    parser.add_argument("--video-dir", type=Path, default=VIDEO_DIR)
    parser.add_argument("--annotation-dir", type=Path, default=ANNOTATION_DIR)
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs")
    parser.add_argument(
        "--person-boxes-only",
        action="store_true",
        help="Write only cad_seqNN_person_boxes.csv (no video/count/JSON; --force overwrites just that CSV)",
    )
    args = parser.parse_args(argv)
    for sequence in args.sequences:
        if args.person_boxes_only:
            try:
                boxes = prepare_person_boxes(args.source, sequence, args.fps, args.annotation_dir, args.force)
            except (FileExistsError, ValueError, OSError, zipfile.BadZipFile) as error:
                print(f"ERROR: sequence {sequence}: {error}", file=sys.stderr)
                return 1
            print(f"Sequence {sequence}: wrote {boxes}")
            continue
        try:
            video, annotations = prepare_sequence(
                args.source, sequence, args.fps, args.video_dir, args.annotation_dir, args.force
            )
        except (VideoLoadError, FileExistsError, ValueError, OSError, zipfile.BadZipFile) as error:
            print(f"ERROR: sequence {sequence}: {error}", file=sys.stderr)
            return 1
        print(f"Sequence {sequence}: wrote {video} and {annotations}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
