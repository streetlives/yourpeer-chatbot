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

## A note on simulating offline

Tests 3 through 6 require the app to believe the browser is offline.
This is subtler than it looks. The app reads **two** different signals
and they aren't perfectly coupled:

- **`navigator.onLine === false`** — what drives the amber "You're
  offline" banner and the `online` / `offline` window events the app
  subscribes to.
- **`fetch()` calls fail** — what drives the red "Can't reach the
  server" banner (via the 60-second health-check poll).

DevTools → Network → **Offline preset** is not always enough on its
own. In some Chrome versions / tab-load sequences it blocks `fetch`
but leaves `navigator.onLine` at `true`. When that happens, you'll see
the **red** "Can't reach the server" banner instead of the **amber**
"You're offline" one — which is the app behaving correctly for what
the browser is telling it.

**Reliable ways to actually flip `navigator.onLine` to `false`:**

1. **DevTools → three-dot menu → More tools → Sensors → Network → Offline** — this is the canonical control and fires the `offline` window event.
2. **Cmd+Shift+P → "Show sensors" → Network → Offline** — command-palette shortcut to the same place.
3. **DevTools → Application → Service Workers → "Offline" checkbox** — works within the SW scope; the `offline` event still fires for the page.
4. **Disconnect Wi-Fi for real.** Guaranteed to work; useful as a sanity check if any of the above behaves oddly on your Chrome version.

If you expect the amber banner and see the red one instead, switch to
one of the methods above and reload.

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
   `yourpeer-v2-static` and `yourpeer-v2-shell` exist
4. Inspect the shell cache: `yourpeer-v2-shell` should contain exactly
   one entry for `/chat` (the real app shell — `/` server-redirects to
   `/chat`, so we precache the post-redirect URL directly to avoid a
   redirect-tainted cache entry that browsers refuse to serve)
5. Inspect the static cache: `yourpeer-v2-static` should contain the
   manifest, favicon, and two icons
6. Use one of the offline-simulation methods from the "Simulating
   offline" note above, then reload. Page should still load (from
   cache).

**Pass criteria:** SW in "activated" state, both `yourpeer-v2-*` caches
populated as described, page loads when the browser is offline.

## Test 3: Cached results are shown when offline

**Why this matters:** this is the primary scenario the whole feature
exists for.

**Steps:**
1. Load the site with a working connection
2. Search for "food in Brooklyn" — wait for results to render
3. Trigger offline via one of the reliable methods from the "Simulating
   offline" note above (DevTools Network → Offline alone may leave
   `navigator.onLine === true` and trigger the red "Can't reach the
   server" banner instead of the amber one)
4. Reload the page (Cmd-R / Ctrl-R)

**Pass criteria:**
- Page loads (from SW cache)
- The last results are visible (rehydrated from Zustand localStorage)
- An **amber** banner appears with:
  - Primary text: "You're offline — results shown may be outdated." (or
    "You're offline." if no cached results are present)
  - Secondary text: "Messages you send will be delivered when you're
    back online." (or "N message(s) will send when you're back online."
    if the queue is non-empty)
- The input field is NOT disabled

**Note:** the banner previously showed "Last updated N minutes ago."
That wording was removed — the messages on screen come from Zustand's
localStorage persistence, not the IDB cache the timer was reading, and
the two can disagree substantially. The current banner tells the user
their view may be outdated without claiming to know when.

**If you see the red "Can't reach the server" banner instead:**
`navigator.onLine` is still reporting `true`. The app is doing the
right thing — that banner fires on `isOnline === true &&
backendStatus === "unreachable"`. Use a different offline-simulation
method (see the note at the top of this doc) and try again.

## Test 4: Messages queue while offline, flush on reconnect

**Why this matters:** users should be able to compose messages in a
dead zone and have them send when they get signal again.

**Steps:**
1. Load the site with a working connection
2. Start a conversation: "shelter in Manhattan" — let results load
3. Go offline (see the "Simulating offline" note at the top)
4. Type and send: "are any of them open now?"
5. Verify:
   - The user message appears in the chat immediately
   - A clock icon + "**Waiting to send**" indicator appears under the
     user message bubble (NOT a separate transient bot message — the
     status lives on the user's own message, WhatsApp-style)
   - A "Cancel" button appears next to the status indicator
   - In DevTools → Application → IndexedDB → keyval-store → keyval,
     find the `yourpeer:send-queue:v1` key — its **value** is a JSON
     array containing your message. Note: the queue is one key whose
     value grows, not one key per message.
   - **Click the refresh icon in the IDB panel** (circular arrow,
     top-right) — the viewer doesn't auto-poll, so new writes aren't
     visible until you refresh.
6. Send another message: "what about showers?"
   - Refresh the IDB viewer again. The `yourpeer:send-queue:v1` value
     should now be an array with 2 entries. Each corresponds to a
     "Waiting to send" message in the chat.
7. Go back online (Sensors → Online, or reconnect Wi-Fi)
8. Within a second or two, both messages should flush and produce
   real bot responses. As each one flushes:
   - The "Waiting to send" indicator changes to a sending check
   - Then to a sent double-check once the response arrives
9. After flush, the IDB queue should be empty

**Pass criteria:** all queued messages flush in order, each gets a
real response, queue empties, no duplicate messages appear in chat,
status indicators progress `pending` → `sending` → `sent`.

**Also verify Cancel works:** go offline again, send a message, click
Cancel while it's still "Waiting to send". The user's message bubble
should fade + strike through, the status should change to "Cancelled",
and the message should disappear from the IDB queue. When you come
back online, nothing should flush for that cancelled message.

## Test 5: Queued message expires after 1 hour

**Why this matters:** stale queued questions shouldn't silently send
hours later when context has changed.

**Steps:**
1. Go offline (see "Simulating offline" note above)
2. Send a message — verify it queues in IndexedDB
3. In DevTools → Application → IndexedDB, **manually edit** the
   `queuedAt` timestamp for the queued message to be more than 1 hour
   in the past (e.g., current ms - 3700000)
4. Go back online

**Pass criteria:** the expired message is NOT sent. Instead:
- The user's own message updates to the "Not sent" status
  (warning-triangle icon, red)
- A transient bot message appears: `Some messages were waiting too
  long and weren't sent. Feel free to ask again: "first 40 chars of
  the expired message…"` — the user can re-ask if still relevant.

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

**Pass criteria:** a transient bot message appears: `You were offline
for a while — starting a fresh conversation.` The queued message is
still sent, but the user is warned the conversation context is gone.

The prior session's results, if any, are offered back via the "See
earlier results" link on the welcome screen — tapping restores the
snapshot; dismissing hides it. The link disappears the moment the
user types a new query (so it doesn't linger against an unrelated
conversation).

## Test 7: Service worker updates on deploy

**Why this matters:** when a new version of the app ships, users
shouldn't be stuck on the old version.

**Steps:**
1. Load the site; note the SW version in DevTools → Application →
   Service Workers. The cache prefix should be `yourpeer-v2-` in the
   current build.
2. Bump `CACHE_VERSION` in `public/sw.js` (e.g., `"v2"` → `"v3"`)
3. Rebuild + restart (`npm run build && npm run start`)
4. Load the site again in a new tab

**Pass criteria:** in Application → Service Workers, a new SW is
"waiting to activate." After a full refresh, it activates, old
caches (with the v2 prefix) are deleted, new caches (v3 prefix)
appear. Existing users hitting the deploy follow the same path — the
activate handler purges any cache whose name doesn't match the
current version prefix, so upgrades are clean even if the prior
version's caches were in a broken state.

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
- **DevTools Network → Offline ≠ `navigator.onLine === false`:** see
  the "Simulating offline" note at the top. This is the single most
  common source of confusion when testing this feature manually.
- **IndexedDB viewer does not auto-refresh.** The panel takes a
  snapshot when opened and doesn't poll. After any write (enqueue,
  cancel, flush), click the refresh icon (circular arrow, top-right
  of the IDB viewer) or re-open the database node to see current
  state. A "nothing is queueing" diagnosis is almost always a stale
  IDB viewer — verify against the "Waiting to send" status under the
  user's message bubble first; that updates live.


## See also

For the **closed-tab** scenarios that this doc cannot cover from
DevTools — Background Sync drain, SW response reconciliation, reset-
during-drain race — see `docs/TESTING_BACKGROUND_SYNC.md`. That
runbook requires a real Android device with USB debugging.
