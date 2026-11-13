"use client";

import { useEffect, useRef, useState } from "react";
import type { components } from "./api-types";
import {
  FINAL_STATUSES,
  eventKey,
  WS_NOT_FOUND,
  type EventMessage,
  type LiveMessage,
  type ProgressMessage,
  type StatusMessage,
} from "./live-types";

export type ConnectionState = "connecting" | "open" | "closed" | "ended" | "not_found";

export interface LiveSocketOptions {
  wsBase?: string;
  WebSocketImpl?: typeof WebSocket;
  now?: () => number;
}

export interface LiveSocket {
  connectionState: ConnectionState;
  lastMessageAt: number | null;
  run: components["schemas"]["Run"] | null;
  progress: ProgressMessage | null;
  events: EventMessage["event"][];
  status: StatusMessage | null;
}

const BACKOFF_S = [1, 2, 4, 8, 15];

/** NEXT_PUBLIC_API_WS if set; the API on :8000 under `next dev` (port 3000); else the page's own origin. */
export function wsBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_WS;
  if (configured) return configured.replace(/\/$/, "");
  if (typeof location === "undefined") return "ws://127.0.0.1:8000";
  if (location.port === "3000") return "ws://127.0.0.1:8000";
  return `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}`;
}

const EMPTY: LiveSocket = {
  connectionState: "connecting", lastMessageAt: null, run: null, progress: null, events: [], status: null,
};

const isFinal = (status: string) => (FINAL_STATUSES as readonly string[]).includes(status);

export function useLiveSocket(videoId: number | null, opts: LiveSocketOptions = {}): LiveSocket {
  const [held, setHeld] = useState<{ id: number | null; data: LiveSocket }>({ id: null, data: EMPTY });
  // Options are read when connecting; callers may pass fresh function objects every render.
  const optsRef = useRef(opts);
  useEffect(() => {
    optsRef.current = opts;
  });

  useEffect(() => {
    if (videoId === null) return;
    const { wsBase: base, WebSocketImpl, now } = optsRef.current;
    const Impl = WebSocketImpl ?? WebSocket;
    const clock = now ?? Date.now;
    const url = `${base ?? wsBase()}/ws/videos/${videoId}`;
    const seen = new Set<string>();
    let attempt = 0;
    let finished = false;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let socket: WebSocket | null = null;

    // State belongs to one run id: a different id starts from EMPTY.
    const setState = (fn: (s: LiveSocket) => LiveSocket) =>
      setHeld((h) => ({ id: videoId, data: fn(h.id === videoId ? h.data : EMPTY) }));

    const connect = () => {
      setState((s) => ({ ...s, connectionState: "connecting" }));
      const ws = new Impl(url);
      socket = ws;
      ws.onopen = () => {
        attempt = 0;
        setState((s) => ({ ...s, connectionState: "open" }));
      };
      ws.onmessage = (ev: MessageEvent) => {
        let message: LiveMessage;
        try {
          message = JSON.parse(ev.data as string) as LiveMessage;
        } catch {
          return;
        }
        const at = clock();
        if (message.type === "status" && isFinal(message.status)) finished = true;
        if (message.type === "snapshot" && isFinal(message.run.status)) finished = true;
        setState((s) => {
          const next = { ...s, lastMessageAt: at };
          switch (message.type) {
            case "snapshot":
              return { ...next, run: message.run, progress: message.progress };
            case "progress":
              return { ...next, progress: message };
            case "status":
              return { ...next, status: message };
            case "event": {
              const key = eventKey(message.event);
              if (seen.has(key)) return next;
              seen.add(key);
              return { ...next, events: [...s.events, message.event] };
            }
            default:
              return next;
          }
        });
      };
      ws.onclose = (ev: CloseEvent) => {
        if (disposed) return;
        if (ev.code === WS_NOT_FOUND) {
          setState((s) => ({ ...s, connectionState: "not_found" }));
          return;
        }
        if (finished) {
          setState((s) => ({ ...s, connectionState: "ended" }));
          return;
        }
        setState((s) => ({ ...s, connectionState: "closed" }));
        const delay = BACKOFF_S[Math.min(attempt, BACKOFF_S.length - 1)] * 1000;
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      if (socket) {
        socket.onopen = socket.onmessage = socket.onclose = null;
        socket.close();
      }
    };
  }, [videoId]);

  return held.id === videoId ? held.data : EMPTY;
}
