"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { EmptyState } from "@/components/EmptyState";
import { LiveRunView } from "@/components/LiveRunView";
import { listRuns } from "@/lib/api";

function parseId(raw: string | null): number | null {
  return raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
}

function LiveEntry() {
  const router = useRouter();
  const id = parseId(useSearchParams().get("id"));
  const runs = useQuery({ queryKey: ["runs"], queryFn: listRuns, enabled: id === null });
  const newest = runs.data
    ?.filter((r) => r.status === "processing")
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

  useEffect(() => {
    if (id === null && newest) router.replace(`/live?id=${newest.video_id}`);
  }, [id, newest, router]);

  if (id !== null) return <LiveRunView key={id} videoId={id} />;
  if (runs.isSuccess && !newest) {
    return (
      <main className="mx-auto max-w-content p-6">
        <EmptyState title="No run is processing — upload a video">
          <Link className="text-primary hover:underline" href="/runs">Go to runs</Link>
        </EmptyState>
      </main>
    );
  }
  if (runs.isError) {
    return <main className="mx-auto max-w-content p-6"><EmptyState title="Cannot load runs">{runs.error.message}</EmptyState></main>;
  }
  return <main className="mx-auto max-w-content p-6"><p className="text-sm text-text-2">Loading…</p></main>;
}

export default function LivePage() {
  return <Suspense fallback={null}><LiveEntry /></Suspense>;
}
