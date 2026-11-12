const DASH = "—";

/** Clock time "HH:MM:SS TZ" in `tz` (default: the browser's zone); Asia/Kathmandu is shown as NPT. */
export function formatClock(iso: string | number, tz?: string): string {
  const date = new Date(iso);
  const options: Intl.DateTimeFormatOptions = {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: tz, timeZoneName: "short",
  };
  const parts = new Intl.DateTimeFormat("en-GB", options).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const zone = tz === "Asia/Kathmandu" ? "NPT" : get("timeZoneName");
  return `${get("hour")}:${get("minute")}:${get("second")} ${zone}`.trim();
}

export function formatSeconds(s: number | null): string {
  if (s === null || !Number.isFinite(s)) return DASH;
  const total = Math.round(s);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  return `${minutes} min ${String(total % 60).padStart(2, "0")} s`;
}

export function formatNumber(n: number | null | undefined, digits: number): string {
  return n === null || n === undefined || !Number.isFinite(n) ? DASH : n.toFixed(digits);
}

/** Number with an explicit sign (true minus sign U+2212 for negatives). */
export function formatSigned(n: number, digits: number): string {
  const text = formatNumber(Math.abs(n), digits);
  if (n > 0) return `+${text}`;
  if (n < 0) return `\u2212${text}`;
  return text;
}
