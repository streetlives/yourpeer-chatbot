# Background Sync — device QA runbook

This is a device-QA checklist for the Background Sync feature added in
PR #77. It complements `docs/TESTING_OFFLINE.md`, which covers
in-browser DevTools testing of the offline queue. Background Sync
requires a **real device** because the behavior under test — drain a
queued message while the tab is closed — cannot be reproduced with
DevTools alone.

Run this checklist on at least one real device before merging changes
that touch `public/sw.js`'s `sync` handler, `src/lib/chat/send-queue.ts`,
or `src/lib/chat/pending-responses.ts`.

## Why a separate runbook

DevTools can simulate offline, simulate poor connectivity, and pause
the SW. It can't simulate the OS killing or freezing a backgrounded
tab while still allowing the SW to wake on network change. That last
case is the ONLY one Background Sync exists to handle — if you can
flush messages from a foregrounded tab, the existing client-side
`online` handler in `use-chat.ts` already covers it. So the test that
matters most is:

> Type while offline → close the tab → restore connectivity → reopen
> the app and verify the response is present.

If that doesn't work, Background Sync isn't doing anything we don't
already get from the client-side flush, and we shouldn't ship it
claiming the wins it's supposed to provide.

## Required hardware and accounts

- Android device running Chrome 80+. Samsung Internet 11+ or Edge
  on Android also works. **iOS does not implement Background Sync at
  all** — Safari/Firefox iOS will fall back to the client-side flush
  and that path is covered by `TESTING_OFFLINE.md`. iOS testing for
  this feature is marking the fallback path works, not the SW path.
- A staging or test deployment with the new SW deployed (the SW must
  be served over HTTPS or `localhost` — Background Sync is gated on
  secure context). Production is fine if you don't mind one anonymous
  test session in the audit log.
- Ability to put the device on real cellular AND Wi-Fi. Airplane mode
  is a poor substitute for some of the tests below — it disables the
  radio entirely and the OS treats it differently from "no signal."

## Tests

### Test BS-1: SW upgrade lands cleanly

**Why this matters:** the new SW must take over from any prior SW
without users having to manually clear site data. If this fails,
nothing else in the runbook will produce reliable results.

**Steps:**

1. With the device on Wi-Fi, open the app in Chrome.
2. Pull-to-refresh once to ensure the latest HTML loads.
3. Open `chrome://inspect` from a desktop Chrome (USB debugging
   enabled on the device), navigate to the device's tab.
4. In the inspected DevTools → Application → Service Workers:
   - Confirm the active SW source URL ends in the deployed SHA or
     timestamp.
   - Confirm `Cache Storage` lists caches named `yourpeer-v4-...`
     (the cache version constant in `sw.js`).
5. Look at the SW status badge. It should say `activated and is
   running`. If it says `waiting to activate`, the previous SW is
   still controlling the tab — wait for the user to close all tabs
   for this origin or trigger `skipWaiting()` from DevTools.

**Pass criteria:** new SW is active, old caches are gone, no console
errors during activation.

### Test BS-2: Queue persists across tab close (foundational)

**Why this matters:** Background Sync is meaningless if the queue
itself doesn't survive a tab kill. This test is the prerequisite for
everything below — if it fails, the queue or IDB persistence has
regressed.

**Steps:**

1. Load the app on Wi-Fi. Send and receive one message normally so a
   session is established.
2. Switch the device to airplane mode.
3. Send a message: "shelter in the bronx tonight". Verify the
   "Waiting to send" indicator appears under your bubble.
4. Force-stop the Chrome tab: swipe it away from the recent-tabs
   view, then open Chrome again from the launcher (a fresh tab,
   navigate to the app URL).
5. While still in airplane mode, observe that:
   - The queued message is still visible in the chat with its
     "Waiting to send" indicator.
   - In `chrome://inspect` → device tab → DevTools → Application →
     IndexedDB → `keyval-store` → `keyval`, the
     `yourpeer:send-queue:v1` key contains the queued entry.

**Pass criteria:** queue contents survive the tab swipe; the user-
visible status survives too.

### Test BS-3: SW drains the queue with the tab CLOSED (the headline test)

**Why this matters:** this is the entire feature in one test. Without
this working, Background Sync is a no-op.

**Steps:**

1. Load the app on Wi-Fi. Send and receive one message normally.
2. Switch the device to airplane mode.
3. Send a message: "food in queens this saturday". Confirm "Waiting
   to send" appears.
4. **Close the Chrome tab entirely** — swipe it away from the recent-
   tabs view. Do NOT reopen the app yet.
5. Wait 10 seconds (lets the OS settle the tab as fully closed, not
   just backgrounded).
6. Turn airplane mode OFF.
7. Wait 60 seconds. The browser usually fires the sync event within
   a few seconds of connectivity, but allow time for the OS scheduler
   and the network handshake.
8. Reopen the app from the launcher (NEW tab, not "Recents").
9. Observe:
   - The originally-queued user message is in the chat with a SENT
     indicator (single grey check or whatever your status icon set
     uses for "delivered").
   - **A bot response appears in the chat directly under it** —
     this is the response the SW fetched and stored to
     `yourpeer:pending-responses:v1` while the tab was closed, then
     reconciled into the chat on mount.
10. In `chrome://inspect`, verify:
    - `yourpeer:send-queue:v1` is empty (drained).
    - `yourpeer:pending-responses:v1` is empty (reconciled).

**Pass criteria:** the queued message has a SENT indicator AND the
bot response is present in the chat — both of these together. If
only the first is true, the SW is reaching the server but the
client isn't reconciling. If neither is true, the SW didn't fire.

**If it fails:** check `chrome://serviceworker-internals` (typing
that into the device's Chrome address bar). Find the entry for the
app's origin and look for a recent "sync event dispatched" log entry.
If you see "sync registered but never fired", the OS didn't grant
the wake-up — possible causes include aggressive battery saver,
data saver mode, or the user having force-stopped the app very
recently (some Android variants treat that as "user doesn't want
this app running" and suppress sync events for a cooldown period).

### Test BS-4: Idempotent retry — server doesn't double-process

**Why this matters:** the client-side flush and the SW-side drain
both run the same queue. If the user reopens the app DURING a SW
drain, both could POST the same message. The server-side X-Request-ID
dedupe in `backend/app/services/idempotency.py` is supposed to prevent
that from causing a duplicate response — verify it actually does.

**Steps:**

1. Load the app on Wi-Fi.
2. Switch to a flaky/poor connection. The simplest reliable way: turn
   on Wi-Fi but disconnect it from the router (so the device thinks
   it's connected but no traffic flows). On most Android devices,
   tap-and-hold the Wi-Fi quick-tile, "Forget" the network, then
   reconnect — there's a short window where the radio is up but
   network is down.
3. Send a message; it should fail with a network error and queue.
4. While in this state, foreground the app. The client-side flush
   will also try to drain.
5. Restore real connectivity (reconnect Wi-Fi).
6. Observe the chat — there should be exactly ONE bot response for
   your message, regardless of how many drain paths happened to fire.

**Pass criteria:** one bot reply for one user message. Look at the
network panel in `chrome://inspect` — you may see two POST attempts
to `/api/chat`, but the second should return with the
`X-Idempotency-Replay: true` response header set by the server.

### Test BS-5: Reset-during-drain doesn't inject orphan messages

**Why this matters:** PR #77 added a reset-epoch mechanism to close
a race where the SW could deliver a response for a since-reset
session, and the client could inject it as an orphaned bot message.
This is the test that exercises that race.

**Steps:**

1. Load the app on Wi-Fi. Send a message and let it complete so a
   session is established.
2. Switch to airplane mode.
3. Send a second message: "shelter in queens".
4. Disable airplane mode (let the SW start its drain).
5. Within 1-2 seconds (while the SW POST is in flight), tap the
   menu and select "Start over" / "New conversation".
6. Wait 10 seconds.
7. Refresh the chat or close-and-reopen the tab.
8. Observe:
   - The chat shows ONLY the welcome message and any messages the
     user has typed since reset.
   - There is NO bot reply to "shelter in queens" — that response
     was for the pre-reset session and should have been dropped.

**Pass criteria:** no orphaned bot messages after reset. The chat
state should look exactly as if the user had reset before any
network activity.

**If you see an orphan message:** check `yourpeer:reset-epoch:v1` in
IDB — it should contain the timestamp of the reset. Then look at
`yourpeer:pending-responses:v1` for any entries with `queuedAt <
resetEpoch` — those should have been dropped by `classifyPending`
(see `frontend-next/scripts/verify/pending-responses.mjs`, runnable
via `npm run verify:pending`, for the unit-level test of this
logic). If `queuedAt` is missing on the entry, it was written by an
older SW — wait an hour for TTL, then retest.

### Test BS-6: PII redaction at enqueue (queue contents)

**Why this matters:** PR #77 added client-side PII redaction at
enqueue so that user-typed phone numbers, SSNs, addresses, etc.
don't sit in IDB in cleartext. Verify that what's stored on disk
matches what the user typed only AFTER redaction.

**Steps:**

1. Switch the device to airplane mode.
2. Type a message containing fake PII: `my number is 555-123-4567,
   my email is test@example.com, looking for shelter in brooklyn`.
3. Send it. The chat UI should show the message **as you typed it**
   (with the phone number and email visible — that's intentional;
   the user sees their own typing).
4. In `chrome://inspect`, open IndexedDB → `keyval-store` → `keyval`,
   find `yourpeer:send-queue:v1`, expand the entry's `text` field.
5. Confirm the stored text reads `my number is [PHONE], my email is
   [EMAIL], looking for shelter in brooklyn` — not the original.

**Pass criteria:** chat UI shows original; queue stores redacted.

**Note:** the chat-history persisted in `localStorage` (zustand
persist) under key `yourpeer-chat` ALSO contains the user's original
text. That's a known gap — see `docs/design/PRESIDIO_MIGRATION_PLAN.md`
for the broader initiative to redact display-layer storage too. This
PR scopes redaction to the offline queue only.

### Test BS-7: iOS / Safari fallback (negative test)

**Why this matters:** Background Sync is Chromium-only. iOS and
Firefox should fall back to the client-side flush in
`use-chat.ts::flushQueue`, which works fine while the tab is alive
but cannot drain a closed tab. The fallback path must NOT crash or
throw.

**Steps (iOS Safari):**

1. Load the app on iPhone Safari.
2. Switch to airplane mode.
3. Send a message; verify it queues with "Waiting to send".
4. Disable airplane mode WHILE THE TAB IS STILL OPEN.
5. The queue should drain via the client-side `online` handler,
   not the SW. Bot reply appears.
6. Now repeat steps 2–3, but close the Safari tab before disabling
   airplane mode. Then reopen the tab.
7. The queued message should still be there (queue persists across
   tab close), and the client-side flush should drain it on reopen.

**Pass criteria:** no errors in Safari Web Inspector console; queue
drains via the client path; the SW's `tryRegisterBackgroundSync`
call in `use-chat.ts` no-ops cleanly.

**Known not-supported:** iOS will NOT drain a closed-tab queue
without the user reopening the app. That's a Safari limitation, not
a YourPeer bug. Do not file a bug report unless Apple ships
Background Sync support.

## Sign-off

When all of BS-1 through BS-6 pass on at least one Android device,
and BS-7 passes on at least one iOS device, the feature is cleared
for release. Record the device model, OS version, Chrome version,
and tester initials in the PR before merging.

If any test fails, see the "If it fails" notes inline above; if
those don't help, re-run with `chrome://inspect` open and capture
the SW console output for the failing test.
