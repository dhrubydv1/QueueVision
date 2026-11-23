"""HTTP client for the API's loopback ingest routes. Every call returns a bool and never raises."""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any

TOKEN_HEADER = "X-Worker-Token"
REQUEST_TIMEOUT_S = 5.0


class IngestClient:
    def __init__(
        self,
        api: str,
        video_id: int,
        token: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = f"{api.rstrip('/')}/api/ingest/{int(video_id)}"
        self._token = token
        self._opener = opener
        self._clock = clock
        self._last_ok = clock()

    def __repr__(self) -> str:  # never show the token
        return f"IngestClient({self._base})"

    def seconds_since_ok(self) -> float:
        """Clock seconds since the last request the API accepted (or since construction)."""
        return self._clock() - self._last_ok

    def _post(self, route: str, body: bytes, content_type: str) -> bool:
        request = urllib.request.Request(
            f"{self._base}/{route}",
            data=body,
            method="POST",
            headers={"Content-Type": content_type, TOKEN_HEADER: self._token},
        )
        try:
            response = self._opener(request, timeout=REQUEST_TIMEOUT_S)
            try:
                status = int(getattr(response, "status", 204))
            finally:
                close = getattr(response, "close", None)
                if close:
                    close()
        except Exception:  # network error, HTTP error status, anything: report False
            return False
        if 200 <= status < 300:
            self._last_ok = self._clock()
            return True
        return False

    def _post_json(self, route: str, payload: dict[str, Any]) -> bool:
        try:
            body = json.dumps(payload, default=str).encode("utf-8")
        except (TypeError, ValueError):
            return False
        return self._post(route, body, "application/json")

    def batch(
        self,
        events: list[dict],
        timeseries: list[dict],
        progress: dict | None,
        frames_processed: int,
        captured_at_unix: float | None,
        seq: int | None = None,
    ) -> bool:
        return self._post_json(
            "batch",
            {
                "seq": seq,
                "events": events,
                "timeseries": timeseries,
                "progress": progress,
                "frames_processed": frames_processed,
                "captured_at_unix": captured_at_unix,
            },
        )

    def preview(self, jpeg: bytes) -> bool:
        return self._post("preview", jpeg, "image/jpeg")

    def heartbeat(self, frames_processed: int, processing_fps: float) -> bool:
        return self._post_json(
            "heartbeat", {"frames_processed": frames_processed, "processing_fps": processing_fps}
        )

    def final(self, status: str, error: str | None, report: dict | None) -> bool:
        return self._post_json("final", {"status": status, "error": error, "report": report})
