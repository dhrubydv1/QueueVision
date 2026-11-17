import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import type { AccuracyData, CallCentreMethod, PredictorOnCallCentreLogs } from "@/lib/accuracy-types";
import { AccuracyView } from "./AccuracyView";

// Made-up fixture values for rendering only.
const pooled = { mae: 1.23, rmse: 1.5, bias: -0.45, within_one_rate: 0.625, frames: 321, clips: ["a"] };
const cat = (key: string, direction: string, per_frame: number) => ({ key, label: `Label ${key}`, direction, total: 10, per_frame });
const split = (frames: number, suffix: string) => ({
  frames,
  categories: [cat(`o1${suffix}`, "over", 0.11), cat(`u1${suffix}`, "under", 0.22), cat(`u2${suffix}`, "under", 0.33), cat(`c${suffix}`, "correct", 5), cat(`x${suffix}`, "other", 0.01)],
});
const data = {
  schema: "accuracy-1", generated_at: "2026-01-01T00:00:00Z", missing_sources: [],
  queue_length: {
    source: "s", protocol: {},
    per_clip: [
      { clip: "clipA", split: "val", mae: 1.1, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 10 },
      { clip: "clipB", split: "test", mae: 2.2, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 20 },
      { clip: "clipC", split: "test", mae: 3.3, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 30 },
    ],
    pooled: { val: pooled, test: pooled },
  },
  count_errors: { source: "s", iou: 0.5, window: "w", per_split: { all: split(100, "-all"), val: split(40, "-val"), test: split(60, "-test") } },
  changes_not_adopted: [
    { name: "Change one", source: "sprint5.json", sprint: "Sprint 5", val_mae: 1.5, test_mae: 2.5, baseline_val_mae: 1.4, baseline_test_mae: 2.4 },
  ],
  waiting_time: null,
  limits: ["Limit one", "Limit two"],
} as unknown as AccuracyData;

describe("AccuracyView", () => {
  it("renders headline cards from the test pooled metrics", () => {
    render(<AccuracyView data={data} error={null} />);
    expect(screen.getByText("1.23")).toBeInTheDocument();
    expect(screen.getByText("−0.45")).toBeInTheDocument();
    expect(screen.getByText("63%")).toBeInTheDocument();
    expect(screen.getByText("Not measured yet")).toBeInTheDocument();
    expect(screen.getAllByText(/321 frames/).length).toBeGreaterThanOrEqual(3);
  });
  it("counts distinct clips for waiting time when a clip has both kinds", () => {
    const d = { ...data, waiting_time: { source: "s", pooled: null, per_clip: [
      { clip: "a", kind: "predicted", source: "x", metrics: {} },
      { clip: "a", kind: "observed", source: "y", metrics: {} },
      { clip: "b", kind: "predicted", source: "z", metrics: {} },
    ] } } as unknown as AccuracyData;
    render(<AccuracyView data={d} error={null} />);
    expect(screen.getByText("2 recorded clips")).toBeInTheDocument();
  });
  it("lists one data row per clip with its split, plus a worded legend", () => {
    render(<AccuracyView data={data} error={null} />);
    const rows = within(screen.getByTestId("clip-bars-data")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("clipA"), expect.stringContaining("clipB"), expect.stringContaining("clipC"),
    ]);
    expect(rows[0]).toHaveTextContent("Validation");
    expect(rows[1]).toHaveTextContent("Test");
    expect(screen.getByText("Validation (tuning) clips")).toBeInTheDocument();
    expect(screen.getByText("Test (held-out) clips")).toBeInTheDocument();
  });
  it("orders clips val, test, unassigned and labels unassigned clips", () => {
    const d = { ...data, queue_length: { ...data.queue_length!, per_clip: [
      { clip: "z", split: null, mae: 1, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 1 },
      { clip: "b", split: "test", mae: 1, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 1 },
      { clip: "a", split: "test", mae: 1, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 1 },
      { clip: "m", split: "val", mae: 1, rmse: 1, bias: 0, within_one_rate: 0.5, frames: 1 },
    ] } } as AccuracyData;
    render(<AccuracyView data={d} error={null} />);
    const rows = within(screen.getByTestId("clip-bars-data")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.firstChild?.textContent)).toEqual(["m", "a", "b", "z"]);
    expect(rows[3]).toHaveTextContent("Unassigned");
    expect(screen.getByText("Unassigned clips")).toBeInTheDocument();
  });
  it("omits the unassigned legend entry when every clip has a split", () => {
    render(<AccuracyView data={data} error={null} />);
    expect(screen.queryByText("Unassigned clips")).toBeNull();
  });
  it("shows test count errors by default and switches split", () => {
    render(<AccuracyView data={data} error={null} />);
    const list = () => screen.getByTestId("count-bars-data");
    expect(screen.getByRole("button", { name: "Test" })).toHaveAttribute("aria-pressed", "true");
    expect(within(list()).getByText("Label o1-test")).toBeInTheDocument();
    expect(within(list()).getAllByRole("row").length).toBe(1 + 3);
    fireEvent.click(screen.getByRole("button", { name: "Validation" }));
    expect(screen.getByRole("button", { name: "Validation" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Test" })).toHaveAttribute("aria-pressed", "false");
    expect(within(list()).getByText("Label u2-val")).toBeInTheDocument();
    expect(screen.getByText("Over-count causes")).toBeInTheDocument();
    expect(screen.getByText("Under-count causes")).toBeInTheDocument();
  });
  it("notes correct/other categories outside the bars", () => {
    render(<AccuracyView data={data} error={null} />);
    const note = screen.getByTestId("count-notes");
    expect(note).toHaveTextContent("Label c-test");
    expect(note).toHaveTextContent("Label x-test");
    expect(within(screen.getByTestId("count-bars-data")).queryByText("Label c-test")).toBeNull();
  });
  it("renders the changes table only when present", () => {
    const { rerender } = render(<AccuracyView data={data} error={null} />);
    expect(screen.getByText("Change one")).toBeInTheDocument();
    expect(screen.getByText("Sprint 5")).toBeInTheDocument();
    expect(screen.getByText("1.40 → 1.50")).toBeInTheDocument();
    rerender(<AccuracyView data={{ ...data, changes_not_adopted: null }} error={null} />);
    expect(screen.queryByText("Changes tried and not adopted")).toBeNull();
  });
  it("scrolls the changes table horizontally inside its card", () => {
    render(<AccuracyView data={data} error={null} />);
    const table = screen.getByText("Change one").closest("table");
    expect(table?.parentElement).toHaveClass("overflow-x-auto");
  });
  it("hides the chart data tables with a clipping wrapper, not on the table itself", () => {
    // A table ignores width and overflow, so sr-only on the table would still widen the page.
    render(<AccuracyView data={data} error={null} />);
    for (const id of ["clip-bars-data", "count-bars-data"]) {
      const table = screen.getByTestId(id);
      expect(table).not.toHaveClass("sr-only");
      expect(table.parentElement).toHaveClass("sr-only");
    }
  });
  it("shows missing sources with reason and path", () => {
    const d = { ...data, queue_length: null, missing_sources: [{ section: "queue_length", path: "outputs/x.json", reason: "file not found" }] };
    render(<AccuracyView data={d} error={null} />);
    expect(screen.getAllByText("Not available — file not found (outputs/x.json)").length).toBeGreaterThan(0);
  });
  it("lists limits", () => {
    render(<AccuracyView data={data} error={null} />);
    expect(screen.getByText("Limit two")).toBeInTheDocument();
  });
  it("shows the empty state on 404", () => {
    render(<AccuracyView data={undefined} error={new ApiError("nope", 404)} />);
    expect(screen.getByText("Accuracy data not generated — run scripts/export_accuracy.py")).toBeInTheDocument();
  });

  describe("call-centre predictor card", () => {
    const iv = (value: number) => ({ value, lo: value - 1, hi: value + 1 });
    const method = (mae: number, coverage: number, bias: number): CallCentreMethod =>
      ({ n_agent: 100, n_covered: 90, coverage: { value: coverage, lo: coverage - 0.01, hi: Math.min(1, coverage + 0.01) }, mae_s: iv(mae), rmse_s: iv(1), median_abs_error_s: iv(1), bias_s: iv(bias), within_30s: iv(0.1), within_60s: iv(0.2), range_coverage: iv(0.25) });
    const methods = (offset: number) => ({
      predictor: method(100 + offset, 0.9, 40), les: method(80, 0.99, -10), recent_mean: method(70, 0.98, -5), regression: method(60, 1, 0),
    });
    const cc: PredictorOnCallCentreLogs = {
      source: "outputs/metrics/anonymous_bank_wait_eval.json", citation: "Made-up citation", caveat: "c",
      protocol_version: 1, interval: "95% test interval", seed: 1, queue: "primary", split: "test", n_days: 5, n_calls: 12345,
      methods: methods(0), paired_mae_difference: {}, hang: { n_hang: 1, n_hang_with_prediction: 1, n_hung_up_after_longer_than_predicted: 0 },
      post_hoc_s1: { post_hoc: true, protocol_version: 2, definition: "d", n_days: 5, n_calls: 777, methods: methods(-20), paired_mae_difference: {}, hang: { n_hang: 1, n_hang_with_prediction: 1, n_hung_up_after_longer_than_predicted: 0 } },
    };
    it("shows the formula next to the three baselines, with caveat and post-hoc note", () => {
      render(<AccuracyView data={{ ...data, predictor_on_call_centre_logs: cc }} error={null} />);
      expect(screen.getByRole("heading", { name: "Waiting-time predictor on real call-centre queues" })).toBeInTheDocument();
      expect(screen.getByText("Phone queue, 1999, Israel; measures the prediction formula, not the cameras.")).toBeInTheDocument();
      const rows = within(screen.getByTestId("call-centre-table")).getAllByRole("row").slice(1);
      expect(rows).toHaveLength(4);
      expect(rows[0]).toHaveTextContent("100.0 [99.0, 101.0]");
      expect(rows[0]).toHaveTextContent("90 [89, 91]");
      expect(rows[0]).toHaveTextContent("+40.0");
      expect(rows[3]).toHaveTextContent("60.0 [59.0, 61.0]");
      expect(screen.getByText(/12,345 answered calls over 5 held-out test days/)).toBeInTheDocument();
      expect(screen.getByTestId("call-centre-s1")).toHaveTextContent(/Post hoc, not the headline.*80\.0 s.*worse than 2 of the 3 baselines/);
      expect(screen.getByText(/Made-up citation/)).toBeInTheDocument();
      // the camera card is unchanged
      expect(screen.getByText("Not measured yet")).toBeInTheDocument();
    });
    it("says so when the section is absent, still under the same title", () => {
      const d = { ...data, predictor_on_call_centre_logs: null, missing_sources: [{ section: "predictor_on_call_centre_logs", path: "outputs/metrics/anonymous_bank_wait_eval.json", reason: "file not found" }] };
      render(<AccuracyView data={d} error={null} />);
      expect(screen.getByRole("heading", { name: "Waiting-time predictor on real call-centre queues" })).toBeInTheDocument();
      expect(screen.getByText("Not available — file not found (outputs/metrics/anonymous_bank_wait_eval.json)")).toBeInTheDocument();
      expect(screen.queryByTestId("call-centre-table")).toBeNull();
    });
  });
});

describe("AccuracyView evidence additions", () => {
  const band = (lo: number, hi: number) => [lo, hi];
  const withIntervals = {
    ...data,
    queue_length: {
      ...data.queue_length!,
      pooled: {
        ...data.queue_length!.pooled,
        test: {
          ...pooled, n_clips: 3, n_frames: 99,
          interval_clip_bootstrap_95: { mae: band(0.51, 2.49), bias: band(0, 1), within_one_rate: band(0, 1) },
          interval_per_frame_optimistic_95: { label: "optimistic label", mae: band(1.01, 1.49), bias: band(0, 1), within_one_rate: band(0, 1) },
        },
      },
    },
    attribution_iou: {
      source: "attr.json", window: "from5s",
      per_split: Object.fromEntries(["all", "val", "test"].map((k) => [k, {
        "iou_0.5": { frames: 12, categories: [cat("k1", "over", 0.44)] },
        "iou_0.3": { frames: 12, categories: [cat("k1", "over", 0.22)] },
      }])),
    },
    comparisons: {
      source: "cmp.json", protocol: "protocol vX", series: "s", bootstrap: "b", notes: [],
      current: { configuration: "A", label: "Current thing", test_mae: 1.5, test_interval_clip_bootstrap_95: band(1, 2), val_mae: 1.1, processing_fps: 31.94, clip_fps: 30 },
      rows: [
        { configuration: "B", label: "Candidate B", metric: "m", test_mae: 1.25, interval_clip_bootstrap_95: band(0.75, 1.75),
          paired_delta_vs_A: -0.25, paired_delta_interval_95: band(-0.5, 0.1), val_mae: 0.95, processing_fps: 20.5, clip_fps: 30,
          adopt: false, failed_criteria: ["a", "c"], source: ["x"] },
        { configuration: "C", label: "Candidate C", metric: "m", test_mae: 1.0, interval_clip_bootstrap_95: band(0.5, 1.5),
          paired_delta_vs_A: -0.5, paired_delta_interval_95: null, val_mae: 0.9, processing_fps: 31, clip_fps: 30,
          adopt: true, failed_criteria: [], source: ["y"] },
      ],
    },
  } as unknown as AccuracyData;

  it("builds the headline sentence only from the JSON values", () => {
    render(<AccuracyView data={withIntervals} error={null} />);
    const line = screen.getByTestId("headline-interval");
    expect(line).toHaveTextContent("1.23 people, 95% interval 0.51\u20132.49, 3 clips");
    expect(line).toHaveTextContent("Per-frame interval 1.01\u20131.49 (optimistic label)");
  });
  it("shows no interval text when intervals are missing", () => {
    render(<AccuracyView data={data} error={null} />);
    expect(screen.queryByTestId("headline-interval")).toBeNull();
    expect(screen.getByText("1.23")).toBeInTheDocument();
  });
  it("renders the comparison table with decisions and failed criteria", () => {
    render(<AccuracyView data={withIntervals} error={null} />);
    const rows = within(screen.getByTestId("comparison-table")).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("1.50 [1.00\u20132.00]");
    expect(rows[0]).toHaveTextContent("31.9 of 30");
    expect(rows[1]).toHaveTextContent("1.25 [0.75\u20131.75]");
    expect(rows[1]).toHaveTextContent("\u22120.25 [\u22120.50\u20130.10]".replace("\u22120.50", "-0.50"));
    expect(rows[1]).toHaveTextContent("20.5 of 30");
    expect(rows[1]).toHaveTextContent("Not adopted");
    expect(rows[1]).toHaveTextContent("(a) test error not clearly lower");
    expect(rows[1]).toHaveTextContent("(c) slower than real time");
    expect(rows[2]).toHaveTextContent(/Adopted$/);
    expect(screen.getByTestId("comparison-caption")).toHaveTextContent("With 1 validation and 1 test clips these intervals are wide. Adoption follows the rule in protocol vX.");
  });
  it("shows both IoU thresholds side by side", () => {
    render(<AccuracyView data={withIntervals} error={null} />);
    expect(within(screen.getByTestId("attribution-table")).getByRole("row", { name: /Label k1/ })).toHaveTextContent("0.440.22");
  });
  it("reports missing comparisons from missing_sources and hides absent keys", () => {
    const missing = { ...data, comparisons: null, attribution_iou: null,
      missing_sources: [{ section: "comparisons", path: "p.json", reason: "file not found" }] } as unknown as AccuracyData;
    render(<AccuracyView data={missing} error={null} />);
    expect(screen.getByText(/file not found \(p\.json\)/)).toBeInTheDocument();
    expect(screen.queryByTestId("comparison-table")).toBeNull();
  });
});
