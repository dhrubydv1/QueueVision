"""Pydantic response models for the REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    device: str | None  # device in use; null until a job has loaded the detector
    configured_device: str
    model: str
    queue_depth: int


class CameraInfo(BaseModel):
    """One camera YAML; ``error`` is set (and the rest empty) when the file is invalid."""

    id: str
    description: str = ""
    has_service_point: bool = False
    alerts_enabled: bool = False
    max_queue_length: float | None = None
    smoothing_window_s: float | None = None
    error: str | None = None


class RunOptions(BaseModel):
    frame_stride: int = 1
    blur_heads: bool = True
    max_frames: int | None = None
    realtime: bool = False


class Run(BaseModel):
    """A run record: status, progress, options, timings and the queue summary."""

    video_id: int
    original_name: str
    camera_id: str
    status: str
    error: str | None = None
    options: RunOptions
    fps: float | None = None
    frames_total: int | None = None
    frames_processed: int = 0
    percent: float | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    summary: dict[str, Any] | None = None


class Wait(BaseModel):
    video_id: int
    person_key: str
    track_ids: str
    join_s: float
    service_s: float | None = None
    wait_s: float | None = None
    abandoned: int
    left_s: float | None = None


class Alert(BaseModel):
    video_id: int
    alert_id: int
    event_type: str
    timestamp_s: float
    frame_index: int
    condition_since_s: float
    reasons: str
    queue_length: float
    predicted_wait_s: float | None = None
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None


class TimeseriesRow(BaseModel):
    time_s: float
    frame: int
    people_in_frame: int | None = None
    raw_queue_count: int | None = None
    queue_length: float | None = None
    service_rate_per_min: float | None = None
    service_rate_status: str | None = None
    predicted_wait_s: float | None = None
    predicted_wait_low_s: float | None = None
    predicted_wait_high_s: float | None = None
    prediction_status: str | None = None
