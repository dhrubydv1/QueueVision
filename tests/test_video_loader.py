"""Tests for VideoLoader and VideoWriter using synthetic videos."""

from pathlib import Path

import numpy as np
import pytest

from ai_engine.video.video_loader import VideoLoader, VideoLoadError, VideoWriter
from tests.conftest import SYNTHETIC_FPS, SYNTHETIC_FRAMES, SYNTHETIC_SIZE


def test_loads_valid_video_and_reads_metadata(synthetic_video: Path) -> None:
    with VideoLoader(synthetic_video) as loader:
        metadata = loader.metadata
        assert (metadata.width, metadata.height) == SYNTHETIC_SIZE
        assert metadata.fps == pytest.approx(SYNTHETIC_FPS)
        assert metadata.frame_count == SYNTHETIC_FRAMES
        assert metadata.duration_seconds == pytest.approx(SYNTHETIC_FRAMES / SYNTHETIC_FPS)
        assert metadata.to_dict()["path"] == str(synthetic_video)


def test_iterates_all_frames_sequentially(synthetic_video: Path) -> None:
    with VideoLoader(synthetic_video) as loader:
        indices = []
        for index, frame in loader:
            assert frame.shape == (SYNTHETIC_SIZE[1], SYNTHETIC_SIZE[0], 3)
            indices.append(index)
    assert indices == list(range(SYNTHETIC_FRAMES))


def test_read_returns_none_after_end_and_after_release(synthetic_video: Path) -> None:
    loader = VideoLoader(synthetic_video)
    for _ in loader:
        pass
    assert loader.read() is None
    loader.release()
    loader.release()  # releasing twice is safe
    assert not loader.is_open
    assert loader.read() is None


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VideoLoadError, match="does not exist"):
        VideoLoader(tmp_path / "missing.mp4")


def test_unsupported_extension_raises(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not a video")
    with pytest.raises(VideoLoadError, match="Unsupported"):
        VideoLoader(path)


def test_corrupt_video_raises(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"this is not an mp4 file" * 10)
    with pytest.raises(VideoLoadError):
        VideoLoader(path)


def test_released_on_exception(synthetic_video: Path) -> None:
    with pytest.raises(RuntimeError), VideoLoader(synthetic_video) as loader:
        raise RuntimeError("processing failed")
    assert not loader.is_open


def test_writer_creates_readable_video(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "out.mp4"
    with VideoWriter(output, 12.0, (64, 48)) as writer:
        for _ in range(5):
            writer.write(np.zeros((48, 64, 3), dtype=np.uint8))
    assert writer.frames_written == 5
    with VideoLoader(output) as loader:
        assert loader.metadata.frame_count == 5
        assert (loader.metadata.width, loader.metadata.height) == (64, 48)


def test_writer_rejects_wrong_frame_size(tmp_path: Path) -> None:
    with VideoWriter(tmp_path / "out.mp4", 10.0, (64, 48)) as writer, pytest.raises(ValueError):
        writer.write(np.zeros((10, 10, 3), dtype=np.uint8))
