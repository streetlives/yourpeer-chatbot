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
  console.log('Error: ', error)
  return (
    <html lang="en">
      <body>
        <div
          style={{
            minHeight: "100dvh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "Inter, system-ui, sans-serif",
            padding: "2rem",
            backgroundColor: "#fafafa",
          }}
        >
          <div style={{ maxWidth: "420px", textAlign: "center" }}>
            <h1
              style={{
                fontSize: "1.25rem",
                fontWeight: 600,
                color: "#171717",
                marginBottom: "0.5rem",
              }}
            >
              Something went wrong
            </h1>
            <p
              style={{
                fontSize: "0.95rem",
                color: "#737373",
                marginBottom: "1.5rem",
                lineHeight: 1.5,
              }}
            >
              We hit an unexpected error. You can try again, or visit{" "}
              <a
                href="https://yourpeer.nyc"
                style={{ color: "#f59e0b", textDecoration: "underline" }}
              >
                yourpeer.nyc
              </a>{" "}
              to find services directly.
            </p>
            <button
              onClick={reset}
              style={{
                padding: "0.6rem 1.5rem",
                fontSize: "0.9rem",
                fontWeight: 500,
                color: "#fff",
                backgroundColor: "#f59e0b",
                border: "none",
                borderRadius: "0.5rem",
                cursor: "pointer",
              }}
            >
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
