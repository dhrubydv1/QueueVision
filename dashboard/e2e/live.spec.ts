import { expect, test } from "@playwright/test";
import { ApiServer, BASE_URL, missingInputs } from "./server";

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

test("live camera session shows on Live page and as a recorded run", async ({ page, request }) => {
  const started = await request.post(`${BASE_URL}/api/cameras/cad_seq25/live`);
  expect(started.status()).toBe(201);
  const run = (await started.json()) as { video_id: number; kind: string };
  expect(run.kind).toBe("live");
  const id = run.video_id;
  let stopped = false;
  try {
    await page.goto(`/live?id=${id}`);
    const queueNumber = page.getByLabel("Queue length", { exact: true }).locator("p").first();
    await expect(queueNumber).toHaveText(/^\d+$/, { timeout: 30_000 });
    const preview = page.getByRole("img", { name: "Annotated camera preview" });
    await expect.poll(() => preview.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);

    const stop = await request.post(`${BASE_URL}/api/videos/${id}/stop`);
    expect(stop.status()).toBe(200);
    stopped = true;
    await expect
      .poll(async () => ((await (await request.get(`${BASE_URL}/api/videos/${id}`)).json()) as { status: string }).status, {
        timeout: 60_000,
        intervals: [500],
      })
      .toBe("completed");

    await page.goto(`/run?id=${id}`);
    await expect(page.getByText("Live session — no video recorded")).toBeVisible();
    await expect(page.locator("video")).toHaveCount(0);
    const href = await page.getByRole("link", { name: "Time series (CSV)" }).getAttribute("href");
    expect(href).toBeTruthy();
    const csv = await request.get(new URL(href as string, BASE_URL).toString());
    expect(csv.status()).toBe(200);
  } finally {
    if (!stopped) await request.post(`${BASE_URL}/api/videos/${id}/stop`).catch(() => undefined);
  }
});
