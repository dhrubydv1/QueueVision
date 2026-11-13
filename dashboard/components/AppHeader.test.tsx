import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
import { AppHeader } from "./AppHeader";

describe("AppHeader", () => {
  afterEach(cleanup);

  it("gives the desktop nav links a 44 px minimum height", () => {
    render(<AppHeader />);
    const nav = screen.getByRole("navigation", { name: "Main" });
    for (const link of Array.from(nav.querySelectorAll("a"))) {
      expect(link.className).toMatch(/\bmin-h-11\b/);
      expect(link.className).toMatch(/\binline-flex\b/);
    }
  });
});
