"""Service point (counter area or line) that marks the head of the queue."""

from __future__ import annotations

from collections.abc import Sequence

from ai_engine.queue.camera_config import CameraConfig
from ai_engine.queue.zone import QueueZone


class ServicePoint:
    """Decides whether a person has reached the service point."""

    kind = "base"

    def reached(
        self,
        previous_foot_px: tuple[float, float] | None,
        foot_px: tuple[float, float],
        frame_size: tuple[int, int],
    ) -> bool:
        raise NotImplementedError


class ServiceZone(ServicePoint):
    """Reached when the foot point is inside the polygon."""

    kind = "zone"

    def __init__(self, points: Sequence[tuple[float, float]]) -> None:
        self.zone = QueueZone(points)

    def reached(self, previous_foot_px, foot_px, frame_size) -> bool:
        return self.zone.contains(foot_px, frame_size)


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> int:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (value > 0) - (value < 0)


def _on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def segments_intersect(
    p1: tuple[float, float], p2: tuple[float, float], q1: tuple[float, float], q2: tuple[float, float]
) -> bool:
    """True if segment p1-p2 crosses or touches segment q1-q2 (collinear touching counts)."""
    o1, o2 = _orientation(p1, p2, q1), _orientation(p1, p2, q2)
    o3, o4 = _orientation(q1, q2, p1), _orientation(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(p1, p2, q1))
        or (o2 == 0 and _on_segment(p1, p2, q2))
        or (o3 == 0 and _on_segment(q1, q2, p1))
        or (o4 == 0 and _on_segment(q1, q2, p2))
    )


class ServiceLine(ServicePoint):
    """Reached when the foot point's last step crosses or touches the line segment."""

    kind = "line"

    def __init__(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.a = (float(a[0]), float(a[1]))
        self.b = (float(b[0]), float(b[1]))

    def reached(self, previous_foot_px, foot_px, frame_size) -> bool:
        if previous_foot_px is None:
            return False
        width, height = frame_size
        previous = (previous_foot_px[0] / width, previous_foot_px[1] / height)
        current = (foot_px[0] / width, foot_px[1] / height)
        return segments_intersect(previous, current, self.a, self.b)


def service_point_from_config(camera: CameraConfig) -> ServicePoint | None:
    """Build the configured service point, or ``None`` when the camera has none."""
    if camera.service_zone is not None:
        return ServiceZone(camera.service_zone)
    if camera.service_line is not None:
        return ServiceLine(camera.service_line[0], camera.service_line[1])
    return None
