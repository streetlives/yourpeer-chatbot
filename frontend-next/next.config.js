/** @type {import('next').NextConfig} */
const nextConfig = {
  pageExtensions: ["ts", "tsx"],
  // Allow both localhost and 127.0.0.1 during local dev
  allowedDevOrigins: ["localhost", "127.0.0.1"],
  async headers() {
    const isDev = process.env.NODE_ENV === "development";
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              // 'unsafe-eval' is required by Turbopack/webpack in dev for
              // source maps. Never included in production builds.
              `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data:",
              "font-src 'self'",
              // All API calls go through Next.js route handlers (relative paths),
              // so 'self' is sufficient in production. Local dev needs the
              // backend directly for hot-reload proxying.
              `connect-src 'self'${
                isDev
                  ? " http://localhost:8000 http://127.0.0.1:8000"
                  : ""
              }`,
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
              "object-src 'none'",
            ].join("; "),
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          // Prevents third-party iframes from silently accessing geolocation,
          // which matters since the chat collects lat/long from users.
          { key: "Permissions-Policy", value: "geolocation=(self), camera=(), microphone=(self)" },
          { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
        ],
      },
    ];
  },
  async rewrites() {
    return { beforeFiles: [] };
  },
};

module.exports = nextConfig;
