import type { components } from "./api-types";
import type { AccuracyData } from "./accuracy-types";

type Schemas = components["schemas"];
export type Run = Schemas["Run"];
export type Wait = Schemas["Wait"];
export type Alert = Schemas["Alert"];
export type TimeseriesRow = Schemas["TimeseriesRow"];
export type CameraInfo = Schemas["CameraInfo"];
export type Health = Schemas["Health"];
export type { QueueEvent } from "./live-types";
import type { QueueEvent } from "./live-types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Fetch base: NEXT_PUBLIC_API_URL if set, else relative (`next dev` proxies /api/* to the API). */
export function apiBase(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
}

/** Base for <img>/<video> sources (no CORS needed; avoids the dev proxy buffering MJPEG). */
export function mediaBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_URL;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof location !== "undefined" && location.port === "3000") return "http://127.0.0.1:8000";
  return "";
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && "msg" in item) {
        const { msg, loc } = item as { msg: unknown; loc?: unknown };
        const field = Array.isArray(loc) ? loc.filter((p) => p !== "body" && p !== "query").join(".") : "";
        return field ? `${field}: ${String(msg)}` : String(msg);
      }
      return "";
    });
    const joined = parts.filter(Boolean).join("; ");
    return joined || null;
  }
  return null;
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${apiBase()}${path}`, init);
  } catch {
    throw new ApiError("Cannot reach the QueueVision API", 0);
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      message = detailMessage(body?.detail) ?? message;
    } catch {
      // not JSON: keep the generic message
    }
    throw new ApiError(message, response.status);
  }
  return response;
}

export async function apiGet<T>(path: string): Promise<T> {
  return (await request(path)).json() as Promise<T>;
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await request(path, init);
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const listRuns = () => apiGet<Run[]>("/api/videos");
export const getRun = (id: number) => apiGet<Run>(`/api/videos/${id}`);
export const getTimeseries = (id: number) => apiGet<TimeseriesRow[]>(`/api/videos/${id}/timeseries`);
export const getEvents = (id: number) => apiGet<QueueEvent[]>(`/api/videos/${id}/events?limit=10000`);
export const getWaits = (id: number) => apiGet<Wait[]>(`/api/videos/${id}/waits`);
export const getAlerts = (id: number) => apiGet<Alert[]>(`/api/videos/${id}/alerts`);
export const ackAlert = (videoId: number, alertId: number) =>
  send<Alert>(`/api/videos/${videoId}/alerts/${alertId}/ack`, { method: "POST" });
export const getCameras = () => apiGet<CameraInfo[]>("/api/cameras");
export const getAccuracy = () => apiGet<AccuracyData>("/api/accuracy");

/** The browser sets the multipart content type and boundary itself. */
export const uploadVideo = (form: FormData) => send<Run>("/api/videos", { method: "POST", body: form });
export const cancelRun = (id: number) => send<Run>(`/api/videos/${id}/cancel`, { method: "POST" });
export const deleteRun = (id: number) => send<void>(`/api/videos/${id}`, { method: "DELETE" });

export const previewMjpegUrl = (id: number) => `${mediaBase()}/api/videos/${id}/preview.mjpg`;
export const videoUrl = (id: number) => `${mediaBase()}/api/videos/${id}/video`;
export const reportUrl = (id: number) => `${apiBase()}/api/videos/${id}/report`;
export const timeseriesCsvUrl = (id: number) => `${apiBase()}/api/videos/${id}/timeseries.csv`;
