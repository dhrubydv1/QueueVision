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


BERN_SHA256 = {
    "April.txt_ger.zip": ("bb5dfa84489c0ad970e056b5dbd8a12933b884e89e404a2d8192e2599d7ebc6d"),
    "August.txt_ger.zip": ("4e559a2a5baccd069a2ac599efe0caa151eb9b9870bdfe9821bb60fd12588292"),
    "December.txt_ger.zip": ("918fbee41c2fbbb74ea9c80db00c88dd4d2e4cc201905a347b1624ec27b7b691"),
    "Documentation_ger.pdf": ("77816db694fdee313a7d612e22199d87632ddca5a82f17c3258bd6799ff6e045"),
    "February.txt_ger.zip": ("eabc2129d38499fa758407b186a869bd8df19c860709bf5cb4dac08f87d0e474"),
    "January.txt_ger.zip": ("a9529831d80bca9d28b8518c8ba84753505832b220af8a423dd33702808f3541"),
    "July.txt_ger.zip": ("4b6d0a92bc789e395001f413f37643e43eabc81b222ab3c118576315cef82c34"),
    "June.txt_ger.zip": ("9a1fd1e99942887fc6e458e1240962e82814b6832219bd2592fe2fe16ae504e7"),
    "March.txt_ger.zip": ("7ff75b5c575d74de9cbcdfc60707ac244e037488d46746a518c6b0e818503e03"),
    "May.txt_ger.zip": ("201d1daed1437593712c50773fcc55331c0a4741a2cd32e29c969cec97360c5b"),
    "November.txt_ger.zip": ("4414687dcbd206fdb94a9fe878186ba0f1d44ee240650c37f0780c89db1ad15c"),
    "October.txt_ger.zip": ("1cb6d018d8b3624aa70b93756209047965be2d3f11d07150336f9ef06adb0eca"),
    "September.txt_ger.zip": ("068a05d0f4498dbd7cba4bf98968581ab6437de50e37bbcf48f5ae261813df06"),
}


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
] + [Item("bern", n, f"{BERN_HOST}{BERN_PREFIX}{d}/{n}", BERN_SHA256[n]) for n, d in _BERN]

Downloader = Callable[[str, Path], None]


class FetchError(RuntimeError):
    pass


def http_download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` via a ``.part`` file, then rename.

    Raises FetchError on network errors or a short read (Content-Length check).
    """
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, part.open("wb") as fh:
            expected = resp.headers.get("Content-Length")
            shutil.copyfileobj(resp, fh, 1 << 20)
        written = part.stat().st_size
        if expected is not None and int(expected) != written:
            raise FetchError(f"short read for {url}: got {written} of {expected} bytes")
    except OSError as exc:  # URLError/HTTPError are OSError subclasses
        part.unlink(missing_ok=True)
        raise FetchError(f"download failed for {url}: {exc}") from exc
    except FetchError:
        part.unlink(missing_ok=True)
        raise
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
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FetchError(f"{zip_path.name} is not a valid zip: {exc}") from exc
    with zf:
        for info in zf.infolist():
            name = info.filename
            p = PurePosixPath(name.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts or (len(name) > 1 and name[1] == ":"):
                raise FetchError(f"unsafe path in {zip_path.name}: {name!r}")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            zf.extractall(out_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            raise FetchError(f"extraction of {zip_path.name} failed: {exc}") from exc
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


def merge_dir(src: Path, dst: Path) -> None:
    """Move the contents of ``src`` into ``dst`` (created if needed), replacing same-named files."""
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if target.is_dir() and child.is_dir():
            merge_dir(child, target)
        else:
            if target.is_dir():
                shutil.rmtree(target)
            child.replace(target)


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
        marker = dest.with_name(f".extracted_{item.name}")
        if dest.exists() and known and sha256_of(dest) == known:
            digest = known
            log(f"skip (hash matches): {key}")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            log(f"download: {item.url}")
            staged = dest.with_name(item.name + ".dl")
            staged.unlink(missing_ok=True)
            try:
                downloader(item.url, staged)
                digest = sha256_of(staged)
                if item.expected_sha256 and digest != item.expected_sha256:
                    raise FetchError(
                        f"SHA-256 mismatch for {item.name}: got {digest}, expected {item.expected_sha256}"
                    )
            except BaseException:
                staged.unlink(missing_ok=True)
                raise
            staged.replace(dest)
            marker.unlink(missing_ok=True)
        rows[key] = {
            "source": item.source,
            "name": item.name,
            "url": item.url,
            "local_path": key,
            "size_bytes": str(dest.stat().st_size),
            "sha256": digest,
        }
        write_manifest(manifest_path, rows)
        if extract and item.name.endswith((".rar", ".zip")) and not marker.exists():
            out = dest.parent / "extracted"
            tmp = dest.parent / "extracted.tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            log(f"extract: {key} -> {out}")
            try:
                (extract_rar if item.name.endswith(".rar") else safe_extract_zip)(dest, tmp)
                merge_dir(tmp, out)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            marker.write_text(digest + "\n", encoding="utf-8")
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
