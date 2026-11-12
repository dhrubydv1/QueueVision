import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
});
