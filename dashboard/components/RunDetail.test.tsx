import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, replace: push }) }));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  getRun: vi.fn(),
  getTimeseries: vi.fn().mockResolvedValue([]),
  getEvents: vi.fn().mockResolvedValue([]),
  getWaits: vi.fn().mockResolvedValue([]),
  getAlerts: vi.fn().mockResolvedValue([]),
  deleteRun: vi.fn().mockResolvedValue(undefined),
}));
import * as api from "@/lib/api";
import { RunDetail } from "./RunDetail";

// Made-up values: they only exercise the field mapping.
const summary = {
  event_counts: { queue_join: 21, service_start: 8, queue_abandon: 3 },
  alerts: { congestion_starts: 2 },
  waiting: { mean_wait_s: 31, median_wait_s: 29, p90_wait_s: 55 },
};
const run = (over = {}) => ({
  video_id: 4, camera_id: "cam", original_name: "clip.mp4", status: "completed", created_at: "2026-01-01T00:00:00Z",
  frames_processed: 100, options: { blur_heads: true, frame_stride: 2, realtime: false }, summary, ...over,
});
function view() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><RunDetail videoId={4} /></QueryClientProvider>);
}

describe("RunDetail", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it("shows summary cards from the report fields", async () => {
    vi.mocked(api.getRun).mockResolvedValue(run() as never);
    view();
    expect(await screen.findByRole("region", { name: "Joined" })).toHaveTextContent("21");
    expect(screen.getByRole("region", { name: "Service started" })).toHaveTextContent("8");
    expect(screen.getByRole("region", { name: "Left without service" })).toHaveTextContent("3");
    expect(screen.getByRole("region", { name: "Left without service" })).toHaveTextContent("3 of 21 joins");
    expect(screen.getByRole("region", { name: "Left without service" })).not.toHaveTextContent("%");
    expect(screen.getByRole("region", { name: "Congestion alerts" })).toHaveTextContent("2");
    expect(screen.getByRole("region", { name: "Mean wait" })).toHaveTextContent("31 s");
    expect(screen.getByRole("region", { name: "Median wait" })).toHaveTextContent("29 s");
    expect(screen.getByRole("region", { name: "P90 wait" })).toHaveTextContent("55 s");
    expect(screen.getAllByText("Unvalidated")).toHaveLength(3);
    expect(screen.getByText("every 2nd frame · heads blurred · fast")).toBeInTheDocument();
  });

  it("shows Not reported for absent figures", async () => {
    vi.mocked(api.getRun).mockResolvedValue(run({ summary: { event_counts: { queue_join: 5 }, waiting: { mean_wait_s: null } } }) as never);
    view();
    expect(await screen.findByRole("region", { name: "Joined" })).toHaveTextContent("5");
    for (const name of ["Service started", "Left without service", "Congestion alerts", "Mean wait", "Median wait", "P90 wait"]) {
      expect(screen.getByRole("region", { name: name })).toHaveTextContent("Not reported");
    }
  });

  it("shows tab counts and switches panels", async () => {
    vi.mocked(api.getRun).mockResolvedValue(run() as never);
    vi.mocked(api.getEvents).mockResolvedValue([{ schema_version: "1.0", event_type: "queue_join", timestamp_s: 1, frame_index: 3 }]);
    view();
    expect(await screen.findByRole("tab", { name: "Events (1)" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Waits (0)" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Events (1)" }));
    expect(within(screen.getByRole("tabpanel")).getByText("Joined the queue")).toBeInTheDocument();
  });

  it("deletes after an inline confirm and goes to /runs", async () => {
    vi.mocked(api.getRun).mockResolvedValue(run() as never);
    view();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(screen.getByText("Delete run 4? This removes its video and results.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/runs"));
    expect(api.deleteRun).toHaveBeenCalledWith(4);
  });

  it("shows the API message when delete fails with 409 and re-enables Delete", async () => {
    vi.mocked(api.getRun).mockResolvedValue(run() as never);
    vi.mocked(api.deleteRun).mockRejectedValueOnce(new api.ApiError("run is processing; cancel it first", 409));
    view();
    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("run is processing; cancel it first");
    expect(screen.getByRole("button", { name: "Delete" })).toBeEnabled();
    expect(push).not.toHaveBeenCalled();
  });

  it("disables Delete and shows a placeholder instead of a video while processing", async () => {
    vi.mocked(api.getRun).mockResolvedValue(run({ status: "processing", summary: null }) as never);
    const { container } = view();
    expect(await screen.findByRole("button", { name: "Delete" })).toBeDisabled();
    expect(container.querySelector("video")).toBeNull();
  });
});
