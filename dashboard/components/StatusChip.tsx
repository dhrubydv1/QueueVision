export type Status = "live" | "congested" | "offline" | "queued" | "processing" | "completed" | "failed" | "cancelled";

const CONFIG: Record<Status, { word: string; classes: string; dot: string }> = {
  live: { word: "Live", classes: "bg-ok-soft text-ok", dot: "bg-ok" },
  congested: { word: "Live · congested", classes: "bg-warn-soft text-warn-text border border-warn-border", dot: "bg-warn" },
  offline: { word: "Offline", classes: "bg-chip text-text-2", dot: "bg-text-3" },
  queued: { word: "Queued", classes: "bg-chip text-text-2", dot: "bg-text-3" },
  processing: { word: "Processing", classes: "bg-primary-soft text-primary", dot: "bg-primary" },
  completed: { word: "Completed", classes: "bg-ok-soft text-ok", dot: "bg-ok" },
  failed: { word: "Failed", classes: "bg-chip text-danger", dot: "bg-danger" },
  cancelled: { word: "Cancelled", classes: "bg-chip text-text-2", dot: "bg-text-3" },
};

/** Words are always shown: colour is never the only signal. */
export function StatusChip({ status }: Readonly<{ status: Status }>) {
  const { word, classes, dot } = CONFIG[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-chip px-2.5 py-0.5 text-xs font-medium ${classes}`}>
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${dot}`} />
      {word}
    </span>
  );
}
