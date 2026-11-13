import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { AlertBanner } from "./AlertBanner";

describe("AlertBanner", () => {
  afterEach(cleanup);

  it("explains Acknowledge in visible text, not a title tooltip", () => {
    render(<AlertBanner reasons={["queue is long"]} />);
    const note = screen.getByText(/nothing is recorded/i);
    expect(note).toBeVisible();
    expect(screen.getByRole("button", { name: "Acknowledge" })).not.toHaveAttribute("title");
  });

  it("links the note to the button and hides the banner on click", async () => {
    render(<AlertBanner reasons={[]} />);
    const button = screen.getByRole("button", { name: "Acknowledge" });
    const note = screen.getByText(/nothing is recorded/i);
    expect(button.getAttribute("aria-describedby")).toBe(note.id);
    await userEvent.tab();
    expect(button).toHaveFocus();
    await userEvent.click(button);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
