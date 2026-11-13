import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import type { AccuracyData } from "@/lib/accuracy-types";
import { AccuracySummaryCard } from "./AccuracySummaryCard";

// Made-up fixture values for rendering only.
const pooled = { mae: 1.23, rmse: 1.5, bias: 0.45, within_one_rate: 0.625, frames: 321, clips: ["a"] };
const data = {
  schema: "accuracy-1", generated_at: "2026-01-01T00:00:00Z", missing_sources: [],
  queue_length: { source: "s", protocol: {}, per_clip: [], pooled: { val: pooled, test: pooled } },
  count_errors: null, changes_not_adopted: null, waiting_time: null, limits: [],
} as unknown as AccuracyData;

describe("AccuracySummaryCard", () => {
  it("renders the held-out numbers, signed bias, percent and frames", () => {
    render(<AccuracySummaryCard data={data} error={null} />);
    expect(screen.getByText("How accurate is it?")).toBeInTheDocument();
    expect(screen.getByText("1.23")).toBeInTheDocument();
    expect(screen.getByText("+0.45")).toBeInTheDocument();
    expect(screen.getByText("63%")).toBeInTheDocument();
    expect(screen.getAllByText(/321 frames · held-out \(test\) split/).length).toBe(3);
    expect(screen.getByText("Not measured yet")).toBeInTheDocument();
  });
  it("shows a negative bias with a minus sign", () => {
    const neg = { ...data, queue_length: { ...data.queue_length!, pooled: { val: pooled, test: { ...pooled, bias: -0.3 } } } };
    render(<AccuracySummaryCard data={neg} error={null} />);
    expect(screen.getByText("−0.30")).toBeInTheDocument();
  });
  it("shows the not-generated message on a 404", () => {
    render(<AccuracySummaryCard data={undefined} error={new ApiError("nope", 404)} />);
    expect(screen.getByText("Accuracy data not generated — run scripts/export_accuracy.py")).toBeInTheDocument();
  });
  it("shows the message when the queue-length section is missing", () => {
    render(<AccuracySummaryCard data={{ ...data, queue_length: null }} error={null} />);
    expect(screen.getByText("Accuracy data not generated — run scripts/export_accuracy.py")).toBeInTheDocument();
  });
  it("shows other errors as their message", () => {
    render(<AccuracySummaryCard data={undefined} error={new ApiError("Cannot reach the QueueVision API", 0)} />);
    expect(screen.getByText("Cannot reach the QueueVision API")).toBeInTheDocument();
  });
});
