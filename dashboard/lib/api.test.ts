import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, cancelRun, previewMjpegUrl, timeseriesCsvUrl, uploadVideo } from "./api";

function respond(status: number, body: unknown, json = true): Response {
  return new Response(json ? JSON.stringify(body) : String(body), {
    status,
    headers: { "content-type": json ? "application/json" : "text/plain" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("apiGet", () => {
  it("turns a JSON detail into an ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(404, { detail: "x" })));
    const error = await apiGet<never>("/api/videos/9").catch((e: ApiError) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.message).toBe("x");
    expect(error.status).toBe(404);
  });
  it("joins a 422 detail array into a readable message", async () => {
    const detail = [{ loc: ["body", "frame_stride"], msg: "must be at most 10", type: "x" }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(422, { detail })));
    const error = await apiGet<never>("/api/x").catch((e: ApiError) => e);
    expect(error.message).toBe("frame_stride: must be at most 10");
    expect(error.status).toBe(422);
  });
  it("falls back to Request failed (STATUS)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(500, "boom", false)));
    await expect(apiGet("/api/x")).rejects.toMatchObject({ message: "Request failed (500)", status: 500 });
  });
  it("reports an unreachable API", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(apiGet("/api/x")).rejects.toThrow("Cannot reach the QueueVision API");
  });
  it("returns parsed JSON on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(200, [1, 2])));
    await expect(apiGet<number[]>("/api/x")).resolves.toEqual([1, 2]);
  });
});

describe("writes and URLs", () => {
  it("posts the upload form without a content-type header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(202, { video_id: 3 }));
    vi.stubGlobal("fetch", fetchMock);
    const form = new FormData();
    await uploadVideo(form);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/videos$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(form);
    expect(init.headers).toBeUndefined();
  });
  it("posts cancel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { video_id: 3 }));
    vi.stubGlobal("fetch", fetchMock);
    await cancelRun(3);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/videos\/3\/cancel$/);
  });
  it("builds media URLs", () => {
    expect(previewMjpegUrl(4)).toMatch(/\/api\/videos\/4\/preview\.mjpg$/);
    expect(timeseriesCsvUrl(4)).toMatch(/\/api\/videos\/4\/timeseries\.csv$/);
  });
});
