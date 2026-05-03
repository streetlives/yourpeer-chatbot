# YourPeer AI Chat as a Progressive Web App (PWA)

## What It Is, Why It Matters, and How to Use It

**Written for:** Streetlives leadership, outreach workers, partner organizations
**Date:** May 3, 2026

---

## In plain language: what is a PWA?

A Progressive Web App — PWA for short — is a website that can install
to a phone's home screen and behave more like an app, without anyone
needing to download it from an app store. When someone visits the
YourPeer AI Chat in their phone's browser, they can tap "Add to Home
Screen" and get an icon on their phone. From then on, tapping that
icon opens the chat in its own window, without the browser toolbar,
and loads faster than a regular web page.

YourPeer AI Chat is now a PWA. That means every feature of the chatbot
— finding shelter, food, showers, legal help, and more — is available
through what feels like a standalone app on any smartphone, tablet, or
computer. No app store. No download. No storage space eaten up. No
account required.

## What "PWA" actually means here — and what it doesn't

**There is an important distinction.** People sometimes use "PWA"
to mean "an app that works without an internet connection." That is
not what the YourPeer AI Chat PWA is, and we should be careful with
the language we use.

The chatbot needs an internet connection to function. The chatbot's
intelligence — understanding what someone's asking for, searching the
service database, generating responses — runs on a server that the
phone has to reach over the network. There is no version of the
chatbot that lives on the phone and can answer questions offline.

What the PWA does is make the experience **more resilient when the
connection is unstable**, and easier to access on any device:

1. **The app is installable to the home screen.** It launches like an
   app instead of a browser tab. This is the biggest user-facing win
   and applies on Android, iPhone, and desktop.

2. **The app shell loads instantly on repeat visits.** The HTML, CSS,
   and JavaScript that make up the YourPeer AI Chat interface are
   cached on the phone after the first visit. This means opening the
   app on a slow connection takes a fraction of the time it would
   take to download the same page fresh.

3. **A connection drop mid-conversation no longer wipes the screen.**
   If someone is reading service results and their Wi-Fi cuts out,
   they can still see the results in front of them. The app shows an
   amber "You're offline" banner so they know the information is from
   before the drop, but the addresses and phone numbers are still
   visible. They can call the numbers directly from the screen.

4. **A single message typed offline is queued, not lost.** If the
   user types a message while disconnected, it's saved locally and
   sent automatically when the connection returns. The chatbot's
   reply only arrives once the connection is back — the bot itself
   cannot run on the phone.

5. **On Android Chrome only**, queued messages can also send while
   the app's tab is closed. This uses a feature called Background
   Sync that's only available in Chromium browsers (Chrome, Edge,
   Samsung Internet on Android). On iOS Safari and Firefox, the
   queued message sends only when the user reopens the app and gets
   online again.

**What is NOT supported** — these are real limits worth knowing:

- **The user cannot start a new search offline.** Typing "I need
  shelter in Brooklyn" while offline queues that single message;
  the actual search runs and the results return only when the phone
  is back online.
- **The user cannot save individual services.** There is no
  bookmark or "save for later" function. The most recent batch of
  search results is cached for 24 hours, but only the most recent
  batch — searching again replaces the cache.
- **Conversations don't persist long-term.** After 30 minutes of
  inactivity, the conversation resets. A "See earlier results" link
  preserves the last set of services that were on screen, but the
  conversation history itself is gone.
- **There is no offline service directory.** The PWA does not bundle
  a copy of the Streetlives database. The user cannot browse the
  full list of services without an active connection.

## Why this matters for the people YourPeer serves

The people using YourPeer often rely on the least reliable internet
connections available: library Wi-Fi, shelter Wi-Fi, free hotspots at
McDonald's or Starbucks, prepaid phone plans with limited data. These
connections drop frequently and without warning.

Before the PWA work, when the internet cut out mid-search, the screen
could go blank or freeze on a half-loaded state. Refreshing wouldn't
help. The user would have to wait until connectivity returned before
they could see anything useful. The PWA changes that:

- **Search results stay on screen during a connection drop.** Someone
  who just found the address of a shelter accepting walk-ins tonight,
  then loses their Wi-Fi walking out of the library, can still see
  that address. They don't have to start over.
- **The app launches in seconds even on slow connections.** Once
  someone has visited once, repeat visits don't re-download the whole
  page — the cached app shell loads first and only the live data has
  to come over the network.
- **No app store, no download, no storage burden.** Many people in
  the target population have phones with limited storage. Asking them
  to download a 50-200MB app is a real barrier. The PWA is a few
  hundred kilobytes and doesn't require going to the App Store or
  Google Play.
- **It works on any phone.** Android, iPhone, even older devices.
  As long as the phone has a web browser, the PWA works. There's no
  "your phone isn't compatible" message.
- **It's private by default.** No app store account is needed to
  install it. There's no sign-up, no login, no profile. The PWA
  keeps the same privacy-first approach as the rest of YourPeer:
  anonymous sessions, transcripts redacted of personal information,
  no cookies tracking the user.

These are real benefits. They are not the same as "the app works
offline." When talking to users and partners, language like
"works better when your connection is unstable" or "your last results
stay visible if your signal drops" is more accurate than "works
offline."

## How it works for end users — step by step

### Getting started (first time)

1. **Open the browser** on your phone — Chrome on Android, Safari on
   iPhone, or any browser on a computer.
2. **Go to the YourPeer AI Chat URL.** Type the address into the
   browser or tap a link from a QR code, text message, or outreach
   flyer.
3. **Use it like normal.** The chatbot works immediately in the
   browser. Ask for help finding food, shelter, showers, clothing,
   health care, legal help, or other services.
4. **Add it to your home screen (optional but recommended).** This is
   what turns the website into an "app" on your phone:
   - **On Android (Chrome):** Look for a banner saying "Add YourPeer
     to Home Screen," or tap the three-dot menu and select "Add to
     Home Screen." Tap "Add."
   - **On iPhone (Safari):** Tap the share button (square with arrow
     pointing up). Scroll down and tap "Add to Home Screen." Tap
     "Add."
   - **On a computer:** In Chrome or Edge, look for a small install
     icon in the address bar.
5. **Open from your home screen.** The icon launches the app
   full-screen — no browser toolbar, no address bar.

### Using it day to day

- **Start a conversation** by typing or tapping what you need. "I
  need food in Brooklyn" or "shelter near me," or just tap the
  quick-reply buttons.
- **The home screen icon includes shortcuts** for the four most
  common needs: shelter, food, showers, and connecting with a peer
  navigator. Long-pressing the icon (or right-clicking, on desktop)
  reveals these shortcuts.
- **Your active conversation stays put** if you close the app and
  come back within 30 minutes. After 30 minutes of inactivity the
  session resets, but if your last conversation produced service
  results, a "See earlier results" link will offer to bring them
  back.
- **If your internet drops mid-conversation**, an amber banner
  appears: "You're offline — results shown may be outdated."
  Whatever was on screen stays visible. You can read addresses, tap
  phone numbers to call. You won't be able to send a new search
  request that completes until you're back online — if you type one,
  it queues and sends automatically when connectivity returns.
- **When you come back online**, the banner disappears, any queued
  messages send, and the chatbot responds.
- **Your information isn't saved between sessions.** After 30
  minutes of inactivity, the session resets. This is intentional —
  on a shared or public phone, the next person who opens YourPeer
  won't see your conversation.

### Tips for outreach workers

- **Show people how to add it to their home screen.** Once it's on
  the home screen, it's always one tap away. Many people don't know
  this is possible.
- **Demonstrate the "stays visible during dropouts" behavior.**
  Open a search, get results, turn on airplane mode. The results are
  still readable. This builds trust — people need to know the
  information they just found won't vanish if their signal cuts.
- **Be careful not to overpromise.** Don't tell people the chatbot
  "works offline" — it doesn't. What it does is keep the screen
  usable during a connection drop, and queue an offline message for
  later sending. Saying "your results stay visible if your connection
  drops" is accurate; "the app works without internet" is not.
- **QR codes on flyers work well.** A QR code that links directly to
  the chat URL means someone can scan it with their phone camera and
  be in a conversation in seconds. No typing.
- **It works on any phone with a browser.** If someone says "I don't
  have room for another app," explain that this doesn't take up
  space like a regular app and doesn't need to be downloaded from a
  store.

## What's currently built

A summary of what the PWA actually includes today:

**Web App Manifest.** Tells the phone "this website can be installed
as an app." Includes the YourPeer name, icons (192px, 512px, and a
maskable variant for Android adaptive icons), brand color
(`#FFD54F`), and the instruction to open in standalone mode (no
browser chrome).

**Home-screen shortcuts.** Long-pressing the YourPeer icon (Android)
or right-clicking it (desktop) reveals four shortcuts: Find shelter,
Find food, Find showers, Talk to a peer navigator. Each shortcut has
its own icon (house, bowl, shower head, person silhouette) for
legibility on a crowded home screen.

**iOS splash screens.** When the PWA is launched from an iPhone or
iPad home screen, a branded splash image is displayed during the
brief moment between tap and first paint. Twelve images cover iPhone
SE through 15 Pro Max and iPad mini through iPad Pro 12.9". Dark grey
background with the YourPeer logo centered.

**Service Worker.** The behind-the-scenes engine that handles the
offline-resilience features:
- Caches the app's core files (HTML, CSS, JavaScript) so the app
  loads quickly on repeat visits.
- Uses a network-first strategy for chat API requests — tries the
  live server first, falls back to a "you're offline" indicator if
  the network is unavailable.
- Serves a basic offline page if the user navigates to YourPeer with
  no cached data and no connection.

**Offline detection.** The app monitors the phone's internet status.
When the connection drops, it shows the amber "You're offline" banner
and adjusts the UI. When the connection returns, the banner
disappears automatically.

**Send queue.** A message typed while offline is stored in IndexedDB
(a database built into every modern browser). When connectivity
returns, the queue flushes automatically, sending messages in the
order they were typed. The user sees a "Waiting to send" indicator
under their message bubble.

**Background Sync (Android Chrome only).** On supported browsers, a
queued message can send even if the user has closed the app's tab.
On iOS Safari, Firefox, and older browsers this falls back to the
client-side flush — the message sends when the user reopens the app
and is online.

**Results cache.** The most recent search results (service names,
addresses, phone numbers, hours) are cached for 24 hours. If someone
searches for shelter, loses their connection, and reopens the app
within 24 hours, the previous results are still visible with a "may
be outdated" note. **Only the most recent batch is cached** — a new
search replaces the previous one.

**Conversation persistence (30 minutes).** The active chat
conversation is saved on the phone via Zustand persist. If the user
closes the app and reopens it within 30 minutes, the conversation
resumes where they left off. After 30 minutes of inactivity the
session resets to a fresh welcome message, with a "See earlier
results" link if there were service results in the previous session.

**Request idempotency.** If the same message gets sent twice (common
when connections are flaky and the phone isn't sure if the first one
went through), the server recognizes the duplicate via a stable
request ID and returns the same response without running the search
again. Prevents confusing duplicate replies.

**PII redaction at persist time.** When a message is held in the
offline queue, OR when the active conversation is saved to
localStorage for the 30-minute resume window, any personally
identifiable information it contains (phone numbers, addresses, SSNs,
etc.) is scrubbed before the data is written to disk. The user sees
their original text in the chat display while the tab is open, but on
refresh / reopen they see the redacted version (which is what's
stored). This applies to both the offline send queue and the
conversation history persistence — the two paths where user-typed
text could otherwise sit on the device.

**Retry on failure.** When a message fails to send (servers hiccup
even when online), the error message includes a "Retry" button. The
system also automatically retries once with a 1.5-second delay
before showing any error, so most transient failures resolve without
the user noticing.

**Dark mode.** Light, dark, or system-following theme. Default is
"follow the OS preference," which most users on iOS or Android have
already configured. A toggle in the app header lets users override.

## What's not built yet

The PWA is functional and live. Several improvements would meaningfully
expand what it can do for the target population. These are listed in
rough order of impact, with honest scoping notes about what each one
would and would not change.

### High priority

**Saved services / bookmarking.** Today, the user cannot save an
individual service for later reference. The most recent search
results are cached for 24 hours, but a new search replaces the
cache, and nothing the user does explicitly preserves a location.
Adding a "Save this service" button on each service card — and a
separate "Saved services" view that's accessible offline — would be
the single biggest step toward true offline usability for this
population. This is not in scope today; see the PWA_PROPOSAL document
for design notes.

**"Text me these results" via SMS.** When someone finds services they
need, they should be able to tap a button that sends the top results
(name, address, phone number) to their phone as a text message. This
is especially valuable for people using shared or public devices —
they can't count on the PWA cache being there next time, but a text
message is permanent. Requires a Twilio or similar SMS integration.

**Shareable results links.** Each service card should include a
"Share" button that copies a direct link to that location on
yourpeer.nyc (these URLs already exist). This lets users text a link
to a friend, a case worker, or themselves for later reference.

**Push notifications for follow-up.** The wireframes describe a
post-visit feedback flow: after someone visits a service, they'd get
a notification asking "Did you get what you needed?" PWAs support
push notifications on Android, and on iPhone since iOS 16.4. The
service worker infrastructure is already in place.

**Full Spanish language support.** The chatbot currently acknowledges
Spanish-speaking users and processes their service request, but
doesn't respond in Spanish. The PWA shell is language-agnostic; the
gap is in the chatbot's response generation.

### Medium priority

**Better offline banner with actionable information.** The current
banner says "You're offline." It could be more useful: "You're
offline. Your last search results are shown below. Tap any phone
number to call directly."

**iOS Background Sync fallback.** Currently, queued messages on iOS
only send when the user reopens the app. There's no way to fully fix
this — Apple has not implemented Background Sync — but a more
prominent "tap to retry sending" prompt could help users who aren't
sure their message went through.

**Sharable URL deep links into specific service categories.** Outreach
workers could share `/?prefill=I+need+food` and have a conversation
start mid-flow. The infrastructure is there (the home-screen
shortcuts use this), but there's no public URL helper or QR-code
generator yet.

### Lower priority (future enhancements)

**Periodic background fetch for service data updates.** The PWA could
periodically fetch updated service data for the user's most-searched
area, so even fully offline sessions show relatively fresh results.
This is an advanced PWA feature with limited browser support.

**Offline-capable geolocation.** The "Use my location" button currently
requires a connection. A small lookup table of NYC borough boundaries
could be bundled with the PWA, allowing basic location resolution
even offline.

**Integration with device contacts.** A "Save this contact" button
that adds a service's phone number directly to the phone's contacts
app. Uses the Web Contact Picker API, which has growing but
incomplete browser support.

## How this compares to building a native app

A question that may come up: why not just build a "real" app and put
it in the App Store?

The short answer is that a PWA gets most of the benefit at a fraction
of the cost — and avoids the biggest barriers for this specific
population.

Building separate native apps for iPhone and Android would mean
maintaining two codebases (or using a cross-platform framework),
going through App Store and Google Play review processes, and asking
users to download a 50-200MB app to devices that may already be full.
The target population is unlikely to search "YourPeer" in an app
store — they're much more likely to scan a QR code on a flyer, get a
link texted to them by an outreach worker, or type a URL into their
browser.

The PWA gives YourPeer a meaningful subset of native-app capabilities:
installs to the home screen, loads quickly, sends push notifications
on supported devices, and feels like a native app once installed.

The things a native app could do that the PWA cannot:

- **Run the chatbot offline.** A native app could in theory bundle a
  smaller language model and a copy of the service database for
  fully-offline operation. The PWA cannot do this — even WebAssembly
  doesn't make a chatbot the size of YourPeer's run on a phone.
  Note: this is an architectural limit, not a PWA limit. Even most
  native chatbot apps depend on a server.
- **Access deep device features.** Bluetooth, health sensors,
  advanced background processing. None of these are relevant to
  YourPeer's use case.
- **Show in the App Store and Google Play.** Useful for brand
  discovery but not for this audience.

## Summary: what the PWA actually does for YourPeer's mission

The people YourPeer serves face enough barriers already. They
shouldn't have to fight their technology to find a meal or a bed.
The PWA reduces three barriers:

**Slow page loads on weak connections** → the app shell is cached
locally, so opening the app is fast even on 2G/3G.

**Connection-drop frustration** → search results stay readable during
a dropout instead of vanishing into a half-loaded state. A queued
message will send when connectivity returns instead of just failing.

**App-store friction** → no download, no sign-up, no storage burden.
Scan a QR code or tap a link, and you're in. One more tap puts it on
the home screen.

The PWA is not the same as "an app that works offline." If we want
true offline usability — searching, viewing saved locations, browsing
without a connection — that requires building features on top of what
this PWA provides. Most importantly, a saved-services feature would
let users preserve specific locations they care about for offline
viewing. That work is not yet scoped; see the PWA proposal document
for design options.

The most important next step isn't technical. It's making sure
outreach workers know the PWA exists and can show people how to add
it to their home screens. That single action — turning a website
visit into a persistent home screen icon — is what transforms a
one-time interaction into a tool someone reaches for whenever they
need help.

---

*YourPeer AI Chat — Streetlives — May 2026*
