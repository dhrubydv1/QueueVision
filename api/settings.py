"""API settings, read from environment variables with local defaults."""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ai_engine.config import DEFAULT_DEVICE, DEFAULT_MODEL_PATH, PROJECT_ROOT


class SettingsError(ValueError):
    """An environment variable holds an unusable value."""


def is_loopback(host: str) -> bool:
    """True for ``localhost`` and loopback IP addresses; anything else (including 0.0.0.0) is not."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def parse_origins(raw: str) -> tuple[str, ...]:
    """Split a comma-separated origin list: trim, strip trailing ``/``, lower-case, drop empties.

    ``","`` therefore gives an empty tuple, i.e. an allowlist that rejects every browser origin.
    """
    items = (item.strip().rstrip("/").lower() for item in raw.split(","))
    return tuple(item for item in items if item)


def _positive(env: Mapping[str, str], name: str, kind: type[int] | type[float], default):
    """Parse a positive int/float from ``env[name]``; unset or empty gives ``default``."""
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = kind(raw)
    except ValueError:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise SettingsError(f"{name} must be a positive number, got {raw!r}")
    return value


@dataclass(frozen=True)
class ApiSettings:
    """Paths and limits for the API; ``from_env`` builds the runtime instance."""

    data_dir: Path
    camera_dir: Path
    max_upload_bytes: int = 500 * 1024 * 1024
    host: str = "127.0.0.1"
    port: int = 8000
    model: str = str(DEFAULT_MODEL_PATH)
    device: str = DEFAULT_DEVICE
    max_queued_jobs: int = 5
    max_video_seconds: float = 1800.0
    max_frame_pixels: int = 3840 * 2160
    allowed_origins: tuple[str, ...] | None = None  # None: derive the defaults from host/port
    dashboard_dir: Path = PROJECT_ROOT / "dashboard" / "out"  # Next.js static export

    @property
    def db_path(self) -> Path:
        return self.data_dir / "queuevision.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def accuracy_path(self) -> Path:
        return self.data_dir / "accuracy.json"

    def effective_allowed_origins(self) -> frozenset[str]:
        """Origins a browser may open the live WebSocket from (exact scheme://host[:port])."""
        if self.allowed_origins is not None:
            return frozenset(origin.lower() for origin in self.allowed_origins)
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        origins = {f"http://{host}:{self.port}", "http://localhost:3000", "http://127.0.0.1:3000"}
        if is_loopback(self.host):
            origins.add(f"http://localhost:{self.port}")
        return frozenset(origin.lower() for origin in origins)

    def origin_allowed(self, origin: str) -> bool:
        """True when ``origin`` is allowed; scheme and host compare case-insensitively, port exactly."""
        return origin.strip().lower() in self.effective_allowed_origins()

    @classmethod
    def from_env(cls) -> ApiSettings:
        env = os.environ
        defaults = cls(data_dir=PROJECT_ROOT / "data", camera_dir=PROJECT_ROOT / "configs" / "cameras")
        max_mb = env.get("QUEUEVISION_MAX_UPLOAD_MB")
        port = env.get("QUEUEVISION_PORT")
        origins = env.get("QUEUEVISION_ALLOWED_ORIGINS")
        return cls(
            allowed_origins=parse_origins(origins) if origins and origins.strip() else None,
            data_dir=Path(env.get("QUEUEVISION_DATA_DIR") or defaults.data_dir),
            camera_dir=Path(env.get("QUEUEVISION_CAMERA_DIR") or defaults.camera_dir),
            dashboard_dir=Path(env.get("QUEUEVISION_DASHBOARD_DIR") or defaults.dashboard_dir),
            max_upload_bytes=int(float(max_mb) * 1024 * 1024) if max_mb else defaults.max_upload_bytes,
            host=env.get("QUEUEVISION_HOST") or defaults.host,
            port=int(port) if port else defaults.port,
            model=env.get("QUEUEVISION_MODEL") or defaults.model,
            device=env.get("QUEUEVISION_DEVICE") or defaults.device,
            max_queued_jobs=_positive(env, "QUEUEVISION_MAX_QUEUED_JOBS", int, defaults.max_queued_jobs),
            max_video_seconds=_positive(
                env, "QUEUEVISION_MAX_VIDEO_SECONDS", float, defaults.max_video_seconds
            ),
            max_frame_pixels=_positive(env, "QUEUEVISION_MAX_FRAME_PIXELS", int, defaults.max_frame_pixels),
        )
