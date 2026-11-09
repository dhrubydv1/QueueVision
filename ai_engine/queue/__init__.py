"""Queue zone, membership and queue-length logic (no model or drawing code)."""

from ai_engine.queue.alerts import CongestionMonitor
from ai_engine.queue.analyzer import QueueAnalyzer, QueueFrameState
from ai_engine.queue.camera_config import (
    AlertSettings,
    CameraConfig,
    CameraConfigError,
    MembershipSettings,
    ServiceRateSettings,
    ServiceSettings,
    SmoothingSettings,
    StitchingSettings,
    load_camera_config,
    save_camera_config,
)
from ai_engine.queue.event_schema import (
    EVENT_TYPES,
    SCHEMA_VERSION,
    CongestionEvent,
    QueueAbandonEvent,
    QueueEventType,
    QueueExitEvent,
    QueueJoinEvent,
    ServiceCompleteEvent,
    ServiceStartEvent,
    ZoneEvent,
)
from ai_engine.queue.events import QueueLifecycle
from ai_engine.queue.membership import MemberStatus, QueueMembershipTracker, foot_point
from ai_engine.queue.queue_length import QueueLengthEstimator
from ai_engine.queue.service_point import ServiceLine, ServicePoint, ServiceZone, service_point_from_config
from ai_engine.queue.stitching import GuardedTrackStitcher, StitchDecision, TrackStitcher
from ai_engine.queue.temporal_membership import QueueState, TemporalMembershipClassifier
from ai_engine.queue.waiting_time import (
    AbandonedWait,
    CompletedWait,
    ServiceRate,
    ServiceRateEstimator,
    WaitLog,
    WaitPrediction,
    predict_wait,
)
from ai_engine.queue.zone import QueueZone, point_in_polygon

__all__ = [
    "EVENT_TYPES",
    "SCHEMA_VERSION",
    "AbandonedWait",
    "AlertSettings",
    "CameraConfig",
    "CameraConfigError",
    "CompletedWait",
    "CongestionEvent",
    "CongestionMonitor",
    "GuardedTrackStitcher",
    "MemberStatus",
    "MembershipSettings",
    "QueueAbandonEvent",
    "QueueAnalyzer",
    "QueueEventType",
    "QueueExitEvent",
    "QueueFrameState",
    "QueueJoinEvent",
    "QueueLengthEstimator",
    "QueueLifecycle",
    "QueueMembershipTracker",
    "QueueState",
    "QueueZone",
    "ServiceCompleteEvent",
    "ServiceLine",
    "ServicePoint",
    "ServiceRate",
    "ServiceRateEstimator",
    "ServiceRateSettings",
    "ServiceSettings",
    "ServiceStartEvent",
    "ServiceZone",
    "SmoothingSettings",
    "StitchDecision",
    "StitchingSettings",
    "TemporalMembershipClassifier",
    "TrackStitcher",
    "WaitLog",
    "WaitPrediction",
    "ZoneEvent",
    "foot_point",
    "load_camera_config",
    "point_in_polygon",
    "predict_wait",
    "save_camera_config",
    "service_point_from_config",
]
