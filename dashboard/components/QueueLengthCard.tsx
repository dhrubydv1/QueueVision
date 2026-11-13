import { formatNumber } from "@/lib/format";

/** Queue length is the smoothed value; the raw count sits underneath it. Never the people-in-frame count. */
export function QueueLengthCard({
  smoothed, raw, windowS, big = false,
}: Readonly<{ smoothed: number | null; raw: number | null; windowS: number | null; big?: boolean }>) {
  const rawText = formatNumber(raw, 0);
  const windowText = windowS === null ? "" : ` · smoothed over ${windowS} s`;
  return (
    <section className="rounded-card border border-border bg-surface p-5" aria-label="Queue length">
      <h3 className="text-[13px] font-medium text-text-2">Queue length</h3>
      <p className={`num mt-2 font-display font-semibold leading-none text-text ${big ? "text-[64px]" : "text-[40px]"}`}>
        {formatNumber(smoothed, 0)}
      </p>
      <p className="num mt-2 text-xs text-text-3">{`Raw count ${rawText}${windowText}`}</p>
    </section>
  );
}
