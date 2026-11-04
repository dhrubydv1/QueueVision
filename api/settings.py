"""API settings, read from environment variables with local defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ai_engine.config import DEFAULT_DEVICE, DEFAULT_MODEL_PATH, PROJECT_ROOT


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

    @property
    def db_path(self) -> Path:
        return self.data_dir / "queuevision.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @classmethod
    def from_env(cls) -> ApiSettings:
        env = os.environ
        defaults = cls(data_dir=PROJECT_ROOT / "data", camera_dir=PROJECT_ROOT / "configs" / "cameras")
        max_mb = env.get("QUEUEVISION_MAX_UPLOAD_MB")
        port = env.get("QUEUEVISION_PORT")
        return cls(
            data_dir=Path(env.get("QUEUEVISION_DATA_DIR") or defaults.data_dir),
            camera_dir=Path(env.get("QUEUEVISION_CAMERA_DIR") or defaults.camera_dir),
            max_upload_bytes=int(float(max_mb) * 1024 * 1024) if max_mb else defaults.max_upload_bytes,
            host=env.get("QUEUEVISION_HOST") or defaults.host,
            port=int(port) if port else defaults.port,
            model=env.get("QUEUEVISION_MODEL") or defaults.model,
            device=env.get("QUEUEVISION_DEVICE") or defaults.device,
        )
