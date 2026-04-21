# Testing offline PWA behavior

Offline behavior is difficult to unit-test because it depends on the
browser's networking stack, the service worker lifecycle, and
IndexedDB — all of which are jsdom's blind spots. The offline feature
should be manually tested using Chrome DevTools before each release.

The unit tests that DO exist cover the pure logic: queue CRUD, cache
TTL, error classification. Anything requiring a browser lives here.

## Prerequisites

- Run `npm run build && npm run start` (NOT `npm run dev` — the
  service worker is disabled in development to avoid caching stale
  HMR code)
- Open the app in Chrome (some of the DevTools features below work
  differently in Firefox/Safari)
- Open DevTools → Application tab — this is where you'll do most of
  the testing

## Test 1: PWA install prompt appears

**Why this matters:** users on mobile should be able to "Add to Home
Screen" so the app launches like a native app.

**Steps:**
1. Open the site fresh (clear caches first: DevTools → Application →
   Storage → Clear site data)
2. In DevTools, go to Application → Manifest — verify the manifest
   loads with the YourPeer name, theme color, and three icons
3. In the URL bar, look for the "install" icon (⊕ on desktop, or the
   three-dot menu → "Install app" on mobile Chrome)
4. Install the app; verify it opens in its own window (no browser
   chrome) and the correct icon appears in your dock / launcher

**Pass criteria:** install succeeds, app launches in standalone mode,
icon is not a broken-image placeholder.

## Test 2: Service worker activates and caches assets

**Why this matters:** without the SW, offline mode can't serve the
app shell.

**Steps:**
1. Load the site fresh
2. DevTools → Application → Service Workers — verify "activated and
   running" status
3. DevTools → Application → Cache Storage — verify caches named
   `yourpeer-v1-static` and `yourpeer-v1-shell` exist and contain
   the app HTML, manifest, and icons
4. DevTools → Network — reload the page with throttling set to
   "Offline". Page should still load (from cache).

**Pass criteria:** SW in "activated" state, caches populated, page
loads when DevTools Network throttling is set to "Offline".

## Test 3: Cached results are shown when offline

**Why this matters:** this is the primary scenario the whole feature
exists for.

**Steps:**
1. Load the site with a working connection
2. Search for "food in Brooklyn" — wait for results to render
3. DevTools → Network → set throttling to "Offline"
4. Reload the page (Cmd-R / Ctrl-R)

**Pass criteria:**
- Page loads (from SW cache)
- The last results are visible (rehydrated from Zustand localStorage)
- An amber banner appears: "You're offline — these results may be
  outdated. Last updated N minutes ago."
- The input field is NOT disabled

## Test 4: Messages queue while offline, flush on reconnect

**Why this matters:** users should be able to compose messages in a
dead zone and have them send when they get signal again.

**Steps:**
1. Load the site with a working connection
2. Start a conversation: "shelter in Manhattan" — let results load
3. DevTools → Network → set throttling to "Offline"
4. Type and send: "are any of them open now?"
5. Verify:
   - The user message appears in the chat immediately
   - A transient bot message says "Saved — I'll send this when you're
     back online"
   - In DevTools → Application → IndexedDB → keyval-store, the
     `yourpeer:send-queue:v1` key contains your message
6. Send another message: "what about showers?"
   - Verify it also queues (IndexedDB key contains 2 entries)
7. DevTools → Network → set throttling back to "Online (no throttling)"
   or "Fast 3G"
8. Within a second or two, both messages should flush and produce
   real bot responses
9. After flush, IndexedDB queue should be empty

**Pass criteria:** all queued messages flush in order, each gets a
real response, queue empties, no duplicate messages appear in chat.

## Test 5: Queued message expires after 1 hour

**Why this matters:** stale queued questions shouldn't silently send
hours later when context has changed.

**Steps:**
1. Go offline with DevTools
2. Send a message — verify it queues in IndexedDB
3. In DevTools → Application → IndexedDB, **manually edit** the
   `queuedAt` timestamp for the queued message to be more than 1 hour
   in the past (e.g., current ms - 3700000)
4. Go back online (DevTools throttling → Online)

**Pass criteria:** expired message is NOT sent. Instead, a transient
bot message appears telling the user "Some messages were waiting too
long and weren't sent" with the expired question text, so they can
re-ask if still relevant.

## Test 6: Session reset warning on long offline period

**Why this matters:** after 30+ minutes offline, the server-side
session expires and conversation context is lost. Users should be
told this rather than silently starting fresh.

**Steps:**
1. Start a conversation, reach the results screen
2. Wait 30+ minutes (or manually expire the session — the simplest
   way is to stop the backend, wait past its TTL, restart it)
3. Go offline and type a message; verify it queues
4. Go back online

**Pass criteria:** a transient bot message appears: "You were offline
for a while — starting a fresh conversation." The queued message is
still sent, but the user is warned the conversation context is gone.

## Test 7: Service worker updates on deploy

**Why this matters:** when a new version of the app ships, users
shouldn't be stuck on the old version.

**Steps:**
1. Load the site; note the SW version in DevTools → Application →
   Service Workers
2. Bump `CACHE_VERSION` in `public/sw.js` (e.g., "v1" → "v2")
3. Rebuild + restart (`npm run build && npm run start`)
4. Load the site again in a new tab

**Pass criteria:** in Application → Service Workers, a new SW is
"waiting to activate." After a full refresh, it activates, old
caches (with the v1 prefix) are deleted, new caches (v2 prefix)
appear.

## Test 8: Admin pages do not use the SW

**Why this matters:** admin flows should always see live data — stale
cached admin data could mislead staff.

**Steps:**
1. With the SW active, navigate to `/admin`
2. DevTools → Network — verify admin requests are going to the
   network (not served from SW cache)
3. Go offline; navigate to `/admin` — should fail to load (NOT show
   a cached version)

**Pass criteria:** admin pages hit the network on every request; go
offline → admin pages are unreachable (as expected).

## Known limitations

- **iOS Safari in standalone mode has quirks:** background fetch
  doesn't work, IndexedDB can be wiped by the OS under low-storage
  pressure. Test manually on a real iOS device before claiming iOS
  support.
- **Private browsing mode:** IndexedDB is in-memory and cleared when
  the tab closes. The feature degrades gracefully (no cache means no
  offline banner) but behaves differently from normal mode.
- **Cache Storage quota:** browsers impose quotas (50MB+ typically).
  The service worker caches are well under this; if they ever grow,
  implement cache eviction in `sw.js` `activate` handler.
