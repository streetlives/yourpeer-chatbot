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

/* Bump when sw.js changes in a way that makes the prior cache unsafe
 * to reuse. v2 bumped because v1's SHELL_CACHE contained a
 * redirect-tainted "/" response that browsers refuse to serve to
 * navigations — see SHELL_PRECACHE_URLS comment. On activate, the
 * cleanup step below purges any cache whose name doesn't match the
 * current version prefix. */
const CACHE_VERSION = "v2";
const STATIC_CACHE = `yourpeer-${CACHE_VERSION}-static`;
const SHELL_CACHE = `yourpeer-${CACHE_VERSION}-shell`;

/** Static assets pre-cached on install into STATIC_CACHE (cache-first). */
const STATIC_PRECACHE_URLS = [
  "/manifest.webmanifest",
  "/favicon.ico",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

/** App shell URLs pre-cached on install into SHELL_CACHE (network-first).
 *
 *  **Critical:** we precache "/chat" and NOT "/". The root "/" is a
 *  server-side redirect to "/chat" (see app/page.tsx — redirect("/chat")).
 *  If we precached "/", cache.addAll() would follow the redirect and
 *  cache a response with response.redirected === true. Navigations use
 *  redirect: "manual" by default, so the browser rejects any cached
 *  redirect-tainted response with a "network error response: a
 *  redirected response was used for a request whose redirect mode is
 *  not 'follow'" error — which is exactly what happens if you try to
 *  reload the app while offline. Precaching the post-redirect URL
 *  directly avoids this.
 *
 *  The navigation fallback in networkFirst() also falls back to "/chat"
 *  — so users who navigate directly to "/" while offline still get the
 *  app shell served, even though "/" itself isn't precached. */
const SHELL_PRECACHE_URLS = [
  "/chat",
];

/** The Next.js app bundle is NOT listed here — those paths have hashes
 *  we don't know at SW build time. They get cached opportunistically on
 *  first fetch into STATIC_CACHE. */

/** Paths that should NEVER be served from cache. Admin endpoints
 *  should always hit the network, and chat POSTs are handled by the
 *  send queue (not by the SW).
 *
 *  Chat pattern matches both `/api/chat` (bare — the send endpoint)
 *  and `/api/chat/...` (feedback, location-feedback). Previously only
 *  the latter matched; `/api/chat` on its own slipped through. In
 *  practice chat is POST-only and the SW filters non-GETs before
 *  bypass check — but if a GET handler is ever added to /api/chat,
 *  the old pattern would silently serve from cache. */
const BYPASS_PATTERNS = [
  /^\/admin(\/|$)/,
  /^\/api\/admin\//,
  /^\/api\/chat(\/|$)/,   // chat is POST; queued client-side, not SW-cached
  /^\/api\/health/,       // always want fresh health status
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    Promise.all([
      caches
        .open(SHELL_CACHE)
        .then((cache) => cache.addAll(SHELL_PRECACHE_URLS)),
      caches
        .open(STATIC_CACHE)
        .then((cache) => cache.addAll(STATIC_PRECACHE_URLS)),
    ])
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
 * Strip the redirected flag from a response by re-wrapping its body in
 * a fresh Response. Browsers refuse to serve cached responses where
 * response.redirected === true to navigations with redirect: "manual"
 * (the default for navigations), so any response we cache must have
 * the flag cleared. Returns a new Response with the same body, status,
 * and headers, but response.redirected === false.
 *
 * Only call this right before caching. Never call on a response the
 * caller also plans to return to the browser directly — cloning + body
 * consumption means the original is unusable afterward.
 */
async function stripRedirect(response) {
  if (!response.redirected) return response;
  const body = await response.blob();
  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
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
      // Strip redirect flag before caching. Navigations use
      // redirect:"manual" and the browser rejects cached
      // redirect-tainted responses.
      const cacheable = await stripRedirect(response.clone());
      cache.put(request, cacheable).catch(() => {});
    }
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;

    // No cache, no network — fall back to the app shell for
    // navigations. "/chat" is the real shell; "/" server-side
    // redirects to it so we never precache "/" directly. A direct
    // navigation to "/" while offline lands here and is served the
    // "/chat" shell, which is what the user expected anyway.
    if (request.mode === "navigate") {
      const shellCache = await cache.match("/chat");
      if (shellCache) return shellCache;
    }

    return new Response("", { status: 503, statusText: "Offline" });
  }
}
