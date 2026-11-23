"""draw_zone helpers."""

from __future__ import annotations

import numpy as np

from ai_engine.tools.draw_zone import read_frame
from ai_engine.video import video_loader


def test_draw_zone_accepts_webcam_index(monkeypatch) -> None:
    opened: list = []

    class FakeCapture:
        def __init__(self, target) -> None:
            opened.append(target)

        def isOpened(self) -> bool:
            return True

        def read(self):
            return True, np.zeros((4, 4, 3), dtype=np.uint8)

        def get(self, _prop) -> float:
            return 4.0

        def release(self) -> None:
            pass

    # capture_factory is bound as a default argument, so patch the default
    monkeypatch.setattr(video_loader.VideoLoader.__init__, "__defaults__", (FakeCapture,))
    assert read_frame("0").shape == (4, 4, 3)
    assert opened == [0] and isinstance(opened[0], int)
