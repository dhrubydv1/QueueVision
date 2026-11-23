"""Messages shared by the API job manager and the live camera worker.

Built from :class:`~ai_engine.run_tracking.FrameUpdate`; the dict shapes are the ``/ws/videos/{id}``
contract and must not change.
"""

from __future__ import annotations

from typing import Any

from ai_engine.run_tracking import FrameUpdate

SCHEMA_VERSION = "1.0"


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def progress_message(video_id: int, update: FrameUpdate) -> dict[str, Any]:
    """Live numbers for one frame (sent about once per video second)."""
    queue = update.queue
    total = update.frames_total
    frames_done = update.frame_index + 1  # source frames, matching the overlay's frame number
    percent = min(100.0, round(100.0 * frames_done / total, 1)) if total else None
    message: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "type": "progress",
        "video_id": video_id,
        "frames_processed": frames_done,
        "frames_total": total,
        "percent": percent,
        "video_time_s": round(update.timestamp_s, 3),
        "people_in_frame": update.people_in_frame,
        "processing_fps": round(update.processing_fps, 2),
        "queue_length": None,
        "raw_queue_count": None,
        "service_rate_per_min": None,
        "service_rate_status": None,
        "predicted_wait_s": None,
        "predicted_wait_low_s": None,
        "predicted_wait_high_s": None,
        "prediction_status": None,
        "alert_active": False,
        "alert_reasons": [],
    }
    if queue is not None:
        message.update(
            queue_length=queue.queue_length,
            raw_queue_count=queue.raw_count,
            service_rate_per_min=_round(queue.service_rate.per_min, 3),
            service_rate_status=queue.service_rate.status,
            predicted_wait_s=_round(queue.prediction.predicted_wait_s, 1),
            predicted_wait_low_s=_round(queue.prediction.low_s, 1),
            predicted_wait_high_s=_round(queue.prediction.high_s, 1),
            prediction_status=queue.prediction.status,
            alert_active=queue.alert_active,
            alert_reasons=list(queue.alert_reasons),
        )
    return message


def event_message(video_id: int, event: dict) -> dict[str, Any]:
    """One queue event; ``event`` is the event's ``to_dict()`` payload, unchanged."""
    return {"schema_version": SCHEMA_VERSION, "type": "event", "video_id": video_id, "event": event}


def timeseries_row(update: FrameUpdate) -> dict[str, Any]:
    """One time-series row; missing values are None (same fields and rounding as the CSV)."""
    queue = update.queue
    assert queue is not None
    rate, prediction = queue.service_rate, queue.prediction
    return {
        "time_s": round(update.timestamp_s, 3),
        "frame": update.frame_index,
        "people_in_frame": update.people_in_frame,
        "raw_queue_count": queue.raw_count,
        "queue_length": queue.queue_length,
        "service_rate_per_min": _round(rate.per_min, 3),
        "service_rate_status": rate.status,
        "predicted_wait_s": _round(prediction.predicted_wait_s, 1),
        "predicted_wait_low_s": _round(prediction.low_s, 1),
        "predicted_wait_high_s": _round(prediction.high_s, 1),
        "prediction_status": prediction.status,
    }
