# YourPeer as a Progressive Web App (PWA)

## What It Is, Why It Matters, and How to Use It

**Written for:** Streetlives leadership, outreach workers, partner organizations
**Date:** April 21, 2026

---

## In plain language: what is a PWA?

A Progressive Web App — PWA for short — is a website that can behave like an app on your phone, without anyone needing to download it from an app store. When someone visits YourPeer's chatbot in their phone's browser, they can tap "Add to Home Screen" and get an icon on their phone that looks and acts just like any other app. It opens in its own window (no browser toolbar), loads fast, and — here's the important part — **keeps working even when the internet connection drops**.

Think of it this way: a regular website is like a phone call. If the connection cuts out, the call ends and everything you were saying is lost. A PWA is more like a text message. If your signal drops, the message waits in your outbox and sends automatically when you're back online. Meanwhile, you can still read the messages you already received.

YourPeer is now a PWA. That means every feature of the chatbot — finding shelter, food, showers, legal help, and more — is available as what feels like a standalone app on any smartphone, tablet, or computer. No app store. No download. No storage space eaten up. No account required.

---

## Why this matters so much for the people YourPeer serves

The people using YourPeer are often relying on the least reliable internet connections available: library Wi-Fi, shelter Wi-Fi, free hotspots at McDonald's or Starbucks, or prepaid phone plans with limited data. These connections drop frequently and without warning.

Before the PWA upgrade, when the internet cut out, the entire chat history and any service results would simply vanish. Imagine someone who just found the address of a shelter accepting walk-ins tonight, then lost their Wi-Fi signal walking out of the library. They'd have to start the whole conversation over — if they even had connectivity to do so.

Here's what the PWA changes:

**Your search results don't disappear.** The last set of services the chatbot found — names, addresses, phone numbers, hours — are saved on the phone itself. Even if the internet goes out completely, the user can still pull up YourPeer and see those results. A small banner at the top says "You're offline — these results may be outdated" so they know the information might not be current, but the addresses and phone numbers are still there when they need them.

**Messages wait instead of failing.** If someone types a message while offline, it doesn't just fail with an error. Instead, it's saved in a queue on the phone. When the connection comes back, those messages are automatically sent in the right order, and the conversation picks up as if nothing happened. The user sees a "sending..." indicator so they know the message is waiting.

**The app loads instantly, even on slow connections.** The first time someone visits YourPeer, the PWA saves the core app files on their phone. After that, opening YourPeer is nearly instant — it doesn't need to download the whole page again every time. This is a big deal on a 2G or slow 3G connection where a regular website might take 10-15 seconds to load.

**No app store, no download, no storage burden.** Many people in the target population have phones with limited storage — full of photos, messages, and the few essential apps they need. Asking them to download another app is a real barrier. The PWA takes up almost no space (a few hundred kilobytes, compared to typical apps that are 50-200 megabytes) and doesn't require going to the App Store or Google Play.

**It works on any phone.** Android, iPhone, even older devices. As long as the phone has a web browser, the PWA works. There's no "your phone isn't compatible" message. This is important because the target population often uses older or lower-cost devices.

**It's private by default.** No app store account needed means no personal information is exchanged to "install" it. There's no sign-up, no login, no profile. The PWA keeps the same privacy-first approach as the regular chatbot — anonymous sessions, no PII stored, no cookies tracking the user.

---

## How it works for end users — step by step

### Getting started (first time)

1. **Open the browser** on your phone — Chrome on Android, Safari on iPhone, or any browser on a computer.

2. **Go to the YourPeer chatbot URL.** Type the address into the browser or tap a link from a QR code, text message, or outreach flyer.

3. **Use it like normal.** The chatbot works immediately in the browser. Ask for help finding food, shelter, showers, clothing, health care, legal help, or other services. Tell it your borough or neighborhood, and it will search for nearby options.

4. **Add it to your home screen (optional but recommended).** This is what turns the website into an "app" on your phone:

   - **On Android (Chrome):** Look for a banner at the bottom of the screen saying "Add YourPeer to Home Screen," or tap the three-dot menu at the top right and select "Add to Home Screen." Tap "Add." An icon labeled "YourPeer" appears on your home screen.
   
   - **On iPhone (Safari):** Tap the share button (the square with an arrow pointing up) at the bottom of the screen. Scroll down and tap "Add to Home Screen." Tap "Add." The YourPeer icon appears on your home screen.
   
   - **On a computer:** In Chrome or Edge, look for a small install icon in the address bar, or go to the browser menu and select "Install YourPeer."

5. **Open from your home screen.** After adding, tap the YourPeer icon just like any app. It opens full-screen — no browser toolbar, no address bar. It looks and feels like a regular app.

### Using it day to day

- **Start a conversation** by typing or tapping what you need. "I need food in Brooklyn" or "shelter near me" or just tap the quick-reply buttons the chatbot offers.

- **Your chat stays put** even if you close the app and come back later (within 30 minutes). The conversation is saved on your phone, so you can pick up where you left off.

- **If your internet drops,** you'll see an amber-colored banner near the top saying "You appear to be offline." Your previous search results are still visible below. You can still scroll through them, note down addresses, or tap phone numbers to call. If you type a message, it will be held and sent automatically when you're back online.

- **When you come back online,** the banner disappears, any queued messages send, and the chatbot responds as normal. You don't need to restart or re-enter anything.

- **Your information isn't saved between sessions.** After 30 minutes of inactivity, the session resets and you start fresh. This is intentional — on a shared or public phone, the next person who opens YourPeer won't see your previous conversation. Your privacy is protected.

### Tips for outreach workers

- **Show people how to add it to their home screen.** This is the single most impactful thing you can do. Once it's on the home screen, it's always one tap away and works offline. Many people don't know this is possible.

- **Demonstrate the offline feature.** Turn on airplane mode and show that the last search results are still visible. This builds trust — people need to know the information won't vanish.

- **QR codes on flyers work well.** A QR code that links directly to the chatbot URL means someone can scan it with their phone camera and be in a conversation in seconds. No typing, no remembering a URL.

- **It works on any phone with a browser.** If someone says "I don't have room for another app," explain that this doesn't take up space like a regular app and doesn't need to be downloaded from a store.

---

## What's already built

Here's a summary of what the PWA currently includes:

**Web App Manifest** — the file that tells the phone "this website can be installed as an app." It includes the YourPeer name, icon specifications, color scheme (amber/yellow theme), and the instruction to open in standalone mode (no browser chrome).

**Service Worker** — the behind-the-scenes engine that makes offline work. It handles three things:
- Caching the app's core files (HTML, CSS, JavaScript) so the app loads instantly on repeat visits.
- Using a "network-first" strategy for chat API requests — it tries the live server first, and falls back to cached results if the network is unavailable.
- Serving a basic offline page if the user navigates to YourPeer with absolutely no cached data and no internet.

**Offline detection** — the app monitors the phone's internet status. When the connection drops, it shows a clear visual indicator (an amber banner) and disables the send button to prevent confusing error messages. When the connection returns, the banner disappears automatically.

**Send queue** — messages typed while offline are stored in IndexedDB (a small database built into every modern browser). When connectivity returns, the queue flushes automatically, sending messages one by one in the order they were typed. This uses a technology called IndexedDB rather than simpler browser storage because IndexedDB is more reliable and accessible to the service worker.

**Results cache** — the most recent search results (service names, addresses, phone numbers, hours) are cached locally with a 24-hour expiration. This means if someone searches for shelter tonight, loses their connection, and checks back 6 hours later, the results are still there with a "may be outdated" note.

**Request idempotency** — if the same message gets sent twice (common when connections are flaky and the phone isn't sure if the first one went through), the server recognizes the duplicate and returns the same response without running the search again. This prevents confusing duplicate results.

**Session management** — sessions last 30 minutes from the last interaction. When a session expires, the user gets a clean start with a friendly welcome message. If they had previous results, a "See earlier results" link lets them review what they found before the reset. Sessions use anonymous IDs with no personal information attached.

**Retry on failure** — when a message fails to send (even when online — servers have hiccups too), the error message includes a "Retry" button. One tap re-sends the message. The system automatically retries once with a 1.5-second delay before showing the error, so most transient failures resolve without the user noticing.

---

## What's not built yet — and what's next

The PWA is functional and live, but there are several improvements that would make it significantly better for the target population. These are listed roughly in order of impact.

### High priority

**Push notifications for follow-up.** The wireframes describe a post-visit feedback flow: after someone visits a service, they'd get a notification asking "Did you get what you needed?" PWAs support push notifications on Android, and on iPhone since iOS 16.4. This would close the feedback loop that the wireframes describe without requiring anyone to download a native app. The technical infrastructure (service worker, manifest) is already in place — what's needed is the notification permission flow, a push server, and the follow-up message logic.

**"Text me these results" via SMS.** When someone finds services they need, they should be able to tap a button that sends the top results (name, address, phone number) to their phone as a text message. This is especially valuable for people using shared or public devices — they can't count on the PWA cache being there next time, but a text message is permanent. This requires a Twilio or similar SMS integration and a brief, privacy-conscious phone number collection (used only for delivery, not stored in transcripts).

**Shareable results links.** Each service card should include a "Share" button that copies a direct link to that location on yourpeer.nyc (these URLs already exist, like `yourpeer.nyc/locations/covenant-house-hells-kitchen`). This lets users text a link to a friend, a case worker, or themselves for later reference.

**Full Spanish language support.** The chatbot currently acknowledges Spanish-speaking users and processes their service request, but doesn't respond in Spanish. The PWA manifest already supports language declaration, and the service worker doesn't care what language the content is in — the gap is in the chatbot's response generation, not the PWA shell. When full Spanish support ships, it will work seamlessly within the existing PWA.

### Medium priority

**Better offline banner with actionable information.** The current banner says "You appear to be offline." It could be more helpful: "You're offline. Your last search results are shown below. Tap any phone number to call directly." This tells the user what they *can* do, not just what they can't.

**Background sync for queued messages.** Currently, queued messages only send when the user has the app open and the connection returns. The Web Background Sync API allows the service worker to send queued messages even if the user has closed the app — as long as the phone has connectivity. This means someone could type a message at the library, close the app, walk to the subway, come out the other end with signal, and find that their message was sent and results are waiting. Browser support is strong on Android/Chrome; Safari support is still limited.

**App icon and branding.** The PWA manifest references icon files that need to be created by a designer — `icon-192.png`, `icon-512.png`, and a maskable variant for Android's adaptive icon system. Without these, the "Add to Home Screen" prompt shows a generic placeholder instead of the YourPeer brand. This is a small design task that makes a big difference in trust and recognizability.

**Dark mode.** Many users browse at night — in shelters, on the street, on public transit. A dark color scheme reduces eye strain and battery usage on phones with OLED screens. The chatbot currently has no dark mode. This is a CSS-level change that doesn't affect PWA functionality.

### Lower priority (future enhancements)

**Periodic background fetch for service data updates.** The PWA could periodically (once a day) fetch updated service data for the user's most-searched area, so even fully offline sessions show relatively fresh results. This is a more advanced PWA feature with limited browser support today.

**Offline-capable geolocation.** The "Use my location" button currently requires an internet connection to convert GPS coordinates into a borough/neighborhood via a server call. A small lookup table of NYC borough boundaries could be bundled with the PWA, allowing basic location resolution even offline.

**Integration with device contacts.** For outreach workers who help multiple clients, the PWA could offer a "Save this contact" button that adds a service's phone number directly to the phone's contacts app. This uses the Web Contact Picker API, which has growing but incomplete browser support.

---

## How this compares to building a native app

A question that may come up: why not just build a "real" app and put it in the App Store?

The short answer is that a PWA gets 90% of the benefit at 10% of the cost — and avoids the biggest barriers for this specific population.

Building separate native apps for iPhone and Android would mean maintaining two codebases (or using a cross-platform framework), going through App Store and Google Play review processes, and asking users to download a 50-200MB app to devices that may already be full. The target population is unlikely to search "YourPeer" in an app store — they're much more likely to scan a QR code on a flyer, get a link texted to them by an outreach worker, or type a URL into their browser.

The PWA gives YourPeer all of these capabilities: works offline, installs to the home screen, loads instantly, sends push notifications (on supported devices), and feels like a native app. The one thing it *can't* do that a native app can is access certain deep device features (Bluetooth, health sensors, advanced background processing). None of those are relevant to YourPeer's use case.

Given Streetlives' team size and the pilot scope, the PWA approach is the right call. If YourPeer grows to a scale where a native app is justified (hundreds of thousands of users, complex device integrations), the PWA serves as a production-quality prototype that validates the design before that investment is made.

---

## Summary: what the PWA means for YourPeer's mission

The people YourPeer serves face enough barriers already. They shouldn't have to fight their technology to find a meal or a bed. The PWA removes three of the most common technology barriers:

**Unreliable internet** → results are cached, messages queue, the app works offline.

**Limited phone storage** → no download required, takes up almost no space.

**Unfamiliarity with app stores** → scan a QR code or tap a link, and you're in. One more tap to put it on your home screen.

The PWA is not a separate product. It's the same YourPeer chatbot, made more resilient for the people who need it most. Every improvement to the chatbot — better service matching, warmer tone, new crisis resources, Spanish support — automatically appears in the PWA because they're the same thing.

The most important next step isn't technical. It's making sure outreach workers know the PWA exists and can show people how to add it to their home screens. That single action — turning a website visit into a persistent home screen icon — is what transforms a one-time interaction into a tool someone reaches for whenever they need help.

---

*YourPeer AI Chat — Streetlives — April 2026*
