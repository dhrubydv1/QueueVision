"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { EmptyState } from "@/components/EmptyState";
import { RunsTable } from "@/components/RunsTable";
import { UploadForm } from "@/components/UploadForm";
import { getCameras, listRuns, type Run } from "@/lib/api";

const newestFirst = (a: Run, b: Run) => b.created_at.localeCompare(a.created_at);

export default function RunsPage() {
  const router = useRouter();
  const runsQuery = useQuery({ queryKey: ["runs"], queryFn: listRuns, refetchInterval: 5000 });
  const cameras = useQuery({ queryKey: ["cameras"], queryFn: getCameras });
  const runs = [...(runsQuery.data ?? [])].sort(newestFirst);

  return (
    <main className="mx-auto flex max-w-content flex-col gap-6 p-6">
      <h1 className="font-display text-[28px] font-semibold text-text">Recorded runs</h1>
      <section id="upload" aria-label="Upload a video">
        {cameras.isError ? (
          <EmptyState title="Cannot load camera configs">{cameras.error.message}</EmptyState>
        ) : cameras.data ? (
          <UploadForm
            cameras={cameras.data}
            onStarted={(run) => router.push(run.options.realtime ? `/live?id=${run.video_id}` : `/run?id=${run.video_id}`)}
          />
        ) : (
          <p className="text-sm text-text-2">Loading…</p>
        )}
      </section>
      {runsQuery.isError ? (
        <EmptyState title="Cannot load runs">{runsQuery.error.message}</EmptyState>
      ) : runsQuery.isSuccess && runs.length === 0 ? (
        <EmptyState title="No runs yet — upload a video" />
      ) : runs.length > 0 ? (
        <section aria-label="All runs" className="rounded-card border border-border bg-surface p-5">
          <RunsTable runs={runs} />
        </section>
      ) : null}
    </main>
  );
}
