import Link from "next/link";
import type { Run } from "@/lib/api";
import { formatClock } from "@/lib/format";
import { StatusChip, type Status } from "./StatusChip";

const KNOWN: Status[] = ["queued", "processing", "completed", "failed", "cancelled"];

export function RunsTable({ runs }: Readonly<{ runs: Run[] }>) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-[13px] font-medium text-text-2">
          <tr>
            <th className="py-2 pr-4 font-medium">Name</th>
            <th className="py-2 pr-4 font-medium">Camera</th>
            <th className="py-2 pr-4 font-medium">Status</th>
            <th className="py-2 pr-4 font-medium">Created</th>
            <th className="py-2 font-medium">Frames processed</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {runs.map((run) => (
            <tr key={run.video_id}>
              <td className="py-2 pr-4">
                <Link className="font-medium text-primary hover:text-primary-hover hover:underline" href={`/run?id=${run.video_id}`}>
                  {run.original_name}
                </Link>
                {run.status === "processing" ? (
                  <Link className="ml-3 text-xs text-primary hover:underline" href={`/live?id=${run.video_id}`}>Watch live</Link>
                ) : null}
              </td>
              <td className="py-2 pr-4 text-text-2">{run.camera_id}</td>
              <td className="py-2 pr-4">
                {(KNOWN as string[]).includes(run.status) ? <StatusChip status={run.status as Status} /> : run.status}
              </td>
              <td className="py-2 pr-4 text-text-2">{formatClock(run.created_at)}</td>
              <td className="num py-2">{run.frames_processed}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
