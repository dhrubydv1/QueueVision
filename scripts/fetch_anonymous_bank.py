"""Fetch the Anonymous Bank call-centre data (Technion primary, Bern mirror).

Example::

    python scripts/fetch_anonymous_bank.py

Downloads into ``datasets/raw/anonymous_bank/`` (git-ignored) and records each
file's source URL, size and SHA-256 in ``datasets/metadata/anonymous_bank_files.csv``
(committed; no data). Re-runs skip files whose hash matches the CSV. Any
network/HTTP/hash error stops the run; nothing is substituted. Archives are
untrusted: they are extracted into their own folder and never executed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "QueueVision-fetch/1.0"
CSV_FIELDS = ["source", "name", "url", "local_path", "size_bytes", "sha256"]

TECHNION_BASE = "https://see-center.iem.technion.ac.il/databases/AnonymousBank/Data/"
BERN_HOST = "https://www.math-stat.unibe.ch"
# Hard-coded from the Bern index page (read 2026-11-13).
BERN_PREFIX = (
    "/unibe/portal/fak_naturwis/a_dept_math/a_dept_ms/content/e237483/e237655/e237803/e237840/e237842/"
)
_BERN = [
    ("Documentation_ger.pdf", "files237843"),
    ("January.txt_ger.zip", "files237844"),
    ("February.txt_ger.zip", "files237845"),
    ("March.txt_ger.zip", "files237846"),
    ("April.txt_ger.zip", "files237847"),
    ("May.txt_ger.zip", "files285545"),
    ("June.txt_ger.zip", "files237848"),
    ("July.txt_ger.zip", "files237849"),
    ("August.txt_ger.zip", "files237850"),
    ("September.txt_ger.zip", "files237851"),
    ("October.txt_ger.zip", "files237852"),
    ("November.txt_ger.zip", "files237853"),
    ("December.txt_ger.zip", "files237854"),
]


@dataclass(frozen=True)
class Item:
    source: str  # "technion" | "bern"
    name: str
    url: str
    expected_sha256: str | None = None  # pinned only where known


ITEMS: list[Item] = [
    Item(
        "technion",
        "AnonymousBank.rar",
        TECHNION_BASE + "AnonymousBank.rar",
        "110527823a296e3163566f5d10d687e13859c60d963fd403d4623d89c7cadc4b",
    ),
    Item(
        "technion",
        "AnonymousBank.pdf",
        TECHNION_BASE + "AnonymousBank.pdf",
        "77816db694fdee313a7d612e22199d87632ddca5a82f17c3258bd6799ff6e045",
    ),
] + [Item("bern", n, f"{BERN_HOST}{BERN_PREFIX}{d}/{n}") for n, d in _BERN]

Downloader = Callable[[str, Path], None]


class FetchError(RuntimeError):
    pass


def http_download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` via a ``.part`` file, then rename."""
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
    except OSError as exc:  # URLError/HTTPError are OSError subclasses
        part.unlink(missing_ok=True)
        raise FetchError(f"download failed for {url}: {exc}") from exc
    part.replace(dest)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["local_path"]: r for r in csv.DictReader(fh)}


def write_manifest(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, lineterminator="\n")
        w.writeheader()
        for key in sorted(rows):
            w.writerow(rows[key])


def safe_extract_zip(zip_path: Path, out_dir: Path) -> list[str]:
    """Extract a zip, rejecting absolute paths and ``..`` components (zip-slip)."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            p = PurePosixPath(name.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or (len(name) > 1 and name[1] == ":"):
                raise FetchError(f"unsafe path in {zip_path.name}: {name!r}")
        out_dir.mkdir(parents=True, exist_ok=True)
        zf.extractall(out_dir)
        return [i.filename for i in zf.infolist()]


def extract_rar(rar_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["bsdtar", "-xf", str(rar_path), "-C", str(out_dir)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        raise FetchError(
            f"bsdtar failed on {rar_path.name}: {exc} {detail.decode(errors='replace')}"
        ) from exc


def fetch_all(
    raw_dir: Path,
    manifest_path: Path,
    downloader: Downloader = http_download,
    items: list[Item] | None = None,
    extract: bool = True,
    log: Callable[[str], None] = print,
) -> dict[str, dict[str, str]]:
    items = ITEMS if items is None else items
    rows = read_manifest(manifest_path)
    for item in items:
        dest = raw_dir / item.source / item.name
        key = dest.relative_to(raw_dir).as_posix()
        known = rows.get(key, {}).get("sha256") or item.expected_sha256
        fresh = False
        if dest.exists() and known and sha256_of(dest) == known:
            digest = known
            log(f"skip (hash matches): {key}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            log(f"download: {item.url}")
            downloader(item.url, dest)
            digest = sha256_of(dest)
            fresh = True
            if item.expected_sha256 and digest != item.expected_sha256:
                raise FetchError(
                    f"SHA-256 mismatch for {item.name}: got {digest}, expected {item.expected_sha256}"
                )
        rows[key] = {
            "source": item.source,
            "name": item.name,
            "url": item.url,
            "local_path": key,
            "size_bytes": str(dest.stat().st_size),
            "sha256": digest,
        }
        write_manifest(manifest_path, rows)
        if extract and item.name.endswith((".rar", ".zip")):
            out = dest.parent / "extracted"
            if fresh or not out.exists():
                log(f"extract: {key} -> {out}")
                (extract_rar if item.name.endswith(".rar") else safe_extract_zip)(dest, out)
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch the Anonymous Bank call-centre data.")
    p.add_argument("--raw-dir", type=Path, default=Path("datasets/raw/anonymous_bank"))
    p.add_argument("--manifest", type=Path, default=Path("datasets/metadata/anonymous_bank_files.csv"))
    p.add_argument("--root", type=Path, default=REPO_ROOT)
    p.add_argument("--no-extract", action="store_true")
    args = p.parse_args(argv)
    root = args.root.resolve()
    raw = args.raw_dir if args.raw_dir.is_absolute() else root / args.raw_dir
    man = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        rows = fetch_all(raw, man, extract=not args.no_extract)
    except FetchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(rows)} files recorded in {man}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
