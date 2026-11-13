export function MetricCard({
  label, value, caption, tag, children,
}: Readonly<{ label: string; value: React.ReactNode; caption?: React.ReactNode; tag?: string; children?: React.ReactNode }>) {
  return (
    <section className="rounded-card border border-border bg-surface p-5" aria-label={label}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[13px] font-medium text-text-2">{label}</h3>
        {tag ? <span className="rounded-chip bg-chip px-2 py-0.5 text-xs text-text-2">{tag}</span> : null}
      </div>
      <p className="num mt-2 font-display text-[32px] font-semibold leading-tight text-text">{value}</p>
      {caption ? <p className="mt-1 text-xs text-text-3">{caption}</p> : null}
      {children}
    </section>
  );
}
