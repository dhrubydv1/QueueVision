"use client";

import { useEffect, useRef, useState } from "react";
import { previewMjpegUrl } from "@/lib/api";

// Hex values mirror the engine's drawing in ai_engine/visualization/metrics_overlay.py.
const LEGEND = [
  { label: "In queue", color: "#FF8C00" },
  { label: "Joining (dwell)", color: "#FFE600" },
  { label: "Not in queue", color: "#00C800" },
  { label: "At the service point / service line", color: "#FF00FF" },
  { label: "Queue zone", color: "#00A0FF" },
];

export function PreviewPanel({ videoId, active }: Readonly<{ videoId: number; active: boolean }>) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!active && timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, [active]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const [shownId, setShownId] = useState(videoId);
  if (shownId !== videoId) {
    setShownId(videoId);
    setAttempt(0);
    setFailed(false);
  }

  function onError() {
    setFailed(true);
    if (!active) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      setFailed(false);
      setAttempt((n) => n + 1);
    }, 2000);
  }

  return (
    <div>
      <div className="relative aspect-[3/2] w-full overflow-hidden rounded-card border border-border bg-chip">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={`${videoId}-${attempt}`}
          src={previewMjpegUrl(videoId)}
          alt="Annotated camera preview"
          onError={onError}
          onLoad={() => setFailed(false)}
          className={`h-full w-full object-contain ${failed ? "invisible" : ""}`}
        />
        {failed ? (
          <p className="absolute inset-0 flex items-center justify-center text-sm text-text-2">No preview frame yet</p>
        ) : null}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-2" aria-label="Preview legend">
        {LEGEND.map((item) => (
          <li key={item.label} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="inline-block h-3 w-3 rounded-sm border border-text-3" style={{ backgroundColor: item.color }} />
            {item.label}
          </li>
        ))}
      </ul>
    </div>
  );
}
