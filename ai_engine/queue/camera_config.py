"""Per-camera queue configuration stored as YAML (``configs/cameras/*.yaml``)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


class CameraConfigError(ValueError):
    """Raised when a camera YAML file is missing fields or has invalid values."""


@dataclass(frozen=True)
class MembershipSettings:
    """Rules that decide when a tracked person counts as queuing."""

    min_dwell_seconds: float = 3.0
    max_speed_heights_per_second: float = 0.5
    exit_grace_seconds: float = 1.5


@dataclass(frozen=True)
class SmoothingSettings:
    """Median smoothing applied to the per-frame queue count."""

    window_seconds: float = 2.0


@dataclass(frozen=True)
class CameraConfig:
    """Queue zone and membership rules for one camera view.

    ``queue_zone`` holds normalized ``(x, y)`` points in ``[0, 1]`` so the
    same file works at any video resolution.
    """

    camera_id: str
    queue_zone: tuple[tuple[float, float], ...]
    description: str = ""
    membership: MembershipSettings = field(default_factory=MembershipSettings)
    smoothing: SmoothingSettings = field(default_factory=SmoothingSettings)

    def __post_init__(self) -> None:
        validate_polygon(self.queue_zone, "queue_zone")
        if not self.camera_id or not isinstance(self.camera_id, str):
            raise CameraConfigError("camera_id must be a non-empty string")
        for name, value in (
            ("membership.min_dwell_seconds", self.membership.min_dwell_seconds),
            ("membership.max_speed_heights_per_second", self.membership.max_speed_heights_per_second),
            ("membership.exit_grace_seconds", self.membership.exit_grace_seconds),
            ("smoothing.window_seconds", self.smoothing.window_seconds),
        ):
            if not isinstance(value, (int, float)) or value <= 0:
                raise CameraConfigError(f"{name} must be a positive number, got {value!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "description": self.description,
            "queue_zone": [list(point) for point in self.queue_zone],
            "membership": asdict(self.membership),
            "smoothing": asdict(self.smoothing),
        }


def validate_polygon(points: Any, name: str) -> tuple[tuple[float, float], ...]:
    """Check that ``points`` is a list of >= 3 normalized ``[x, y]`` pairs."""
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise CameraConfigError(f"{name} needs at least 3 points")
    validated = []
    for index, point in enumerate(points):
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise CameraConfigError(f"{name}[{index}] must be an [x, y] pair, got {point!r}")
        x, y = point
        for value in (x, y):
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise CameraConfigError(f"{name}[{index}] values must be numbers in [0, 1], got {point!r}")
        validated.append((float(x), float(y)))
    return tuple(validated)


def _section(data: dict[str, Any], key: str, settings_type: type) -> Any:
    raw = data.get(key) or {}
    if not isinstance(raw, dict):
        raise CameraConfigError(f"'{key}' must be a mapping")
    allowed = set(settings_type.__dataclass_fields__)
    unknown = set(raw) - allowed
    if unknown:
        raise CameraConfigError(f"Unknown '{key}' settings: {', '.join(sorted(unknown))}")
    return settings_type(**raw)


def camera_config_from_dict(data: dict[str, Any]) -> CameraConfig:
    """Build and validate a :class:`CameraConfig` from parsed YAML."""
    if not isinstance(data, dict):
        raise CameraConfigError("Camera config must be a YAML mapping")
    if "queue_zone" not in data:
        raise CameraConfigError("Camera config is missing 'queue_zone'")
    return CameraConfig(
        camera_id=data.get("camera_id", ""),
        description=data.get("description", "") or "",
        queue_zone=validate_polygon(data["queue_zone"], "queue_zone"),
        membership=_section(data, "membership", MembershipSettings),
        smoothing=_section(data, "smoothing", SmoothingSettings),
    )


def load_camera_config(path: str | Path) -> CameraConfig:
    """Read and validate a camera YAML file."""
    path = Path(path)
    if not path.is_file():
        raise CameraConfigError(f"Camera config does not exist: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise CameraConfigError(f"Invalid YAML in {path}: {error}") from error
    return camera_config_from_dict(data)


def save_camera_config(config: CameraConfig, path: str | Path) -> Path:
    """Write ``config`` as YAML, creating parent folders."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False), encoding="utf-8")
    return path
