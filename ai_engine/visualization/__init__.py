"""Frame annotation: boxes, track IDs, queue zone, metrics panel and privacy blur."""

from ai_engine.visualization.metrics_overlay import (
    PANEL_POSITIONS,
    OverlayStats,
    draw_metrics_panel,
    draw_people,
    draw_queue_zone,
)
from ai_engine.visualization.privacy import blur_heads

__all__ = [
    "PANEL_POSITIONS",
    "OverlayStats",
    "blur_heads",
    "draw_metrics_panel",
    "draw_people",
    "draw_queue_zone",
]
