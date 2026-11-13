"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { EmptyState } from "@/components/EmptyState";
import { RunDetail } from "@/components/RunDetail";

function RunEntry() {
  const raw = useSearchParams().get("id");
  if (raw === null || !/^\d+$/.test(raw)) {
    return (
      <main className="mx-auto max-w-content p-6">
        <EmptyState title="Run not found">
          <Link className="text-primary hover:underline" href="/runs">Back to runs</Link>
        </EmptyState>
      </main>
    );
  }
  return <RunDetail key={raw} videoId={Number(raw)} />;
}

export default function RunPage() {
  return <Suspense fallback={null}><RunEntry /></Suspense>;
}
