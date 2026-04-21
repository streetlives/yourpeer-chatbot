# YourPeer Offline & Delivery UX — Design Document

**Status:** Draft for review
**Author:** Engineering
**Date:** April 2026
**Audience:** Streetlives leadership, engineering, data stewardship, pilot partners

---

## 1. Problem statement

YourPeer serves people looking for shelter, food, showers, clothing,
health care, and other services in NYC. The target user often has an
unreliable connection — prepaid data that runs out, shelter WiFi that
drops, phones that were free or damaged, dead zones in the subway.
They're frequently in crisis or near-crisis when they reach the app.

Before this work, the app dead-ended offline:

- When the browser went offline, the input was disabled and a red
  banner said "You're offline — service search is unavailable." No
  messages could be composed, let alone queued.
- If the browser came back online, there was no mechanism to replay
  a message the user had been trying to send.
- Session expiry (30 minutes) silently wiped the user's last search,
  so someone who found a shelter, lost signal for 45 minutes, and
  reopened the app lost the address they'd been trying to hold onto.
- A network flap between server and client during a response would
  cause a retry to run the full LLM pipeline twice, charging Anthropic
  twice and producing confusing duplicate replies.

The design goal: **a user in a dead zone trying to find a shelter
should never be blocked by the app, and should never be confused
about what has or hasn't actually happened on their behalf.**

---

## 2. Solution overview

Four coupled pieces of functionality, shipped together:

| Feature | What it does |
|---|---|
| **PWA offline support** | Service worker caches app shell; cached results visible offline; messages queue locally and flush on reconnect |
| **Per-message delivery status** | WhatsApp-style tick indicators under each user message; inline Cancel button for pending queued messages |
| **Session-reset results preservation** | When a 30-min session expires, the last result set is snapshotted; welcome UI offers a "See earlier results" link |
| **Backend idempotency** | Chat endpoint dedupes retries with the same `X-Request-ID` within 60s so a lost-response retry doesn't cost a second LLM call |

They're coupled because they share state (idempotency keys flow
through the queue; status transitions depend on the queue lifecycle;
session reset needs to coordinate with the queue) and because
shipping any subset would leave observable holes (queueing without
status = confusing UX; status without idempotency = double-sends).

---

## 3. Reasoning behind key design decisions

### 3.1 Why a PWA, not a native app

A native app would theoretically give us richer offline capability
(background sync, push notifications, persistent storage guarantees).
We chose PWA anyway:

- **Distribution:** Users can reach YourPeer through a URL — no
  App Store, no Play Store, no install friction. For someone in
  crisis seeking shelter tonight, a working URL beats an install
  flow every time.
- **No signing / review cycles:** Shipping a fix to the field is
  hours, not days.
- **No account required:** Same pattern as the existing web app.
- **iOS caveat:** Safari PWA support has real limitations
  (IndexedDB can be wiped under storage pressure, standalone mode
  has layout quirks). Accepted.

### 3.2 Why a hand-written service worker, not Workbox/Serwist

Workbox would add ~30KB of code and additional dependencies. Our
caching rules are ~170 lines total:

- Cache-first for `/_next/static/` (hashed Next.js assets)
- Network-first for HTML (app shell)
- Bypass `/admin/*`, `/api/admin/*`, `/api/chat/*`, `/api/health`
- Precache `/`, `/manifest.webmanifest`, `/icons/*`

Framework would solve problems we don't have. If we later need
Workbox features (background sync, advanced precaching), migrating
forward is straightforward.

### 3.3 Why enable the input when offline

The previous behavior disabled the send button when offline. This
is hostile UX for the target user: someone in a dead zone can't
even type their question, so they lose their train of thought by
the time signal returns.

With the queue, the input stays active. A WhatsApp-style clock
indicator under the user's message signals "waiting to send." When
signal returns, the queue auto-flushes. The user never has to
notice the transition.

### 3.4 Why timeouts are NOT network errors

`AbortSignal.timeout()` firing means the server was slow, not that
the user was offline. Telling a user "I'll send this when you're
back online" while they're clearly online would be confusing and
contradict visible state.

Timeouts flow to the normal error path ("taking longer than
expected — try again"). Only genuine network failures
(`TypeError: Failed to fetch`, plus a `navigator.onLine === false`
ground-truth check) trigger the queue.

### 3.5 Why a 1-hour queue TTL and 50-message cap

After an hour, "what food is near me?" is probably no longer the
question the user wants answered. They've moved, changed context,
or given up. Silently sending a stale question from an hour ago is
worse than dropping it with an explanation.

The 50-message cap is a safety valve against pathological patterns
(e.g., a user repeatedly tapping send on a slow device, not seeing
any feedback because they're offline). Past 50 entries, new sends
are rejected with a soft retryable error rather than silently
accepted.

### 3.6 Why a 24-hour results cache TTL

Service hours, availability, and eligibility can change. 24 hours
is the point where "maybe it's still accurate" starts tipping
toward "probably not." Past 24 hours, the user needs a fresh query.
The banner always says "may be outdated" regardless — we don't
display a specific staleness number because the chat log they see
comes from localStorage (via Zustand persist), not from the IDB
results cache, and the two can disagree substantially.

### 3.7 Why WhatsApp-style status indicators

Users have a strong mental model from messaging apps. Adopting the
same language (single tick, double tick, clock, warning) gets us
comprehension for free:

- Single faint check = "sending" (request in flight)
- Double check = "sent" (server acknowledged)
- Clock + "Waiting to send" = "pending" (queued while offline)
- Warning triangle + "Not sent" = "failed" (surface the error)
- X + "Cancelled" = user pulled it back

The alternative — a separate bot message saying "Saved, I'll send
this when you're back online" — clutters the chat and doesn't
clearly belong to any particular user message. Placing the status
directly under the user bubble ties the state to the thing it
describes.

### 3.8 Why Cancel only appears on pending messages

A user whose message is mid-flight (`sending`) can't meaningfully
cancel; the server is already processing. A `sent` message is done.
Only `pending` has a real cancel semantic ("yank from the queue
before it flushes"). Showing a non-functional Cancel would be
worse than no button.

### 3.9 Why session-reset preserves results

The target scenario: a user finds a shelter, loses signal, reopens
the app 45 minutes later. Their 30-min session expired, so the
chat gets wiped. Without preservation, they lose the shelter's
address they were trying to remember.

With preservation, the welcome screen shows a subtle "See earlier
results" link. Tapping restores the snapshot with a prefix ("Here
are the services you were looking at before"). Dismiss discards.
The snapshot has no server-side dependency — it's pure localStorage
— so it works even if the user is still offline.

### 3.10 Why a 60-second idempotency TTL

Short enough that a user who accidentally resends a similar message
minutes later doesn't get a surprising "why did the bot repeat
itself?" response (they have a fresh request ID anyway, so no cache
hit). Long enough to cover realistic retry windows: network flap,
reconnect delay, queue flush.

The alternative — longer TTL with content-hash keys — would be
more forgiving but would add complexity without solving a real
problem at pilot scale.

### 3.11 Why the idempotency cache lives in memory

Matches the existing pattern for `session_store.py` and
`rate_limiter.py`. The backend is a single-process deployment on
Render Starter tier. Redis would be the drop-in replacement if we
scale horizontally.

### 3.12 Why position-based retry-status lookup

Earlier iterations used a text-match heuristic to identify the
original failed user message when a retry succeeded. This failed
in edge cases: if the user had a failed text message followed by
a failed geo message, clicking retry on the geo error would
incorrectly match the text message (because geo uses trigger
tokens like `__use_geolocation__`, not visible text).

The current approach: when retry fires, find the user message
immediately preceding the error bubble in chronological order.
This is exact, not heuristic, and handles duplicate messages,
stacked errors, and non-literal trigger text correctly.

---

## 4. Architecture

### 4.1 Client components and state

```
┌────────────────────────────────────────────────────────────┐
│                      ChatContainer                         │
│  - reads: messages, isLoading, cacheAge, queueDepth,       │
│           lastResultsBeforeReset                           │
│  - renders: OfflineBanner, EarlierResultsLink, ChatMessage │
└───────────┬──────────────────────────────────┬─────────────┘
            │                                  │
            ▼                                  ▼
    ┌───────────────┐                 ┌─────────────────┐
    │  useChat hook │                 │useOfflineState  │
    │ - send()      │                 │ - queueDepth    │
    │ - retry()     │                 │ - cacheAge      │
    │ - cancelQueued│                 │ (event-driven)  │
    │ - flushQueue  │                 └────────┬────────┘
    └───┬──────┬────┘                          │
        │      │                               │
        ▼      ▼                               ▼
┌──────────-─┐ ┌────────────────┐     ┌────────────────┐
│useChatStore│ │   send-queue   │     │ offline-cache  │
│(Zustand +  │ │ (IDB + events) │     │ (IDB + events) │
│ persist)   │ │ - enqueue/deq  │     │ - last results │
│- messages  │ │ - TTL, cap     │     │ - TTL          │
│- status    │ │                │     │                │
│- snapshot  │ │                │     │                │
└────────────┘ └────────────────┘     └────────────────┘
                                             │
                                             ▼
                                    ┌────────────────┐
                                    │ Service Worker │
                                    │  (sw.js)       │
                                    │ - cache-first  │
                                    │ - network-first│
                                    └────────────────┘
```

**Key state surfaces:**

- **Zustand store** (persisted to localStorage) holds chat messages,
  session ID, and the `lastResultsBeforeReset` snapshot. Messages
  include `status` and `requestId` fields.
- **IndexedDB send queue** holds messages awaiting delivery. Each
  entry is `{ id, text, coords, sessionId, queuedAt, requestId }`.
- **IndexedDB results cache** holds the last bot message with
  service cards (24-hour TTL, distinct from the Zustand layer).
- **Custom DOM events** (`yourpeer:queue-change`,
  `yourpeer:cache-change`) notify `useOfflineState` subscribers
  when either IDB store mutates, so UI updates immediately rather
  than waiting on a polling interval.

### 4.2 Message lifecycle (happy path, online)

```
User types → send() → addMessage(status=sending) →
  sendChatMessage(requestId) → response →
  updateMessage(status=sent) + addMessage(botReply) +
  cacheIfResults()
```

### 4.3 Message lifecycle (offline → online)

```
User types offline → send() → addMessage(status=sending) →
  fetch fails → handleNetworkError() →
  isNetworkError ? enqueue() + updateMessage(status=pending) → ✋

[...time passes, user goes online...]

online event → flushQueue() → reapExpired() →
  readQueue() → for each entry:
    check cancelled? → if yes, dequeue+continue
    updateMessage(status=sending) →
    sendChatMessage(queued.requestId) →
    check cancelled again? → if yes, dequeue+continue
    updateMessage(status=sent) + dequeue() + addMessage(botReply)
```

### 4.4 Message lifecycle (user cancels)

```
Message at status=pending in queue
User taps Cancel → cancelQueued(id) →
  dequeueMessage() + updateMessage(status=cancelled)

[Flush, if currently running, guards against race:]
- First check before marking "sending" skips this entry
- Second check after response arrives still catches cancels that
  happened mid-flight
- updateMessage refuses to change status on cancelled messages
  (defense in depth against any missed race window)
```

### 4.5 Idempotency flow

```
Client: sendChatMessage(text, session, coords, requestId)
        ↓ X-Request-ID header
Server: check idempotency cache by request_id
        → hit: return cached body (X-Idempotency-Replay: true)
        → miss: generate_reply(), cache response, return

Queue flush: sendChatMessage uses queued.requestId (stable)
  → If original response was cached server-side, client gets it
    without re-running LLM
  → If original didn't reach server, normal processing

Retry button: uses originalFailedMsg.requestId (stable)
  → Same dedupe benefit
```

---

## 5. Functionality covered

### 5.1 PWA / offline

- ✅ Service worker caches app shell; reload-while-offline works
- ✅ Install-as-app manifest with icons, theme color, standalone mode
- ✅ Cached service results still visible when returning offline
- ✅ Amber offline banner (distinct from brand yellow)
- ✅ Input stays enabled when offline
- ✅ Messages typed offline queue locally (IDB, 1-hour TTL,
  50-message cap)
- ✅ Queue auto-flushes on `online` event (not just on page load)
- ✅ Flush is serial — conversational context isn't scrambled
- ✅ Admin pages bypass the SW entirely (never serve stale admin data)

### 5.2 Delivery status

- ✅ Five states: `sending`, `sent`, `pending`, `failed`, `cancelled`
- ✅ Status renders under user bubble with icon + accessible label
- ✅ Status transitions work for normal send, geo flow, crisis flow,
  retry, retry-geo, and queue flush
- ✅ Cancel button only appears on `pending` messages
- ✅ Cancelled messages get visual fade + strikethrough but remain in
  chat (audit trail for the user's own action)
- ✅ Cancel/flush race protected by two status checks + a
  terminal-state guard in `updateMessage`

### 5.3 Session reset preservation

- ✅ Last bot message with service cards captured on reset
- ✅ "See earlier results" link in the welcome screen
- ✅ Restore prepends a clarifying prefix ("Here are the services
  you were looking at before")
- ✅ Dismiss discards without restoring
- ✅ Snapshot survives reload (persisted, schema v2 with migration)

### 5.4 Backend idempotency

- ✅ 60-second TTL, 1000-entry cap, in-memory with threading.Lock
- ✅ Cache hit returns `X-Idempotency-Replay: true` header
- ✅ Only success responses are cached; errors re-run
- ✅ Client threads stable request IDs through queue flushes and
  retry button (retry reuses original ID when present)
- ✅ Zero new backend dependencies

### 5.5 Robustness fixes (bugs found during design)

Ten bugs were identified in successive audit passes and fixed in
place before this document was written:

- `resetChat` now clears IDB queue + cache (was leaving stale data)
- `isNetworkError` excludes `TimeoutError`/`AbortError` and uses
  `navigator.onLine` as ground truth
- Queue capped at 50 entries with `{accepted: bool}` return
- `cacheIfResults` rejects messages with `retryMessage` set
- Event-driven UI refresh replaces 30s polling
- All 5 error paths (normal, geo, crisis, retry, retry-geo) go
  through the same `handleNetworkError` helper
- Offline banner no longer shows misleading "Last updated N minutes
  ago" — actual visible messages come from localStorage, not the
  IDB cache the timer was reading
- Crisis step-down doesn't queue on network error (bare "Yes,
  search" trigger has no meaning without session context)
- Cancel/flush race: live-state check twice per iteration + terminal
  guard in `updateMessage`
- Retry success: position-based lookup correctly updates the
  original failed user message's status
- Geo + crisis flows track their own user message ID and idempotency
  key (earlier builds orphaned these from status transitions)
- Retry uses the original message's requestId for idempotent dedupe

---

## 6. Known limitations

### 6.1 No unit tests

The frontend doesn't have a test framework set up. `offline-cache.ts`,
`send-queue.ts`, `idempotency.py`, and the `updateMessage` terminal-
state guard are all testable pure logic that would benefit from unit
coverage.

**Recommendation:** separate PR to set up vitest (frontend) and add
targeted tests.

### 6.2 iOS Safari caveats

- IndexedDB can be wiped by the OS under low-storage pressure. A user
  who backgrounds the app for weeks, then comes back, might find
  their queue empty.
- Standalone mode has layout quirks that may require testing on real
  devices before declaring iOS support.
- No Background Sync API — we rely on the `online` event, which
  requires the page to be open.

**Recommendation:** manual testing on a real iOS device before
declaring iOS-ready. Add a note in user-facing copy that the app
"works best with the page open."

### 6.3 No idempotency cache persistence across restarts

The idempotency cache is in-memory. If the backend restarts within
60 seconds of a client flushing a queued message, the retry will
miss the cache and re-run the LLM.

**Rationale:** backend restarts are rare and usually expected
(deploys). Accepting the occasional duplicate LLM call is cheaper
than maintaining Redis for this one cache.

**Recommendation:** revisit if we scale out to multiple workers
(Redis becomes necessary for sessions/rate-limiting anyway, at
which point idempotency cache tags along).

### 6.4 `lastResultsBeforeReset` persists until explicitly dismissed

If the user resets, doesn't click either "See earlier results" or
"Dismiss," and returns days later without triggering another reset
(e.g., within the 30-min session TTL), the stale link is still
there. The snapshot data inside is potentially stale.

**Rationale:** we chose this over a TTL because "here's what you
were looking at" survives brief absences gracefully. A TTL would
add complexity without a clear winning threshold.

**Recommendation:** consider a 24-hour snapshot TTL if usability
testing surfaces confusion.

### 6.5 Request ID persistence in localStorage

User messages persist their `requestId` to localStorage (via Zustand
persist). A user with a very long-open session could have a 60s-old
request ID sitting in their chat history when they click Retry. The
server-side idempotency entry has expired by then, so the retry
runs fresh anyway. No bug, but worth noting the coupling.

### 6.6 Cancel does not interrupt in-flight server work

If the user cancels during the `sending` state (server is actively
processing), we honor the cancel client-side (don't show the bot
response) but the server-side work completes. The response is
cached in idempotency for 60 seconds but nothing reads it.

**Rationale:** interrupting server-side work would require server
cooperation (WebSocket or similar). Sunk cost is acceptable;
client-side semantics match the user's intent.

### 6.7 No indicator that the queue is actively flushing

When connection returns and the queue has, say, 5 messages, the
user sees status transitions pending → sending → sent for each one,
but no overall "flushing 5/5" indicator. On a slow connection, this
could feel choppy.

**Rationale:** per-message state is clearer than aggregate progress
for this use case. A user watching their pending messages turn into
sent ones understands what's happening.

**Recommendation:** if usability testing shows confusion, add a
"sending 3 queued messages..." header bar during a flush.

### 6.8 Single pre-existing lint error unfixed

`chat-container.tsx:81` — React 19's `react-hooks/set-state-in-effect`
rule flags the Zustand hydration pattern that existed before this PR.
Not caused by this work; tracked in the separate pre-existing-lint-fix
PR. Not blocking merge.

---

## 7. Unaddressed gaps and what we defer

### 7.1 No telemetry

We ship without instrumentation for:
- How often the offline banner fires per session
- Cache hit rate (did users actually come back and look at old results?)
- Queue flush depth and age distribution
- Idempotency cache hit rate on the backend
- Cancel button click-through

**Why defer:** telemetry adds complexity and privacy implications
for a vulnerable user population. We want to land functionality
first and instrument after seeing clear questions that need answers.

**Cost of deferring:** we can't tell whether the queue is solving a
real problem or theater. We'll be flying partially blind for the
pilot.

**Recommended follow-up:** a minimal, privacy-respecting metrics
pipeline (count-only, no content, no identifiers) before scaling
beyond the pilot.

### 7.2 No push notifications for flush completion

If the user queues a message, closes the browser, and the flush
completes in the background, they won't know. Background Sync + push
notifications would handle this but iOS Safari support is unreliable.

**Why defer:** the primary scenario is "user in dead zone, then
back online with the page still open." Background-while-closed
delivery is a secondary scenario.

### 7.3 No conflict resolution for multiple devices

If a user has the app open on two devices, they each have their own
Zustand store and IDB state. Messages from one don't appear on the
other.

**Why defer:** this has always been true; not regressed by this PR.
Solving it requires server-side conversation persistence, which is
out of scope.

### 7.4 Service cards don't indicate cached vs. fresh

When a user sees service results after coming back online, they
don't know if those results are from a fresh search or from the
cache. The banner says "may be outdated" but individual cards
don't have per-card staleness.

**Why defer:** complicates the UI for marginal benefit. The banner
at the top is sufficient cue.

### 7.5 No way to clear the cache manually

If a user wants to force-refresh cached results, they can't. The
app provides no "clear cache" button. They have to wait for the
24-hour TTL or clear browser storage.

**Why defer:** edge case. Most users won't notice or need this.

### 7.6 No Spanish / multilingual support in the offline banner

The offline banner, status labels ("Sent", "Waiting to send",
etc.), and the earlier-results link are all English-only. The rest
of the app is also primarily English, so this isn't a regression,
but it's a gap.

**Why defer:** full multilingual support is a separate, larger
effort.

### 7.7 No fine-grained accessibility audit

We've used `aria-label` on status indicators and buttons, and
`role="status"`/`role="alert"` on banners. No screen-reader audit
has been done.

**Recommendation:** axe-core in CI as a follow-up PR.

### 7.8 Service worker can't serve cached backend API responses

We cache the app shell but not the chat API responses themselves
(because they're POSTs and vary by input). A user who reloads the
page offline sees the welcome screen, not their most recent
conversation. Their chat history is in localStorage (via Zustand
persist), so messages DO reappear; it's just that the route through
the SW is shell-only, not API-aware.

**Why this is fine:** the shell + localStorage combination covers
the core user value.

### 7.9 No rate limiting on the Cancel button

A user rapidly tapping Cancel fires multiple `cancelQueued` calls.
Each is idempotent (dequeue is safe on already-dequeued; the
terminal-state guard on updateMessage prevents status regression),
so it's harmless, but IDB writes happen in a storm.

**Why defer:** the impact is negligible and fixing it adds
complexity.

### 7.10 Cached bot message on restore has stale quick replies stripped, but preserves stale services

When the user taps "See earlier results," the restored message has
`quick_replies: undefined` (we strip them because they were
contextual to the old session state) but `services` is preserved.
If any of those services have since closed or changed hours, the
restored results are stale.

**Why accept this:** the user explicitly asked to see earlier
results. We labeled them "earlier." The alternative — block restore
unless we verify freshness — would require a backend call that
might not be available offline, and would defeat the feature.

---

## 8. Validation strategy

### 8.1 What has been validated

- **TypeScript:** clean on all new/modified code
- **ESLint:** clean on all new/modified code (one pre-existing
  error on `chat-container.tsx:81` tracked separately)
- **Manual test plan:** `TESTING_OFFLINE.md` covers 8 scenarios
  via Chrome DevTools → Network → Offline

### 8.2 What has not been validated

- **Automated test coverage:** no unit tests; no integration tests
- **Real-device iOS testing:** not performed
- **Real-device Android testing:** not performed
- **High-concurrency backend testing:** idempotency cache hasn't
  been load-tested
- **Accessibility (screen reader / keyboard-only):** not audited
- **Usability testing with target population:** not performed
- **Production telemetry:** none

### 8.3 Recommended pre-pilot validation

1. Manual test on a real iPhone (Safari standalone mode)
2. Manual test on a real Android (Chrome)
3. axe-core scan for accessibility regressions
4. Internal usability session with 2–3 people not involved in the
   development, using the 8 scenarios in TESTING_OFFLINE.md

---

## 9. Rollout and rollback

### 9.1 Rollout

- Single PR merge deploys all four features together
- No feature flag — the components are intertwined enough that
  flagging individual pieces is more complex than the value gained
- Backend change is backward-compatible (clients without idempotency
  support simply never populate the cache)
- Client change requires a browser refresh to pick up the new SW
  and the updated Zustand schema. Existing localStorage data is
  migrated to v2 via the `migrate` function.

### 9.2 Rollback

- **Backend:** `chat.py` revert is safe — idempotency cache is
  in-memory, wiping it on deploy has no downstream effect
- **Frontend:** revert the PR. Users with queued messages in IDB
  will find their messages stranded (next send would produce a
  chat with them visible as `status: "pending"` but no flush logic
  to drain). If we need to roll back urgently, a follow-up client
  push that clears IDB on boot would be needed.
- **Schema migration:** v2 → v1 is not reversibly supported. A user
  who rehydrates v2 state into a v1 client would lose
  `lastResultsBeforeReset` (dropped as unknown field) but otherwise
  function normally.

---

## 10. Open questions for review

1. Do we want a feature flag despite the complexity? If so, we'd
   probably flag the backend idempotency cache separately (trivial)
   and ship the client features as one block.
2. Should `lastResultsBeforeReset` have its own TTL, or is
   persist-until-dismissed the right default?
3. Is the "no aggregate flushing indicator" acceptable for pilot,
   or should we add a header bar during drain?
4. How aggressively should we evict the idempotency cache? 60s is a
   starting point — monitoring in production would tell us whether
   to raise or lower.
5. When icons ship, should we gate the install prompt on their
   presence, or is "broken install icon" acceptable interim UX?
6. Is the 50-message queue cap right? Too low (users hit it) or too
   high (pathological cases could still fill it)?

---

*End of design document.*
