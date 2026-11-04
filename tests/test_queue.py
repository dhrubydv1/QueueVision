"""Tests for the queue package using synthetic tracks (no model, no drawing)."""

from pathlib import Path

import numpy as np
import pytest

from ai_engine.detection.types import PersonDetection
from ai_engine.queue import (
    CameraConfig,
    CameraConfigError,
    MembershipSettings,
    MemberStatus,
    QueueAnalyzer,
    QueueLengthEstimator,
    QueueMembershipTracker,
    QueueZone,
    load_camera_config,
    point_in_polygon,
    save_camera_config,
)

FPS = 10.0
FRAME_SIZE = (1000, 1000)
# Zone covers x 0.2-0.8, y 0.4-0.9 of the frame.
ZONE = ((0.2, 0.4), (0.8, 0.4), (0.8, 0.9), (0.2, 0.9))
SETTINGS = MembershipSettings(min_dwell_seconds=3.0, max_speed_heights_per_second=0.5, exit_grace_seconds=1.5)


def person_at(foot_x: float, foot_y: float, track_id: int = 1, height: int = 200) -> PersonDetection:
    """A 100 px wide box whose bottom-centre (foot point) is at the given pixel."""
    return PersonDetection(
        bbox=(int(foot_x - 50), int(foot_y - height), int(foot_x + 50), int(foot_y)),
        confidence=0.9,
        track_id=track_id,
    )


def run(tracker: QueueMembershipTracker, frames: list[list[PersonDetection]], start: int = 0):
    """Feed frames at FPS; return all events and the statuses after each frame."""
    events, statuses = [], []
    for offset, people in enumerate(frames):
        index = start + offset
        events += tracker.update(index / FPS, index, people, FRAME_SIZE)
        statuses.append(tracker.statuses.copy())
    return events, statuses


def new_tracker() -> QueueMembershipTracker:
    return QueueMembershipTracker(QueueZone(ZONE), SETTINGS)


# --- zone ---------------------------------------------------------------------


def test_point_in_polygon_and_zone_scaling() -> None:
    square = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
    assert point_in_polygon((0.5, 0.5), square)
    assert not point_in_polygon((1.5, 0.5), square)

    zone = QueueZone(ZONE)
    assert zone.to_pixels((1000, 500)).tolist() == [[200, 200], [800, 200], [800, 450], [200, 450]]
    # The same relative point is inside at any resolution.
    assert zone.contains((500, 650), (1000, 1000))
    assert zone.contains((960, 351), (1920, 540))
    assert not zone.contains((100, 650), (1000, 1000))
    with pytest.raises(ValueError):
        QueueZone(((0, 0), (1, 1)))


# --- membership ---------------------------------------------------------------


def test_stationary_person_joins_only_after_dwell_time() -> None:
    tracker = new_tracker()
    still = [[person_at(500, 700)]] * 40  # 4 s at 10 fps
    events, statuses = run(tracker, still)
    # Before 3 s of dwell: candidate only.
    assert statuses[29][1] is MemberStatus.CANDIDATE
    assert statuses[30][1] is MemberStatus.IN_QUEUE
    assert [e.event_type for e in events] == ["zone_enter"]
    assert events[0].timestamp_s == pytest.approx(3.0)
    assert events[0].zone_entered_s == 0.0
    assert tracker.queue_count == 1


def test_person_walking_through_is_never_counted() -> None:
    tracker = new_tracker()
    # Walk left to right across the zone at 1.5 box heights per second
    # (300 px/s for a 200 px tall person), spending ~4 s inside.
    frames = [[person_at(100 + 30 * i, 700)] for i in range(40)]
    events, statuses = run(tracker, frames)
    assert events == []
    assert all(status.get(1) is not MemberStatus.IN_QUEUE for status in statuses)


def test_slow_shuffling_still_counts() -> None:
    tracker = new_tracker()
    frames = [[person_at(500 + 2 * i, 700)] for i in range(40)]  # 0.1 heights/s
    events, _ = run(tracker, frames)
    assert [e.event_type for e in events] == ["zone_enter"]


def test_short_occlusion_does_not_cause_exit() -> None:
    tracker = new_tracker()
    frames = [[person_at(500, 700)]] * 35 + [[]] * 10 + [[person_at(500, 700)]] * 10  # 1 s gap
    events, statuses = run(tracker, frames)
    assert [e.event_type for e in events] == ["zone_enter"]
    assert statuses[40][1] is MemberStatus.IN_QUEUE  # still counted while occluded
    assert tracker.queue_count == 1


def test_leaving_for_longer_than_grace_emits_exit_and_forgets_track() -> None:
    tracker = new_tracker()
    frames = [[person_at(500, 700)]] * 35 + [[]] * 20  # gone for 2 s > 1.5 s grace
    events, _ = run(tracker, frames)
    assert [e.event_type for e in events] == ["zone_enter", "zone_exit"]
    exit_event = events[1]
    assert exit_event.timestamp_s == pytest.approx(3.4)  # last time seen in the zone
    assert exit_event.frame_index == 34
    assert tracker.queue_count == 0
    assert tracker.tracked_state_count == 0  # memory is bounded


def test_stepping_out_of_zone_but_visible_emits_exit_after_grace() -> None:
    tracker = new_tracker()
    frames = [[person_at(500, 700)]] * 35 + [[person_at(500, 980)]] * 20  # feet below zone
    events, statuses = run(tracker, frames)
    assert [e.event_type for e in events] == ["zone_enter", "zone_exit"]
    assert statuses[-1][1] is MemberStatus.LEFT


def test_foot_point_not_box_centre_decides_membership() -> None:
    tracker = new_tracker()
    # Box centre (y=850) is inside the zone but the feet (y=950) are below it.
    centre_inside = PersonDetection(bbox=(450, 750, 550, 950), confidence=0.9, track_id=1)
    # Box centre (y=300) is above the zone but the feet (y=500) are inside it.
    feet_inside = PersonDetection(bbox=(450, 100, 550, 500), confidence=0.9, track_id=2)
    events, statuses = run(tracker, [[centre_inside, feet_inside]] * 35)
    assert 1 not in statuses[-1]
    assert statuses[-1][2] is MemberStatus.IN_QUEUE
    assert [e.track_id for e in events] == [2]


def test_untracked_detections_are_ignored() -> None:
    tracker = new_tracker()
    events, statuses = run(tracker, [[PersonDetection((450, 500, 550, 700), 0.9, None)]] * 40)
    assert events == [] and statuses[-1] == {}


# --- queue length -------------------------------------------------------------


def test_median_smoothing_removes_one_frame_spike() -> None:
    estimator = QueueLengthEstimator(window_seconds=1.0)
    counts = [2, 2, 2, 2, 9, 2, 2, 2, 0, 2, 2]
    smoothed = [estimator.update(i / FPS, c) for i, c in enumerate(counts)]
    assert smoothed[4] == 2 and smoothed[8] == 2
    assert max(smoothed) == 2


def test_median_window_drops_old_samples() -> None:
    estimator = QueueLengthEstimator(window_seconds=1.0)
    for i in range(10):
        estimator.update(i / FPS, 5)
    values = [estimator.update(1.0 + i / FPS, 1) for i in range(10)]
    assert values[-1] == 1
    with pytest.raises(ValueError):
        QueueLengthEstimator(0)


def test_analyzer_combines_membership_and_smoothing() -> None:
    camera = CameraConfig(camera_id="test", queue_zone=ZONE, membership=SETTINGS)
    analyzer = QueueAnalyzer(camera)
    state = None
    for i in range(70):  # 3 s dwell + 2 s smoothing window must elapse
        people = [person_at(400, 700, 1), person_at(600, 700, 2)]
        state = analyzer.update(i / FPS, i, people, FRAME_SIZE)
    assert state.raw_count == 2
    assert state.queue_length == 2.0
    assert set(state.statuses) == {1, 2}


# --- camera config ------------------------------------------------------------


def test_example_camera_config_loads() -> None:
    camera = load_camera_config(Path(__file__).parent.parent / "configs/cameras/example_queue.yaml")
    assert camera.camera_id == "example_queue"
    assert len(camera.queue_zone) == 4
    assert camera.membership.min_dwell_seconds == 3.0
    assert camera.smoothing.window_seconds == 2.0


def test_camera_config_round_trip(tmp_path: Path) -> None:
    camera = CameraConfig(camera_id="cam", queue_zone=ZONE, description="test")
    path = save_camera_config(camera, tmp_path / "cfg" / "cam.yaml")
    assert load_camera_config(path) == camera


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("camera_id: x\nqueue_zone: [[0, 0], [1, 1]]\n", "at least 3 points"),
        ("camera_id: x\nqueue_zone: [[0, 0], [1.2, 0], [1, 1]]\n", r"\[0, 1\]"),
        ("camera_id: x\nqueue_zone: [[0, 0], [1, 0], [1]]\n", "pair"),
        ("camera_id: x\n", "missing 'queue_zone'"),
        ("queue_zone: [[0, 0], [1, 0], [1, 1]]\n", "camera_id"),
        (
            "camera_id: x\nqueue_zone: [[0, 0], [1, 0], [1, 1]]\nmembership: {min_dwell_seconds: 0}\n",
            "positive",
        ),
        (
            "camera_id: x\nqueue_zone: [[0, 0], [1, 0], [1, 1]]\nmembership: {dwell: 3}\n",
            "Unknown",
        ),
        ("camera_id: x\nqueue_zone: [[0, 0], [1, 0], [1, 1]\n", "Invalid YAML"),
    ],
)
def test_invalid_camera_configs_rejected(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(CameraConfigError, match=message):
        load_camera_config(path)


def test_missing_camera_config_file(tmp_path: Path) -> None:
    with pytest.raises(CameraConfigError, match="does not exist"):
        load_camera_config(tmp_path / "missing.yaml")


# --- sprint 3: schema, config, service point ----------------------------------


def test_event_schema_to_dict_is_versioned_and_json_safe() -> None:
    import json

    from ai_engine.queue import (
        EVENT_TYPES,
        SCHEMA_VERSION,
        CongestionEvent,
        QueueAbandonEvent,
        QueueJoinEvent,
        ServiceStartEvent,
        ZoneEvent,
    )

    events = [
        ZoneEvent("zone_enter", 1, 4.0, 40, 1.0, 10, 4.0),
        ZoneEvent("zone_exit", 1, 9.0, 90, 1.0, 10, 11.0),
        QueueJoinEvent("p0001", 1, 1.0, 10, 4.0),
        ServiceStartEvent("p0001", (1, 2), 12.0, 120, 12.0, 1.0, 11.0),
        QueueAbandonEvent("p0002", (3,), 8.0, 80, 10.0, 2.0, 6.0),
        CongestionEvent("congestion_start", 20.0, 200, 20.0, 10.0, ("queue_length",), 9.0, None),
        CongestionEvent(
            "congestion_end", 40.0, 400, 40.0, 30.0, ("queue_length", "predicted_wait"), 3.0, 55.5
        ),
    ]
    assert [e.to_dict()["event_type"] for e in events] == list(EVENT_TYPES)
    for event in events:
        data = event.to_dict()
        assert data["schema_version"] == SCHEMA_VERSION == "1.0"
        assert json.loads(json.dumps(data)) == data
    assert events[3].to_dict()["track_ids"] == [1, 2]
    assert events[6].to_dict()["reasons"] == ["queue_length", "predicted_wait"]


def test_zone_event_still_importable_from_membership() -> None:
    from ai_engine.queue import ZoneEvent
    from ai_engine.queue.membership import ZoneEvent as Reexported

    assert Reexported is ZoneEvent


def test_membership_events_carry_entry_frame_and_detection_time() -> None:
    events, _ = run(new_tracker(), [[person_at(500, 700)]] * 40)
    (enter,) = events
    assert enter.zone_entered_frame == 0
    assert enter.detected_s == pytest.approx(3.0)
    assert enter.timestamp_s == pytest.approx(3.0)


def test_new_config_sections_round_trip(tmp_path: Path) -> None:
    from ai_engine.queue import AlertSettings, ServiceRateSettings, StitchingSettings

    config = CameraConfig(
        camera_id="cam",
        queue_zone=ZONE,
        service_line=((0.1, 0.9), (0.9, 0.9)),
        stitching=StitchingSettings(enabled=False, max_gap_seconds=3.0, max_distance_heights=1.5),
        service_rate=ServiceRateSettings(method="ewma", min_services=3),
        alerts=AlertSettings(max_queue_length=8, max_predicted_wait_seconds=None),
    )
    assert config.alerts.enabled
    assert not AlertSettings().enabled
    path = save_camera_config(config, tmp_path / "cam.yaml")
    assert load_camera_config(path) == config
    assert "service_zone" not in config.to_dict()
    zone_config = CameraConfig("cam", ZONE, service_zone=ZONE)
    assert "service_line" not in zone_config.to_dict()
    assert load_camera_config(save_camera_config(zone_config, tmp_path / "z.yaml")) == zone_config


def test_example_config_loads_with_service_line() -> None:
    config = load_camera_config(Path(__file__).parents[1] / "configs" / "cameras" / "example_queue.yaml")
    assert config.service_line is not None and config.alerts.enabled


@pytest.mark.parametrize(
    "extra",
    [
        {"service_zone": [list(p) for p in ZONE], "service_line": [[0.1, 0.9], [0.9, 0.9]]},
        {"service_line": [[0.1, 0.9], [0.9, 0.9], [0.5, 0.5]]},
        {"service_line": [[0.1, 1.9], [0.9, 0.9]]},
        {"service_zone": [[0.1, 0.1], [0.2, 0.2]]},
        {"service_rate": {"method": "median"}},
        {"service_rate": {"min_services": 0}},
        {"service_rate": {"min_services": 1.5}},
        {"alerts": {"max_queue_length": -1}},
        {"alerts": {"cooldown_seconds": 0}},
        {"alerts": {"unknown": 1}},
        {"stitching": {"enabled": "yes"}},
        {"stitching": {"max_gap_seconds": -2}},
    ],
)
def test_invalid_new_config_is_rejected(extra: dict) -> None:
    from ai_engine.queue.camera_config import camera_config_from_dict

    data = {"camera_id": "cam", "queue_zone": [list(p) for p in ZONE], **extra}
    with pytest.raises(CameraConfigError):
        camera_config_from_dict(data)


def test_service_line_crossing_and_touching() -> None:
    from ai_engine.queue import ServiceLine

    line = ServiceLine((0.2, 0.8), (0.8, 0.8))
    size = (1000, 1000)
    assert line.reached((500, 700), (500, 900), size)  # crosses
    assert line.reached((500, 900), (500, 700), size)  # either direction
    assert line.reached((500, 700), (500, 800), size)  # ends on the line
    assert not line.reached((500, 700), (500, 790), size)
    assert not line.reached((100, 700), (100, 900), size)  # crosses the extension only
    assert not line.reached(None, (500, 800), size)
    assert line.reached((100, 800), (300, 800), size)  # collinear overlap


def test_service_zone_containment_and_factory() -> None:
    from ai_engine.queue import ServiceLine, ServiceZone, service_point_from_config

    zone = ServiceZone(ZONE)
    assert zone.reached(None, (500, 700), FRAME_SIZE)
    assert not zone.reached((500, 700), (50, 50), FRAME_SIZE)
    assert service_point_from_config(CameraConfig("c", ZONE)) is None
    assert isinstance(service_point_from_config(CameraConfig("c", ZONE, service_zone=ZONE)), ServiceZone)
    line_config = CameraConfig("c", ZONE, service_line=((0.1, 0.9), (0.9, 0.9)))
    assert isinstance(service_point_from_config(line_config), ServiceLine)


# --- stitching ----------------------------------------------------------------


def make_stitcher(enabled: bool = True):
    from ai_engine.queue import StitchingSettings, TrackStitcher

    return TrackStitcher(QueueZone(ZONE), StitchingSettings(enabled=enabled, max_gap_seconds=2.0))


def test_stitcher_links_id_switch_inside_queue() -> None:
    stitcher = make_stitcher()
    assert stitcher.update(0.0, [person_at(500, 700, 1)], FRAME_SIZE) == {1: "p0001"}
    assert stitcher.update(1.0, [], FRAME_SIZE) == {}
    keys = stitcher.update(1.5, [person_at(520, 700, 2)], FRAME_SIZE)
    assert keys == {2: "p0001"}
    assert stitcher.stitch_count == 1
    assert stitcher.track_ids("p0001") == (1, 2)


def test_stitcher_does_not_link_far_late_outside_or_disabled() -> None:
    far = make_stitcher()
    far.update(0.0, [person_at(500, 700, 1)], FRAME_SIZE)
    elsewhere = PersonDetection(bbox=(750, 500, 850, 700), confidence=0.9, track_id=2)
    assert far.update(0.5, [elsewhere], FRAME_SIZE)[2] == "p0002"
    assert far.stitch_count == 0

    late = make_stitcher()
    late.update(0.0, [person_at(500, 700, 1)], FRAME_SIZE)
    assert late.update(2.5, [person_at(500, 700, 2)], FRAME_SIZE) == {2: "p0002"}

    outside = make_stitcher()
    outside.update(0.0, [person_at(50, 100, 1)], FRAME_SIZE)  # outside the queue zone
    assert outside.update(0.5, [person_at(50, 100, 2)], FRAME_SIZE) == {2: "p0002"}

    off = make_stitcher(enabled=False)
    off.update(0.0, [person_at(500, 700, 1)], FRAME_SIZE)
    assert off.update(0.5, [person_at(500, 700, 2)], FRAME_SIZE) == {2: "p0002"}
    assert off.stitch_count == 0


def test_stitcher_retired_id_gets_new_key_and_memory_is_bounded() -> None:
    stitcher = make_stitcher()
    stitcher.update(0.0, [person_at(500, 700, 1)], FRAME_SIZE)
    stitcher.update(0.5, [person_at(510, 700, 2)], FRAME_SIZE)  # 1 retired, stitched into p0001
    keys = stitcher.update(0.6, [person_at(510, 700, 2), person_at(800, 700, 1)], FRAME_SIZE)
    assert keys[2] == "p0001" and keys[1] == "p0002"
    stitcher.update(10.0, [], FRAME_SIZE)
    assert stitcher.track_ids("p0001") == () and stitcher.track_ids("p0002") == ()
