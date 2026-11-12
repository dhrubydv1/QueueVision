import type { SVGProps } from "react";

function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
      {children}
    </svg>
  );
}

export const OverviewIcon = (p: SVGProps<SVGSVGElement>) => (
  <Icon {...p}><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></Icon>
);
export const LiveIcon = (p: SVGProps<SVGSVGElement>) => (
  <Icon {...p}><path d="M23 7l-7 5 7 5V7z" /><rect x="1" y="5" width="15" height="14" rx="2" /></Icon>
);
export const RunsIcon = (p: SVGProps<SVGSVGElement>) => (
  <Icon {...p}><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></Icon>
);
export const AccuracyIcon = (p: SVGProps<SVGSVGElement>) => (
  <Icon {...p}><path d="M3 3v18h18" /><path d="M7 15l4-4 3 3 5-6" /></Icon>
);
export const LogoMark = (p: SVGProps<SVGSVGElement>) => (
  <Icon width="28" height="28" {...p}><circle cx="6" cy="8" r="2" /><circle cx="12" cy="8" r="2" /><circle cx="18" cy="8" r="2" /><path d="M3 20v-3a3 3 0 013-3M9 20v-3a3 3 0 013-3M15 20v-3a3 3 0 013-3" /></Icon>
);
export const AlertIcon = (p: SVGProps<SVGSVGElement>) => (
  <Icon {...p}><path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" /><path d="M12 9v4M12 17h.01" /></Icon>
);
