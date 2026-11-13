"""Validate the dataset manifest, licence register, splits and video files.

Pure standard library plus OpenCV; importable without torch.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import cv2

REQUIRED_MANIFEST_COLUMNS = [
    "clip_id",
    "dataset",
    "source_sequence",
    "acquisition_status",
    "original_path",
    "video_path",
    "annotation_paths",
    "annotation_types",
    "split",
    "license_id",
    "expected_fps",
    "fps_assumed",
    "notes",
]
REQUIRED_LICENSE_COLUMNS = [
    "license_id",
    "dataset",
    "license_name",
    "license_url",
    "terms_verified",
    "redistribution_permitted",
    "commercial_use_permitted",
    "attribution_required",
    "source_url",
    "last_checked",
    "notes",
]
VALID_SPLITS = {"train", "val", "test", ""}
SPLIT_NAMES = ("train", "val", "test")
FPS_TOLERANCE = 0.5


class ManifestError(ValueError):
    """Raised when a manifest or register cannot be parsed."""


@dataclass
class VideoInfo:
    fps: float
    width: int
    height: int
    frame_count: int
    duration_s: float
    codec: str
    first_frame_ok: bool
    last_frame_ok: bool


@dataclass
class Issue:
    severity: str  # "error" or "warning"
    code: str
    message: str


@dataclass
class ClipReport:
    clip_id: str
    dataset: str
    acquisition_status: str
    status: str = "ok"
    video: VideoInfo | None = None
    issues: list[Issue] = field(default_factory=list)


@dataclass
class ValidationReport:
    generated_at: str
    manifest: str
    summary: dict[str, int]
    clips: list[ClipReport]
    global_issues: list[Issue]
    structural_errors: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def exit_code(self) -> int:
        clip_errors = any(
            i.severity == "error" for c in self.clips if c.acquisition_status == "acquired" for i in c.issues
        )
        return 1 if clip_errors or self.structural_errors else 0


def read_csv_rows(path: Path, required: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ManifestError(f"file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise ManifestError(f"{path}: missing required columns: {', '.join(missing)}")
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def probe_video(path: Path) -> VideoInfo | None:
    """Open a video, read first and near-last frames. None if it cannot be opened."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        codec = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")
        first_ok, _ = cap.read()
        last_ok = False
        if frames > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(frames - 2, 0))
            last_ok, _ = cap.read()
        duration = frames / fps if fps > 0 else 0.0
        return VideoInfo(
            fps=round(fps, 3),
            width=width,
            height=height,
            frame_count=frames,
            duration_s=round(duration, 3),
            codec=codec,
            first_frame_ok=bool(first_ok),
            last_frame_ok=bool(last_ok),
        )
    finally:
        cap.release()


def read_split_files(splits_dir: Path) -> tuple[dict[str, set[str]], list[Issue]]:
    splits: dict[str, set[str]] = {}
    issues: list[Issue] = []
    for name in SPLIT_NAMES:
        path = splits_dir / f"{name}.txt"
        clips: set[str] = set()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    clips.add(line)
        else:
            issues.append(Issue("error", "split_file_missing", f"split file not found: {path}"))
        splits[name] = clips
    return splits, issues


def _split_issues(rows: list[dict[str, str]], splits: dict[str, set[str]]) -> list[Issue]:
    issues: list[Issue] = []
    manifest_ids = {r["clip_id"] for r in rows}
    seen: dict[str, str] = {}
    for name, clips in splits.items():
        for clip in sorted(clips):
            if clip in seen:
                issues.append(
                    Issue(
                        "error",
                        "split_duplicate",
                        f"clip {clip} is in both {seen[clip]}.txt and {name}.txt",
                    )
                )
            else:
                seen[clip] = name
            if clip not in manifest_ids:
                issues.append(
                    Issue(
                        "error",
                        "split_clip_not_in_manifest",
                        f"clip {clip} in {name}.txt is not in the manifest",
                    )
                )
    return issues


def _num(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def validate(
    manifest_path: Path,
    licenses_path: Path,
    splits_dir: Path,
    root: Path,
) -> ValidationReport:
    rows = read_csv_rows(manifest_path, REQUIRED_MANIFEST_COLUMNS)
    licenses = {r["license_id"]: r for r in read_csv_rows(licenses_path, REQUIRED_LICENSE_COLUMNS)}
    splits, global_issues = read_split_files(splits_dir)
    global_issues.extend(_split_issues(rows, splits))

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["clip_id"]] = counts.get(r["clip_id"], 0) + 1
    for clip_id, n in sorted(counts.items()):
        if n > 1:
            global_issues.append(Issue("error", "duplicate_clip_id", f"clip_id {clip_id} appears {n} times"))

    clips: list[ClipReport] = []
    unverified: set[str] = set()
    for r in rows:
        clip = ClipReport(r["clip_id"], r["dataset"], r["acquisition_status"])
        clips.append(clip)
        iss = clip.issues

        def add(sev: str, code: str, msg: str, iss: list[Issue] = iss) -> None:
            iss.append(Issue(sev, code, msg))

        if not r["clip_id"]:
            add("error", "missing_clip_id", "clip_id is empty")
        if r["acquisition_status"] not in {"acquired", "planned"}:
            add("error", "invalid_acquisition_status", f"unknown status {r['acquisition_status']!r}")
        if r["split"] not in VALID_SPLITS:
            add("error", "invalid_split", f"split {r['split']!r} not in train/val/test/empty")
        else:
            file_split = next((n for n, c in splits.items() if r["clip_id"] in c), "")
            if file_split != r["split"]:
                add(
                    "error",
                    "split_mismatch",
                    f"manifest split {r['split']!r} but splits files say {file_split!r}",
                )
        lic = licenses.get(r["license_id"])
        if lic is None:
            add("error", "license_not_registered", f"license_id {r['license_id']!r} not in register")
        elif lic["terms_verified"].lower() != "yes":
            unverified.add(r["license_id"])
            add("warning", "license_unverified", f"terms for {r['license_id']} not verified")

        if r["acquisition_status"] == "planned":
            clip.status = "planned_not_acquired"
            # Only structural issues are meaningful for planned rows.
            continue
        if r["acquisition_status"] != "acquired":
            clip.status = "invalid_status"
            continue

        status = "ok"
        video_rel = r["video_path"]
        if not video_rel or not (root / video_rel).is_file():
            add("error", "missing_video", f"video not found: {video_rel or '(empty)'}")
            status = "missing_video"
        else:
            info = probe_video(root / video_rel)
            clip.video = info
            if info is None:
                add("error", "unreadable_video", f"cannot open video: {video_rel}")
                status = "unreadable_video"
            elif not info.first_frame_ok or info.frame_count <= 0:
                add("error", "unreadable_video", "first frame could not be read")
                status = "unreadable_video"
            elif not info.last_frame_ok:
                add("error", "truncated_video", "frame near the end could not be read")
                status = "unreadable_video"
            else:
                expected = _num(r["expected_fps"])
                if expected is not None and abs(info.fps - expected) > FPS_TOLERANCE:
                    add(
                        "warning",
                        "fps_mismatch",
                        f"measured fps {info.fps} differs from expected {expected}",
                    )
        if r["original_path"] and not (root / r["original_path"]).exists():
            add("error", "missing_original", f"original not found: {r['original_path']}")
        paths = [p for p in r["annotation_paths"].split(";") if p.strip()]
        if not paths:
            add("error", "missing_annotations", "no annotation paths listed")
            if status == "ok":
                status = "missing_annotations"
        else:
            absent = [p for p in paths if not (root / p.strip()).exists()]
            for p in absent:
                add("error", "missing_annotation_file", f"annotation not found: {p}")
            if absent and status == "ok":
                status = "missing_annotations"
        if status == "ok" and any(i.severity == "error" for i in iss):
            status = "error"
        clip.status = status

    acquired = [c for c in clips if c.acquisition_status == "acquired"]
    summary = {
        "clips_total": len(clips),
        "acquired": len(acquired),
        "planned_not_acquired": sum(c.status == "planned_not_acquired" for c in clips),
        "video_found": sum(c.status != "missing_video" for c in acquired),
        "video_readable": sum(
            c.video is not None and c.video.first_frame_ok and c.video.last_frame_ok for c in acquired
        ),
        "annotated": sum(
            not any(i.code.startswith("missing_annotation") for i in c.issues) for c in acquired
        ),
        "license_verified": sum(
            1
            for c in acquired
            if licenses.get(next((r["license_id"] for r in rows if r["clip_id"] == c.clip_id), ""), {})
            .get("terms_verified", "")
            .lower()
            == "yes"
        ),
        "errors": 0,
        "warnings": 0,
    }
    all_issues = global_issues + [i for c in clips for i in c.issues]
    summary["errors"] = sum(i.severity == "error" for i in all_issues)
    summary["warnings"] = sum(i.severity == "warning" for i in all_issues)
    structural = sum(i.severity == "error" for i in global_issues) + sum(
        1
        for c in clips
        for i in c.issues
        if i.severity == "error"
        and i.code
        in {
            "invalid_split",
            "split_mismatch",
            "license_not_registered",
            "invalid_acquisition_status",
            "missing_clip_id",
        }
        and c.acquisition_status != "acquired"
    )
    return ValidationReport(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        manifest=str(manifest_path),
        summary=summary,
        clips=clips,
        global_issues=global_issues,
        structural_errors=structural,
    )


def write_report(report: ValidationReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
