import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  listRuns: vi.fn(() => new Promise(() => {})),
  getAccuracy: vi.fn(() => new Promise(() => {})),
  getCameras: vi.fn(() => new Promise(() => {})),
}));
import OverviewPage from "./page";

afterEach(cleanup);

describe("OverviewPage", () => {
  it("does not claim a processing count before the runs have loaded", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><OverviewPage /></QueryClientProvider>);
    expect(screen.queryByText(/runs? processing/)).toBeNull();
  });
});
