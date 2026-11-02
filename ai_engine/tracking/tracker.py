"""ByteTrack person tracking built on a shared :class:`PersonDetector`."""

from __future__ import annotations

import numpy as np

from ai_engine import config
from ai_engine.detection.detector import PersonDetection, PersonDetector, parse_person_boxes


class PersonTracker:
    """Track people across consecutive frames of one video with ByteTrack.

    Call :meth:`reset` before starting a different video so track IDs and
    motion state from the previous video are discarded. Track IDs are only
    meaningful within one video and may change after occlusions.
    """

    def __init__(
        self, detector: PersonDetector, tracker_config: str = config.TRACKER_CONFIG
    ) -> None:
        self.detector = detector
        self.tracker_config = tracker_config
        self._needs_reset = True

    @property
    def last_inference_seconds(self) -> float:
        """Detection + tracking time of the most recent :meth:`update` call."""
        return self.detector.last_inference_seconds

    def reset(self) -> None:
        """Start fresh tracker state (IDs restart at 1) on the next frame."""
        self._needs_reset = True

    def update(self, frame: np.ndarray) -> list[PersonDetection]:
        """Detect and track people in the next frame of the current video.

        Detections that ByteTrack has not (yet) confirmed as tracks are not
        returned, so every returned person has a ``track_id``.
        """
        # persist=False makes Ultralytics build a new ByteTrack instance.
        persist = not self._needs_reset
        self._needs_reset = False
        result = self.detector.run_timed(
            "track", frame, persist=persist, tracker=self.tracker_config
        )
        people = parse_person_boxes(result, with_track_ids=True)
        return [person for person in people if person.track_id is not None]

    def warmup(self, frame_shape: tuple[int, int, int]) -> None:
        """Run one blank frame so model setup is excluded from timing."""
        self.update(np.zeros(frame_shape, dtype=np.uint8))
        self.reset()
