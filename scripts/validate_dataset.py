"""Validate the dataset manifest, licence register, splits and videos.

Example::

    python scripts/validate_dataset.py --manifest datasets/metadata/dataset_manifest.csv

Writes ``outputs/metrics/dataset_validation_report.json``. Exit code 1 if an
acquired clip has an error or the manifest has structural errors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ai_engine.data.validation import ManifestError, validate, write_report

REPO_ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate the QueueVision dataset manifest.")
    p.add_argument("--manifest", type=Path, default=Path("datasets/metadata/dataset_manifest.csv"))
    p.add_argument("--licenses", type=Path, default=Path("datasets/metadata/dataset_licensing_register.csv"))
    p.add_argument("--splits-dir", type=Path, default=Path("datasets/splits"))
    p.add_argument("--root", type=Path, default=REPO_ROOT)
    p.add_argument("--output", type=Path, default=Path("outputs/metrics/dataset_validation_report.json"))
    args = p.parse_args(argv)

    root = args.root.resolve()

    def rel(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    try:
        report = validate(rel(args.manifest), rel(args.licenses), rel(args.splits_dir), root)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    write_report(report, rel(args.output))

    s = report.summary
    print(f"Dataset validation: {s['clips_total']} clips")
    print(
        f"  acquired {s['acquired']}, planned (not acquired) {s['planned_not_acquired']}, "
        f"video found {s['video_found']}, readable {s['video_readable']}, annotated {s['annotated']}"
    )
    print(f"  licences verified {s['license_verified']}, errors {s['errors']}, warnings {s['warnings']}")
    for issue in report.global_issues:
        print(f"  [{issue.severity}] {issue.code}: {issue.message}")
    for c in report.clips:
        for i in c.issues:
            if i.severity == "error" and c.acquisition_status == "acquired":
                print(f"  [error] {c.clip_id}: {i.code}: {i.message}")
    print(f"  report: {rel(args.output)}")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
