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
 * current version prefix.
 *
 * v3 bumped because:
 *   - icon-512.png was resized (was 440x512, now actually 512x512 as
 *     the manifest declares). Old caches would serve the wrong-sized
 *     image indefinitely otherwise.
 *   - manifest.webmanifest gained a shortcuts array (home-screen
 *     quick actions). Users with v2 cached manifest would never see
 *     the shortcuts; forcing a cache refresh at activate fixes that.
 *
 * v4 bumped because the SW gained a Background Sync handler. The
 * previous v3 SW has no `sync` event listener, so even if the client
 * registers a sync tag, the v3 SW wouldn't act on it. Forcing the
 * new SW to activate (skipWaiting + clients.claim on install/activate)
 * is already how the caches rotate — bumping the version guarantees
 * all prior-version caches are purged and the new SW takes control.
 *
 * v5 bumped because:
 *   - manifest.webmanifest now references four distinct shortcut
 *     icons (shortcut-{shelter,food,shower,peer}-96.png) instead of
 *     the previous shared shortcut-96.png. Without a cache bump,
 *     users on v4 caches would have the old single-icon manifest
 *     and continue to render all four shortcuts identically — the
 *     primary motivation for adding distinct icons (low-literacy
 *     legibility on the home screen) wouldn't reach them until
 *     their browser independently revalidated the manifest.
 *   - The previous shortcut-96.png file has been removed from the
 *     repo. Any v4 cache still holding it would serve a 404 on the
 *     manifest's icon refs if a stale-but-valid v4 manifest somehow
 *     loaded — bumping forces clients onto the v5 manifest that
 *     references the new files.
 *   - SW logic changed (reset-epoch awareness in the sync handler;
 *     queuedAt threading through storePendingResponse). Existing
 *     in-flight v4 SWs would not honor those filters; making the
 *     new SW activate ASAP closes that gap. */
const CACHE_VERSION = "v5";
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

/* ========================================================================
 * BACKGROUND SYNC: drain the offline send queue
 * ========================================================================
 *
 * When the client enqueues a message (user sent it while offline or the
 * POST failed with a network error), it also registers the sync tag
 * below. The browser keeps that registration even if the tab is closed
 * or frozen, and fires the `sync` event when connectivity returns.
 *
 * This handler reads the queue from IndexedDB (the same store the
 * client uses via idb-keyval), POSTs each message to /api/chat, and
 * writes the response to a second IDB store (`yourpeer:pending-
 * responses:v1`). Next time the client mounts, it reconciles that
 * store into its chat log.
 *
 * Why inline IDB: idb-keyval works in a service worker, but using it
 * from a classic (non-module) SW would require a bundler step. This
 * SW is hand-written and doesn't get bundled (see top-of-file comment).
 * Rather than change the build setup, we use raw indexedDB APIs for
 * the two keys we care about. Same database name and shape that
 * idb-keyval uses by default — see readIdbValue() / updateIdbValue()
 * below for the contract.
 *
 * Key contract with client (these MUST match send-queue.ts and
 * pending-responses.ts exactly):
 *   SEND_QUEUE_KEY = "yourpeer:send-queue:v1"
 *   PENDING_KEY    = "yourpeer:pending-responses:v1"
 *
 * Browser support: Background Sync API is Chromium-based browsers
 * (Chrome, Edge, Samsung Internet) and Android WebView. Safari and
 * Firefox don't implement it — those users fall back to the client-
 * side `online` handler in use-chat.ts, which still works fine when
 * the tab is in the foreground. Safari iOS in particular is where
 * the gap matters most, but there's no workaround until Apple ships
 * it. */

const SEND_QUEUE_KEY = "yourpeer:send-queue:v1";
const PENDING_KEY = "yourpeer:pending-responses:v1";
const RESET_EPOCH_KEY = "yourpeer:reset-epoch:v1";
const SYNC_TAG = "yourpeer-send-queue";

/** idb-keyval's default config: database name "keyval-store", object
 *  store "keyval", key-value pairs. Mirror that here so both readers
 *  see the same rows. */
const IDB_DB_NAME = "keyval-store";
const IDB_STORE_NAME = "keyval";

/** Open (or create) the shared keyval database. Returns an IDBDatabase
 *  handle. Callers close it after their transaction. */
function openIdb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_DB_NAME, 1);
    req.onupgradeneeded = () => {
      // idb-keyval uses a single object store with default keyPath = null
      // so the key is the IDBObjectStore key (passed explicitly on put).
      // This onupgradeneeded will only fire if the DB doesn't exist yet
      // — i.e., the user never used the client before. In practice the
      // client's first use created the store already.
      const db = req.result;
      if (!db.objectStoreNames.contains(IDB_STORE_NAME)) {
        db.createObjectStore(IDB_STORE_NAME);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Read one key from the keyval store. Returns undefined if missing.
 *  Single transaction, read-only. */
async function readIdbValue(key) {
  const db = await openIdb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, "readonly");
      const store = tx.objectStore(IDB_STORE_NAME);
      const req = store.get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}

/** Atomic read-modify-write for one key, mirroring idb-keyval's
 *  update(). The updater receives the current value (undefined if
 *  missing) and returns the new value. Runs inside a single readwrite
 *  transaction so a concurrent write from the client won't clobber us.
 *  Throws if updater throws. */
async function updateIdbValue(key, updater) {
  const db = await openIdb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE_NAME, "readwrite");
      const store = tx.objectStore(IDB_STORE_NAME);
      const getReq = store.get(key);
      getReq.onsuccess = () => {
        let newValue;
        try {
          newValue = updater(getReq.result);
        } catch (err) {
          reject(err);
          return;
        }
        const putReq = store.put(newValue, key);
        putReq.onerror = () => reject(putReq.error);
      };
      getReq.onerror = () => reject(getReq.error);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

/**
 * Background Sync handler. The browser fires this when the sync tag
 * was previously registered and connectivity is available. If we
 * throw from event.waitUntil, the browser retries later (with its
 * own backoff). If we resolve cleanly, the sync is considered done.
 *
 * We throw only if the queue is still non-empty after a drain attempt
 * — e.g., the server is up but returning 5xx. That way the browser
 * retries in a few minutes. Transient failures resolve cleanly; the
 * next enqueue triggers a fresh sync registration.
 */
self.addEventListener("sync", (event) => {
  if (event.tag !== SYNC_TAG) return;
  event.waitUntil(drainSendQueue());
});

async function drainSendQueue() {
  let queue;
  try {
    queue = (await readIdbValue(SEND_QUEUE_KEY)) || [];
  } catch (err) {
    console.warn("[sw sync] failed to read queue:", err);
    // If we can't even read IDB, there's nothing we can usefully do.
    // Resolve so the browser doesn't hammer retry.
    return;
  }
  if (!Array.isArray(queue) || queue.length === 0) return;

  // TTL reap: drop entries older than 1 hour, mirroring
  // send-queue.ts QUEUE_TTL_MS. Anything expired wouldn't be useful
  // to the user anyway.
  const now = Date.now();
  const QUEUE_TTL_MS = 60 * 60 * 1000;
  const fresh = queue.filter((m) => now - (m.queuedAt || 0) <= QUEUE_TTL_MS);
  if (fresh.length !== queue.length) {
    try {
      await updateIdbValue(SEND_QUEUE_KEY, () => fresh);
    } catch (err) {
      console.warn("[sw sync] failed to reap expired entries:", err);
    }
  }
  if (fresh.length === 0) return;

  // Reset-epoch check: if the user reset their session AFTER queuing
  // these messages, the messages belong to a session the user has
  // walked away from. Sending them anyway would deliver bot replies
  // for input the user has explicitly discarded — which can surface
  // as orphaned messages on next mount, or worse, as live API calls
  // for content the user no longer wants associated with their
  // session. Drop pre-reset queue entries before sending.
  //
  // Read once at the top of the drain. A reset that happens MID-drain
  // is still partially handled: client-side reconcilePending also
  // checks the epoch, so any pending response we write for a since-
  // reset message will be filtered before injection. The check here
  // just avoids the wasted POST and the wasted server processing.
  let resetEpoch = 0;
  try {
    const v = await readIdbValue(RESET_EPOCH_KEY);
    resetEpoch = typeof v === "number" ? v : 0;
  } catch (err) {
    console.warn("[sw sync] failed to read reset epoch:", err);
  }

  // Drain one-at-a-time in order. Serial to avoid race with the
  // client-side flush if both are active (client is singleton per-tab;
  // we're singleton per-SW). The server dedupes on X-Request-ID so a
  // race on the same message is harmless — whichever hits first wins.
  let drained = 0;
  let transientFailure = false;

  for (const msg of fresh) {
    // Pre-reset entries: drop without sending. The client side ALSO
    // filters these on reconcile, but skipping the POST here saves
    // the round trip entirely.
    if (resetEpoch > 0 && (msg.queuedAt || 0) < resetEpoch) {
      console.log(
        `[sw sync] dropping pre-reset queue entry ${msg.id} ` +
          `(queued ${msg.queuedAt} < reset ${resetEpoch})`,
      );
      await dequeueFromSw(msg.id);
      continue;
    }
    try {
      const body = {
        message: msg.text,
        session_id: msg.sessionId,
      };
      if (msg.coords) {
        body.latitude = msg.coords.latitude;
        body.longitude = msg.coords.longitude;
      }
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Reuse the stable request ID so the server dedupes a
          // successful response the client never saw.
          "X-Request-ID": msg.requestId || crypto.randomUUID(),
        },
        body: JSON.stringify(body),
      });
      if (res.status === 429 || res.status >= 500) {
        // Transient. Leave the message in the queue and let the
        // browser retry the sync later.
        transientFailure = true;
        break;
      }
      if (!res.ok) {
        // 4xx non-rate-limit (e.g., 400 malformed) — the message is
        // bad and retrying won't help. Drop it so it doesn't wedge
        // the queue forever.
        await dequeueFromSw(msg.id);
        continue;
      }
      let responseBody;
      try {
        responseBody = await res.json();
      } catch (err) {
        console.warn("[sw sync] non-JSON response; dropping entry:", err);
        await dequeueFromSw(msg.id);
        continue;
      }
      await storePendingResponse(msg.id, responseBody, msg.queuedAt);
      await dequeueFromSw(msg.id);
      drained++;
    } catch (err) {
      // Network error or fetch exception — treat as transient so the
      // browser will retry when connectivity is better.
      console.warn("[sw sync] POST failed, will retry:", err);
      transientFailure = true;
      break;
    }
  }

  console.log(`[sw sync] drained ${drained}/${fresh.length}`);

  if (transientFailure) {
    // Throw so the browser schedules another sync with its own backoff.
    // This is the documented API for requesting a retry from
    // Background Sync.
    throw new Error("sw sync: transient failure, requesting retry");
  }
}

/** Remove a message from the send queue. Mirrors send-queue.ts
 *  dequeue() — same key, same atomicity via updateIdbValue. */
async function dequeueFromSw(id) {
  try {
    await updateIdbValue(SEND_QUEUE_KEY, (old) => {
      const current = Array.isArray(old) ? old : [];
      return current.filter((m) => m.id !== id);
    });
  } catch (err) {
    console.warn("[sw sync] dequeue failed:", err);
  }
}

/** Append a server response to the pending-responses store, under the
 *  same ID as the originating queued message. On next client mount,
 *  use-chat reads and reconciles it into the chat log.
 *
 *  queuedAt is the user's-typing-time of the underlying QueuedMessage,
 *  threaded through so the client's reconcilePending can compare it
 *  against the reset epoch. We can't use the response's own
 *  receivedAt for that comparison: a SW that completes a POST after
 *  the user resets would write a post-reset receivedAt for pre-reset
 *  typing, slipping through the filter. queuedAt is unambiguous —
 *  it's stamped at the moment the user typed, well before any reset
 *  could race with the SW. */
async function storePendingResponse(id, body, queuedAt) {
  const MAX_PENDING_DEPTH = 50;
  try {
    await updateIdbValue(PENDING_KEY, (old) => {
      const current = Array.isArray(old) ? old : [];
      // Dedup by id so repeated POSTs (shouldn't happen, but defensive)
      // overwrite rather than stack.
      const deduped = current.filter((p) => p.id !== id);
      // Cap: drop the oldest if we're at the limit. Realistically the
      // queue drain should never produce 50+ pending entries; this is
      // only a guard against pathological SW loops.
      //
      // Hitting the cap is a real signal that something has gone
      // wrong — either:
      //   1. The client is failing to reconcile (mounting and not
      //      consuming pending responses), causing the store to grow
      //      unboundedly until each new write evicts an old one. The
      //      user would observe a quiet "my offline messages aren't
      //      coming back" gradual failure.
      //   2. The SW is in a buggy loop generating duplicate-but-not-
      //      deduped writes. Less likely given the dedup-by-id above,
      //      but a code change that breaks dedup would surface here.
      // Either way, the operator wants to know rather than have it
      // silently mask the underlying issue. Log at warn so it shows
      // in the SW console without being filtered out by the default
      // info-level threshold.
      if (deduped.length >= MAX_PENDING_DEPTH) {
        const evicted = deduped.shift();
        console.warn(
          `[sw sync] pending-responses cap (${MAX_PENDING_DEPTH}) hit; ` +
            `evicted oldest entry (id=${evicted && evicted.id}, ` +
            `receivedAt=${evicted && evicted.receivedAt}). ` +
            `If this fires repeatedly, the client is not reconciling ` +
            `pending responses on mount.`,
        );
      }
      deduped.push({
        id,
        sessionId: body && typeof body === "object" ? body.session_id || null : null,
        body,
        receivedAt: Date.now(),
        // Preserve the original queue time so the client can compare
        // it against the reset epoch on reconcile. Coerce a missing
        // value to undefined explicitly so the field is omitted from
        // the stored entry rather than serialized as null — keeps the
        // shape clean and the client's `typeof === "number"` guard
        // works as intended.
        queuedAt: typeof queuedAt === "number" ? queuedAt : undefined,
      });
      return deduped;
    });
  } catch (err) {
    console.warn("[sw sync] storePendingResponse failed:", err);
  }
}
