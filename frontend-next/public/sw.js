// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/* global self, caches, fetch, Response */

/**
 * YourPeer service worker.
 *
 * Hand-written (no Workbox) because the caching rules here are simple
 * and explicit: cache-first for Next static assets, network-first for
 * the app shell, skip everything else. A framework would add bytes
 * and indirection without solving any problem this code doesn't.
 *
 * Cache version bump invalidates everything: change the number in
 * CACHE_VERSION and all caches are purged on activate.
 *
 * Scope: this SW is registered on the root scope, but we explicitly
 * skip `/admin/*` paths in fetch so admin flows never see cached data.
 */

const CACHE_VERSION = "v1";
const STATIC_CACHE = `yourpeer-${CACHE_VERSION}-static`;
const SHELL_CACHE = `yourpeer-${CACHE_VERSION}-shell`;

/** Static assets pre-cached on install. The Next.js app bundle
 *  is NOT listed here — those paths have hashes we don't know at
 *  SW build time. They get cached opportunistically on first fetch. */
const PRECACHE_URLS = [
  "/",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

/** Paths that should NEVER be served from cache. Admin endpoints
 *  should always hit the network, and chat POSTs are handled by the
 *  send queue (not by the SW). */
const BYPASS_PATTERNS = [
  /^\/admin(\/|$)/,
  /^\/api\/admin\//,
  /^\/api\/chat\//,   // chat is POST; queued client-side, not SW-cached
  /^\/api\/health/,   // always want fresh health status
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
      .catch((err) => {
        // If precache fails (e.g., icons missing in dev), don't block
        // activation — we can still cache opportunistically.
        console.warn("[sw] precache failed:", err);
        return self.skipWaiting();
      }),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("yourpeer-") && !k.startsWith(`yourpeer-${CACHE_VERSION}-`))
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Don't touch non-GET — POSTs (chat, feedback) are handled by the
  // client-side send queue.
  if (request.method !== "GET") return;

  // Skip paths we deliberately don't cache
  if (BYPASS_PATTERNS.some((p) => p.test(url.pathname))) return;

  // Hashed static assets from Next.js — cache-first. The hash in the
  // filename means a change in content means a change in URL, so
  // serving from cache forever is safe.
  if (url.pathname.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(request, STATIC_CACHE));
    return;
  }

  // App shell HTML — network-first so users see deploys, but fall
  // back to cache when offline. Applies to the root page; other
  // routes (like /admin) were already filtered above.
  if (request.mode === "navigate" || request.destination === "document") {
    event.respondWith(networkFirst(request, SHELL_CACHE));
    return;
  }

  // Images, fonts, other GETs — cache-first with network fallback.
  event.respondWith(cacheFirst(request, STATIC_CACHE));
});

/**
 * Cache-first: return cached response immediately if present, else
 * fetch and cache. On network failure with no cache, fails naturally
 * (the fetch error propagates) — the page will handle that case.
 */
async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    // Only cache successful responses; 4xx/5xx stay uncached so we
    // don't persist error states.
    if (response.ok) {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch {
    // Nothing cached, network failed — return a minimal offline
    // response for assets. For navigations, this path isn't hit
    // because navigate requests go through networkFirst.
    return new Response("", { status: 503, statusText: "Offline" });
  }
}

/**
 * Network-first: try the network, fall back to cache. This is the
 * right strategy for HTML and API responses that may change between
 * deploys. The user gets fresh content when online and stale (but
 * usable) content when offline.
 */
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);

  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;

    // No cache, no network — return a minimal offline HTML page for
    // navigations. This should be rare because the root "/" is
    // precached on install.
    if (request.mode === "navigate") {
      const rootCache = await cache.match("/");
      if (rootCache) return rootCache;
    }

    return new Response("", { status: 503, statusText: "Offline" });
  }
}
