"""SQLite persistence for the API (stdlib ``sqlite3``; one connection per thread)."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_engine.metrics.queue_outputs import TIMESERIES_COLUMNS

BUSY_TIMEOUT_S = 30.0
_BASE_VERSION = 1  # the schema _SCHEMA creates; MIGRATIONS take it forward from here

_TS_TYPES = {
    "time_s": "REAL",
    "frame": "INTEGER",
    "people_in_frame": "INTEGER",
    "raw_queue_count": "INTEGER",
    "queue_length": "REAL",
    "service_rate_per_min": "REAL",
    "service_rate_status": "TEXT",
    "predicted_wait_s": "REAL",
    "predicted_wait_low_s": "REAL",
    "predicted_wait_high_s": "REAL",
    "prediction_status": "TEXT",
}
_TS_COLUMN_SQL = ",\n    ".join(f"{name} {_TS_TYPES[name]}" for name in TIMESERIES_COLUMNS)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
    video_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name    TEXT NOT NULL,
    camera_id        TEXT NOT NULL,
    status           TEXT NOT NULL,
    error            TEXT,
    options          TEXT NOT NULL,
    fps              REAL,
    frames_total     INTEGER,
    frames_processed INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    report           TEXT,
    output_dir       TEXT
);
CREATE TABLE IF NOT EXISTS events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    timestamp_s    REAL NOT NULL,
    detected_s     REAL NOT NULL,
    frame_index    INTEGER NOT NULL,
    person_key     TEXT,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_video_time ON events(video_id, timestamp_s);
CREATE INDEX IF NOT EXISTS events_type ON events(video_id, event_type);
CREATE TABLE IF NOT EXISTS waits (
    video_id   INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    person_key TEXT NOT NULL,
    track_ids  TEXT NOT NULL,
    join_s     REAL NOT NULL,
    service_s  REAL,
    wait_s     REAL,
    abandoned  INTEGER NOT NULL,
    left_s     REAL,
    PRIMARY KEY (video_id, person_key)
);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id          INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    event_type        TEXT NOT NULL,
    timestamp_s       REAL NOT NULL,
    frame_index       INTEGER NOT NULL,
    condition_since_s REAL NOT NULL,
    reasons           TEXT NOT NULL,
    queue_length      REAL NOT NULL,
    predicted_wait_s  REAL
);
CREATE TABLE IF NOT EXISTS timeseries (
    video_id INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    {_TS_COLUMN_SQL}
);
CREATE INDEX IF NOT EXISTS timeseries_video ON timeseries(video_id, time_s);
"""

_FINISHED = ("completed", "failed", "cancelled")
_JSON_COLUMNS = ("options", "report")


@contextlib.contextmanager
def _txn(conn: sqlite3.Connection, commit: bool):
    """Commit on exit (``with conn``), or leave the transaction open for the caller when not ``commit``."""
    if commit:
        with conn:
            yield
    else:
        yield


def _now() -> str:
    return datetime.now(UTC).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    """Open a connection (WAL, foreign keys on, rows as ``sqlite3.Row``), creating the parent dir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S)  # waiting for another writer blocks, not fails
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Alert acknowledgement: who/when an operator acknowledged an alert."""
    conn.execute("ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT")
    conn.execute("ALTER TABLE alerts ADD COLUMN acknowledged_by TEXT")


# (target version, migration from the previous version). Append only; versions are consecutive from 2.
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [(2, _migrate_1_to_2)]


def _read_version(conn: sqlite3.Connection) -> int | None:
    """The stored schema version, or None for a database with no version row (new)."""
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if not has_meta:
        return None
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else None


def _backup_database(conn: sqlite3.Connection, from_version: int) -> Path | None:
    """Copy the database to ``<db>.bak-v<from>-<UTC timestamp>`` with the SQLite backup API (WAL-safe)."""
    file = conn.execute("PRAGMA database_list").fetchone()[2]
    if not file:
        return None  # in-memory database: nothing on disk to protect
    source = Path(file)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = source.with_name(f"{source.name}.bak-v{from_version}-{stamp}")
    counter = 1
    while target.exists():  # two starts in one second must not overwrite an earlier backup
        target = source.with_name(f"{source.name}.bak-v{from_version}-{stamp}-{counter}")
        counter += 1
    # A separate connection reads the committed (pre-migration) state; backing up through `conn` itself
    # would block while it holds the write transaction the caller already opened.
    reader, dest = sqlite3.connect(source, timeout=BUSY_TIMEOUT_S), sqlite3.connect(target)
    try:
        reader.backup(dest)
    finally:
        dest.close()
        reader.close()
    return target


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema for a new database, or migrate an older one (idempotent).

    A new database gets the version 1 tables and then every migration, with no backup. An existing
    database older than the latest version is backed up once, then each pending migration runs in its
    own transaction together with its version bump; a failing migration rolls back and re-raises.
    """
    current = _read_version(conn)
    fresh = current is None
    if fresh:
        with conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (str(_BASE_VERSION),),
            )
        current = _BASE_VERSION
    backed_up = fresh  # a new database has nothing worth a backup
    for version, migrate in MIGRATIONS:
        if version <= current:
            continue
        # IMMEDIATE takes the write lock up front, so a second process waits (busy timeout) here, then
        # re-reads the version below and skips what the first one already applied.
        conn.execute("BEGIN IMMEDIATE")
        try:
            applied = _read_version(conn)
            if applied is not None and applied >= version:
                conn.rollback()
                current = applied
                continue
            if not backed_up:
                _backup_database(conn, applied)
                backed_up = True
            migrate(conn)
            conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(version),))
        except BaseException:
            conn.rollback()
            raise
        conn.commit()
        current = version


def acknowledge_alert(conn: sqlite3.Connection, video_id: int, alert_id: int) -> dict | None:
    """Stamp ``acknowledged_at`` once (UTC, seconds); later calls return the row unchanged. None if absent."""
    with conn:
        conn.execute(
            "UPDATE alerts SET acknowledged_at = ? WHERE alert_id = ? AND video_id = ?"
            " AND acknowledged_at IS NULL",
            (datetime.now(UTC).isoformat(timespec="seconds"), alert_id, video_id),
        )
    row = conn.execute(
        "SELECT * FROM alerts WHERE alert_id = ? AND video_id = ?", (alert_id, video_id)
    ).fetchone()
    return dict(row) if row else None


def create_video(conn: sqlite3.Connection, original_name: str, camera_id: str, options: dict) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO videos (original_name, camera_id, status, options, created_at)"
            " VALUES (?, ?, 'queued', ?, ?)",
            (original_name, camera_id, json.dumps(options), _now()),
        )
    return int(cur.lastrowid)


def set_status(
    conn: sqlite3.Connection, video_id: int, status: str, error: str | None = None, commit: bool = True
) -> None:
    """Update the status; stamps ``started_at`` on processing and ``finished_at`` on a final status."""
    with _txn(conn, commit):
        conn.execute("UPDATE videos SET status = ?, error = ? WHERE video_id = ?", (status, error, video_id))
        if status == "processing":
            conn.execute("UPDATE videos SET started_at = ? WHERE video_id = ?", (_now(), video_id))
        elif status in _FINISHED:
            conn.execute("UPDATE videos SET finished_at = ? WHERE video_id = ?", (_now(), video_id))


def set_media(
    conn: sqlite3.Connection, video_id: int, *, fps: float, frames_total: int | None, output_dir: str
) -> None:
    with conn:
        conn.execute(
            "UPDATE videos SET fps = ?, frames_total = ?, output_dir = ? WHERE video_id = ?",
            (fps, frames_total, output_dir, video_id),
        )


def set_progress(conn: sqlite3.Connection, video_id: int, frames_processed: int, commit: bool = True) -> None:
    with _txn(conn, commit):
        conn.execute(
            "UPDATE videos SET frames_processed = ? WHERE video_id = ?", (frames_processed, video_id)
        )


def add_events(conn: sqlite3.Connection, video_id: int, events: list[dict], commit: bool = True) -> None:
    """Store event payloads, plus a wait row per service/abandon and an alert row per congestion event."""
    with _txn(conn, commit):
        for ev in events:
            kind = ev["event_type"]
            conn.execute(
                "INSERT INTO events (video_id, schema_version, event_type, timestamp_s, detected_s,"
                " frame_index, person_key, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    video_id,
                    ev["schema_version"],
                    kind,
                    ev["timestamp_s"],
                    ev["detected_s"],
                    ev["frame_index"],
                    ev.get("person_key"),
                    json.dumps(ev),
                ),
            )
            if kind in ("service_start", "queue_abandon"):
                served = kind == "service_start"
                conn.execute(
                    "INSERT OR REPLACE INTO waits (video_id, person_key, track_ids, join_s, service_s,"
                    " wait_s, abandoned, left_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        video_id,
                        ev["person_key"],
                        ";".join(str(t) for t in ev["track_ids"]),
                        ev["join_s"],
                        ev["timestamp_s"] if served else None,
                        ev["wait_s"] if served else None,
                        0 if served else 1,
                        None if served else ev["timestamp_s"],
                    ),
                )
            elif kind.startswith("congestion_"):
                conn.execute(
                    "INSERT INTO alerts (video_id, event_type, timestamp_s, frame_index, condition_since_s,"
                    " reasons, queue_length, predicted_wait_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        video_id,
                        kind,
                        ev["timestamp_s"],
                        ev["frame_index"],
                        ev["condition_since_s"],
                        ";".join(ev["reasons"]),
                        ev["queue_length"],
                        ev.get("predicted_wait_s"),
                    ),
                )


def add_timeseries(conn: sqlite3.Connection, video_id: int, rows: list[dict], commit: bool = True) -> None:
    columns = ", ".join(TIMESERIES_COLUMNS)
    marks = ", ".join("?" for _ in range(len(TIMESERIES_COLUMNS) + 1))
    with _txn(conn, commit):
        conn.executemany(
            f"INSERT INTO timeseries (video_id, {columns}) VALUES ({marks})",
            [(video_id, *(row.get(name) for name in TIMESERIES_COLUMNS)) for row in rows],
        )


def set_report(conn: sqlite3.Connection, video_id: int, report: dict, commit: bool = True) -> None:
    with _txn(conn, commit):
        conn.execute("UPDATE videos SET report = ? WHERE video_id = ?", (json.dumps(report), video_id))


def _video_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for name in _JSON_COLUMNS:
        if out[name] is not None:
            out[name] = json.loads(out[name])
    return out


def get_video(conn: sqlite3.Connection, video_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return _video_dict(row) if row else None


def list_videos(conn: sqlite3.Connection) -> list[dict]:
    """All videos, newest first."""
    return [_video_dict(r) for r in conn.execute("SELECT * FROM videos ORDER BY video_id DESC")]


def list_events(
    conn: sqlite3.Connection,
    video_id: int,
    event_type: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    """Decoded event payloads ordered by ``timestamp_s`` then insertion."""
    sql = "SELECT payload FROM events WHERE video_id = ?"
    args: list[Any] = [video_id]
    if event_type is not None:
        sql += " AND event_type = ?"
        args.append(event_type)
    sql += " ORDER BY timestamp_s, event_id LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [json.loads(r["payload"]) for r in conn.execute(sql, args)]


def list_waits(conn: sqlite3.Connection, video_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM waits WHERE video_id = ? ORDER BY join_s, person_key", (video_id,))
    return [dict(r) for r in rows]


def list_alerts(conn: sqlite3.Connection, video_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM alerts WHERE video_id = ? ORDER BY timestamp_s, alert_id", (video_id,))
    return [dict(r) for r in rows]


def list_timeseries(conn: sqlite3.Connection, video_id: int) -> list[dict]:
    columns = ", ".join(TIMESERIES_COLUMNS)
    rows = conn.execute(
        f"SELECT {columns} FROM timeseries WHERE video_id = ? ORDER BY time_s, rowid", (video_id,)
    )
    return [dict(r) for r in rows]


def delete_video(conn: sqlite3.Connection, video_id: int) -> bool:
    with conn:
        cur = conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
    return cur.rowcount > 0


def mark_interrupted(conn: sqlite3.Connection) -> int:
    """Fail every queued or processing video left over from a previous server run."""
    with conn:
        cur = conn.execute(
            "UPDATE videos SET status = 'failed', error = 'interrupted by server restart', finished_at = ?"
            " WHERE status IN ('queued', 'processing')",
            (_now(),),
        )
    return cur.rowcount
