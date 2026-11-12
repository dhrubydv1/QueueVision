// Mirrors the JSON built by scripts/export_accuracy.py (schema "accuracy-1").
// A section is null when its source files are missing; see missing_sources.

export interface MissingSource {
  section: string;
  path: string;
  reason: string;
}

export interface QueueMetrics {
  mae: number;
  rmse: number;
  bias: number;
  within_one_rate: number;
  frames: number;
}

export interface QueueClipMetrics extends QueueMetrics {
  clip: string;
  split: "val" | "test" | null;
}

export interface QueuePooledMetrics extends QueueMetrics {
  clips: string[];
}

export interface QueueLengthSection {
  source: string;
  protocol: { series: string; window: string; tolerance_seconds: number; from_seconds: number };
  per_clip: QueueClipMetrics[];
  pooled: { val: QueuePooledMetrics; test: QueuePooledMetrics };
}

export type ErrorDirection = "correct" | "over" | "under" | "other";

export interface CountErrorCategory {
  key: string;
  label: string;
  direction: ErrorDirection;
  total: number;
  per_frame: number;
}

export interface CountErrorSplit {
  frames: number;
  categories: CountErrorCategory[];
}

export interface CountErrorsSection {
  source: string;
  iou: number;
  window: string;
  per_split: { all: CountErrorSplit; val: CountErrorSplit; test: CountErrorSplit };
}

export interface ChangeNotAdopted {
  name: string;
  source: string;
  sprint: string;
  val_mae: number;
  test_mae: number;
  baseline_val_mae: number;
  baseline_test_mae: number;
}

/** Contents come straight from outputs/metrics/<clip>_wait_eval.json; the exporter does not reshape them. */
export interface WaitingTimeClip {
  clip: string;
  source: string;
  metrics: Record<string, unknown>;
}

export interface WaitingTimeSection {
  source: string;
  per_clip: WaitingTimeClip[];
  pooled: Record<string, unknown> | null;
}

export interface AccuracyData {
  schema: "accuracy-1";
  generated_at: string;
  missing_sources: MissingSource[];
  queue_length: QueueLengthSection | null;
  count_errors: CountErrorsSection | null;
  changes_not_adopted: ChangeNotAdopted[] | null;
  waiting_time: WaitingTimeSection | null;
  limits: string[];
}
