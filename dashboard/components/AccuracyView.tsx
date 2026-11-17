"use client";

import { useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ApiError } from "@/lib/api";
import type {
  AccuracyData, Band, CallCentreMethod, ComparisonRow, CriterionLetter, CallCentreMethodKey, CountErrorCategory, Interval, MissingSource, QueuePooledMetrics,
} from "@/lib/accuracy-types";
import { formatCount, formatNumber, formatSigned } from "@/lib/format";
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
      <div className="sr-only"><table data-testid="clip-bars-data">
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
      </table></div>
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
      <div className="sr-only"><table data-testid="count-bars-data">
        <caption>Count errors per labelled frame</caption>
        <thead><tr><th>Cause</th><th>Direction</th><th>Per labelled frame</th></tr></thead>
        <tbody>
          {[...over, ...under].map((c) => (
            <tr key={c.key}><td>{c.label}</td><td>{c.direction === "over" ? "Over-count" : "Under-count"}</td><td>{formatNumber(c.per_frame, 2)}</td></tr>
          ))}
        </tbody>
      </table></div>
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

const range = (b: Band, digits = 2) => `${formatNumber(b[0], digits)}\u2013${formatNumber(b[1], digits)}`;

/** "1.62 people, 95% interval 0.97\u20132.03, 4 clips"; null when the intervals are missing. */
export function headlineInterval(t: QueuePooledMetrics): string | null {
  const band = t.interval_clip_bootstrap_95?.mae;
  if (!band || t.n_clips === undefined) return null;
  return `${formatNumber(t.mae, 2)} people, 95% interval ${range(band)}, ${t.n_clips} clips`;
}

export const COMPARISON_TITLE = "Candidate configurations against the current default";
const CRITERION_TEXT: Record<CriterionLetter, string> = {
  a: "(a) test error not clearly lower",
  b: "(b) validation error worse",
  c: "(c) slower than real time",
};

function ComparisonsCard({ data }: Readonly<{ data: AccuracyData }>) {
  const cmp = data.comparisons;
  if (cmp === undefined) return null;
  if (!cmp) return <Section title={COMPARISON_TITLE}><Unavailable text={unavailable(data.missing_sources, "comparisons")} /></Section>;
  const withBand = (v: number, b: Band | null) => (b ? `${formatSigned(v, 2)} [${range(b)}]` : formatSigned(v, 2));
  const cur = cmp.current;
  const ql = data.queue_length?.pooled;
  const clipCounts = ql ? `With ${ql.val.clips.length} validation and ${ql.test.clips.length} test clips` : "With so few clips";
  const decision = (r: ComparisonRow) => (r.adopt ? "Adopted" : "Not adopted");
  return (
    <Section title={COMPARISON_TITLE}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid="comparison-table">
          <caption className="sr-only">Test and validation error, speed and adoption decision for each configuration</caption>
          <thead>
            <tr className="text-text-2">
              <th className="py-1 pr-4 font-medium">Configuration</th>
              <th className="py-1 pr-4 font-medium">Test MAE, people (95% interval)</th>
              <th className="py-1 pr-4 font-medium">Change vs current, paired (95% interval)</th>
              <th className="py-1 pr-4 font-medium">Val MAE</th>
              <th className="py-1 pr-4 font-medium">Processing fps</th>
              <th className="py-1 font-medium">Decision</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-border">
              <td className="py-1 pr-4 font-medium text-text">{`${cur.configuration}: ${cur.label} (current)`}</td>
              <td className="num py-1 pr-4">{cur.test_interval_clip_bootstrap_95
                  ? `${formatNumber(cur.test_mae, 2)} [${range(cur.test_interval_clip_bootstrap_95)}]`
                  : formatNumber(cur.test_mae, 2)}</td>
              <td className="py-1 pr-4">{"\u2014"}</td>
              <td className="num py-1 pr-4">{formatNumber(cur.val_mae, 2)}</td>
              <td className="num py-1 pr-4">
                {cur.processing_fps != null && cur.clip_fps != null
                  ? `${formatNumber(cur.processing_fps, 1)} of ${formatNumber(cur.clip_fps, 0)}`
                  : "\u2014"}
              </td>
              <td className="py-1">Current default</td>
            </tr>
            {cmp.rows.map((r) => (
              <tr key={r.configuration} className="border-t border-border">
                <td className="py-1 pr-4 text-text">{`${r.configuration}: ${r.label}`}</td>
                <td className="num py-1 pr-4">{`${formatNumber(r.test_mae, 2)} [${range(r.interval_clip_bootstrap_95)}]`}</td>
                <td className="num py-1 pr-4">{withBand(r.paired_delta_vs_A, r.paired_delta_interval_95)}</td>
                <td className="num py-1 pr-4">{formatNumber(r.val_mae, 2)}</td>
                <td className="num py-1 pr-4">{`${formatNumber(r.processing_fps, 1)} of ${formatNumber(r.clip_fps, 0)}`}</td>
                <td className="py-1">
                  {decision(r)}
                  {r.failed_criteria.length > 0 ? ` \u2014 ${r.failed_criteria.map((c) => CRITERION_TEXT[c]).join("; ")}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p data-testid="comparison-caption" className="mt-3 text-xs text-text-3">
        {`${clipCounts} these intervals are wide. Adoption follows the rule in ${cmp.protocol}. Source: ${cmp.source}.`}
      </p>
    </Section>
  );
}

function AttributionCard({ data }: Readonly<{ data: AccuracyData }>) {
  const at = data.attribution_iou;
  if (at === undefined) return null;
  if (!at) return <Section title={ATTRIBUTION_TITLE}><Unavailable text={unavailable(data.missing_sources, "attribution_iou")} /></Section>;
  const s = at.per_split.test;
  const loose = new Map(s["iou_0.3"].categories.map((c) => [c.key, c]));
  return (
    <Section title={ATTRIBUTION_TITLE}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid="attribution-table">
          <caption className="sr-only">Test-split count errors per labelled frame at two box-overlap thresholds</caption>
          <thead>
            <tr className="text-text-2">
              <th className="py-1 pr-4 font-medium">Cause</th>
              <th className="py-1 pr-4 font-medium">IoU 0.5, per frame</th>
              <th className="py-1 font-medium">IoU 0.3, per frame</th>
            </tr>
          </thead>
          <tbody>
            {s["iou_0.5"].categories.map((c) => (
              <tr key={c.key} className="border-t border-border">
                <td className="py-1 pr-4">{c.label}</td>
                <td className="num py-1 pr-4">{formatNumber(c.per_frame, 2)}</td>
                <td className="num py-1">{formatNumber(loose.get(c.key)?.per_frame, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="num mt-3 text-xs text-text-3">{`${s["iou_0.5"].frames} labelled test frames · Source: ${at.source}`}</p>
    </Section>
  );
}

export const ATTRIBUTION_TITLE = "Count errors at two box-overlap thresholds";

export const CALL_CENTRE_TITLE = "Waiting-time predictor on real call-centre queues";
export const CALL_CENTRE_CAVEAT = "Phone queue, 1999, Israel; measures the prediction formula, not the cameras.";

const CALL_CENTRE_ROWS: { key: CallCentreMethodKey; label: string }[] = [
  { key: "predictor", label: "Prediction formula (queue length ÷ service rate)" },
  { key: "les", label: "Baseline: wait of the last caller to enter service" },
  { key: "recent_mean", label: "Baseline: mean wait of the last 20 callers" },
  { key: "regression", label: "Baseline: queue-length regression" },
];

const withInterval = (i: Interval, digits: number, scale = 1) =>
  `${formatNumber(i.value * scale, digits)} [${formatNumber(i.lo * scale, digits)}, ${formatNumber(i.hi * scale, digits)}]`;

function CallCentreCard({ data }: Readonly<{ data: AccuracyData }>) {
  const cc = data.predictor_on_call_centre_logs ?? null;
  if (!cc) {
    return <Section title={CALL_CENTRE_TITLE}><Unavailable text={unavailable(data.missing_sources, "predictor_on_call_centre_logs")} /></Section>;
  }
  const s1 = cc.post_hoc_s1.methods.predictor;
  const beating = (["les", "recent_mean", "regression"] as const)
    .filter((k) => cc.post_hoc_s1.methods[k].mae_s.value < s1.mae_s.value).length;
  const cell = (m: CallCentreMethod) => ({
    mae: withInterval(m.mae_s, 1), cov: withInterval(m.coverage, 0, 100), bias: formatSigned(m.bias_s.value, 1),
  });
  return (
    <Section title={CALL_CENTRE_TITLE}>
      <p className="text-sm text-text-2">{CALL_CENTRE_CAVEAT}</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid="call-centre-table">
          <caption className="sr-only">Test-month waiting-time error of the formula and three baselines</caption>
          <thead>
            <tr className="text-text-2">
              <th className="py-1 pr-4 font-medium">Method</th>
              <th className="py-1 pr-4 font-medium">Mean absolute error, s (95% interval)</th>
              <th className="py-1 pr-4 font-medium">Calls with a prediction, % (95% interval)</th>
              <th className="py-1 font-medium">Bias, s</th>
            </tr>
          </thead>
          <tbody>
            {CALL_CENTRE_ROWS.map(({ key, label }) => {
              const c = cell(cc.methods[key]);
              return (
                <tr key={key} className="border-t border-border">
                  <td className={`py-1 pr-4 ${key === "predictor" ? "font-medium text-text" : ""}`}>{label}</td>
                  <td className="num py-1 pr-4">{c.mae}</td>
                  <td className="num py-1 pr-4">{c.cov}</td>
                  <td className="num py-1">{c.bias}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="num mt-3 text-xs text-text-3">
        {`${formatCount(cc.n_calls)} answered calls over ${cc.n_days} held-out test days (Nov–Dec 1999) · protocol v${cc.protocol_version} · range coverage ${formatNumber((cc.methods.predictor.range_coverage?.value ?? NaN) * 100, 0)}% · ${cc.interval}`}
      </p>
      <p data-testid="call-centre-s1" className="mt-2 text-sm text-text-2">
        {`Post hoc, not the headline: counting every service start (protocol v${cc.post_hoc_s1.protocol_version}) gives ${formatNumber(s1.mae_s.value, 1)} s mean error and ${formatNumber(s1.coverage.value * 100, 0)}% coverage, worse than ${beating} of the 3 baselines.`}
      </p>
      <p className="mt-2 text-xs text-text-3">{`Source: ${cc.source}. ${cc.citation}.`}</p>
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
          caption={data.waiting_time ? `${new Set(data.waiting_time.per_clip.map((c) => c.clip)).size} recorded clips` : undefined}
        />
      </section>
      {t && headlineInterval(t) ? (
        <p data-testid="headline-interval" className="num -mt-2 text-sm text-text-2">
          {`Mean absolute error on the held-out split: ${headlineInterval(t)}.`}
          {t.interval_per_frame_optimistic_95
            ? ` Per-frame interval ${range(t.interval_per_frame_optimistic_95.mae)} (${t.interval_per_frame_optimistic_95.label}).`
            : ""}
        </p>
      ) : null}
      <CallCentreCard data={data} />
      <Section title="Error by clip"><ClipSection data={data} /></Section>
      <Section title="Where the count goes wrong, per labelled frame"><CountSection data={data} /></Section>
      <AttributionCard data={data} />
      <ComparisonsCard data={data} />
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
