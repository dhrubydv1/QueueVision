export function EmptyState({ title, children }: Readonly<{ title: string; children?: React.ReactNode }>) {
  return (
    <div className="rounded-card border border-border bg-surface p-5 text-center">
      <p className="text-sm font-medium text-text">{title}</p>
      {children ? <div className="mt-2 text-sm text-text-2">{children}</div> : null}
    </div>
  );
}
