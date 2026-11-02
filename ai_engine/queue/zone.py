"""Queue zone polygon in normalized coordinates."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


def point_in_polygon(point: tuple[float, float], polygon: np.ndarray) -> bool:
    """Ray-casting point-in-polygon test (pure NumPy, any coordinate system)."""
    x, y = point
    xs, ys = polygon[:, 0], polygon[:, 1]
    xs_next, ys_next = np.roll(xs, -1), np.roll(ys, -1)
    crosses = (ys > y) != (ys_next > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_at_y = xs + (y - ys) * (xs_next - xs) / (ys_next - ys)
    return bool(np.count_nonzero(crosses & (x < x_at_y)) % 2)


class QueueZone:
    """A polygon defined in normalized ``[0, 1]`` coordinates.

    Containment is tested in normalized space, so a zone and a point taken
    from frames of different sizes are compared consistently.
    """

    def __init__(self, points: Sequence[tuple[float, float]]) -> None:
        if len(points) < 3:
            raise ValueError("A queue zone needs at least 3 points")
        self.points = np.asarray(points, dtype=float)

    def to_pixels(self, frame_size: tuple[int, int]) -> np.ndarray:
        """Return the polygon as integer pixel points for a ``(width, height)`` frame."""
        width, height = frame_size
        return np.round(self.points * [width, height]).astype(np.int32)

    def contains(self, point_px: tuple[float, float], frame_size: tuple[int, int]) -> bool:
        """Return True if a pixel point lies inside the zone for this frame size."""
        width, height = frame_size
        return point_in_polygon((point_px[0] / width, point_px[1] / height), self.points)
