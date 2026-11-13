"""Per-camera queue configuration stored as YAML (``configs/cameras/*.yaml``)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


class CameraConfigError(ValueError):
    """Raised when a camera YAML file is missing fields or has invalid values."""


MEMBERSHIP_ALGORITHMS = ("legacy", "temporal")
SERVICE_RATE_BASES = ("service_start", "service_complete")
STITCHING_ALGORITHMS = ("legacy", "guarded")


@dataclass(frozen=True)
class MembershipSettings:
    """Rules that decide when a tracked person counts as queuing.

    Shared by both algorithms (seconds / box heights per second):

    * ``min_dwell_seconds``: time inside the zone before someone counts.
    * ``max_speed_heights_per_second``: maximum net foot displacement over the
      dwell window, in box heights per second.
    * ``exit_grace_seconds``: how long a person may be outside the zone or
      undetected before they are considered gone.

    Used only by ``algorithm: temporal`` (all defaults keep older YAMLs valid):

    * ``algorithm``: ``"legacy"`` (per track ID) or ``"temporal"`` (per person,
      explicit states; see ``ai_engine/queue/temporal_membership.py``).
    * ``min_observed_fraction``: fraction in [0, 1] of processed frames inside
      the dwell window in which the person was detected; 0 disables.
    * ``min_track_observations``: absolute minimum number of detections (>= 1)
      inside the dwell window.
    * ``boundary_margin_heights``: >= 0, in box heights. Entry needs the foot
      point at least margin x box height inside the zone; a waiting person
      stays until more than margin x box height outside (hysteresis). 0 uses
      the plain polygon test.
    * ``min_mean_detection_confidence``: in [0, 1]; mean detector confidence
      over the dwell window; 0 disables.
    * ``scale_motion_weight``: >= 0; weight of motion along the camera axis,
      inferred from box-height change, added to the speed test; 0 disables.
    * ``count_service_as_queue``: whether people in the SERVICE state (inside
      the configured service *zone*) are part of the queue count.
    * ``terminal_retention_seconds``: > 0; how long finished (EXITED /
      ABANDONED) people stay in the reported state counts before being forgotten.
    """

    min_dwell_seconds: float = 3.0
    max_speed_heights_per_second: float = 0.5
    exit_grace_seconds: float = 1.5
    algorithm: str = "legacy"
    min_observed_fraction: float = 0.0
    min_track_observations: int = 1
    boundary_margin_heights: float = 0.0
    min_mean_detection_confidence: float = 0.0
    scale_motion_weight: float = 0.0
    count_service_as_queue: bool = True
    terminal_retention_seconds: float = 5.0


@dataclass(frozen=True)
class SmoothingSettings:
    """Median smoothing applied to the per-frame queue count."""

    window_seconds: float = 2.0


@dataclass(frozen=True)
class StitchingSettings:
    """Linking a new track ID to a person who just vanished inside the queue zone.

    ``enabled``, ``max_gap_seconds`` and ``max_distance_heights`` apply to both
    algorithms. The rest is used only by ``algorithm: guarded``:

    * ``algorithm``: ``"legacy"`` (greedy nearest match as soon as a person is
      unseen) or ``"guarded"`` (deferred, scored, ambiguity-checked linking).
    * ``min_lost_seconds``: a lost person is not linked before being unseen this
      long. ByteTrack keeps a lost track for ``track_buffer`` frames
      (``TrackerSettings.buffer_seconds`` x processed fps, default 1.0 s) and
      revives it with the same ID, so set this to the tracker's buffer seconds.
    * ``max_size_ratio``: > 1; largest allowed box-height ratio.
    * ``ambiguity_margin``: >= 0; reject when the runner-up score is this close.
    * ``min_direction_similarity``: in [-1, 1]; minimum cosine between the lost
      person's velocity and the displacement to the new track.
    * ``direction_min_speed_heights``: >= 0; direction test only applies above
      this speed (box heights per second).
    * ``max_duplicate_iou``: in (0, 1]; reject a new track overlapping a visible
      known person's box above this IoU (probably a duplicate detection).
    * ``max_links_per_person``: >= 1; cap on the chain length of one person.
    * ``diagnostics``: keep every stitch decision (``StitchDecision``).
    """

    enabled: bool = True
    max_gap_seconds: float = 2.0
    max_distance_heights: float = 1.0
    algorithm: str = "legacy"
    min_lost_seconds: float = 1.0
    max_size_ratio: float = 1.5
    ambiguity_margin: float = 0.2
    min_direction_similarity: float = -0.2
    direction_min_speed_heights: float = 0.2
    max_duplicate_iou: float = 0.5
    max_links_per_person: int = 4
    diagnostics: bool = False


@dataclass(frozen=True)
class ServiceRateSettings:
    """How the service rate (people served per minute) is estimated."""

    method: str = "window"  # "window" | "ewma"
    window_seconds: float = 300.0
    ewma_time_constant_seconds: float = 120.0
    min_history_seconds: float = 60.0
    min_services: int = 2
    basis: str = "service_start"  # "service_start" | "service_complete"


@dataclass(frozen=True)
class ServiceSettings:
    """When a stay in the service zone counts as a completed service.

    A ``service_complete`` needs the person to have been in the zone for at
    least ``min_service_seconds`` and then to be seen outside it (while
    visible) for ``exit_confirm_seconds``.
    """

    min_service_seconds: float = 2.0
    exit_confirm_seconds: float = 1.0


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
    service: ServiceSettings = field(default_factory=ServiceSettings)

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

    def _validate_membership_algorithm_settings(self) -> None:
        m = self.membership
        if m.algorithm not in MEMBERSHIP_ALGORITHMS:
            raise CameraConfigError(
                f"membership.algorithm must be one of {', '.join(MEMBERSHIP_ALGORITHMS)}, got {m.algorithm!r}"
            )
        if isinstance(m.min_track_observations, bool) or not isinstance(m.min_track_observations, int):
            raise CameraConfigError(
                f"membership.min_track_observations must be an integer >= 1, got {m.min_track_observations!r}"
            )
        if m.min_track_observations < 1:
            raise CameraConfigError(
                f"membership.min_track_observations must be an integer >= 1, got {m.min_track_observations!r}"
            )
        if not isinstance(m.count_service_as_queue, bool):
            raise CameraConfigError(
                f"membership.count_service_as_queue must be true or false, got {m.count_service_as_queue!r}"
            )
        for name, value, upper in (
            ("membership.min_observed_fraction", m.min_observed_fraction, 1.0),
            ("membership.min_mean_detection_confidence", m.min_mean_detection_confidence, 1.0),
            ("membership.boundary_margin_heights", m.boundary_margin_heights, None),
            ("membership.scale_motion_weight", m.scale_motion_weight, None),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise CameraConfigError(f"{name} must be a number >= 0, got {value!r}")
            if upper is not None and value > upper:
                raise CameraConfigError(f"{name} must be at most {upper:g}, got {value!r}")
        retention = m.terminal_retention_seconds
        if isinstance(retention, bool) or not isinstance(retention, (int, float)) or retention <= 0:
            raise CameraConfigError(
                f"membership.terminal_retention_seconds must be a positive number, got {retention!r}"
            )

    def _validate_stitching_algorithm_settings(self) -> None:
        st = self.stitching
        if st.algorithm not in STITCHING_ALGORITHMS:
            raise CameraConfigError(
                f"stitching.algorithm must be one of {', '.join(STITCHING_ALGORITHMS)}, got {st.algorithm!r}"
            )
        for name in ("diagnostics",):
            if not isinstance(getattr(st, name), bool):
                raise CameraConfigError(f"stitching.{name} must be true or false")
        if (
            isinstance(st.max_links_per_person, bool)
            or not isinstance(st.max_links_per_person, int)
            or st.max_links_per_person < 1
        ):
            raise CameraConfigError(
                f"stitching.max_links_per_person must be an integer >= 1, got {st.max_links_per_person!r}"
            )
        for name, low, high, strict_low in (
            ("min_lost_seconds", 0.0, None, False),
            ("max_size_ratio", 1.0, None, True),
            ("ambiguity_margin", 0.0, None, False),
            ("min_direction_similarity", -1.0, 1.0, False),
            ("direction_min_speed_heights", 0.0, None, False),
            ("max_duplicate_iou", 0.0, 1.0, True),
        ):
            value = getattr(st, name)
            bad = isinstance(value, bool) or not isinstance(value, (int, float))
            bad = bad or value < low or (strict_low and value <= low)
            bad = bad or (high is not None and value > high)
            if bad:
                raise CameraConfigError(f"stitching.{name} is out of range, got {value!r}")

    def _validate_new_settings(self) -> None:
        self._validate_membership_algorithm_settings()
        if not isinstance(self.stitching.enabled, bool):
            raise CameraConfigError(
                f"stitching.enabled must be true or false, got {self.stitching.enabled!r}"
            )
        self._validate_stitching_algorithm_settings()
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
        if rate.basis not in SERVICE_RATE_BASES:
            raise CameraConfigError(
                f"service_rate.basis must be one of {', '.join(SERVICE_RATE_BASES)}, got {rate.basis!r}"
            )
        if rate.basis == "service_complete" and self.service_zone is None:
            raise CameraConfigError(
                "service_rate.basis 'service_complete' needs a service_zone: completions cannot be "
                "observed without one (a service_line gives no dwell area)"
            )
        for name, value in (
            ("service.min_service_seconds", self.service.min_service_seconds),
            ("service.exit_confirm_seconds", self.service.exit_confirm_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise CameraConfigError(f"{name} must be a non-negative number, got {value!r}")
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
            service=asdict(self.service),
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
        service=_section(data, "service", ServiceSettings),
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
