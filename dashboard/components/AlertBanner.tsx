"use client";

import { useState } from "react";
import { AlertIcon } from "./icons";

/** `onAcknowledge` is null while the alert's row is not recorded yet (the button waits for it). */
export function AlertBanner({ reasons, onAcknowledge }: Readonly<{
  reasons: string[];
  onAcknowledge: (() => Promise<void>) | null;
}>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function click() {
    if (!onAcknowledge) return;
    setBusy(true);
    setError(null);
    try {
      await onAcknowledge();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Acknowledge failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div role="alert" className="flex items-start gap-3 rounded-card border border-warn-border bg-warn-soft p-4 text-warn-text">
      <AlertIcon className="mt-0.5 shrink-0" />
      <div className="flex-1">
        <p className="font-medium">Congestion alert</p>
        {reasons.length > 0 ? <ul className="mt-1 list-disc pl-5 text-sm">{reasons.map((r) => <li key={r}>{r}</li>)}</ul> : null}
        <p id="alert-ack-note" className="mt-1 text-xs">Acknowledging records the time for this alert.</p>
        {error ? <p className="mt-1 text-xs font-medium">{error}</p> : null}
      </div>
      <button
        type="button"
        aria-describedby="alert-ack-note"
        disabled={!onAcknowledge || busy}
        onClick={click}
        className="h-10 rounded-control border border-warn-border bg-surface px-3 text-sm font-medium text-warn-text disabled:opacity-50 max-sm:h-11"
      >
        Acknowledge
      </button>
    </div>
  );
}
