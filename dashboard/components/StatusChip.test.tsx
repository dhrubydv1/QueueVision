import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusChip } from "./StatusChip";

const words = {
  live: "Live", congested: "Live · congested", offline: "Offline", queued: "Queued",
  processing: "Processing", completed: "Completed", failed: "Failed", cancelled: "Cancelled",
} as const;

describe("StatusChip", () => {
  it.each(Object.entries(words))("renders the word for %s", (status, word) => {
    render(<StatusChip status={status as keyof typeof words} />);
    expect(screen.getByText(word)).toBeInTheDocument();
  });
});
