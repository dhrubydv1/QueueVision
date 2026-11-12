import { formatSeconds } from "@/lib/format";

/** Engine statuses: prediction "ok" | "unknown"; service rate "ok" | "insufficient_data"; null = no queue yet. */
export function WaitCard({
  label, valueS, lowS, highS, status, unvalidated,
}: Readonly<{ label: string; valueS: number | null; lowS: number | null; highS: number | null; status: string | null; unvalidated: boolean }>) {
  let body: React.ReactNode;
  if (status === "ok" && valueS !== null) {
    body = (
      <>
        <p className="num mt-2 font-display text-[32px] font-semibold leading-tight text-text">{formatSeconds(valueS)}</p>
        {lowS !== null && highS !== null ? (
          <p className="num mt-1 text-xs text-text-3">{`range ${formatSeconds(lowS)}–${formatSeconds(highS)}`}</p>
        ) : null}
      </>
    );
  } else {
    const text = status === "insufficient_data" ? "Insufficient data" : "Unknown";
    body = <p className="mt-2 font-display text-[32px] font-semibold leading-tight text-text-2">{text}</p>;
  }
  return (
    <section className="rounded-card border border-border bg-surface p-5" aria-label={label}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[13px] font-medium text-text-2">{label}</h3>
        {unvalidated ? <span className="rounded-chip bg-chip px-2 py-0.5 text-xs text-text-2">Unvalidated</span> : null}
      </div>
      {body}
    </section>
  );
}
