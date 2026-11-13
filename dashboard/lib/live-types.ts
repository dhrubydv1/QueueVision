// Message shapes of WS /ws/videos/{id}: docs/api.md "WebSocket", api/jobs.py and api/main.py live().
import type { components } from "./api-types";

type Run = components["schemas"]["Run"];

export interface ProgressMessage {
  schema_version: string;
  type: "progress";
  video_id: number;
  frames_processed: number;
  frames_total: number | null;
  percent: number | null;
  video_time_s: number;
  people_in_frame: number;
  processing_fps: number;
  queue_length: number | null;
  raw_queue_count: number | null;
  service_rate_per_min: number | null;
  service_rate_status: string | null;
  predicted_wait_s: number | null;
  predicted_wait_low_s: number | null;
  predicted_wait_high_s: number | null;
  prediction_status: string | null;
  alert_active: boolean;
  alert_reasons: string[];
}

/** The unchanged event schema 1.0 payload. Fields other than the common ones depend on `event_type`. */
export interface QueueEvent {
  schema_version: string;
  event_type: string;
  timestamp_s: number;
  frame_index: number;
  detected_s?: number;
  person_key?: string;
  track_id?: number;
  track_ids?: number[];
  [field: string]: unknown;
}

export interface EventMessage {
  schema_version: string;
  type: "event";
  video_id: number;
  event: QueueEvent;
}

export interface StatusMessage {
  schema_version: string;
  type: "status";
  video_id: number;
  status: string;
  error: string | null;
  frames_processed: number | null;
  frames_total: number | null;
  started_at: string | null;
  finished_at: string | null;
  percent: number | null;
}

export interface SnapshotMessage {
  schema_version: string;
  type: "snapshot";
  video_id: number;
  run: Run;
  progress: ProgressMessage | null;
}

export type LiveMessage = SnapshotMessage | ProgressMessage | EventMessage | StatusMessage;

export const FINAL_STATUSES = ["completed", "failed", "cancelled"] as const;
export const WS_NOT_FOUND = 4404;

/** Identity of an event across the socket and the history endpoint: the same event never appears twice. */
export function eventKey(e: QueueEvent): string {
  return [
    e.event_type,
    e.timestamp_s,
    e.frame_index,
    e.person_key === undefined ? "" : `p:${e.person_key}`,
    e.track_id === undefined ? "" : `t:${e.track_id}`,
    (e.track_ids ?? []).join(","),
  ].join("|");
}
