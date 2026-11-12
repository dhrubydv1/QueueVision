"use client";

import { useQuery } from "@tanstack/react-query";
import { AccuracyView } from "@/components/AccuracyView";
import { getAccuracy } from "@/lib/api";

export default function AccuracyPage() {
  const query = useQuery({ queryKey: ["accuracy"], queryFn: getAccuracy });
  return (
    <main className="mx-auto flex max-w-content flex-col gap-6 p-6">
      <h1 className="font-display text-[28px] font-semibold text-text">Accuracy</h1>
      <AccuracyView data={query.data} error={query.error} />
    </main>
  );
}
