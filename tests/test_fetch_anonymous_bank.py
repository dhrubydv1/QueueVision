"""Tests for the Anonymous Bank fetch script (fake downloader, no network)."""

import hashlib
import zipfile
from pathlib import Path
from typing import ClassVar

import pytest

from scripts import fetch_anonymous_bank as fab


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for n, d in entries.items():
            zf.writestr(n, d)


def test_item_list_shape():
    assert len(fab.ITEMS) == 15
    assert sum(i.source == "bern" for i in fab.ITEMS) == 13
    assert all(i.url.startswith("https://") for i in fab.ITEMS)


def test_download_records_and_skips(tmp_path):
    calls = []

    def fake(url, dest):
        calls.append(url)
        dest.write_bytes(b"payload-" + url.encode())

    items = [fab.Item("bern", "a.pdf", "https://x/a.pdf"), fab.Item("technion", "b.pdf", "https://x/b.pdf")]
    man = tmp_path / "m.csv"
    rows = fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    assert len(calls) == 2
    r = rows["bern/a.pdf"]
    assert r["sha256"] == hashlib.sha256(b"payload-https://x/a.pdf").hexdigest()
    assert r["size_bytes"] == str(len(b"payload-https://x/a.pdf"))
    assert man.read_text().splitlines()[0] == ",".join(fab.CSV_FIELDS)
    fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    assert len(calls) == 2  # skipped on second run


def test_hash_mismatch_redownloads(tmp_path):
    def fake(url, dest):
        dest.write_bytes(b"good")

    items = [fab.Item("bern", "a.pdf", "https://x/a.pdf")]
    man = tmp_path / "m.csv"
    fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    (tmp_path / "raw/bern/a.pdf").write_bytes(b"corrupt")
    fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    assert (tmp_path / "raw/bern/a.pdf").read_bytes() == b"good"


def test_pinned_hash_mismatch_stops(tmp_path):
    def fake(url, dest):
        dest.write_bytes(b"other")

    items = [fab.Item("technion", "a.pdf", "https://x/a.pdf", "0" * 64)]
    with pytest.raises(fab.FetchError, match="mismatch"):
        fab.fetch_all(tmp_path / "raw", tmp_path / "m.csv", fake, items, log=lambda m: None)


def test_download_error_propagates(tmp_path):
    def boom(url, dest):
        raise fab.FetchError("network down")

    with pytest.raises(fab.FetchError, match="network down"):
        fab.fetch_all(
            tmp_path / "raw", tmp_path / "m.csv", boom, [fab.Item("bern", "a.pdf", "u")], log=lambda m: None
        )


def test_zip_extracted_and_slip_rejected(tmp_path):
    def fake(url, dest):
        _zip(dest, {"January.txt": b"row\n"})

    items = [fab.Item("bern", "January.txt_ger.zip", "https://x/j.zip")]
    fab.fetch_all(tmp_path / "raw", tmp_path / "m.csv", fake, items, log=lambda m: None)
    assert (tmp_path / "raw/bern/extracted/January.txt").read_bytes() == b"row\n"

    bad = tmp_path / "bad.zip"
    for name in ("../evil.txt", "/abs.txt"):
        _zip(bad, {name: b"x"})
        with pytest.raises(fab.FetchError, match="unsafe"):
            fab.safe_extract_zip(bad, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_failed_extraction_is_retried(tmp_path, monkeypatch):
    good = tmp_path / "good.zip"
    _zip(good, {"January.txt": b"row\n"})

    def fake(url, dest):
        dest.write_bytes(good.read_bytes())

    items = [fab.Item("bern", "January.txt_ger.zip", "https://x/j.zip")]
    man = tmp_path / "m.csv"
    real = fab.safe_extract_zip

    def failing(zp, out):
        out.mkdir(parents=True)
        (out / "partial.txt").write_bytes(b"p")
        raise fab.FetchError("boom")

    monkeypatch.setattr(fab, "safe_extract_zip", failing)
    with pytest.raises(fab.FetchError, match="boom"):
        fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    assert not (tmp_path / "raw/bern/extracted.tmp").exists()
    assert not (tmp_path / "raw/bern/extracted/partial.txt").exists()
    monkeypatch.setattr(fab, "safe_extract_zip", real)
    fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    assert (tmp_path / "raw/bern/extracted/January.txt").exists()


def test_extraction_retried_without_redownload(tmp_path):
    good = tmp_path / "good.zip"
    _zip(good, {"a.txt": b"x"})
    calls = []

    def fake(url, dest):
        calls.append(url)
        dest.write_bytes(good.read_bytes())

    items = [fab.Item("bern", "a.zip", "https://x/a.zip")]
    man = tmp_path / "m.csv"
    fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    for f in (tmp_path / "raw/bern/extracted").iterdir():
        f.unlink()
    (tmp_path / "raw/bern/.extracted_a.zip").unlink()
    fab.fetch_all(tmp_path / "raw", man, fake, items, log=lambda m: None)
    assert len(calls) == 1
    assert (tmp_path / "raw/bern/extracted/a.txt").exists()


def test_pinned_mismatch_leaves_no_file(tmp_path):
    def fake(url, dest):
        dest.write_bytes(b"other")

    items = [fab.Item("technion", "a.pdf", "https://x/a.pdf", "0" * 64)]
    with pytest.raises(fab.FetchError):
        fab.fetch_all(tmp_path / "raw", tmp_path / "m.csv", fake, items, log=lambda m: None)
    assert list((tmp_path / "raw/technion").iterdir()) == []


def test_short_read_raises(tmp_path, monkeypatch):
    class Resp:
        headers: ClassVar[dict[str, str]] = {"Content-Length": "10"}

        def __init__(self):
            self._d = [b"abc", b""]

        def read(self, n=-1):
            return self._d.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fab.urllib.request, "urlopen", lambda *a, **k: Resp())
    dest = tmp_path / "f.zip"
    with pytest.raises(fab.FetchError, match="short read"):
        fab.http_download("https://x/f.zip", dest)
    assert not dest.exists()
    assert not (tmp_path / "f.zip.part").exists()


def test_bern_items_pinned():
    assert all(i.expected_sha256 for i in fab.ITEMS)
