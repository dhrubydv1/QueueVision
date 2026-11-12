import { afterEach, describe, expect, it, vi } from "vitest";
import { formatClock, formatNumber, formatSeconds } from "./format";

describe("formatClock", () => {
  afterEach(() => vi.restoreAllMocks());
  it("shows Kathmandu time as NPT", () => {
    expect(formatClock("2026-11-12T06:15:00Z", "Asia/Kathmandu")).toBe("12:00:00 NPT");
  });
  it("accepts epoch milliseconds", () => {
    expect(formatClock(Date.parse("2026-11-12T06:15:00Z"), "Asia/Kathmandu")).toBe("12:00:00 NPT");
  });
  it("maps the browser zone to NPT when no zone is passed", () => {
    const real = Intl.DateTimeFormat.prototype.resolvedOptions;
    for (const zone of ["Asia/Kathmandu", "Asia/Katmandu"]) {
      vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(function (this: Intl.DateTimeFormat) {
        return { ...real.call(this), timeZone: zone };
      });
      expect(formatClock("2026-11-12T06:15:00Z")).toMatch(/ NPT$/);
      vi.restoreAllMocks();
    }
  });
  it("keeps the Intl short name for other zones", () => {
    expect(formatClock("2026-11-12T06:15:00Z", "UTC")).toMatch(/^06:15:00 UTC$/);
  });
});

describe("formatSeconds", () => {
  it("formats minutes and seconds", () => expect(formatSeconds(65)).toBe("1 min 05 s"));
  it("formats seconds only", () => expect(formatSeconds(42)).toBe("42 s"));
  it("returns a dash for null", () => expect(formatSeconds(null)).toBe("—"));
});

describe("formatNumber", () => {
  it("fixes the digits", () => expect(formatNumber(3.14159, 2)).toBe("3.14"));
  it("returns a dash for null", () => expect(formatNumber(null, 1)).toBe("—"));
});
