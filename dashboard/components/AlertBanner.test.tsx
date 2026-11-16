import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AlertBanner } from "./AlertBanner";

describe("AlertBanner", () => {
  afterEach(cleanup);

  it("explains Acknowledge in visible text, not a title tooltip", () => {
    render(<AlertBanner reasons={["queue is long"]} onAcknowledge={vi.fn()} />);
    expect(screen.getByText("Acknowledging records the time for this alert.")).toBeVisible();
    expect(screen.queryByText(/nothing is recorded/i)).toBeNull();
    expect(screen.getByRole("button", { name: "Acknowledge" })).not.toHaveAttribute("title");
  });

  it("links the note to the button and calls onAcknowledge on click", async () => {
    const onAcknowledge = vi.fn().mockResolvedValue(undefined);
    render(<AlertBanner reasons={[]} onAcknowledge={onAcknowledge} />);
    const button = screen.getByRole("button", { name: "Acknowledge" });
    const note = screen.getByText(/records the time/i);
    expect(button.getAttribute("aria-describedby")).toBe(note.id);
    await userEvent.tab();
    expect(button).toHaveFocus();
    await userEvent.click(button);
    expect(onAcknowledge).toHaveBeenCalledTimes(1);
  });

  it("shows the failure message and keeps the banner", async () => {
    render(<AlertBanner reasons={[]} onAcknowledge={vi.fn().mockRejectedValue(new Error("Alert not found"))} />);
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(await screen.findByText("Alert not found")).toBeVisible();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeEnabled();
  });

  it("disables Acknowledge while the alert row is not recorded yet", () => {
    render(<AlertBanner reasons={[]} onAcknowledge={null} />);
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeDisabled();
  });
});
