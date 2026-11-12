"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ApiError, deleteRun, getAlerts, getEvents, getRun, getTimeseries, getWaits, reportUrl, timeseriesCsvUrl, videoUrl,
  type Alert, type Run,
} from "@/lib/api";
import { formatSeconds } from "@/lib/format";
import { EmptyState } from "./EmptyState";
import { EventFeed } from "./EventFeed";
import { MetricCard } from "./MetricCard";
import { QueueChart } from "./QueueChart";
import { StatusChip, type Status } from "./StatusChip";
import { WaitsTable } from "./WaitsTable";

const KNOWN: Status[] = ["queued", "processing", "completed", "failed", "cancelled"];
const NOT_REPORTED = "Not reported";

/** Reads a finite number from the report's `queue` section by key path; undefined when absent. */
function pick(summary: Run["summary"], ...path: string[]): number | undefined {
  let node: unknown = summary;
  for (const key of path) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[key];
  }
  return typeof node === "number" && Number.isFinite(node) ? node : undefined;
}

const count = (n: number | undefined) => (n === undefined ? NOT_REPORTED : String(n));
const wait = (n: number | undefined) => (n === undefined ? NOT_REPORTED : formatSeconds(n));

function ordinal(n: number): string {
  if (n === 1) return "every frame";
  const rest = n % 100;
  const suffix = rest >= 11 && rest <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `every ${n}${suffix} frame`;
}

function optionsSummary(run: Run): string {
  const { frame_stride, blur_heads, realtime } = run.options;
  return `${ordinal(frame_stride)} · ${blur_heads ? "heads blurred" : "heads not blurred"} · ${realtime ? "real time" : "fast"}`;
}

/** Counts only, from the report's own figures: no percentage is computed in the browser. */
function LeftWithoutService({ summary }: Readonly<{ summary: Run["summary"] }>) {
  const left = pick(summary, "event_counts", "queue_abandon");
  const joined = pick(summary, "event_counts", "queue_join");
  if (left === undefined || joined === undefined) return <MetricCard label="Left without service" value={NOT_REPORTED} />;
  return <MetricCard label="Left without service" value={String(left)} caption={`${left} of ${joined} joins`} />;
}

function SummaryCards({ summary }: Readonly<{ summary: Run["summary"] }>) {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <MetricCard label="Joined" value={count(pick(summary, "event_counts", "queue_join"))} />
      <MetricCard label="Service started" value={count(pick(summary, "event_counts", "service_start"))} />
      <LeftWithoutService summary={summary} />
      <MetricCard label="Congestion alerts" value={count(pick(summary, "alerts", "congestion_starts"))} />
      <MetricCard label="Mean wait" tag="Unvalidated" value={wait(pick(summary, "waiting", "mean_wait_s"))} />
      <MetricCard label="Median wait" tag="Unvalidated" value={wait(pick(summary, "waiting", "median_wait_s"))} />
      <MetricCard label="P90 wait" tag="Unvalidated" value={wait(pick(summary, "waiting", "p90_wait_s"))} />
    </div>
  );
}

function AlertsTable({ alerts }: Readonly<{ alerts: Alert[] }>) {
  if (alerts.length === 0) return <p className="text-sm text-text-3">No alerts</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-[13px] font-medium text-text-2">
          <tr>
            <th className="py-2 pr-4 font-medium">Time</th>
            <th className="py-2 pr-4 font-medium">Event</th>
            <th className="py-2 pr-4 font-medium">Queue length</th>
            <th className="py-2 font-medium">Reasons</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {alerts.map((a) => (
            <tr key={a.alert_id}>
              <td className="num py-2 pr-4">{formatSeconds(a.timestamp_s)}</td>
              <td className="py-2 pr-4">{a.event_type === "congestion_end" ? "Congestion ended" : "Congestion started"}</td>
              <td className="num py-2 pr-4">{a.queue_length}</td>
              <td className="py-2 text-text-2">{a.reasons}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type Tab = "waits" | "events" | "alerts";

export function RunDetail({ videoId }: Readonly<{ videoId: number }>) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("waits");
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const runQuery = useQuery({
    queryKey: ["run", videoId],
    queryFn: () => getRun(videoId),
    refetchInterval: (q) => (q.state.data && ["queued", "processing"].includes(q.state.data.status) ? 3000 : false),
  });
  const run = runQuery.data;
  const done = run?.status === "completed";
  const series = useQuery({ queryKey: ["run", videoId, "timeseries"], queryFn: () => getTimeseries(videoId), enabled: done });
  const events = useQuery({ queryKey: ["run", videoId, "events"], queryFn: () => getEvents(videoId), enabled: done });
  const waits = useQuery({ queryKey: ["run", videoId, "waits"], queryFn: () => getWaits(videoId), enabled: done });
  const alerts = useQuery({ queryKey: ["run", videoId, "alerts"], queryFn: () => getAlerts(videoId), enabled: done });

  if (runQuery.isError) {
    return (
      <main className="mx-auto max-w-content p-6">
        <EmptyState title={runQuery.error instanceof ApiError && runQuery.error.status === 404 ? "Run not found" : "Cannot load run"}>
          {runQuery.error.message} <Link className="text-primary hover:underline" href="/runs">Back to runs</Link>
        </EmptyState>
      </main>
    );
  }
  if (!run) return <main className="mx-auto max-w-content p-6"><p className="text-sm text-text-2">Loading…</p></main>;

  async function confirmDelete() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteRun(videoId);
      router.push("/runs");
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : "Delete failed");
      setDeleting(false);
      setConfirming(false);
    }
  }

  const eventList = events.data ?? [];
  const waitList = waits.data ?? [];
  const alertList = alerts.data ?? [];
  const points = (series.data ?? []).map((r) => ({ t: r.time_s, smoothed: r.queue_length ?? null, raw: r.raw_queue_count ?? null }));
  const markers = eventList.filter((e) => e.event_type === "service_start").map((e) => ({ t: e.timestamp_s, kind: "service_start" as const }));
  const tabs: { id: Tab; label: string }[] = [
    { id: "waits", label: `Waits (${waitList.length})` },
    { id: "events", label: `Events (${eventList.length})` },
    { id: "alerts", label: `Alerts (${alertList.length})` },
  ];
  const link = "rounded-control border border-border-input bg-surface px-3 py-2 text-sm text-text hover:bg-chip";
  const processing = run.status === "processing" || run.status === "queued";

  return (
    <main className="mx-auto flex max-w-content flex-col gap-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm"><Link className="text-primary hover:underline" href="/runs">Recorded runs</Link></p>
          <h1 className="mt-1 break-all font-display text-[28px] font-semibold text-text">{run.original_name}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-text-2">
            {(KNOWN as string[]).includes(run.status) ? <StatusChip status={run.status as Status} /> : run.status}
            <span>{optionsSummary(run)}</span>
            <span>{run.camera_id}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {processing ? <Link className={link} href={`/live?id=${videoId}`}>Watch live</Link> : null}
          {done ? (
            <>
              <a className={link} href={reportUrl(videoId)} target="_blank" rel="noreferrer">Report (JSON)</a>
              <a className={link} href={timeseriesCsvUrl(videoId)} download>Time series (CSV)</a>
            </>
          ) : null}
          {confirming ? (
            <span role="group" aria-label="Confirm delete" className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-text">{`Delete run ${videoId}? This removes its video and results.`}</span>
              <button type="button" onClick={confirmDelete} disabled={deleting} className="rounded-control bg-danger px-3 py-2 text-sm font-medium text-white disabled:opacity-60">Confirm</button>
              <button type="button" onClick={() => setConfirming(false)} disabled={deleting} className={link}>Keep</button>
            </span>
          ) : (
            <button type="button" onClick={() => setConfirming(true)} disabled={processing} className={`${link} text-danger disabled:opacity-50`}>Delete</button>
          )}
        </div>
      </div>
      {deleteError ? <p role="alert" className="text-sm text-danger">{deleteError}</p> : null}
      {run.error ? <p role="alert" className="text-sm text-danger">{run.error}</p> : null}

      {done ? (
        <video controls preload="metadata" src={videoUrl(videoId)} className="w-full rounded-card border border-border bg-surface" />
      ) : (
        <p className="rounded-card border border-border bg-surface p-5 text-sm text-text-2">
          {processing ? "The annotated video appears here when processing finishes." : "No annotated video for this run."}
        </p>
      )}

      {done ? (
        <>
          <SummaryCards summary={run.summary} />
          <section aria-label="Queue length through the clip" className="rounded-card border border-border bg-surface p-5">
            <h2 className="text-[13px] font-medium text-text-2">Queue length through the clip</h2>
            <QueueChart points={points} markers={markers} dwellBandS={5} />
          </section>
          <section className="rounded-card border border-border bg-surface p-5">
            <div role="tablist" aria-label="Run details" className="flex gap-4 border-b border-border">
              {tabs.map((t) => (
                <button
                  key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-selected={tab === t.id} aria-controls="run-tabpanel"
                  onClick={() => setTab(t.id)}
                  className={`-mb-px border-b-2 px-1 py-2 text-sm font-medium ${tab === t.id ? "border-primary text-primary" : "border-transparent text-text-2 hover:text-text"}`}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div role="tabpanel" id="run-tabpanel" aria-labelledby={`tab-${tab}`} className="pt-4">
              {tab === "waits" ? (waitList.length === 0 ? <p className="text-sm text-text-3">No waits</p> : <WaitsTable waits={waitList} />) : null}
              {tab === "events" ? <EventFeed events={eventList} /> : null}
              {tab === "alerts" ? <AlertsTable alerts={alertList} /> : null}
            </div>
          </section>
        </>
      ) : null}
    </main>
  );
}
