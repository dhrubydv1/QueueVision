"""SQLite store tests (torch-free, temporary database)."""

from __future__ import annotations

import re
import sqlite3

import pytest

from ai_engine.queue.event_schema import (
    CongestionEvent,
    QueueAbandonEvent,
    QueueJoinEvent,
    ServiceStartEvent,
)
from api import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "sub" / "queuevision.db")
    db.init_db(connection)
    yield connection
    connection.close()


def _events():
    return [
        QueueJoinEvent("p1", 28, 1.0, 10, 3.0).to_dict(),
        ServiceStartEvent("p1", (28, 70), 20.0, 200, 20.5, 1.0, 19.0).to_dict(),
        QueueAbandonEvent("p2", (5,), 30.0, 300, 31.0, 4.0, 26.0).to_dict(),
        CongestionEvent(
            "congestion_start", 40.0, 400, 41.0, 35.0, ("queue_length", "predicted_wait"), 6.0, 120.0
        ).to_dict(),
    ]


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_init_twice_and_schema_version(conn):
    db.init_db(conn)
    rows = conn.execute("SELECT key, value FROM schema_meta").fetchall()
    assert [(r["key"], r["value"]) for r in rows] == [("schema_version", "2")]
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_create_and_status_transitions(conn):
    vid = db.create_video(conn, "a.mp4", "cam1", {"frame_stride": 2})
    video = db.get_video(conn, vid)
    assert video["status"] == "queued"
    assert video["options"] == {"frame_stride": 2}
    assert video["created_at"] and video["started_at"] is None
    db.set_status(conn, vid, "processing")
    video = db.get_video(conn, vid)
    assert video["started_at"] and video["finished_at"] is None
    db.set_status(conn, vid, "failed", "boom")
    video = db.get_video(conn, vid)
    assert (video["status"], video["error"]) == ("failed", "boom")
    assert video["finished_at"]


def test_media_progress_report(conn):
    vid = db.create_video(conn, "a.mp4", "cam1", {})
    db.set_media(conn, vid, fps=25.0, frames_total=None, output_dir="out/x")
    db.set_progress(conn, vid, 12)
    db.set_report(conn, vid, {"mean_wait_s": 3.5})
    video = db.get_video(conn, vid)
    assert (video["fps"], video["frames_total"], video["output_dir"]) == (25.0, None, "out/x")
    assert video["frames_processed"] == 12
    assert video["report"] == {"mean_wait_s": 3.5}
    assert db.get_video(conn, 999) is None
    assert [v["video_id"] for v in db.list_videos(conn)] == [vid]


def test_add_events_derives_waits_and_alerts(conn):
    vid = db.create_video(conn, "a.mp4", "cam1", {})
    events = _events()
    db.add_events(conn, vid, events)
    assert _count(conn, "events") == 4
    waits = db.list_waits(conn, vid)
    assert [(w["track_ids"], w["abandoned"], w["service_s"], w["left_s"]) for w in waits] == [
        ("28;70", 0, 20.0, None),
        ("5", 1, None, 30.0),
    ]
    alerts = db.list_alerts(conn, vid)
    assert len(alerts) == 1 and alerts[0]["reasons"] == "queue_length;predicted_wait"
    assert db.list_events(conn, vid, event_type="service_start") == [events[1]]
    assert db.list_events(conn, vid) == events
    assert db.list_events(conn, vid, limit=1, offset=1) == [events[1]]


def test_timeseries_roundtrip(conn):
    vid = db.create_video(conn, "a.mp4", "cam1", {})
    row = {"time_s": 0.0, "frame": 0, "people_in_frame": 3, "queue_length": 2.0, "prediction_status": "ok"}
    db.add_timeseries(conn, vid, [row])
    out = db.list_timeseries(conn, vid)
    assert len(out) == 1 and out[0]["people_in_frame"] == 3 and out[0]["predicted_wait_s"] is None


def test_delete_cascades_and_ids_not_reused(conn):
    vid = db.create_video(conn, "a.mp4", "cam1", {})
    db.add_events(conn, vid, _events())
    db.add_timeseries(conn, vid, [{"time_s": 0.0, "frame": 0}])
    assert db.delete_video(conn, vid) is True
    assert db.delete_video(conn, vid) is False
    for table in ("events", "waits", "alerts", "timeseries"):
        assert _count(conn, table) == 0
    assert db.create_video(conn, "b.mp4", "cam1", {}) > vid


def test_mark_interrupted(conn):
    queued = db.create_video(conn, "a.mp4", "c", {})
    processing = db.create_video(conn, "b.mp4", "c", {})
    done = db.create_video(conn, "c.mp4", "c", {})
    db.set_status(conn, processing, "processing")
    db.set_status(conn, done, "completed")
    assert db.mark_interrupted(conn) == 2
    assert db.get_video(conn, queued)["error"] == "interrupted by server restart"
    assert db.get_video(conn, processing)["status"] == "failed"
    assert db.get_video(conn, done)["status"] == "completed"


def test_service_complete_and_queue_exit_are_events_only_no_wait_rows(conn):
    from ai_engine.queue.event_schema import QueueExitEvent, ServiceCompleteEvent

    vid = db.create_video(conn, "a.mp4", "cam1", {})
    events = [
        ServiceStartEvent("p1", (28,), 20.0, 200, 20.0, 1.0, 19.0).to_dict(),
        ServiceCompleteEvent("p1", (28,), 30.0, 300, 31.0, 20.0, 10.0).to_dict(),
        QueueExitEvent("p1", (28,), 32.0, 320, 34.0, "served").to_dict(),
    ]
    db.add_events(conn, vid, events)
    assert db.list_events(conn, vid) == events
    assert db.list_events(conn, vid, event_type="queue_exit") == [events[2]]
    assert len(db.list_waits(conn, vid)) == 1  # only the service_start
    assert _count(conn, "alerts") == 0


# --- migrations -----------------------------------------------------------------------------------

# The version 1 schema exactly as shipped at b5bb0f9 (the timeseries columns are fixed by the old
# TIMESERIES_COLUMNS list, so it is copied here instead of imported).
_V1_DDL = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE videos (
    video_id INTEGER PRIMARY KEY AUTOINCREMENT, original_name TEXT NOT NULL, camera_id TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT, options TEXT NOT NULL, fps REAL, frames_total INTEGER,
    frames_processed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT,
    finished_at TEXT, report TEXT, output_dir TEXT
);
CREATE TABLE events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    schema_version TEXT NOT NULL, event_type TEXT NOT NULL, timestamp_s REAL NOT NULL,
    detected_s REAL NOT NULL, frame_index INTEGER NOT NULL, person_key TEXT, payload TEXT NOT NULL
);
CREATE TABLE waits (
    video_id INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    person_key TEXT NOT NULL, track_ids TEXT NOT NULL, join_s REAL NOT NULL, service_s REAL,
    wait_s REAL, abandoned INTEGER NOT NULL, left_s REAL, PRIMARY KEY (video_id, person_key)
);
CREATE TABLE alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL, timestamp_s REAL NOT NULL, frame_index INTEGER NOT NULL,
    condition_since_s REAL NOT NULL, reasons TEXT NOT NULL, queue_length REAL NOT NULL,
    predicted_wait_s REAL
);
CREATE TABLE timeseries (
    video_id INTEGER NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    time_s REAL, frame INTEGER
);
INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1');
INSERT INTO videos (original_name, camera_id, status, options, created_at)
    VALUES ('old.mp4', 'cam1', 'completed', '{}', '2026-01-01T00:00:00+00:00');
INSERT INTO alerts (video_id, event_type, timestamp_s, frame_index, condition_since_s, reasons,
    queue_length, predicted_wait_s)
    VALUES (1, 'congestion_start', 40.0, 400, 35.0, 'queue_length', 6.0, 120.0);
"""


def _v1_db(tmp_path):
    path = tmp_path / "queuevision.db"
    raw = db.connect(path)
    raw.executescript(_V1_DDL)
    raw.commit()
    raw.close()
    return path


def _backups(path):
    return sorted(path.parent.glob(f"{path.name}.bak-*"))


def _version(conn):
    return conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]


def _columns(conn, table="alerts"):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def test_fresh_database_is_latest_without_backup(tmp_path):
    path = tmp_path / "queuevision.db"
    conn = db.connect(path)
    db.init_db(conn)
    assert _version(conn) == str(db.MIGRATIONS[-1][0]) == "2"
    assert {"acknowledged_at", "acknowledged_by"} <= set(_columns(conn))
    assert _backups(path) == []
    conn.close()


def test_migrates_v1_database_keeping_rows_and_backing_up(tmp_path):
    path = _v1_db(tmp_path)
    conn = db.connect(path)
    db.init_db(conn)
    assert _version(conn) == "2"
    assert {"acknowledged_at", "acknowledged_by"} <= set(_columns(conn))
    alert = db.list_alerts(conn, 1)[0]
    assert (alert["event_type"], alert["queue_length"], alert["predicted_wait_s"]) == (
        "congestion_start",
        6.0,
        120.0,
    )
    assert alert["acknowledged_at"] is None and alert["acknowledged_by"] is None
    assert db.get_video(conn, 1)["original_name"] == "old.mp4"
    (backup,) = _backups(path)
    assert re.fullmatch(r"queuevision\.db\.bak-v1-\d{8}T\d{6}Z", backup.name)
    old = sqlite3.connect(backup)
    assert "acknowledged_at" not in [r[1] for r in old.execute("PRAGMA table_info(alerts)")]
    assert old.execute("SELECT value FROM schema_meta").fetchone()[0] == "1"
    assert old.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1
    old.close()
    conn.close()


def test_second_init_is_a_noop(tmp_path):
    path = _v1_db(tmp_path)
    conn = db.connect(path)
    db.init_db(conn)
    before = _backups(path)
    db.init_db(conn)
    conn.close()
    conn = db.connect(path)
    db.init_db(conn)
    assert _backups(path) == before and len(before) == 1
    assert _version(conn) == "2"
    conn.close()


def test_failing_migration_rolls_back_and_keeps_backup(tmp_path, monkeypatch):
    def boom(conn):
        conn.execute("ALTER TABLE alerts ADD COLUMN half_done TEXT")
        raise RuntimeError("migration failed")

    path = _v1_db(tmp_path)
    monkeypatch.setattr(db, "MIGRATIONS", [(2, boom)])
    conn = db.connect(path)
    with pytest.raises(RuntimeError, match="migration failed"):
        db.init_db(conn)
    assert _version(conn) == "1"
    assert "half_done" not in _columns(conn)
    assert len(_backups(path)) == 1
    assert not conn.in_transaction
    conn.close()


def test_later_migration_failure_keeps_earlier_ones(tmp_path, monkeypatch):
    def add_three(conn):
        conn.execute("ALTER TABLE alerts ADD COLUMN three TEXT")

    def fail(conn):
        raise RuntimeError("nope")

    path = _v1_db(tmp_path)
    monkeypatch.setattr(db, "MIGRATIONS", [(2, db._migrate_1_to_2), (3, add_three), (4, fail)])
    conn = db.connect(path)
    with pytest.raises(RuntimeError):
        db.init_db(conn)
    assert _version(conn) == "3" and "three" in _columns(conn)
    assert len(_backups(path)) == 1
    conn.close()


def test_acknowledge_alert_is_idempotent(conn):
    vid = db.create_video(conn, "a.mp4", "cam1", {})
    db.add_events(conn, vid, _events())
    alert_id = db.list_alerts(conn, vid)[0]["alert_id"]
    first = db.acknowledge_alert(conn, vid, alert_id)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", first["acknowledged_at"])
    assert first["acknowledged_by"] is None
    assert db.acknowledge_alert(conn, vid, alert_id) == first
    assert db.acknowledge_alert(conn, vid + 1, alert_id) is None
    assert db.acknowledge_alert(conn, vid, alert_id + 99) is None
