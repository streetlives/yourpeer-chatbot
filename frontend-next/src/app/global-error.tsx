// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // Dev-only: log the error to the browser console so it's visible
  // when working locally. Production users hit React's normal error
  // pipeline (and any APM that's configured) — they don't need to see
  // the raw error object in the browser console. Per FRONTEND_AUDIT
  // 2026-05 P2 #9.
  if (process.env.NODE_ENV !== "production") {
    console.log("Error: ", error);
  }
  return (
    <html lang="en">
      <body>
        {/* Dark mode uses prefers-color-scheme only (not the class-based
            toggle used everywhere else). This page is a last-resort
            fallback rendered OUTSIDE the normal tree when React itself
            crashes — the FOUC inline script in the root layout may not
            have run, so we can't rely on the .dark class on <html>.
            OS preference is the safest signal here; users with an
            explicit light/dark override will briefly see OS default on
            this rare error page, which is acceptable. */}
        <style>{`
          .ge-root {
            min-height: 100dvh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: Inter, system-ui, sans-serif;
            padding: 2rem;
            background-color: #fafafa;
            color: #171717;
          }
          .ge-title { font-size: 1.25rem; font-weight: 600; color: #171717; margin-bottom: 0.5rem; }
          .ge-body { font-size: 0.95rem; color: #737373; margin-bottom: 1.5rem; line-height: 1.5; }
          .ge-link { color: #f59e0b; text-decoration: underline; }
          .ge-btn {
            padding: 0.6rem 1.5rem; font-size: 0.9rem; font-weight: 500;
            color: #fff; background-color: #f59e0b; border: none;
            border-radius: 0.5rem; cursor: pointer;
          }
          @media (prefers-color-scheme: dark) {
            .ge-root { background-color: #0a0a0a; color: #f5f5f5; }
            .ge-title { color: #f5f5f5; }
            .ge-body { color: #a3a3a3; }
            .ge-link { color: #fbbf24; }
            .ge-btn { background-color: #d97706; color: #fff; }
          }
        `}</style>
        <div className="ge-root">
          <div style={{ maxWidth: "420px", textAlign: "center" }}>
            <h1 className="ge-title">Something went wrong</h1>
            <p className="ge-body">
              We hit an unexpected error. You can try again, or visit{" "}
              <a href="https://yourpeer.nyc" className="ge-link">yourpeer.nyc</a>{" "}
              to find services directly.
            </p>
            <button onClick={reset} className="ge-btn">Try again</button>
          </div>
        </div>
      </body>
    </html>
  );
}
