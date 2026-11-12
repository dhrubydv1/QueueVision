"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { AccuracySummaryCard } from "@/components/AccuracySummaryCard";
import { EmptyState } from "@/components/EmptyState";
import { ProcessingRunCard } from "@/components/ProcessingRunCard";
import { RunsTable } from "@/components/RunsTable";
import { getAccuracy, getCameras, listRuns, type Run } from "@/lib/api";

const newestFirst = (a: Run, b: Run) => b.created_at.localeCompare(a.created_at);

export default function OverviewPage() {
  const runsQuery = useQuery({ queryKey: ["runs"], queryFn: listRuns, refetchInterval: 5000 });
  const accuracy = useQuery({ queryKey: ["accuracy"], queryFn: getAccuracy, retry: false, refetchInterval: false });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: getCameras });

  const runs = [...(runsQuery.data ?? [])].sort(newestFirst);
  const processing = runs.filter((r) => r.status === "processing");
  const completed = runs.filter((r) => r.status === "completed").slice(0, 3);
  const date = new Intl.DateTimeFormat("en-GB", { dateStyle: "full" }).format(new Date());
  const windowFor = (cameraId: string) => cameras.data?.find((c) => c.id === cameraId)?.smoothing_window_s ?? null;

  return (
    <main className="mx-auto flex max-w-content flex-col gap-6 p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-[28px] font-semibold text-text">Overview</h1>
          <p className="text-sm text-text-2">{`${date} · ${processing.length} ${processing.length === 1 ? "run" : "runs"} processing`}</p>
        </div>
        <Link href="/runs#upload" className="rounded-control bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-hover">
          Upload a video
        </Link>
      </div>

      {runsQuery.isError ? (
        <EmptyState title="Cannot load runs">{runsQuery.error.message}</EmptyState>
      ) : runsQuery.isSuccess && runs.length === 0 ? (
        <EmptyState title="No runs yet — upload a video" />
      ) : null}

      {processing.length > 0 ? (
        <section aria-label="Runs processing now" className="grid gap-4 lg:grid-cols-2">
          {processing.map((run) => (
            <ProcessingRunCard key={run.video_id} run={run} windowS={windowFor(run.camera_id)} />
          ))}
        </section>
      ) : null}

      {completed.length > 0 ? (
        <section aria-label="Latest completed runs" className="rounded-card border border-border bg-surface p-5">
          <h2 className="text-[13px] font-medium text-text-2">Latest completed runs</h2>
          <ul className="mt-2 divide-y divide-border">
            {completed.map((run) => (
              <li key={run.video_id} className="flex items-center justify-between py-2 text-sm">
                <Link className="font-medium text-primary hover:underline" href={`/run?id=${run.video_id}`}>{run.original_name}</Link>
                <span className="num text-text-3">{`${run.frames_processed} frames`}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {runs.length > 0 ? (
        <section aria-label="Recent recorded runs" className="rounded-card border border-border bg-surface p-5">
          <h2 className="text-[13px] font-medium text-text-2">Recent recorded runs</h2>
          <div className="mt-2"><RunsTable runs={runs.slice(0, 10)} /></div>
        </section>
      ) : null}

      <AccuracySummaryCard data={accuracy.data} error={accuracy.error} />
    </main>
  );
}
