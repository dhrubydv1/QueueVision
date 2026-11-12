"use client";

import Link from "next/link";
import { useState } from "react";
import type { Run } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { QueueChart, type QueuePoint } from "./QueueChart";
import { QueueLengthCard } from "./QueueLengthCard";
import { StatusChip } from "./StatusChip";
import { WaitCard } from "./WaitCard";

const MAX_POINTS = 120;

export function ProcessingRunCard({ run, windowS }: Readonly<{ run: Run; windowS: number | null }>) {
  const { progress, lastMessageAt, connectionState } = useLiveSocket(run.video_id);
  const [points, setPoints] = useState<QueuePoint[]>([]);

  // Display buffer only (last received values, no statistics); appended during render when a new message arrives.
  const [seen, setSeen] = useState<typeof progress>(null);
  if (progress !== seen) {
    setSeen(progress);
    if (progress) {
      const point = { t: progress.video_time_s, smoothed: progress.queue_length, raw: progress.raw_queue_count };
      setPoints((prev) => [...prev, point].slice(-MAX_POINTS));
    }
  }

  const congested = progress?.alert_active === true;
  const chip = connectionState === "open" ? (congested ? "congested" : "live") : "offline";

  return (
    <article className="rounded-card border border-border bg-surface p-5" aria-label={`Run ${run.original_name}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link className="font-medium text-primary hover:underline" href={`/live?id=${run.video_id}`}>{run.original_name}</Link>
        <StatusChip status={chip} />
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <QueueLengthCard smoothed={progress?.queue_length ?? null} raw={progress?.raw_queue_count ?? null} windowS={windowS} />
        <WaitCard
          label="Predicted wait"
          valueS={progress?.predicted_wait_s ?? null}
          lowS={progress?.predicted_wait_low_s ?? null}
          highS={progress?.predicted_wait_high_s ?? null}
          status={progress?.prediction_status ?? null}
          unvalidated
        />
      </div>
      <div className="mt-3">
        <QueueChart points={points} compact />
      </div>
      <p className="num mt-1 text-xs text-text-3">
        {lastMessageAt === null ? "Waiting for first update" : `updated ${formatClock(lastMessageAt)}`}
      </p>
    </article>
  );
}
