"""ByteTrack person tracking built on a shared :class:`PersonDetector`."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ai_engine import config
from ai_engine.detection.detector import PersonDetector, parse_person_boxes
from ai_engine.detection.types import PersonDetection

RUNTIME_DIR = Path(tempfile.gettempdir()) / "queuevision" / "trackers"


def load_base_tracker_config(name: str = config.TRACKER_CONFIG) -> dict[str, Any]:
    """Read Ultralytics' bundled tracker YAML (e.g. ``bytetrack.yaml``)."""
    from ultralytics.utils.checks import check_yaml

    return yaml.safe_load(Path(check_yaml(name)).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class TrackerSettings:
    """How detections are fed into ByteTrack.

    * ``input_confidence``: detector threshold for boxes passed to ByteTrack.
      Low-score boxes (between ``track_low_thresh`` and ``track_high_thresh``)
      are what ByteTrack's second association stage uses to keep tracks alive
      through occlusion, so this must stay below ``track_high_thresh``.
    * ``new_track_confidence``: minimum score for a box to start a new track
      (ByteTrack's ``new_track_thresh``).
    * ``buffer_seconds``: how long a lost track is kept, converted to
      processed frames for ``track_buffer``.
    """

    new_track_confidence: float = config.CONFIDENCE
    input_confidence: float = config.TRACKER_INPUT_CONFIDENCE
    buffer_seconds: float = config.TRACK_BUFFER_SECONDS

    def __post_init__(self) -> None:
        for name in ("new_track_confidence", "input_confidence"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1], got {value}")
        if self.buffer_seconds <= 0:
            raise ValueError("buffer_seconds must be positive")


def resolve_tracker_config(
    settings: TrackerSettings,
    effective_fps: float,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return ByteTrack settings with our thresholds applied.

    In Ultralytics 8.4.x ``BYTETracker`` uses ``track_buffer`` directly as
    the number of *processed* frames a lost track survives, so it is derived
    from ``effective_fps`` (source FPS / frame stride).
    """
    if effective_fps <= 0:
        raise ValueError("effective_fps must be positive")
    resolved = dict(base if base is not None else load_base_tracker_config())
    resolved["new_track_thresh"] = settings.new_track_confidence
    resolved["track_buffer"] = max(1, round(settings.buffer_seconds * effective_fps))
    return resolved


def write_tracker_config(resolved: dict[str, Any], directory: Path = RUNTIME_DIR) -> Path:
    """Write a resolved tracker YAML, named by its content so reruns reuse it."""
    text = yaml.safe_dump(resolved, sort_keys=True)
    digest = hashlib.sha1(text.encode()).hexdigest()[:10]
    path = directory / f"{resolved.get('tracker_type', 'tracker')}_{digest}.yaml"
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path


class PersonTracker:
    """Track people across consecutive frames of one video with ByteTrack.

    Call :meth:`configure` (or :meth:`reset`) before starting a different
    video so track IDs and motion state from the previous video are
    discarded. Track IDs are only meaningful within one video and may change
    after occlusions.
    """

    def __init__(
        self,
        detector: PersonDetector,
        settings: TrackerSettings | None = None,
        effective_fps: float = 30.0,
        base_config: dict[str, Any] | None = None,
    ) -> None:
        self.detector = detector
        self.settings = settings or TrackerSettings(new_track_confidence=detector.confidence)
        # Ultralytics' bytetrack.yaml unless given (tests pass a dict to stay model-free).
        self._base_config = base_config if base_config is not None else load_base_tracker_config()
        self._needs_reset = True
        self.configure(effective_fps)

    def configure(self, effective_fps: float) -> None:
        """Regenerate the tracker YAML for a new processing frame rate and reset."""
        self.effective_fps = effective_fps
        self.resolved_config = resolve_tracker_config(self.settings, effective_fps, self._base_config)
        self.config_path = write_tracker_config(self.resolved_config)
        self.reset()

    @property
    def last_inference_seconds(self) -> float:
        """Detection + tracking time of the most recent :meth:`update` call."""
        return self.detector.last_inference_seconds

    @property
    def last_speed_ms(self) -> dict[str, float] | None:
        """Ultralytics preprocess/inference/postprocess milliseconds of the last frame, if reported."""
        return self.detector.last_speed_ms

    def reset(self) -> None:
        """Start fresh tracker state (IDs restart at 1) on the next frame."""
        self._needs_reset = True

    def update(self, frame: np.ndarray) -> list[PersonDetection]:
        """Detect and track people in the next frame of the current video.

        Every confirmed track is returned, including ones whose current box
        score is below ``new_track_confidence``: those are tracks kept alive
        by low-score detections during occlusion. Unconfirmed boxes without
        a track ID are not returned.
        """
        # persist=False makes Ultralytics build a new ByteTrack instance.
        persist = not self._needs_reset
        self._needs_reset = False
        result = self.detector.run_timed(
            "track",
            frame,
            conf=self.settings.input_confidence,
            persist=persist,
            tracker=str(self.config_path),
        )
        people = parse_person_boxes(result, with_track_ids=True)
        return [person for person in people if person.track_id is not None]

    def warmup(self, frame_shape: tuple[int, int, int]) -> None:
        """Run one blank frame so model setup is excluded from timing."""
        self.update(np.zeros(frame_shape, dtype=np.uint8))
        self.reset()

    def describe(self) -> dict[str, Any]:
        """Tracker settings for reports."""
        return {
            "type": self.resolved_config.get("tracker_type", "bytetrack"),
            "input_confidence": self.settings.input_confidence,
            "new_track_confidence": self.settings.new_track_confidence,
            "buffer_seconds": self.settings.buffer_seconds,
            "effective_fps": round(self.effective_fps, 3),
            "resolved": self.resolved_config,
        }
