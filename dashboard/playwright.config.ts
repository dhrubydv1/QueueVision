import { defineConfig } from "@playwright/test";

// One API server on a fixed port with a real model: run the projects one after another.
export default defineConfig({
  testDir: "./e2e",
  outputDir: "test-results",
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  workers: 1,
  fullyParallel: false,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8765" },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
});
