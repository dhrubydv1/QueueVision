"use client";

import { useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError } from "@/lib/api";
import type { AccuracyData, CountErrorCategory, MissingSource } from "@/lib/accuracy-types";
import { formatNumber, formatSigned } from "@/lib/format";
import { EmptyState } from "@/components/EmptyState";
import { MetricCard } from "@/components/MetricCard";
import { NOT_GENERATED } from "@/components/AccuracySummaryCard";

type SplitKey = "all" | "val" | "test";
const SPLITS: { key: SplitKey; label: string }[] = [
  { key: "all", label: "All" }, { key: "val", label: "Validation" }, { key: "test", label: "Test" },
];
const SPLIT_WORD = { val: "Validation", test: "Test" } as const;

function unavailable(missing: MissingSource[], section: string): string {
  const m = missing.find((x) => x.section === section);
  return m ? `Not available — ${m.reason} (${m.path})` : "Not available";
}

function Section({ title, children }: Readonly<{ title: string; children: React.ReactNode }>) {
  return (
    <section className="rounded-card border border-border bg-surface p-5" aria-label={title}>
      <h2 className="font-display text-lg font-semibold text-text">{title}</h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Unavailable({ text }: Readonly<{ text: string }>) {
  return <p className="text-sm text-text-2">{text}</p>;
}

interface BarDatum { name: string; value: number; fill: string; detail: string }

function HBars({ data, label, unit }: Readonly<{ data: BarDatum[]; label: string; unit: string }>) {
  return (
    <div className="w-full" style={{ height: Math.max(80, data.length * 32 + 40) }} role="img" aria-label={label}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
          <XAxis type="number" tick={{ fontSize: 12 }} />
          <YAxis type="category" dataKey="name" width={160} tick={{ fontSize: 12 }} interval={0} />
          <Tooltip formatter={(v, _n, item) => [`${String(v)} ${unit}`, (item.payload as BarDatum).detail]} />
          <Bar dataKey="value" isAnimationActive={false}>
            {data.map((d) => <Cell key={d.name} fill={d.fill} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Swatch({ color, text }: Readonly<{ color: string; text: string }>) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-text-2">
      <span aria-hidden className="inline-block size-3 rounded-sm" style={{ background: color }} />
      {text}
    </span>
  );
}

function ClipSection({ data }: Readonly<{ data: AccuracyData }>) {
  const ql = data.queue_length;
  if (!ql) return <Unavailable text={unavailable(data.missing_sources, "queue_length")} />;
  const rank = (c: { split: "val" | "test" | null }) => (c.split === "val" ? 0 : c.split === "test" ? 1 : 2);
  const clips = [...ql.per_clip].sort((a, b) => rank(a) - rank(b) || a.clip.localeCompare(b.clip));
  const hasUnassigned = clips.some((c) => c.split === null);
  const fillFor = (split: "val" | "test" | null) =>
    split === "val" ? "var(--color-bar-val)" : split === "test" ? "var(--color-bar-test)" : "var(--color-text-3)";
  const bars: BarDatum[] = clips.map((c) => ({
    name: c.clip, value: Number(formatNumber(c.mae, 2)), fill: fillFor(c.split),
    detail: `${c.split ? SPLIT_WORD[c.split] : "Unassigned"} split`,
  }));
  return (
    <>
      <div className="mb-2 flex flex-wrap gap-4">
        <Swatch color="var(--color-bar-val)" text="Validation (tuning) clips" />
        <Swatch color="var(--color-bar-test)" text="Test (held-out) clips" />
        {hasUnassigned ? <Swatch color="var(--color-text-3)" text="Unassigned clips" /> : null}
      </div>
      <HBars data={bars} label="Mean absolute error by clip, in people" unit="people" />
      <table data-testid="clip-bars-data" className="sr-only">
        <caption>Mean absolute error by clip (people)</caption>
        <thead><tr><th>Clip</th><th>Split</th><th>Mean absolute error (people)</th><th>Frames</th></tr></thead>
        <tbody>
          {clips.map((c) => (
            <tr key={c.clip}>
              <td>{c.clip}</td><td>{c.split ? SPLIT_WORD[c.split] : "Unassigned"}</td>
              <td>{formatNumber(c.mae, 2)}</td><td>{c.frames}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function CountGroup({ title, cats, color }: Readonly<{ title: string; cats: CountErrorCategory[]; color: string }>) {
  return (
    <div>
      <h3 className="text-sm font-medium text-text">{title}</h3>
      {cats.length === 0 ? <p className="mt-1 text-sm text-text-3">None in this split.</p> : (
        <HBars
          label={`${title}, per labelled frame`} unit="per labelled frame"
          data={cats.map((c) => ({ name: c.label, value: Number(formatNumber(c.per_frame, 2)), fill: color, detail: `${c.total} in total` }))}
        />
      )}
    </div>
  );
}

function CountSection({ data }: Readonly<{ data: AccuracyData }>) {
  const [split, setSplit] = useState<SplitKey>("test");
  const ce = data.count_errors;
  if (!ce) return <Unavailable text={unavailable(data.missing_sources, "count_errors")} />;
  const s = ce.per_split[split];
  const over = s.categories.filter((c) => c.direction === "over");
  const under = s.categories.filter((c) => c.direction === "under");
  const rest = s.categories.filter((c) => c.direction === "correct" || c.direction === "other");
  return (
    <>
      <div role="group" aria-label="Frame split" className="mb-3 inline-flex rounded-card border border-border-input">
        {SPLITS.map((o) => (
          <button
            key={o.key} type="button" aria-pressed={split === o.key} onClick={() => setSplit(o.key)}
            className={`px-3 py-1 text-sm ${split === o.key ? "bg-primary-soft font-medium text-primary" : "text-text-2"}`}
          >{o.label}</button>
        ))}
      </div>
      <p className="num mb-3 text-xs text-text-3">{`${s.frames} labelled frames · values are per labelled frame`}</p>
      <div className="grid gap-6 md:grid-cols-2">
        <CountGroup title="Over-count causes" cats={over} color="var(--color-warn)" />
        <CountGroup title="Under-count causes" cats={under} color="var(--color-primary)" />
      </div>
      <table data-testid="count-bars-data" className="sr-only">
        <caption>Count errors per labelled frame</caption>
        <thead><tr><th>Cause</th><th>Direction</th><th>Per labelled frame</th></tr></thead>
        <tbody>
          {[...over, ...under].map((c) => (
            <tr key={c.key}><td>{c.label}</td><td>{c.direction === "over" ? "Over-count" : "Under-count"}</td><td>{formatNumber(c.per_frame, 2)}</td></tr>
          ))}
        </tbody>
      </table>
      {rest.length > 0 ? (
        <p data-testid="count-notes" className="mt-3 text-xs text-text-3">
          {"Not shown as bars: "}
          {rest.map((c) => `${c.label} (${c.total} total, ${formatNumber(c.per_frame, 2)} per labelled frame)`).join("; ")}
        </p>
      ) : null}
    </>
  );
}

function ChangesSection({ data }: Readonly<{ data: AccuracyData }>) {
  const rows = data.changes_not_adopted;
  if (!rows) return null;
  const pair = (a: number, b: number) => `${formatNumber(a, 2)} → ${formatNumber(b, 2)}`;
  return (
    <Section title="Changes tried and not adopted">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-text-2">
              <th className="py-1 pr-4 font-medium">Change</th><th className="py-1 pr-4 font-medium">Sprint</th>
              <th className="py-1 pr-4 font-medium">Val MAE (baseline → tried)</th>
              <th className="py-1 pr-4 font-medium">Test MAE (baseline → tried)</th>
              <th className="py-1 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.sprint}-${r.name}`} className="border-t border-border">
                <td className="py-1 pr-4 text-text">{r.name}</td>
                <td className="py-1 pr-4">{r.sprint}</td>
                <td className="num py-1 pr-4">{pair(r.baseline_val_mae, r.val_mae)}</td>
                <td className="num py-1 pr-4">{pair(r.baseline_test_mae, r.test_mae)}</td>
                <td className="py-1 text-text-3">{r.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

export function AccuracyView({ data, error }: Readonly<{ data: AccuracyData | undefined; error: Error | null }>) {
  if (error) {
    return <EmptyState title={error instanceof ApiError && error.status === 404 ? NOT_GENERATED : error.message} />;
  }
  if (!data) return <p className="text-sm text-text-3">Loading…</p>;
  const t = data.queue_length?.pooled.test;
  return (
    <div className="flex flex-col gap-6">
      <section aria-label="Headline accuracy" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {t ? (
          <>
            <MetricCard label="Mean absolute error (people)" value={formatNumber(t.mae, 2)} caption={`${t.frames} frames · held-out (test) split`} />
            <MetricCard label="Bias (people)" value={formatSigned(t.bias, 2)} caption={`${t.frames} frames · held-out (test) split`} />
            <MetricCard label="Within ±1 person" value={`${formatNumber(t.within_one_rate * 100, 0)}%`} caption={`${t.frames} frames · held-out (test) split`} />
          </>
        ) : (
          <div className="sm:col-span-2 lg:col-span-3"><Unavailable text={unavailable(data.missing_sources, "queue_length")} /></div>
        )}
        <MetricCard
          label="Waiting time"
          value={data.waiting_time ? "Measured" : "Not measured yet"}
          caption={data.waiting_time ? `${data.waiting_time.per_clip.length} recorded clips` : undefined}
        />
      </section>
      <Section title="Error by clip"><ClipSection data={data} /></Section>
      <Section title="Where the count goes wrong, per labelled frame"><CountSection data={data} /></Section>
      <ChangesSection data={data} />
      <Section title="Limits of these numbers">
        {data.limits.length === 0 ? <Unavailable text="No limits listed." /> : (
          <ul className="list-disc pl-5 text-sm text-text-2">
            {data.limits.map((l) => <li key={l}>{l}</li>)}
          </ul>
        )}
      </Section>
    </div>
  );
}
