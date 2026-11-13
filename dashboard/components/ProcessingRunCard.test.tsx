import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LiveSocket } from "@/lib/useLiveSocket";

const socket: { value: LiveSocket } = { value: undefined as unknown as LiveSocket };
vi.mock("@/lib/useLiveSocket", () => ({ useLiveSocket: () => socket.value }));
vi.mock("next/link", () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));
import { ProcessingRunCard } from "./ProcessingRunCard";

const run = {
  video_id: 7, camera_id: "cam", original_name: "clip.mp4", status: "processing", created_at: "x",
  frames_processed: 10, options: { blur_heads: true, frame_stride: 1, realtime: false },
};
const setSocket = (over: Partial<LiveSocket>) => {
  socket.value = { connectionState: "connecting", lastMessageAt: null, run: null, progress: null, events: [], status: null, ...over };
};
const card = () => render(<ProcessingRunCard run={run as never} windowS={2} />);

afterEach(cleanup);

describe("ProcessingRunCard chip", () => {
  it("is neutral Processing during the first connect", () => {
    setSocket({});
    card();
    expect(screen.getByText("Processing")).toBeInTheDocument();
    expect(screen.queryByText("Offline")).toBeNull();
  });
  it("is Offline when the socket closed", () => {
    setSocket({ connectionState: "closed" });
    card();
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });
  it("is Offline when reconnecting after data", () => {
    setSocket({ connectionState: "connecting", lastMessageAt: Date.now() });
    card();
    expect(screen.getByText("Offline")).toBeInTheDocument();
  });
});
