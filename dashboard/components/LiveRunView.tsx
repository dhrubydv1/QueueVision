"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ApiError, cancelRun, getCameras, getEvents, getTimeseries } from "@/lib/api";
import { formatClock, formatNumber } from "@/lib/format";
import { FINAL_STATUSES, eventKey } from "@/lib/live-types";
import { useLiveSocket } from "@/lib/useLiveSocket";
import { AlertBanner } from "./AlertBanner";
import { EmptyState } from "./EmptyState";
import { EventFeed } from "./EventFeed";
import { MetricCard } from "./MetricCard";
import { PreviewPanel } from "./PreviewPanel";
import { QueueChart, type QueuePoint } from "./QueueChart";
import { QueueLengthCard } from "./QueueLengthCard";
import { StaleBanner } from "./StaleBanner";
import { StatusChip, type Status } from "./StatusChip";
import { WaitCard } from "./WaitCard";

const MAX_LIVE_POINTS = 5000;

export function LiveRunView({ videoId }: Readonly<{ videoId: number }>) {
  const { run, progress, events, status, connectionState, lastMessageAt } = useLiveSocket(videoId);
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: getCameras });
  const history = useQuery({
    queryKey: ["timeseries", videoId], queryFn: () => getTimeseries(videoId), staleTime: Infinity, refetchOnWindowFocus: false,
  });

  // Recorded events: the socket sends none for a run that already finished.
  const eventHistory = useQuery({
    queryKey: ["events", videoId], queryFn: () => getEvents(videoId), staleTime: Infinity, refetchOnWindowFocus: false,
  });

  // After a drop, refetch the history on the next open so the chart and feed have no permanent gap.
  const refetchHistory = history.refetch;
  const refetchEvents = eventHistory.refetch;
  const link = useRef<{ opened: boolean; dropped: boolean }>({ opened: false, dropped: false });
  useEffect(() => {
    const l = link.current;
    if (connectionState === "open") {
      if (l.opened && l.dropped) {
        void refetchHistory();
        void refetchEvents();
      }
      l.opened = true;
      l.dropped = false;
    } else if (l.opened) {
      l.dropped = true;
    }
  }, [connectionState, refetchHistory, refetchEvents]);

  // History first, then socket events not already in it; newest first by timestamp_s, ties newest arrival first.
  const feed = useMemo(() => {
    const seenKeys = new Set<string>();
    const merged = [...(eventHistory.data ?? []), ...events].filter((e) => {
      const key = eventKey(e);
      if (seenKeys.has(key)) return false;
      seenKeys.add(key);
      return true;
    });
    return merged.reverse().sort((a, b) => b.timestamp_s - a.timestamp_s);
  }, [eventHistory.data, events]);

  // Display buffer of received values (no statistics), appended during render when a new message arrives.
  const [live, setLive] = useState<QueuePoint[]>([]);
  const [seen, setSeen] = useState<typeof progress>(null);
  if (progress !== seen) {
    setSeen(progress);
    if (progress) {
      const point = { t: progress.video_time_s, smoothed: progress.queue_length, raw: progress.raw_queue_count };
      setLive((prev) => [...prev, point].slice(-MAX_LIVE_POINTS));
    }
  }
  const points = useMemo<QueuePoint[]>(() => {
    const base = (history.data ?? []).map((r) => ({ t: r.time_s, smoothed: r.queue_length ?? null, raw: r.raw_queue_count ?? null }));
    const last = base.length > 0 ? base[base.length - 1].t : -Infinity;
    return [...base, ...live.filter((p) => p.t > last)];
  }, [history.data, live]);

  // One AlertBanner per alert episode (its dismissal lasts for the component's lifetime).
  const alertActive = progress?.alert_active === true;
  const [episode, setEpisode] = useState(0);
  const [wasAlert, setWasAlert] = useState(false);
  if (alertActive !== wasAlert) {
    setWasAlert(alertActive);
    if (alertActive) setEpisode((n) => n + 1);
  }

  // false on the server and during hydration, true afterwards (clock text depends on the browser's zone).
  const mounted = useSyncExternalStore(() => () => {}, () => true, () => false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  if (connectionState === "not_found") {
    return (
      <main className="mx-auto max-w-content p-6">
        <EmptyState title="Run not found">
          <Link className="text-primary hover:underline" href="/runs">Go to runs</Link>
        </EmptyState>
      </main>
    );
  }

  const runStatus = status?.status ?? run?.status ?? null;
  const isFinal = runStatus !== null && (FINAL_STATUSES as readonly string[]).includes(runStatus);
  const isProcessing = runStatus === "processing";
  const offline = !isFinal && (connectionState === "closed" || (connectionState === "connecting" && lastMessageAt !== null));
  const camera = cameras.data?.find((c) => c.id === run?.camera_id);
  const alertLimit = camera?.max_queue_length ?? null;
  const windowS = camera?.smoothing_window_s ?? null;

  let chip: Status;
  if (isFinal) chip = runStatus as Status;
  else if (offline) chip = "offline";
  else if (connectionState !== "open") chip = "processing"; // neutral while the first connection is made
  else chip = alertActive ? "congested" : "live";

  const rate = progress?.service_rate_per_min ?? null;
  const rateOk = progress?.service_rate_status === "ok" && rate !== null;
  const rateWords = progress?.service_rate_status === "insufficient_data" ? "Insufficient data" : "Unknown";

  async function onCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelRun(videoId);
      setCancelRequested(true); // stays disabled until the socket reports a final status
    } catch (e) {
      setCancelError(e instanceof ApiError ? e.message : "Cancel failed");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-content flex-col gap-4 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-[28px] font-semibold text-text">
            {run ? `${run.original_name} · ${run.camera_id}` : `Run ${videoId}`}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-text-2">
            <StatusChip status={chip} />
            {mounted && lastMessageAt !== null ? <span className="num">{`Last update ${formatClock(lastMessageAt)}`}</span> : null}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            disabled={!isProcessing || cancelling || cancelRequested}
            onClick={onCancel}
            className="h-10 rounded-control border border-border bg-surface px-4 text-sm font-medium text-text disabled:opacity-50"
          >
            Cancel run
          </button>
          {cancelError ? <p role="alert" className="text-xs text-danger">{cancelError}</p> : null}
          {isFinal ? (
            <Link className="text-sm text-primary hover:underline" href={`/run?id=${videoId}`}>Open the recorded run</Link>
          ) : null}
        </div>
      </div>

      {alertActive ? <AlertBanner key={episode} reasons={progress?.alert_reasons ?? []} /> : null}
      {offline ? (
        <p role="status" className="rounded-control border border-warn-border bg-warn-soft px-3 py-2 text-sm text-warn-text">
          Connection lost. Reconnecting; values below are not current.
        </p>
      ) : null}

      <div className="grid items-start gap-4 min-[900px]:grid-cols-2">
        <PreviewPanel videoId={videoId} active={!isFinal} />
        <StaleBanner lastMessageAt={lastMessageAt} connectionState={connectionState}>
          <div className="flex flex-col gap-4">
            <QueueLengthCard smoothed={progress?.queue_length ?? null} raw={progress?.raw_queue_count ?? null} windowS={windowS} big />
            <WaitCard
              label="Predicted wait"
              valueS={progress?.predicted_wait_s ?? null}
              lowS={progress?.predicted_wait_low_s ?? null}
              highS={progress?.predicted_wait_high_s ?? null}
              status={progress?.prediction_status ?? null}
              unvalidated
            />
            <MetricCard
              label="Service rate"
              value={rateOk ? formatNumber(rate, 1) : rateWords}
              caption={rateOk ? "per min" : undefined}
            />
            <MetricCard label="People in frame" value={formatNumber(progress?.people_in_frame ?? null, 0)} caption="Not the queue length" />
          </div>
        </StaleBanner>
      </div>

      <section aria-label="Queue length since run start" className="rounded-card border border-border bg-surface p-5">
        <h2 className="text-[13px] font-medium text-text-2">Queue length since run start</h2>
        <QueueChart points={points} alertLimit={alertLimit} />
      </section>

      <section aria-label="Events" className="rounded-card border border-border bg-surface p-5">
        <h2 className="text-[13px] font-medium text-text-2">Events</h2>
        <EventFeed events={feed} />
      </section>
    </main>
  );
}
