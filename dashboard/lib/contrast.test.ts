import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { contrastRatio } from "./contrast";

// Read the tokens from globals.css so the test cannot drift from the real values.
const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
const token = (name: string): string => {
  const match = css.match(new RegExp(`--color-${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`token ${name} not found in globals.css`);
  return match[1];
};

const WHITE = "#ffffff";
const pairs: Array<[string, string, string]> = [
  ["text", "page", "page"],
  ["text", "surface", "surface"],
  ["text-2", "page", "page"],
  ["text-2", "surface", "surface"],
  ["text-3", "page", "page"],
  ["text-3", "surface", "surface"],
  ["primary", "surface", "surface"],
  ["primary", "primary-soft", "primary-soft"],
  ["white", "primary", "primary"],
  ["warn-text", "warn-soft", "warn-soft"],
  ["ok", "ok-soft", "ok-soft"],
  ["danger", "surface", "surface"],
  ["text", "chip", "chip"],
];

// Pairs that fail 4.5:1 with the spec's colours (reported, colours not changed).
const KNOWN_FAILING = new Set<string>([]);

describe("WCAG AA contrast (>= 4.5:1)", () => {
  for (const [fg, , bg] of pairs) {
    const name = `${fg} on ${bg}`;
    const run = KNOWN_FAILING.has(name) ? it.fails : it;
    run(name, () => {
      const a = fg === "white" ? WHITE : token(fg);
      expect(contrastRatio(a, token(bg))).toBeGreaterThanOrEqual(4.5);
    });
  }

  it("contrastRatio is 21 for black on white", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
  });
});
