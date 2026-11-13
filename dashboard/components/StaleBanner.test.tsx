import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StaleBanner } from "./StaleBanner";

const T = Date.UTC(2026, 10, 12, 10, 0, 0);

describe("StaleBanner", () => {
  it("shows no banner when fresh", () => {
    render(<StaleBanner lastMessageAt={T - 1000} connectionState="open" now={() => T}>value</StaleBanner>);
    expect(screen.queryByText(/Last update/)).not.toBeInTheDocument();
    expect(screen.getByText("value")).not.toHaveAttribute("aria-disabled");
  });
  it("shows the banner after 5 s without a message", () => {
    render(<StaleBanner lastMessageAt={T - 5000} connectionState="open" now={() => T}>value</StaleBanner>);
    expect(screen.getByText(/Last update/)).toBeInTheDocument();
    expect(screen.getByText("value").closest("[aria-disabled]")).toHaveAttribute("aria-disabled", "true");
  });
  it("shows the banner when the socket is closed", () => {
    render(<StaleBanner lastMessageAt={T - 100} connectionState="closed" now={() => T}>value</StaleBanner>);
    expect(screen.getByText(/Last update/)).toBeInTheDocument();
  });
  it("shows the banner while reconnecting after data was received", () => {
    render(<StaleBanner lastMessageAt={T - 100} connectionState="connecting" now={() => T}>value</StaleBanner>);
    expect(screen.getByText(/Last update/)).toBeInTheDocument();
  });
  it("shows no banner on a first connect with no data yet", () => {
    render(<StaleBanner lastMessageAt={null} connectionState="connecting" now={() => T}>value</StaleBanner>);
    expect(screen.queryByText(/Last update/)).not.toBeInTheDocument();
  });
  it("flags an open socket that never delivers a message after 5 s", () => {
    vi.useFakeTimers();
    let t = T;
    render(<StaleBanner lastMessageAt={null} connectionState="open" now={() => t}>value</StaleBanner>);
    expect(screen.queryByText(/Last update/)).not.toBeInTheDocument();
    t = T + 5000;
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.getByText(/Last update/)).toBeInTheDocument();
  });
  it("does not flag ended or not_found", () => {
    render(<StaleBanner lastMessageAt={T - 60000} connectionState="ended" now={() => T}>value</StaleBanner>);
    expect(screen.queryByText(/Last update/)).not.toBeInTheDocument();
  });
});

afterEach(() => vi.useRealTimers());
