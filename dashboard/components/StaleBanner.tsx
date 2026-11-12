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
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const silent = lastMessageAt !== null && now() - lastMessageAt >= STALE_AFTER_MS;
  const stale = connectionState === "closed" || (connectionState === "open" && silent);

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
