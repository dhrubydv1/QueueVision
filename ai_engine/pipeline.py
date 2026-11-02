"""Frame-by-frame processing generator shared by file output and future live serving."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from ai_engine.detection.types import PersonDetection
from ai_engine.queue.analyzer import QueueAnalyzer, QueueFrameState
from ai_engine.tracking.tracker import PersonTracker
from ai_engine.video.video_loader import VideoLoader

DEFAULT_FPS = 30.0  # used for timestamps only when a source reports no FPS


@dataclass(frozen=True)
class FrameResult:
    """Everything known about one processed frame.

    ``frame`` is the original BGR image (consumers may draw on it).
    ``timestamp_s`` is video time: ``frame_index / source_fps``.
    ``started_at`` is the ``perf_counter`` value when work on this frame
    began (including skipping strided frames and decoding), so consumers can
    measure full processing time after their own drawing and writing.
    """

    frame_index: int
    timestamp_s: float
    frame: np.ndarray
    people: list[PersonDetection]
    queue: QueueFrameState | None
    inference_seconds: float
    started_at: float


def source_fps(loader: VideoLoader) -> float:
    return loader.metadata.fps if loader.metadata.fps > 0 else DEFAULT_FPS


def iter_frame_results(
    loader: VideoLoader,
    tracker: PersonTracker,
    queue_analyzer: QueueAnalyzer | None = None,
    frame_stride: int = 1,
    max_frames: int | None = None,
) -> Iterator[FrameResult]:
    """Yield a :class:`FrameResult` for every ``frame_stride``-th frame.

    Skipped frames are grabbed without decoding. ``max_frames`` limits the
    number of *processed* frames. The tracker should already be configured
    for ``source_fps / frame_stride`` and warmed up.
    """
    if frame_stride < 1:
        raise ValueError("frame_stride must be >= 1")
    fps = source_fps(loader)
    frame_size = (loader.metadata.width, loader.metadata.height)
    processed = 0
    while max_frames is None or processed < max_frames:
        started_at = time.perf_counter()
        if processed > 0 and not all(loader.skip() for _ in range(frame_stride - 1)):
            return
        frame = loader.read()
        if frame is None:
            return
        frame_index = processed * frame_stride
        timestamp_s = frame_index / fps

        people = tracker.update(frame)
        inference_seconds = tracker.last_inference_seconds
        queue_state = (
            queue_analyzer.update(timestamp_s, frame_index, people, frame_size)
            if queue_analyzer is not None
            else None
        )
        processed += 1
        yield FrameResult(
            frame_index=frame_index,
            timestamp_s=timestamp_s,
            frame=frame,
            people=people,
            queue=queue_state,
            inference_seconds=inference_seconds,
            started_at=started_at,
        )
