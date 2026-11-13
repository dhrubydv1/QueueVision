import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveSocket } from "./useLiveSocket";

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  open() {
    this.onopen?.();
  }
  message(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  close(code = 1006) {
    this.closed = true;
    this.onclose?.({ code });
  }
}
const Impl = FakeSocket as unknown as typeof WebSocket;
const last = () => FakeSocket.instances[FakeSocket.instances.length - 1];

const run = (id: number, status = "processing") => ({
  video_id: id, camera_id: "c", status, created_at: "t", original_name: "a.mp4", frames_processed: 0,
  options: { blur_heads: true, frame_stride: 1, realtime: false },
});
const snapshot = (id: number, status = "processing") => ({
  schema_version: "1.0", type: "snapshot", video_id: 1, run: run(id, status), progress: null,
});
const event = (t: number, key = "p1") => ({
  schema_version: "1.0", type: "event", video_id: 1,
  event: { schema_version: "1.0", event_type: "queue_join", person_key: key, track_ids: [1], timestamp_s: t, frame_index: 1 },
});
const status = (s: string) => ({
  schema_version: "1.0", type: "status", video_id: 1, status: s, error: null,
  frames_processed: 1, frames_total: 1, started_at: null, finished_at: null, percent: 100,
});

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function mount(id: number | null = 1) {
  let t = 1000;
  return renderHook(() => useLiveSocket(id, { wsBase: "ws://x", WebSocketImpl: Impl, now: () => (t += 1) }));
}

describe("useLiveSocket", () => {
  it("connects to the run's socket", () => {
    const { result } = mount();
    expect(last().url).toBe("ws://x/ws/videos/1");
    expect(result.current.connectionState).toBe("connecting");
    act(() => last().open());
    expect(result.current.connectionState).toBe("open");
  });

  it("does not connect without an id", () => {
    mount(null);
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("reconnects after close with delays 1 s, 2 s, 4 s", () => {
    const { result } = mount();
    act(() => last().close());
    expect(result.current.connectionState).toBe("closed");
    expect(FakeSocket.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(999));
    expect(FakeSocket.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => last().close());
    act(() => vi.advanceTimersByTime(1999));
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeSocket.instances).toHaveLength(3);
    act(() => last().close());
    act(() => vi.advanceTimersByTime(3999));
    expect(FakeSocket.instances).toHaveLength(3);
    act(() => vi.advanceTimersByTime(1));
    expect(FakeSocket.instances).toHaveLength(4);
  });

  it("caps the delay at 15 s and resets it after a successful open", () => {
    mount();
    for (const delay of [1, 2, 4, 8, 15, 15]) {
      act(() => last().close());
      const before = FakeSocket.instances.length;
      act(() => vi.advanceTimersByTime(delay * 1000 - 1));
      expect(FakeSocket.instances).toHaveLength(before);
      act(() => vi.advanceTimersByTime(1));
      expect(FakeSocket.instances).toHaveLength(before + 1);
    }
    act(() => last().open());
    act(() => last().close());
    const before = FakeSocket.instances.length;
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeSocket.instances).toHaveLength(before + 1);
  });

  it("applies the snapshot on each reconnect", () => {
    const { result } = mount();
    act(() => { last().open(); last().message(snapshot(1)); });
    expect(result.current.run?.video_id).toBe(1);
    act(() => last().close());
    act(() => vi.advanceTimersByTime(1000));
    act(() => { last().open(); last().message(snapshot(2)); });
    expect(result.current.run?.video_id).toBe(2);
  });

  it("accumulates events across reconnects without duplicates", () => {
    const { result } = mount();
    act(() => { last().open(); last().message(event(1)); last().message(event(2)); });
    act(() => last().close());
    act(() => vi.advanceTimersByTime(1000));
    act(() => { last().open(); last().message(event(2)); last().message(event(3)); });
    expect(result.current.events.map((e) => e.timestamp_s)).toEqual([1, 2, 3]);
  });

  it("stores progress and status", () => {
    const { result } = mount();
    act(() => {
      last().open();
      last().message({ type: "progress", video_id: 1, percent: 5, queue_length: 2 });
      last().message(status("processing"));
    });
    expect(result.current.progress?.percent).toBe(5);
    expect(result.current.status?.status).toBe("processing");
  });

  it("updates lastMessageAt on every message", () => {
    const { result } = mount();
    expect(result.current.lastMessageAt).toBeNull();
    act(() => { last().open(); last().message(snapshot(1)); });
    const first = result.current.lastMessageAt;
    expect(first).not.toBeNull();
    act(() => last().message(event(1)));
    expect(result.current.lastMessageAt).toBeGreaterThan(first as number);
  });

  it.each(["completed", "failed", "cancelled"])("does not reconnect after a final status (%s)", (s) => {
    const { result } = mount();
    act(() => { last().open(); last().message(snapshot(1)); last().message(status(s)); });
    act(() => last().close(1000));
    expect(result.current.connectionState).toBe("ended");
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after close code 4404", () => {
    const { result } = mount();
    act(() => last().close(4404));
    expect(result.current.connectionState).toBe("not_found");
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("stops reconnecting on unmount", () => {
    const { unmount } = mount();
    act(() => last().close());
    unmount();
    act(() => vi.advanceTimersByTime(60_000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("ends without reconnecting when a completed run's snapshot is followed by a normal close", () => {
    const { result } = mount();
    act(() => { last().open(); last().message(snapshot(1, "completed")); });
    act(() => last().close(1000));
    expect(result.current.connectionState).toBe("ended");
    act(() => vi.advanceTimersByTime(30_000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("defaults to the API on :8000 under port 3000", () => {
    vi.stubGlobal("location", { port: "3000", protocol: "http:", host: "localhost:3000" });
    renderHook(() => useLiveSocket(5, { WebSocketImpl: Impl }));
    expect(last().url).toBe("ws://127.0.0.1:8000/ws/videos/5");
  });

  it("defaults to wss on the same host over https", () => {
    vi.stubGlobal("location", { port: "", protocol: "https:", host: "demo.example:443" });
    renderHook(() => useLiveSocket(5, { WebSocketImpl: Impl }));
    expect(last().url).toBe("wss://demo.example:443/ws/videos/5");
  });
});
