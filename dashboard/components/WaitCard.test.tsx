import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WaitCard } from "./WaitCard";

const base = { label: "Predicted wait", lowS: null, highS: null, unvalidated: false };

describe("WaitCard", () => {
  it("shows the value and range when ok", () => {
    render(<WaitCard {...base} valueS={90} lowS={60} highS={125} status="ok" />);
    expect(screen.getByText("1 min 30 s")).toBeInTheDocument();
    expect(screen.getByText(/range 1 min 00 s–2 min 05 s/)).toBeInTheDocument();
  });
  it("shows Unknown verbatim and never 0", () => {
    const { container } = render(<WaitCard {...base} valueS={0} status="unknown" />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\b0\b/);
  });
  it("shows Insufficient data verbatim and never 0", () => {
    const { container } = render(<WaitCard {...base} valueS={0} status="insufficient_data" />);
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\b0\b/);
  });
  it("treats a null status as Unknown", () => {
    render(<WaitCard {...base} valueS={null} status={null} />);
    expect(screen.getByText("Unknown")).toBeInTheDocument();
  });
  it("shows the Unvalidated tag", () => {
    render(<WaitCard {...base} valueS={30} status="ok" unvalidated />);
    expect(screen.getByText("Unvalidated")).toBeInTheDocument();
  });
});
