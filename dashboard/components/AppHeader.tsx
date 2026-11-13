"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { AccuracyIcon, LiveIcon, LogoMark, OverviewIcon, RunsIcon } from "./icons";

const NAV = [
  { href: "/", label: "Overview", Icon: OverviewIcon, match: (p: string) => p === "/" },
  { href: "/live", label: "Live camera", Icon: LiveIcon, match: (p: string) => p.startsWith("/live") },
  { href: "/runs", label: "Recorded runs", Icon: RunsIcon, match: (p: string) => p.startsWith("/runs") || p.startsWith("/run") },
  { href: "/accuracy", label: "Accuracy", Icon: AccuracyIcon, match: (p: string) => p.startsWith("/accuracy") },
];

export function AppHeader() {
  const pathname = usePathname() ?? "/";
  return (
    <>
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-content items-center gap-6 px-6 py-3">
          <Link href="/" className="flex items-center gap-2 font-display text-lg font-semibold text-text">
            <LogoMark className="text-primary" />
            QueueVision
          </Link>
          <nav aria-label="Main" className="hidden flex-1 gap-1 md:flex">
            {NAV.map(({ href, label, match }) => {
              const active = match(pathname);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`inline-flex min-h-11 items-center rounded-control px-3 py-2 text-sm font-medium ${active ? "bg-primary-soft text-primary" : "text-text-2 hover:text-text"}`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>
          <span className="ml-auto rounded-chip bg-chip px-2.5 py-0.5 text-xs text-text-2">localhost</span>
        </div>
      </header>
      <nav aria-label="Main (mobile)" className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-4 border-t border-border bg-surface md:hidden">
        {NAV.map(({ href, label, Icon, match }) => {
          const active = match(pathname);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs font-medium ${active ? "bg-primary-soft text-primary" : "text-text-2"}`}
            >
              <Icon />
              {label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
