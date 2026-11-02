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
class StitchingSettings:
    """Linking a new track ID to a person who just vanished inside the queue zone."""

    enabled: bool = True
    max_gap_seconds: float = 2.0
    max_distance_heights: float = 1.0


@dataclass(frozen=True)
class ServiceRateSettings:
    """How the service rate (people served per minute) is estimated."""

    method: str = "window"  # "window" | "ewma"
    window_seconds: float = 300.0
    ewma_time_constant_seconds: float = 120.0
    min_history_seconds: float = 60.0
    min_services: int = 2


@dataclass(frozen=True)
class AlertSettings:
    """Congestion alert thresholds; alerts are off while both thresholds are ``None``."""

    max_queue_length: float | None = None
    max_predicted_wait_seconds: float | None = None
    min_duration_seconds: float = 10.0
    cooldown_seconds: float = 60.0

    @property
    def enabled(self) -> bool:
        return self.max_queue_length is not None or self.max_predicted_wait_seconds is not None


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
    service_zone: tuple[tuple[float, float], ...] | None = None
    service_line: tuple[tuple[float, float], ...] | None = None
    stitching: StitchingSettings = field(default_factory=StitchingSettings)
    service_rate: ServiceRateSettings = field(default_factory=ServiceRateSettings)
    alerts: AlertSettings = field(default_factory=AlertSettings)

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
        if self.service_zone is not None and self.service_line is not None:
            raise CameraConfigError("Use either 'service_zone' or 'service_line', not both")
        if self.service_zone is not None:
            validate_polygon(self.service_zone, "service_zone")
        if self.service_line is not None:
            validate_points(self.service_line, "service_line", exactly=2)
        self._validate_new_settings()

    def _validate_new_settings(self) -> None:
        if not isinstance(self.stitching.enabled, bool):
            raise CameraConfigError(
                f"stitching.enabled must be true or false, got {self.stitching.enabled!r}"
            )
        rate = self.service_rate
        if rate.method not in ("window", "ewma"):
            raise CameraConfigError(f"service_rate.method must be 'window' or 'ewma', got {rate.method!r}")
        if (
            isinstance(rate.min_services, bool)
            or not isinstance(rate.min_services, int)
            or rate.min_services < 1
        ):
            raise CameraConfigError(
                f"service_rate.min_services must be an integer >= 1, got {rate.min_services!r}"
            )
        for name, value in (
            ("stitching.max_gap_seconds", self.stitching.max_gap_seconds),
            ("stitching.max_distance_heights", self.stitching.max_distance_heights),
            ("service_rate.window_seconds", rate.window_seconds),
            ("service_rate.ewma_time_constant_seconds", rate.ewma_time_constant_seconds),
            ("service_rate.min_history_seconds", rate.min_history_seconds),
            ("alerts.min_duration_seconds", self.alerts.min_duration_seconds),
            ("alerts.cooldown_seconds", self.alerts.cooldown_seconds),
            ("alerts.max_queue_length", self.alerts.max_queue_length),
            ("alerts.max_predicted_wait_seconds", self.alerts.max_predicted_wait_seconds),
        ):
            if value is None and name.startswith("alerts.max_"):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise CameraConfigError(f"{name} must be a positive number, got {value!r}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "camera_id": self.camera_id,
            "description": self.description,
            "queue_zone": [list(point) for point in self.queue_zone],
        }
        if self.service_zone is not None:
            data["service_zone"] = [list(point) for point in self.service_zone]
        if self.service_line is not None:
            data["service_line"] = [list(point) for point in self.service_line]
        data.update(
            membership=asdict(self.membership),
            smoothing=asdict(self.smoothing),
            stitching=asdict(self.stitching),
            service_rate=asdict(self.service_rate),
            alerts=asdict(self.alerts),
        )
        return data


def validate_polygon(points: Any, name: str) -> tuple[tuple[float, float], ...]:
    """Check that ``points`` is a list of >= 3 normalized ``[x, y]`` pairs."""
    return validate_points(points, name, minimum=3)


def validate_points(
    points: Any, name: str, minimum: int = 1, exactly: int | None = None
) -> tuple[tuple[float, float], ...]:
    """Check that ``points`` is a list of normalized ``[x, y]`` pairs of the required count."""
    if exactly is not None:
        if not isinstance(points, (list, tuple)) or len(points) != exactly:
            raise CameraConfigError(f"{name} needs exactly {exactly} points")
    elif not isinstance(points, (list, tuple)) or len(points) < minimum:
        raise CameraConfigError(f"{name} needs at least {minimum} points")
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


def _optional_points(data: dict[str, Any], key: str, validator: Any) -> Any:
    if data.get(key) is None:
        return None
    return validator(data[key], key)


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
        service_zone=_optional_points(data, "service_zone", validate_polygon),
        service_line=_optional_points(data, "service_line", lambda p, n: validate_points(p, n, exactly=2)),
        stitching=_section(data, "stitching", StitchingSettings),
        service_rate=_section(data, "service_rate", ServiceRateSettings),
        alerts=_section(data, "alerts", AlertSettings),
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
