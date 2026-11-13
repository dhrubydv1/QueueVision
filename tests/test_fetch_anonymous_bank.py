"""Tests for the Anonymous Bank fetch script (fake downloader, no network)."""

import hashlib
import zipfile
from pathlib import Path

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
