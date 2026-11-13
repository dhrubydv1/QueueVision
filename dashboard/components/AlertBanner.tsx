"use client";

import { useState } from "react";
import { AlertIcon } from "./icons";

export function AlertBanner({ reasons }: Readonly<{ reasons: string[] }>) {
  const [hidden, setHidden] = useState(false);
  if (hidden) return null;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-card border border-warn-border bg-warn-soft p-4 text-warn-text">
      <AlertIcon className="mt-0.5 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">Congestion alert</p>
        {reasons.length > 0 ? <ul className="mt-1 list-disc pl-5 text-sm">{reasons.map((r) => <li key={r}>{r}</li>)}</ul> : null}
        <p id="alert-ack-note" className="mt-1 text-xs">Acknowledge hides this banner on this screen only; nothing is recorded.</p>
      </div>
      <button
        type="button"
        aria-describedby="alert-ack-note"
        onClick={() => setHidden(true)}
        className="h-10 rounded-control border border-warn-border bg-surface px-3 text-sm font-medium text-warn-text max-sm:h-11"
      >
        Acknowledge
      </button>
    </div>
  );
}
