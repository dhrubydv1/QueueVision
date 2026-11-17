"""The ``frame`` privacy mode: whole-frame pixelation before any overlay is drawn (torch-free)."""

from __future__ import annotations

import numpy as np
import pytest

from ai_engine.frame_processor import FrameProcessor
from ai_engine.visualization.privacy import PRIVACY_MODES, blur_heads, pixelate_frame
from tests.conftest import FakeTracker

SIZE = (640, 480)  # width, height -> default block = 16 px
PATCH = (400, 320, 448, 368)  # unreported "person": 16-px aligned, clear of box and panel


def textured_frame() -> np.ndarray:
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 256, (SIZE[1], SIZE[0], 3), dtype=np.uint8)
    x1, y1, x2, y2 = PATCH
    # High-contrast 1-px checkerboard: maximal high-frequency detail.
    yy, xx = np.mgrid[y1:y2, x1:x2]
    frame[y1:y2, x1:x2] = (((yy + xx) % 2) * 255).astype(np.uint8)[..., None]
    return frame


def run(mode: str | None, frame: np.ndarray, **kwargs: object) -> np.ndarray:
    processor = FrameProcessor(FakeTracker(), privacy_mode=mode, **kwargs)  # type: ignore[arg-type]
    return processor.process(frame.copy(), 0, 0.0).frame


def block_variances(region: np.ndarray, block: int = 16) -> list[float]:
    return [
        float(region[y : y + block, x : x + block].var())
        for y in range(0, region.shape[0], block)
        for x in range(0, region.shape[1], block)
    ]


def test_modes_constant() -> None:
    assert PRIVACY_MODES == ("none", "boxes", "frame")


def test_pixelate_removes_detail_of_unreported_person() -> None:
    frame = textured_frame()
    x1, y1, x2, y2 = PATCH
    assert min(block_variances(frame[y1:y2, x1:x2])) > 1000  # detail present before
    out = pixelate_frame(frame.copy())
    assert max(block_variances(out[y1:y2, x1:x2])) == 0  # one flat colour per block after


def test_frame_mode_hides_person_the_detector_missed() -> None:
    frame = textured_frame()
    x1, y1, x2, y2 = PATCH  # FakeTracker reports only a person at (60,30,100,110)
    assert max(block_variances(run("frame", frame)[y1:y2, x1:x2])) == 0
    assert min(block_variances(run("none", frame)[y1:y2, x1:x2])) > 1000
    assert min(block_variances(run("boxes", frame)[y1:y2, x1:x2])) > 1000  # boxes misses them


def test_overlays_drawn_after_pixelation_equal_unblurred_overlays() -> None:
    flat = np.full((SIZE[1], SIZE[0], 3), 90, dtype=np.uint8)  # pixelating a flat frame is a no-op
    drawn_none = run("none", flat)
    assert np.array_equal(run("frame", flat), drawn_none)
    # Opaque overlay pixels (box edge, panel text) are the ones identical on two different flat
    # backgrounds; the panel's translucent fill blends with the background so it is excluded.
    drawn_other = run("none", np.full_like(flat, 200))
    overlay = (drawn_none != flat).any(axis=2) & (drawn_none == drawn_other).all(axis=2)
    assert overlay.sum() > 100
    # On a textured frame, every overlay pixel still carries the exact overlay colour.
    out = run("frame", textured_frame())
    assert np.array_equal(out[overlay], drawn_none[overlay])


def test_boxes_matches_legacy_blur_heads_path() -> None:
    frame = textured_frame()
    reference_input = frame.copy()
    blur_heads(reference_input, FakeTracker().update(frame), 0.2)
    reference = run("none", reference_input)
    assert np.array_equal(run("boxes", frame), reference)
    assert np.array_equal(run(None, frame, blur_heads=True), reference)
    assert not np.array_equal(reference, run("none", frame))


def test_none_unchanged_and_default() -> None:
    frame = textured_frame()
    assert FrameProcessor(FakeTracker()).privacy_mode == "none"
    assert np.array_equal(run(None, frame), run("none", frame))
    # Outside overlays (patch is far from the box and panel) the frame is untouched.
    x1, y1, x2, y2 = PATCH
    assert np.array_equal(run("none", frame)[y1:y2, x1:x2], frame[y1:y2, x1:x2])


def test_conflicting_arguments_raise() -> None:
    with pytest.raises(ValueError):
        FrameProcessor(FakeTracker(), blur_heads=True, privacy_mode="frame")
    with pytest.raises(ValueError):
        FrameProcessor(FakeTracker(), blur_heads=True, privacy_mode="none")
    with pytest.raises(ValueError):
        FrameProcessor(FakeTracker(), privacy_mode="bogus")
    assert FrameProcessor(FakeTracker(), blur_heads=True, privacy_mode="boxes").privacy_mode == "boxes"
    assert FrameProcessor(FakeTracker(), blur_heads=True).blur_heads is True


def test_second_render_from_clean_copy_in_another_mode() -> None:
    frame = textured_frame()
    processor = FrameProcessor(FakeTracker(), privacy_mode="boxes")
    result = processor.process(frame.copy(), 0, 0.0, keep_clean=True)
    assert processor.clean_frame is not None and np.array_equal(processor.clean_frame, frame)
    second = processor.clean_frame.copy()
    processor.draw(result, None, privacy_mode="frame", frame=second)
    assert np.array_equal(second, run("frame", frame))
    assert np.array_equal(result.frame, run("boxes", frame))  # primary output unaffected
    assert FrameProcessor(FakeTracker()).process(frame.copy(), 0, 0.0).frame is not None
