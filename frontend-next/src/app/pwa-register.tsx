// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Service worker registration.
 *
 * Mounted once from RootLayout as a hidden client component.
 *
 * Behavior splits on NODE_ENV:
 *   - production: registers /sw.js at the root scope on first paint,
 *     polls for updates hourly. Failures are logged but never surface
 *     to the user — if SW registration fails, the app just works
 *     without offline support.
 *   - development: skips registration AND actively unregisters any
 *     SW that was installed by a previous production build (or a
 *     dev session with NEXT_PUBLIC_ENABLE_SW=1). Without the
 *     unregister, a previously-installed SW keeps intercepting
 *     requests across dev sessions and serves stale cached JS chunks,
 *     causing hydration mismatches that look like cache bugs.
 *
 * Override knob: setting NEXT_PUBLIC_ENABLE_SW=1 forces production
 * behavior in dev (registers, doesn't unregister). Use this when
 * actively testing PWA / offline behavior. Otherwise leave it off.
 */

"use client";

import { useEffect } from "react";

export function PWARegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;

    const devMode = process.env.NODE_ENV === "development";
    const forceEnable = process.env.NEXT_PUBLIC_ENABLE_SW === "1";

    // Dev mode without the override: actively clear any SW left over
    // from a previous production build or a NEXT_PUBLIC_ENABLE_SW=1
    // session. This prevents the "cold load fails, hard refresh
    // fixes it" hydration-mismatch pattern, which happens when a
    // stale SW intercepts /_next/static/ requests and serves cached
    // chunks while the dev server is producing fresh ones.
    //
    // We also drain caches.delete(...) for the same reason — even
    // after unregister, an SW's Cache Storage entries persist until
    // explicitly cleared, and a re-installed SW could pick them
    // back up.
    if (devMode && !forceEnable) {
      navigator.serviceWorker
        .getRegistrations()
        .then((regs) => {
          if (regs.length === 0) return;
          console.info(
            `[pwa] Dev mode: unregistering ${regs.length} existing service worker(s).`,
          );
          return Promise.all(regs.map((r) => r.unregister()));
        })
        .then(() => {
          if (typeof caches === "undefined") return;
          return caches.keys().then((keys) => {
            if (keys.length === 0) return;
            console.info(
              `[pwa] Dev mode: clearing ${keys.length} Cache Storage entries.`,
            );
            return Promise.all(keys.map((k) => caches.delete(k)));
          });
        })
        .catch((err) => {
          console.warn("[pwa] Dev mode SW cleanup failed:", err);
        });
      return;
    }

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
