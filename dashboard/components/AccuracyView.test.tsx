import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import type { AccuracyData } from "@/lib/accuracy-types";
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
});
