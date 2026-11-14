import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@alter/api-client"],
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
      ...[
        "/auth/callback",
        "/auth/reauth/callback",
        "/auth/discord/callback",
      ].map((source) => ({
        source,
        headers: [{ key: "Referrer-Policy", value: "no-referrer" }],
      })),
    ];
  },
};
export default nextConfig;
