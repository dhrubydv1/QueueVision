"""Detection data types with no model dependencies (safe for queue logic and tests)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_engine import config


@dataclass(frozen=True)
class PersonDetection:
    """One detected (and optionally tracked) person in a frame.

    ``track_id`` is a per-video ByteTrack ID, not a permanent identity: the
    same person can receive a new ID after occlusion or leaving the frame.
    """

    bbox: tuple[int, int, int, int]  # x1, y1, x2, y2 in pixels
    confidence: float
    track_id: int | None = None

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bbox
        return (x1 + x2) // 2, (y1 + y2) // 2

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "class": config.PERSON_CLASS_NAME,
            "confidence": round(self.confidence, 4),
            "bbox": list(self.bbox),
        }
        if self.track_id is not None:
            data["track_id"] = self.track_id
        return data
