"""SQLite store tests (torch-free, temporary database)."""

from __future__ import annotations

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
    assert [(r["key"], r["value"]) for r in rows] == [("schema_version", "1")]
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
