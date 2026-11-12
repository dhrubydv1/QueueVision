import type { Wait } from "@/lib/api";
import { formatSeconds } from "@/lib/format";

export function WaitsTable({ waits }: Readonly<{ waits: Wait[] }>) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-[13px] font-medium text-text-2">
          <tr>
            <th className="py-2 pr-4 font-medium">Person</th>
            <th className="py-2 pr-4 font-medium">Joined</th>
            <th className="py-2 pr-4 font-medium">Service</th>
            <th className="py-2 pr-4 font-medium">Wait</th>
            <th className="py-2 font-medium">Abandoned</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {waits.map((w) => (
            <tr key={w.person_key}>
              <td className="py-2 pr-4">{w.person_key}</td>
              <td className="num py-2 pr-4">{formatSeconds(w.join_s)}</td>
              <td className="num py-2 pr-4">{formatSeconds(w.service_s ?? null)}</td>
              <td className="num py-2 pr-4">{formatSeconds(w.wait_s ?? null)}</td>
              <td className="py-2">{w.abandoned ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
