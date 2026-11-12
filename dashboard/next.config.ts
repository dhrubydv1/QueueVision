import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
  // Rewrites are unsupported in static export builds, so only add them in `next dev`.
  // WebSockets connect directly to NEXT_PUBLIC_API_WS (rewrites do not proxy them).
  ...(isDev
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
        },
      }
    : {}),
};

export default nextConfig;
