import { ApiError } from "@/lib/api";
import type { AccuracyData } from "@/lib/accuracy-types";
import { formatNumber } from "@/lib/format";

export const NOT_GENERATED = "Accuracy data not generated — run scripts/export_accuracy.py";

function signed(n: number, digits: number): string {
  const text = formatNumber(Math.abs(n), digits);
  if (n > 0) return `+${text}`;
  if (n < 0) return `−${text}`;
  return text;
}

function Figure({ label, value, frames }: Readonly<{ label: string; value: string; frames: number }>) {
  return (
    <div>
      <dt className="text-[13px] font-medium text-text-2">{label}</dt>
      <dd className="num font-display text-[28px] font-semibold leading-tight text-text">{value}</dd>
      <dd className="num text-xs text-text-3">{`${frames} frames · held-out (test) split`}</dd>
    </div>
  );
}

export function AccuracySummaryCard({
  data, error,
}: Readonly<{ data: AccuracyData | undefined; error: Error | null }>) {
  let body: React.ReactNode;
  if (error) {
    const text = error instanceof ApiError && error.status === 404 ? NOT_GENERATED : error.message;
    body = <p className="mt-3 text-sm text-text-2">{text}</p>;
  } else if (!data) {
    body = <p className="mt-3 text-sm text-text-3">Loading</p>;
  } else if (!data.queue_length) {
    body = <p className="mt-3 text-sm text-text-2">{NOT_GENERATED}</p>;
  } else {
    const t = data.queue_length.pooled.test;
    body = (
      <>
        <h4 className="mt-3 text-sm font-medium text-text">Queue length</h4>
        <dl className="mt-2 grid gap-4 sm:grid-cols-3">
          <Figure label="Mean absolute error (people)" value={formatNumber(t.mae, 2)} frames={t.frames} />
          <Figure label="Bias (people)" value={signed(t.bias, 2)} frames={t.frames} />
          <Figure label="Within ±1 person" value={`${formatNumber(t.within_one_rate * 100, 0)}%`} frames={t.frames} />
        </dl>
        <h4 className="mt-4 text-sm font-medium text-text">Waiting time</h4>
        <p className="mt-1 text-sm text-text-2">
          {data.waiting_time ? "Measured on recorded clips; see the Accuracy page." : "Not measured yet"}
        </p>
      </>
    );
  }
  return (
    <section className="rounded-card border border-border bg-surface p-5" aria-label="How accurate is it?">
      <h3 className="text-[13px] font-medium text-text-2">How accurate is it?</h3>
      {body}
    </section>
  );
}
