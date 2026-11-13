import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { type APIRequestContext, type Page, expect, test } from "@playwright/test";
import { ApiServer, BASE_URL, CLIP, missingInputs } from "./server";

const CLIP27 = path.join(path.dirname(CLIP), "cad_seq27.mp4");
const SKIP_REASON = missingInputs();
test.skip(SKIP_REASON !== null, `real-model end-to-end tests need local inputs: ${SKIP_REASON}`);

let server: ApiServer;

test.beforeEach(async () => {
  server = new ApiServer();
  await server.start();
});

test.afterEach(async () => {
  await server.dispose();
});

type Run = { video_id: number; status: string };

/** Upload the CAD clip through the API (the UI has no max_frames field). */
async function uploadClip(
  request: APIRequestContext,
  options: { maxFrames: number | null; realtime: boolean; clip?: string; camera?: string },
): Promise<Run> {
  const clip = options.clip ?? CLIP;
  const multipart: Record<string, string | number | boolean | { name: string; mimeType: string; buffer: Buffer }> = {
    file: { name: path.basename(clip), mimeType: "video/mp4", buffer: readFileSync(clip) },
    camera_id: options.camera ?? "cad_seq25",
    realtime: options.realtime,
  };
  if (options.maxFrames !== null) multipart.max_frames = options.maxFrames;
  const res = await request.post(`${BASE_URL}/api/videos`, { multipart });
  expect(res.status()).toBe(202);
  return (await res.json()) as Run;
}

async function runStatus(request: APIRequestContext, id: number): Promise<string> {
  const res = await request.get(`${BASE_URL}/api/videos/${id}`);
  return ((await res.json()) as Run).status;
}

async function waitForStatus(request: APIRequestContext, id: number, status: string, timeoutMs = 120_000) {
  await expect.poll(() => runStatus(request, id), { timeout: timeoutMs, intervals: [500] }).toBe(status);
}

test("upload in real time, Live updates, completion, video plays and seeks", async ({ page, request }) => {
  const run = await uploadClip(request, { maxFrames: 150, realtime: true });
  await page.goto(`/live?id=${run.video_id}`);

  const queueNumber = page.getByLabel("Queue length", { exact: true }).locator("p").first();
  await expect(queueNumber).toHaveText(/^\d+$/, { timeout: 15_000 });

  const preview = page.getByRole("img", { name: "Annotated camera preview" });
  await expect.poll(() => preview.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);

  await expect(page.getByText("Completed", { exact: true })).toBeVisible({ timeout: 90_000 });

  await page.goto(`/run?id=${run.video_id}`);
  const video = page.locator("video");
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(1);
  const seeked = video.evaluate(
    (v: HTMLVideoElement) =>
      new Promise<number>((resolve) => {
        v.addEventListener("seeked", () => resolve(v.currentTime), { once: true });
        v.currentTime = 3;
      }),
  );
  expect(await seeked).toBeCloseTo(3, 0);
});

test("the upload form starts a real-time run and opens the Live page", async ({ page, request }) => {
  await page.goto("/runs");
  await page.locator('input[type="file"]').setInputFiles(CLIP);
  await page.getByLabel("Camera config").selectOption("cad_seq25");
  await page.getByLabel("Process in real time").check();
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page).toHaveURL(/\/live\?id=\d+/);
  const id = Number(new URL(page.url()).searchParams.get("id"));
  const res = await request.get(`${BASE_URL}/api/videos/${id}`);
  expect(((await res.json()) as { options: { realtime: boolean } }).options.realtime).toBe(true);
  // The full 20.6 s clip would run in real time; stop it once navigation is verified.
  await request.post(`${BASE_URL}/api/videos/${id}/cancel`);
  await waitForStatus(request, id, "cancelled", 30_000);
});

test("API restart shows Offline, then recovers without a reload", async ({ page, request }) => {
  const run = await uploadClip(request, { maxFrames: null, realtime: true });
  await page.goto(`/live?id=${run.video_id}`);
  await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Queue length", { exact: true }).locator("p").first()).toHaveText(/^\d+$/, {
    timeout: 15_000,
  });

  // Marker on window: survives only while the page is not reloaded.
  await page.evaluate(() => {
    (window as unknown as { __noReload: boolean }).__noReload = true;
  });

  await server.stop("SIGKILL"); // a crash, so the restart must fail the interrupted run
  await expect(page.getByText("Offline", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Last update/).first()).toBeVisible();

  await server.start(); // same data dir: the interrupted run is marked failed
  await expect(page.getByText("Failed", { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Offline", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __noReload?: boolean }).__noReload)).toBe(true);
  expect(await runStatus(request, run.video_id)).toBe("failed");
});

test("graceful API restart shows Offline, then the page shows Cancelled without a reload", async ({ page, request }) => {
  const run = await uploadClip(request, { maxFrames: null, realtime: true });
  await page.goto(`/live?id=${run.video_id}`);
  await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel("Queue length", { exact: true }).locator("p").first()).toHaveText(/^\d+$/, {
    timeout: 15_000,
  });

  // Marker on window: survives only while the page is not reloaded.
  await page.evaluate(() => {
    (window as unknown as { __noReload: boolean }).__noReload = true;
  });

  await server.stop(); // graceful shutdown: the running job ends cancelled
  await expect(page.getByText("Offline", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Last update/).first()).toBeVisible();

  await server.start(); // same data dir
  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText("Offline", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __noReload?: boolean }).__noReload)).toBe(true);
  expect(await runStatus(request, run.video_id)).toBe("cancelled");
});

async function checkPhonePage(page: Page, url: string) {
  await page.goto(url);
  await page.waitForLoadState("networkidle");
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width, `${url} scrollWidth`).toBeLessThanOrEqual(390);
  await expect(page.getByRole("navigation", { name: "Main (mobile)" })).toBeVisible();
  const controls = page.locator("button, a[href], input, select");
  const count = await controls.count();
  for (let i = 0; i < count; i++) {
    const control = controls.nth(i);
    if (!(await control.isVisible())) continue;
    await control.scrollIntoViewIfNeeded();
    await expect(control, `${url} control ${i}`).toBeInViewport();
  }
}

test("phone viewport 390x844: no horizontal scroll, every control reachable", async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const run = await uploadClip(request, { maxFrames: 60, realtime: false });
  await waitForStatus(request, run.video_id, "completed");
  for (const url of ["/", "/runs", `/run?id=${run.video_id}`, `/live?id=${run.video_id}`, "/accuracy"]) {
    await checkPhonePage(page, url);
  }
});

test("Overview hydrates without console errors and shows no build-time date or count", async ({ page, request }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));

  const html = await (await request.get(`${BASE_URL}/`)).text();
  expect(html).not.toMatch(/runs? processing/);
  // The rendered date is "<weekday> <day> <month name> <year>"; RSC module ids may contain digits, so look for that shape.
  const monthYear = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric" }).format(new Date());
  expect(html).not.toContain(monthYear);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText(/\d+ runs? processing/)).toBeVisible();
  expect(problems.filter((p) => /hydrat|did not match|server rendered/i.test(p))).toEqual([]);
});

// cad_seq27 crosses the alert limit (8) around 7 s and holds it past min_duration_seconds (10 s), so a real
// congestion_start row appears at about 17 s of video; real-time pacing keeps the run live until then.
test("Acknowledge saves the alert, survives a reload, and shows in the recorded run", async ({ page, request }) => {
  test.skip(!existsSync(CLIP27), "datasets/raw/cad_seq27.mp4 is not present");
  test.setTimeout(300_000);
  const run = await uploadClip(request, { maxFrames: 1200, realtime: true, clip: CLIP27, camera: "cad_seq27" });
  await page.goto(`/live?id=${run.video_id}`);

  const banner = page.getByRole("alert").filter({ hasText: "Congestion alert" });
  await expect(banner).toBeVisible({ timeout: 90_000 });
  const ack = banner.getByRole("button", { name: "Acknowledge" });
  await expect(ack).toBeEnabled({ timeout: 10_000 });
  await ack.click();
  await expect(banner).toBeHidden();

  const alerts = async () =>
    (await (await request.get(`${BASE_URL}/api/videos/${run.video_id}/alerts`)).json()) as {
      event_type: string; acknowledged_at: string | null;
    }[];
  const starts = (await alerts()).filter((a) => a.event_type === "congestion_start");
  expect(starts.at(-1)?.acknowledged_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|\+00:00)$/);

  await page.reload();
  await expect(page.getByLabel("Queue length", { exact: true }).locator("p").first()).toHaveText(/^\d+$/, { timeout: 15_000 });
  await page.waitForTimeout(5_000); // longer than the 2 s alert poll
  await expect(banner).toBeHidden();

  await waitForStatus(request, run.video_id, "completed", 150_000);
  await page.goto(`/run?id=${run.video_id}`);
  await page.getByRole("tab", { name: /^Alerts/ }).click();
  await expect(page.getByText(/^Acknowledged \d{2}:\d{2}:\d{2} NPT$/)).toBeVisible();
});
