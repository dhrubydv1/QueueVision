import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PreviewPanel } from "./PreviewPanel";

afterEach(() => vi.useRealTimers());

describe("PreviewPanel", () => {
  it("retries after an error while active", () => {
    vi.useFakeTimers();
    const { container } = render(<PreviewPanel videoId={1} active />);
    const first = container.querySelector("img");
    fireEvent.error(first!);
    expect(screen.getByText("No preview frame yet")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(2000); });
    expect(container.querySelector("img")).not.toBe(first);
  });
  it("makes no new request once the run is no longer active", () => {
    vi.useFakeTimers();
    const { container, rerender } = render(<PreviewPanel videoId={1} active />);
    const first = container.querySelector("img");
    fireEvent.error(first!);
    rerender(<PreviewPanel videoId={1} active={false} />);
    act(() => { vi.advanceTimersByTime(5000); });
    expect(container.querySelector("img")).toBeNull();
  });
  it("renders no img and no request when the run is not active", () => {
    const { container } = render(<PreviewPanel videoId={1} active={false} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("Run finished — open the recorded run")).toBeInTheDocument();
  });
});
