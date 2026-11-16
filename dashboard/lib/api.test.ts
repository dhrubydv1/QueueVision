import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, ackAlert, apiGet, cancelRun, previewMjpegUrl, reportUrl, timeseriesCsvUrl, uploadVideo, videoUrl } from "./api";

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
  it("posts an alert acknowledgement and surfaces the API detail on failure", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { alert_id: 5 }));
    vi.stubGlobal("fetch", fetchMock);
    await ackAlert(3, 5);
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/videos\/3\/alerts\/5\/ack$/);
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(404, { detail: "Alert 5 not found" })));
    await expect(ackAlert(3, 5)).rejects.toMatchObject({ message: "Alert 5 not found", status: 404 });
  });
  it("builds media URLs", () => {
    expect(previewMjpegUrl(4)).toMatch(/\/api\/videos\/4\/preview\.mjpg$/);
    expect(timeseriesCsvUrl(4)).toMatch(/\/api\/videos\/4\/timeseries\.csv$/);
  });
});

describe("base URLs", () => {
  it("on port 3000 fetches relative URLs and media goes straight to the API", async () => {
    vi.stubGlobal("location", { port: "3000", protocol: "http:", host: "localhost:3000" });
    const fetchMock = vi.fn().mockImplementation(async () => respond(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await apiGet("/api/videos");
    await uploadVideo(new FormData());
    await cancelRun(1);
    for (const call of fetchMock.mock.calls) expect(call[0]).toMatch(/^\/api\//);
    expect(previewMjpegUrl(2)).toBe("http://127.0.0.1:8000/api/videos/2/preview.mjpg");
    expect(videoUrl(2)).toBe("http://127.0.0.1:8000/api/videos/2/video");
    expect(reportUrl(2)).toBe("/api/videos/2/report");
    expect(timeseriesCsvUrl(2)).toBe("/api/videos/2/timeseries.csv");
  });
  it("on another port everything is same-origin relative", async () => {
    vi.stubGlobal("location", { port: "8000", protocol: "http:", host: "127.0.0.1:8000" });
    const fetchMock = vi.fn().mockImplementation(async () => respond(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await apiGet("/api/videos");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/videos");
    expect(previewMjpegUrl(2)).toBe("/api/videos/2/preview.mjpg");
    expect(videoUrl(2)).toBe("/api/videos/2/video");
  });
});
