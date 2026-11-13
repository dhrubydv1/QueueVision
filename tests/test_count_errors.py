"""Error-attribution tests: synthetic GT/tracks for every category and the consistency identities."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_engine.analyze_queue_errors import main as analyze_main
from ai_engine.detection.types import PersonDetection
from ai_engine.evaluation.count_errors import (
    GtBox,
    analyze_clip,
    analyze_frame,
    iou,
    match_boxes,
    read_person_boxes,
    summarize,
)
from ai_engine.queue import CameraConfig, MembershipSettings
from ai_engine.queue.membership import MemberStatus

IN, CAND = MemberStatus.IN_QUEUE, MemberStatus.CANDIDATE
FRAME = (200, 100)


def gt(
    x: float, action: int = 4, name: str = "queueing", y: float = 0, w: float = 20, h: float = 40
) -> GtBox:
    return GtBox(x, y, w, h, action, name)


def det(x: float, tid: int, w: int = 20, h: int = 40) -> PersonDetection:
    return PersonDetection((int(x), 0, int(x) + w, h), 0.9, tid)


def run(gt_boxes, people, statuses, raw=None, inside=lambda foot: True, blocker=lambda tid: None, thr=0.5):
    raw = sum(1 for s in statuses.values() if s is IN) if raw is None else raw
    return analyze_frame(0.0, 0, gt_boxes, people, statuses, raw, inside, blocker, thr)


def test_iou_and_hungarian_matching() -> None:
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert match_boxes([(0, 0, 10, 10), (50, 0, 60, 10)], [(50, 0, 60, 10), (0, 0, 10, 10)], 0.5) == {
        0: 1,
        1: 0,
    }
    assert match_boxes([(0, 0, 10, 10)], [(8, 0, 18, 10)], 0.5) == {}


def test_tp() -> None:
    r = run([gt(0)], [det(0, 1)], {1: IN})
    assert r.counts == {"tp": 1} and r.error == 0


def test_fp_label() -> None:
    r = run([gt(0, 5, "walking")], [det(0, 1)], {1: IN})
    assert r.counts == {"fp_label:walking": 1} and r.gt_queueing == 0 and r.error == 1


def test_fp_unmatched_nothing_there_vs_duplicate() -> None:
    nothing = run([], [det(100, 1)], {1: IN})
    assert nothing.counts["fp_unmatched"] == 1 and nothing.counts["fp_unmatched_no_gt_overlap"] == 1
    # a second, shifted box over the same person: one matches (tp), the other is a duplicate
    dup = run([gt(0)], [det(0, 1), det(8, 2)], {1: IN, 2: IN})
    assert dup.counts["tp"] == 1 and dup.counts["fp_unmatched"] == 1
    assert dup.counts["fp_unmatched_overlaps_gt"] == 1


def test_counted_not_visible() -> None:
    r = run([], [], {9: IN})
    assert r.counts == {"counted_not_visible": 1} and r.raw_count == 1


def test_fn_no_detection() -> None:
    r = run([gt(0)], [], {})
    assert r.counts == {"fn_no_detection": 1} and r.error == -1


def test_fn_foot_outside_zone() -> None:
    r = run([gt(0)], [det(0, 1)], {1: None}, inside=lambda foot: False)
    assert r.counts["fn_foot_outside_zone"] == 1
    assert r.counts["gt_queueing_outside_zone"] == 1  # the GT foot is outside the zone as well


def test_fn_candidate_dwell_speed_other() -> None:
    for reason, key in (("dwell", "fn_candidate_dwell"), ("speed", "fn_candidate_speed"), (None, "fn_other")):
        r = run([gt(0)], [det(0, 1)], {1: CAND}, blocker=lambda tid, reason=reason: reason)
        assert r.counts == {key: 1}


def test_gt_queueing_outside_zone_is_separate_from_sum_identities() -> None:
    r = run([gt(0), gt(100)], [det(0, 1)], {1: IN}, inside=lambda foot: foot[0] < 50)
    assert r.counts["tp"] == 1 and r.counts["fn_no_detection"] == 1
    assert r.counts["gt_queueing_outside_zone"] == 1


def test_mixed_frame_identities() -> None:
    boxes = [gt(0), gt(30), gt(60, 3, "waiting"), gt(90)]
    people = [det(0, 1), det(60, 2), det(150, 3), det(30, 4)]
    r = run(boxes, people, {1: IN, 2: IN, 3: IN, 4: CAND, 7: IN}, blocker=lambda tid: "dwell")
    assert r.counts["tp"] == 1
    assert r.counts["fp_label:waiting"] == 1 and r.counts["fp_unmatched"] == 1
    assert r.counts["counted_not_visible"] == 1
    assert r.counts["fn_candidate_dwell"] == 1 and r.counts["fn_no_detection"] == 1
    assert r.raw_count - r.gt_queueing == r.fp_total - r.fn_total
    summary = summarize([r])
    assert summary["bias"] == r.error and summary["fp_total"] - summary["fn_total"] == r.error


def test_raw_count_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        run([], [], {1: IN}, raw=2)


def _camera(**membership: float) -> CameraConfig:
    zone = ((0.0, 0.5), (1.0, 0.5), (1.0, 1.0), (0.0, 1.0))
    return CameraConfig(camera_id="c", queue_zone=zone, membership=MembershipSettings(**membership))


def test_analyze_clip_end_to_end_dwell_then_counted() -> None:
    fps = 2.0
    box = (80, 20, 100, 90)  # foot (90, 90) inside the lower half of 200x100
    tracks = {i: [PersonDetection(box, 0.9, 1)] for i in range(12)}
    boxes = {i: (i / fps, [GtBox(80, 20, 20, 70, 4, "queueing")]) for i in (0, 4, 10)}
    out = analyze_clip(tracks, _camera(min_dwell_seconds=3.0), fps, FRAME, 12, boxes)[0.5]
    by_frame = {f.frame: f for f in out.frames}
    assert by_frame[0].counts == {"fn_candidate_dwell": 1}
    assert by_frame[4].counts == {"fn_candidate_dwell": 1}  # 2.0 s < 3.0 s
    assert by_frame[10].counts == {"tp": 1}  # 5.0 s >= 3.0 s
    for f in out.frames:
        assert f.raw_count - f.gt_queueing == f.fp_total - f.fn_total


def test_analyze_clip_foot_outside_zone_and_counted_not_visible() -> None:
    fps = 2.0
    inside = (80, 20, 100, 90)
    outside = (80, 0, 100, 40)  # foot y=40 is above the zone (y >= 50)
    tracks = {i: [PersonDetection(inside, 0.9, 1)] for i in range(8)}
    tracks.update({i: [] for i in range(8, 10)})  # within the 1.5 s grace -> still counted
    tracks.update({i: [PersonDetection(outside, 0.9, 2)] for i in range(10, 12)})
    boxes = {
        8: (4.0, []),  # person left view but still counted (grace)
        11: (5.5, [GtBox(80, 0, 20, 40, 4, "queueing")]),
    }
    out = analyze_clip(tracks, _camera(min_dwell_seconds=1.0), fps, FRAME, 12, boxes)[0.5]
    by_frame = {f.frame: f for f in out.frames}
    assert by_frame[8].counts == {"counted_not_visible": 1}
    assert by_frame[11].counts["fn_foot_outside_zone"] == 1
    assert by_frame[11].counts["gt_queueing_outside_zone"] == 1


def test_read_person_boxes_and_cli_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "c_person_boxes.csv"
    path.write_text("time_s,frame,x,y,w,h,action,action_name,pose\n0.000,0,1,2,3,4,4,queueing,1\n")
    frames = read_person_boxes(path)
    assert frames == {0: (0.0, [GtBox(1.0, 2.0, 3.0, 4.0, 4, "queueing")])}
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        read_person_boxes(bad)
    assert (
        analyze_main(["--clips", "nope", "--tracks-dir", str(tmp_path), "--camera-dir", str(tmp_path)]) == 1
    )
