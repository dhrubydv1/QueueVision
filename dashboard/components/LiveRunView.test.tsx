import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveSocket } from "@/lib/useLiveSocket";

const socket: { value: LiveSocket } = { value: undefined as unknown as LiveSocket };
vi.mock("@/lib/useLiveSocket", () => ({ useLiveSocket: () => socket.value }));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  getTimeseries: vi.fn().mockResolvedValue([]),
  getCameras: vi.fn().mockResolvedValue([{ id: "cam", max_queue_length: 8, smoothing_window_s: 2 }]),
  cancelRun: vi.fn(),
}));
import * as api from "@/lib/api";
import { LiveRunView } from "./LiveRunView";

const run = {
  video_id: 7, camera_id: "cam", original_name: "clip.mp4", status: "processing", created_at: "x",
  frames_processed: 10, options: { blur_heads: true, frame_stride: 1, realtime: false },
};
const progress = (over = {}) => ({
  schema_version: "1.0", type: "progress" as const, video_id: 7, frames_processed: 10, frames_total: 100, percent: 10,
  video_time_s: 3, people_in_frame: 5, processing_fps: 10, queue_length: 3.4, raw_queue_count: 4,
  service_rate_per_min: null, service_rate_status: "insufficient_data", predicted_wait_s: null,
  predicted_wait_low_s: null, predicted_wait_high_s: null, prediction_status: "unknown",
  alert_active: false, alert_reasons: [], ...over,
});
const setSocket = (over: Partial<LiveSocket> = {}) => {
  socket.value = {
    connectionState: "open", lastMessageAt: Date.now(), run: run as never, progress: progress(), events: [], status: null, ...over,
  };
};
function view() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><LiveRunView videoId={7} /></QueryClientProvider>);
}

describe("LiveRunView", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows raw count line, people note, service words and Live chip", async () => {
    setSocket();
    view();
    expect(await screen.findByText("Raw count 4 · smoothed over 2 s")).toBeInTheDocument();
    expect(screen.getByText("Not the queue length")).toBeInTheDocument();
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText("Unvalidated")).toBeInTheDocument();
  });

  it("shows the alert banner with reasons and the congested chip", async () => {
    setSocket({ progress: progress({ alert_active: true, alert_reasons: ["Queue length above 8"] }) });
    view();
    expect(await screen.findByText("Queue length above 8")).toBeInTheDocument();
    expect(screen.getByText("Live · congested")).toBeInTheDocument();
  });

  it("shows Offline and an offline banner when the socket is down", async () => {
    setSocket({ connectionState: "closed" });
    view();
    expect(await screen.findByText("Offline")).toBeInTheDocument();
    expect(screen.getByText(/connection lost/i)).toBeInTheDocument();
  });

  it("final run: final chip, cancel disabled, link to recorded run", async () => {
    setSocket({ connectionState: "ended", run: { ...run, status: "completed" } as never });
    view();
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel run" })).toBeDisabled();
    expect(screen.getByRole("link", { name: "Open the recorded run" })).toHaveAttribute("href", "/run?id=7");
  });

  it("not found shows Run not found with a link to /runs", async () => {
    setSocket({ connectionState: "not_found", run: null, progress: null });
    view();
    expect(await screen.findByText("Run not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /runs/i })).toHaveAttribute("href", "/runs");
  });

  it("cancel shows the API error text", async () => {
    setSocket();
    vi.mocked(api.cancelRun).mockRejectedValue(new api.ApiError("Run already finished", 409));
    view();
    await userEvent.click(await screen.findByRole("button", { name: "Cancel run" }));
    await waitFor(() => expect(screen.getByText("Run already finished")).toBeInTheDocument());
  });

  it("lists events newest first", async () => {
    const ev = (t: number, type: string) => ({ schema_version: "1.0", event_type: type, timestamp_s: t, frame_index: t });
    setSocket({ events: [ev(1, "queue_join"), ev(2, "service_start")] });
    view();
    const items = await screen.findAllByRole("listitem");
    const texts = items.map((i) => i.textContent);
    expect(texts.findIndex((t) => t?.includes("Service started"))).toBeLessThan(texts.findIndex((t) => t?.includes("Joined the queue")));
  });

  it("refetches history after a close then open, not on the first open", async () => {
    setSocket();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const ui = () => <QueryClientProvider client={qc}><LiveRunView videoId={7} /></QueryClientProvider>;
    const { rerender } = render(ui());
    await waitFor(() => expect(api.getTimeseries).toHaveBeenCalledTimes(1));
    setSocket({ connectionState: "closed" });
    rerender(ui());
    setSocket({ connectionState: "open" });
    rerender(ui());
    await waitFor(() => expect(api.getTimeseries).toHaveBeenCalledTimes(2));
  });

  it("first connect shows no Offline chip; cancel stays disabled after success", async () => {
    setSocket({ connectionState: "connecting", lastMessageAt: null, progress: null });
    view();
    expect(screen.queryByText("Offline")).not.toBeInTheDocument();
    cleanup();
    setSocket();
    vi.mocked(api.cancelRun).mockResolvedValue({} as never);
    view();
    const btn = await screen.findByRole("button", { name: "Cancel run" });
    await userEvent.click(btn);
    await waitFor(() => expect(btn).toBeDisabled());
  });
});
