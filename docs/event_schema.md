# Queue event schema (version 1.0)

This is the contract between the AI engine (Sprint 3) and the backend and
dashboard (Sprint 4). The definitions live in
`ai_engine/queue/event_schema.py` (`SCHEMA_VERSION = "1.0"`,
`EVENT_TYPES`). Every event is a frozen dataclass with a `to_dict()` method
that returns a JSON-safe dict (tuples become lists).

A run with `--camera-config` writes every event, in the order it was
produced, to `outputs/events/<name>_events.jsonl`: one JSON object per line.
The same events also feed `<name>_zone_events.csv` (zone events only),
`<name>_waits.csv` (service and abandon events) and `<name>_alerts.csv`
(congestion events).

The JSON examples below are real lines copied from the MOT17 smoke runs
(`mot17_09_events.jsonl`, `mot17_04_events.jsonl`). MOT17 is street footage
without a queue, so the values only illustrate the format; they say nothing
about accuracy on real queues.

## Common fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | `"1.0"`. Increase it when a field is removed or changes meaning |
| `event_type` | string | One of `zone_enter`, `zone_exit`, `queue_join`, `service_start`, `queue_abandon`, `congestion_start`, `congestion_end` |
| `timestamp_s` | number | Video time (seconds, `frame_index / FPS`) at which the thing happened. Never wall-clock |
| `frame_index` | integer | Source frame index of `timestamp_s` (0-based, counts frames skipped by `--frame-stride`) |
| `detected_s` | number | Video time at which the system emitted the event. It is greater than or equal to `timestamp_s` |

`timestamp_s` versus `detected_s`: the system often only knows something
after a delay. A join is confirmed after the dwell time, and an abandonment
is only final after the abandon timeout, so `timestamp_s` is back-dated to
when it really happened while `detected_s` is when it became known. Use
`timestamp_s` for analysis and ordering of what happened; use `detected_s`
for what a live dashboard could have shown at a given moment. Events in the
JSONL file are in `detected_s` order, not necessarily `timestamp_s` order.

Identifiers:

* `track_id` is a per-video ByteTrack integer. It restarts for each video
  and changes after occlusion. It is not an identity.
* `person_key` (`p0001`, `p0002`, ...) is an anonymous counter assigned by
  the stitcher, per video. One `person_key` may cover several `track_id`s
  when an ID switch inside the queue zone was bridged. It carries no identity
  and is not comparable across videos.
* Scope every key by video (`name`/camera and run) when storing events.

Nothing is emitted at the end of a video. People still waiting then are
reported in the metrics JSON (`queue.waiting_at_end`,
`queue.pending_abandon_at_end`), not as events.

## Event types

### `zone_enter`

A person was confirmed as a queue member (dwell and speed rules met).

| Field | Type | Meaning |
|---|---|---|
| `track_id` | integer | ByteTrack id |
| `timestamp_s`, `frame_index` | number, integer | Confirmation time |
| `zone_entered_s`, `zone_entered_frame` | number, integer | When the track first stepped into the zone |
| `detected_s` | number | Same as `timestamp_s` |

```json
{"schema_version": "1.0", "event_type": "zone_enter", "track_id": 4, "timestamp_s": 3.0, "frame_index": 90, "zone_entered_s": 0.0, "zone_entered_frame": 0, "detected_s": 3.0}
```

### `zone_exit`

A confirmed member has been outside the zone (or unseen) for longer than
`exit_grace_seconds`. A stitched person may still be in the queue under a new
track, so this is a per-track event; use `queue_abandon` for the person-level outcome.

| Field | Type | Meaning |
|---|---|---|
| `track_id` | integer | ByteTrack id |
| `timestamp_s`, `frame_index` | number, integer | Last time the track was seen inside the zone |
| `zone_entered_s`, `zone_entered_frame` | number, integer | When the track first stepped into the zone |
| `detected_s` | number | When the exit became known (after the grace period) |

```json
{"schema_version": "1.0", "event_type": "zone_exit", "track_id": 4, "timestamp_s": 3.5, "frame_index": 105, "zone_entered_s": 0.0, "zone_entered_frame": 0, "detected_s": 5.033333333333333}
```

### `queue_join`

A person joined the queue. Emitted once per `person_key`.

| Field | Type | Meaning |
|---|---|---|
| `person_key` | string | Anonymous per-video key |
| `track_id` | integer | Track that triggered the join |
| `timestamp_s`, `frame_index` | number, integer | First time in the queue zone (earliest over stitched tracks), not the confirmation time |
| `detected_s` | number | Confirmation time |

```json
{"schema_version": "1.0", "event_type": "queue_join", "person_key": "p0012", "track_id": 4, "timestamp_s": 0.0, "frame_index": 0, "detected_s": 3.0}
```

### `service_start`

A queued person reached the service point (a `service_line` crossing or
presence in a `service_zone`). Only emitted when a service point is configured.

| Field | Type | Meaning |
|---|---|---|
| `person_key` | string | Anonymous per-video key |
| `track_ids` | array of integers | All tracks of this person so far |
| `timestamp_s`, `frame_index` | number, integer | When the service point was reached |
| `detected_s` | number | Same as `timestamp_s` |
| `join_s` | number | The `timestamp_s` of the matching `queue_join` |
| `wait_s` | number | `timestamp_s - join_s` |

```json
{"schema_version": "1.0", "event_type": "service_start", "person_key": "p0031", "track_ids": [38, 57], "timestamp_s": 15.766666666666667, "frame_index": 473, "detected_s": 15.766666666666667, "join_s": 9.4, "wait_s": 6.366666666666667}
```

### `queue_abandon`

A queued person left without reaching the service point. Only emitted when a
service point is configured, because without one nobody can be served.

| Field | Type | Meaning |
|---|---|---|
| `person_key` | string | Anonymous per-video key |
| `track_ids` | array of integers | All tracks of this person |
| `timestamp_s`, `frame_index` | number, integer | Last time seen inside the queue zone |
| `detected_s` | number | When the abandonment became final: at least `max(exit_grace_seconds, stitching max_gap_seconds)` after `timestamp_s` (the stitching gap only counts when stitching is enabled) |
| `join_s` | number | The `timestamp_s` of the matching `queue_join` |
| `time_in_queue_s` | number | `timestamp_s - join_s` |

```json
{"schema_version": "1.0", "event_type": "queue_abandon", "person_key": "p0002", "track_ids": [2], "timestamp_s": 8.3, "frame_index": 249, "detected_s": 10.3, "join_s": 0.0, "time_in_queue_s": 8.3}
```

An abandonment can be wrong if the service point lies outside the queue zone
and people legitimately walk out of the zone to reach it (see
[architecture.md](architecture.md), service point placement).

### `congestion_start`

The congestion condition (queue length above `max_queue_length`, and/or a
predicted wait above `max_predicted_wait_seconds`) held for `min_duration_seconds`.

| Field | Type | Meaning |
|---|---|---|
| `timestamp_s`, `frame_index` | number, integer | Detection time (condition held long enough) |
| `detected_s` | number | Same as `timestamp_s` |
| `condition_since_s` | number | When the condition started holding |
| `reasons` | array of strings | Subset of `"queue_length"`, `"predicted_wait"` |
| `queue_length` | number | Smoothed queue length at `timestamp_s` |
| `predicted_wait_s` | number or null | Predicted wait at `timestamp_s`; null when unknown |

```json
{"schema_version": "1.0", "event_type": "congestion_start", "timestamp_s": 10.9, "frame_index": 327, "detected_s": 10.9, "condition_since_s": 7.9, "reasons": ["queue_length"], "queue_length": 6.0, "predicted_wait_s": null}
```

### `congestion_end`

The condition has been false for `min_duration_seconds` while an alert was
active. A new alert cannot start until `cooldown_seconds` after this event.

Fields are those of `congestion_start`, except `condition_since_s` is when
the condition stopped holding and `reasons` are the reasons that were
active when the alert ended.

```json
{"schema_version": "1.0", "event_type": "congestion_end", "timestamp_s": 22.0, "frame_index": 660, "detected_s": 22.0, "condition_since_s": 19.0, "reasons": ["queue_length"], "queue_length": 1.0, "predicted_wait_s": 7.333333333333334}
```

## Other Sprint 3 files (not events)

| File | Columns |
|---|---|
| `outputs/events/<name>_waits.csv` | `person_key, track_ids, join_s, service_s, wait_s, abandoned, left_s` (`track_ids` joined with `;`; blanks where not applicable) |
| `outputs/events/<name>_alerts.csv` | `event_type, timestamp_s, frame, condition_since_s, reasons, queue_length, predicted_wait_s` (`reasons` joined with `;`) |
| `outputs/events/<name>_zone_events.csv` | `event_type, track_id, timestamp_s, frame, zone_entered_s, zone_entered_frame` |
| `outputs/timeseries/<name>_queue.csv` | `time_s, frame, people_in_frame, raw_queue_count, queue_length, service_rate_per_min, service_rate_status, predicted_wait_s, predicted_wait_low_s, predicted_wait_high_s, prediction_status` |

In the time series, `service_rate_status` is `ok` or `insufficient_data`,
`prediction_status` is `ok` or `unknown`, and an empty cell means the value is
not available (never a fake 0 or infinity).

## Suggested SQLite tables for Sprint 4

Proposal only: nothing in Sprint 3 creates or reads a database. The tables are
derived from the schema above; adjust freely. `video_id` identifies one
processed video or camera run, because `track_id` and `person_key` are only
unique within one.

```sql
CREATE TABLE videos (
    video_id   INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,          -- video stem or camera<N>
    camera_id  TEXT,
    fps        REAL,
    started_at TEXT                    -- wall-clock start of the run, if live
);

-- Every event, common columns plus the full JSON line as payload.
CREATE TABLE events (
    event_id       INTEGER PRIMARY KEY,
    video_id       INTEGER NOT NULL REFERENCES videos(video_id),
    schema_version TEXT    NOT NULL,
    event_type     TEXT    NOT NULL,
    timestamp_s    REAL    NOT NULL,
    detected_s     REAL    NOT NULL,
    frame_index    INTEGER NOT NULL,
    person_key     TEXT,               -- null for zone and congestion events
    payload        TEXT    NOT NULL    -- JSON from event.to_dict()
);
CREATE INDEX events_video_time ON events(video_id, timestamp_s);
CREATE INDEX events_type ON events(video_id, event_type);

-- One row per completed or abandoned wait (mirrors <name>_waits.csv).
CREATE TABLE waits (
    video_id   INTEGER NOT NULL REFERENCES videos(video_id),
    person_key TEXT    NOT NULL,
    track_ids  TEXT    NOT NULL,       -- e.g. "28;70;82"
    join_s     REAL    NOT NULL,
    service_s  REAL,                   -- null when abandoned
    wait_s     REAL,                   -- null when abandoned
    abandoned  INTEGER NOT NULL,       -- 0 or 1
    left_s     REAL,                   -- null when served
    PRIMARY KEY (video_id, person_key)
);

-- One row per congestion_start / congestion_end (mirrors <name>_alerts.csv).
CREATE TABLE alerts (
    alert_id          INTEGER PRIMARY KEY,
    video_id          INTEGER NOT NULL REFERENCES videos(video_id),
    event_type        TEXT    NOT NULL, -- congestion_start | congestion_end
    timestamp_s       REAL    NOT NULL,
    frame_index       INTEGER NOT NULL,
    condition_since_s REAL    NOT NULL,
    reasons           TEXT    NOT NULL, -- "queue_length;predicted_wait"
    queue_length      REAL    NOT NULL,
    predicted_wait_s  REAL
);
```

The per-second time series (`<name>_queue.csv`) can be stored as a fourth
table with the same columns if the dashboard needs historical charts.
