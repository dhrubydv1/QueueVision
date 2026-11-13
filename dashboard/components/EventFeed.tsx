import { formatSeconds } from "@/lib/format";
import type { QueueEvent } from "@/lib/live-types";

type Shape = "circle" | "triangle" | "square" | "diamond" | "ring";

const KINDS: Record<string, { label: string; shape: Shape }> = {
  queue_join: { label: "Joined the queue", shape: "circle" },
  service_start: { label: "Service started", shape: "triangle" },
  service_complete: { label: "Service completed", shape: "square" },
  queue_abandon: { label: "Left without service", shape: "diamond" },
  queue_exit: { label: "Left the area", shape: "ring" },
  zone_exit: { label: "Left the area", shape: "ring" },
  zone_enter: { label: "Entered the area", shape: "ring" },
  congestion_start: { label: "Congestion started", shape: "diamond" },
  congestion_end: { label: "Congestion ended", shape: "ring" },
};

function Marker({ shape }: Readonly<{ shape: Shape }>) {
  const common = { width: 12, height: 12, viewBox: "0 0 12 12", "aria-hidden": true as const, className: "shrink-0 text-primary" };
  switch (shape) {
    case "circle": return <svg {...common}><circle cx="6" cy="6" r="5" fill="currentColor" /></svg>;
    case "triangle": return <svg {...common}><path d="M6 1l5 10H1z" fill="currentColor" /></svg>;
    case "square": return <svg {...common}><rect x="1" y="1" width="10" height="10" fill="currentColor" /></svg>;
    case "diamond": return <svg {...common}><path d="M6 0l6 6-6 6-6-6z" fill="currentColor" /></svg>;
    case "ring": return <svg {...common}><circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="2" /></svg>;
  }
}

export function EventFeed({ events }: Readonly<{ events: QueueEvent[] }>) {
  const known = events.filter((e) => e.event_type in KINDS);
  if (known.length === 0) return <p className="text-sm text-text-3">No events yet</p>;
  return (
    <ul className="divide-y divide-border">
      {known.map((e, i) => {
        const { label, shape } = KINDS[e.event_type];
        return (
          <li key={`${e.event_type}-${e.frame_index}-${i}`} className="flex items-center gap-3 py-2 text-sm">
            <Marker shape={shape} />
            <span className="flex-1 text-text">{label}</span>
            <span className="num text-xs text-text-3">{formatSeconds(e.timestamp_s)}</span>
          </li>
        );
      })}
    </ul>
  );
}
