"use client";

import { CartesianGrid, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, ComposedChart, Scatter, XAxis, YAxis, Tooltip } from "recharts";

export interface QueuePoint { t: number; smoothed: number | null; raw?: number | null }

function Triangle({ cx, cy }: Readonly<{ cx?: number; cy?: number }>) {
  if (cx === undefined || cy === undefined) return null;
  return <path d={`M${cx} ${cy - 6}L${cx + 6} ${cy + 5}L${cx - 6} ${cy + 5}Z`} fill="var(--color-primary)" />;
}

export function QueueChart({
  points, alertLimit = null, markers = [], dwellBandS, compact = false,
}: Readonly<{
  points: QueuePoint[];
  alertLimit?: number | null;
  markers?: { t: number; kind: "service_start" }[];
  dwellBandS?: number;
  compact?: boolean;
}>) {
  const markerData = markers.map((m) => {
    const near = points.reduce<QueuePoint | null>((best, p) => (best === null || Math.abs(p.t - m.t) < Math.abs(best.t - m.t) ? p : best), null);
    return { t: m.t, y: near?.smoothed ?? 0 };
  });
  return (
    <div className={compact ? "h-12 w-full" : "h-72 w-full"} role="img" aria-label="Queue length over time">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={points} margin={compact ? { top: 2, right: 2, bottom: 2, left: 2 } : { top: 8, right: 16, bottom: 4, left: 0 }}>
          {!compact && <CartesianGrid stroke="var(--color-border)" vertical={false} />}
          <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} hide={compact} tick={{ fontSize: 12 }} unit=" s" />
          <YAxis hide={compact} allowDecimals={false} tick={{ fontSize: 12 }} width={32} />
          {!compact && <Tooltip />}
          {!compact && dwellBandS !== undefined ? (
            <ReferenceArea x1={0} x2={dwellBandS} fill="var(--color-chip)" fillOpacity={0.8} label={{ value: "first 5 s: dwell and smoothing", fontSize: 11, position: "insideTopLeft" }} />
          ) : null}
          {!compact && alertLimit !== null ? <ReferenceLine y={alertLimit} stroke="var(--color-warn)" strokeDasharray="6 4" /> : null}
          <Line dataKey="raw" stroke="var(--color-text-3)" strokeDasharray="2 3" dot={false} isAnimationActive={false} connectNulls />
          <Line dataKey="smoothed" stroke="var(--color-primary)" strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
          {!compact && markerData.length > 0 ? <Scatter data={markerData} dataKey="y" shape={<Triangle />} isAnimationActive={false} /> : null}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
