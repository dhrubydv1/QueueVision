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

/**
 * Contents come straight from outputs/metrics/<clip>_wait_eval.json (kind "predicted") or
 * <clip>_observed_wait_eval.json (kind "observed"); the exporter does not reshape them.
 * `clip` never carries the `_observed` suffix.
 */
export interface WaitingTimeClip {
  clip: string;
  kind: "predicted" | "observed";
  source: string;
  metrics: Record<string, unknown>;
}

export interface WaitingTimeSection {
  source: string;
  per_clip: WaitingTimeClip[];
  pooled: Record<string, unknown> | null;
}

/** A metric with its 95 % day-level bootstrap interval, as written by the scoring script. */
export interface Interval {
  value: number;
  lo: number;
  hi: number;
}

export type CallCentreMethodKey = "predictor" | "les" | "recent_mean" | "regression";

export interface CallCentreMethod {
  n_agent: number;
  n_covered: number;
  coverage: Interval;
  mae_s: Interval;
  rmse_s: Interval;
  median_abs_error_s: Interval;
  bias_s: Interval;
  within_30s: Interval;
  within_60s: Interval;
  /** Predictor only: share of actual waits inside the predicted low-high range. */
  range_coverage?: Interval;
}

export interface CallCentreHang {
  n_hang: number;
  n_hang_with_prediction: number;
  n_hung_up_after_longer_than_predicted: number;
}

/** Post-hoc sensitivity S1 ("all service starts", protocol v2): never the headline. */
export interface CallCentrePostHocS1 {
  post_hoc: true;
  protocol_version: number;
  definition: string;
  n_days: number;
  n_calls: number;
  methods: Record<CallCentreMethodKey, CallCentreMethod>;
  paired_mae_difference: Record<string, { n_common: number; mae_diff_s: Interval }>;
  hang: CallCentreHang;
}

/** Predictor accuracy on real call-centre queues ("Anonymous Bank" logs); test months, primary queue. */
export interface PredictorOnCallCentreLogs {
  source: string;
  citation: string;
  caveat: string;
  protocol_version: number;
  interval: string;
  seed: number;
  queue: string;
  split: string;
  n_days: number;
  n_calls: number;
  methods: Record<CallCentreMethodKey, CallCentreMethod>;
  paired_mae_difference: Record<string, { n_common: number; mae_diff_s: Interval }>;
  hang: CallCentreHang;
  post_hoc_s1: CallCentrePostHocS1;
}

export interface AccuracyData {
  schema: "accuracy-1";
  generated_at: string;
  missing_sources: MissingSource[];
  queue_length: QueueLengthSection | null;
  count_errors: CountErrorsSection | null;
  changes_not_adopted: ChangeNotAdopted[] | null;
  waiting_time: WaitingTimeSection | null;
  /** Absent in files exported before this section existed. */
  predictor_on_call_centre_logs?: PredictorOnCallCentreLogs | null;
  limits: string[];
}
