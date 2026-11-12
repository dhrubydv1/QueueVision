"use client";

import { useEffect, useState } from "react";
import { formatClock } from "@/lib/format";

const STALE_AFTER_MS = 5000;

export function StaleBanner({
  lastMessageAt, connectionState, now = Date.now, children,
}: Readonly<{
  lastMessageAt: number | null;
  connectionState: "connecting" | "open" | "closed" | "ended" | "not_found";
  now?: () => number;
  children: React.ReactNode;
}>) {
  const [, setTick] = useState(0);
  const [openSince, setOpenSince] = useState<number | null>(null);
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  // Baseline for a socket that opened but never delivered a message (adjusted during render).
  const [seenState, setSeenState] = useState(connectionState);
  if (seenState !== connectionState || (connectionState === "open" && openSince === null)) {
    setSeenState(connectionState);
    if (connectionState === "open") setOpenSince((prev) => prev ?? now());
    else if (connectionState === "closed") setOpenSince(null);
  }

  const baseline = lastMessageAt ?? openSince;
  const silent = baseline !== null && now() - baseline >= STALE_AFTER_MS;
  const stale =
    connectionState === "closed" ||
    (connectionState === "connecting" && (lastMessageAt !== null)) ||
    (connectionState === "open" && silent);

  if (!stale) return <div>{children}</div>;
  return (
    <div>
      <p role="status" className="mb-3 rounded-control border border-warn-border bg-warn-soft px-3 py-2 text-sm text-warn-text">
        {`Last update ${lastMessageAt === null ? "—" : formatClock(lastMessageAt)}`}
      </p>
      <div aria-disabled="true" className="opacity-50">{children}</div>
    </div>
  );
}
