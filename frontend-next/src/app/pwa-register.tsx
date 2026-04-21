// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Service worker registration.
 *
 * Mounted once from RootLayout as a hidden client component. Registers
 * /sw.js at the root scope on first paint. Failures are logged but
 * never surface to the user — if SW registration fails, the app just
 * works without offline support.
 *
 * Registration is skipped in development to avoid caching stale code
 * during iteration. Flip the guard if you need to test SW behavior
 * in dev (or just build + serve via `npm run start`).
 */

"use client";

import { useEffect } from "react";

export function PWARegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;

    // Skip SW registration in dev — webpack HMR and service workers
    // don't mix. To test SW in dev, set NEXT_PUBLIC_ENABLE_SW=1.
    const devMode = process.env.NODE_ENV === "development";
    const forceEnable = process.env.NEXT_PUBLIC_ENABLE_SW === "1";
    if (devMode && !forceEnable) return;

    // Register on load so we don't compete with initial page resources
    // for network. The SW itself is tiny, but its install step
    // pre-caches assets, which does.
    const onLoad = () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .then((reg) => {
          // Poll for updates every hour. Users in long sessions will
          // pick up new deploys without needing to manually refresh.
          setInterval(() => {
            reg.update().catch(() => {});
          }, 60 * 60 * 1000);
        })
        .catch((err) => {
          console.warn("[pwa] SW registration failed:", err);
        });
    };

    if (document.readyState === "complete") {
      onLoad();
    } else {
      window.addEventListener("load", onLoad, { once: true });
      return () => window.removeEventListener("load", onLoad);
    }
  }, []);

  // This component renders nothing — it's purely for side effects.
  return null;
}
