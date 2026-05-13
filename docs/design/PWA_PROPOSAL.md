# YourPeer AI Chat — PWA proposal: scope, offline, and discoverability

**Status:** Proposal for review by Adam Reichmann (Executive Director, Streetlives)
**Author:** Engineering, in response to Adam's feedback dated [date]
**Date:** May 3, 2026

---

## Why this document exists

After reviewing an early version of `PWA_GUIDE.md`, Adam raised a
substantive concern: the document overpromises offline functionality
relative to what the AI chat PWA actually delivers. He drew a clear
three-category distinction we should adopt across all communication:

1. **Add-to-home-screen / app-like access.**
2. **Preserving an already-loaded screen during connection loss.**
3. **True offline usability — reopen, refresh, search, or view saved
   information without a connection.**

The current PWA implementation delivers (1) and (2), plus a narrow
slice of (3) (a single queued message can send when connectivity
returns). It does not deliver (3) in any user-meaningful sense:
nobody can save a service for later, nobody can browse without a
connection, nobody can search offline and have results return later.

Adam asked whether we plan to build (3), and if so, how it should be
scoped, and how it changes outreach language. He also raised a
separate set of questions about discoverability: how does the AI
chat fit into the existing YourPeer.nyc presence; is it a feature
or a separate product; how do users find it; what does reporting
look like.

This document proposes answers, identifies the product calls that
need to be made, flags what cannot be addressed cleanly, and grounds
each recommendation in research where available.

---

## Part 1 — Offline usability (Category 3)

### What we know about the population's connectivity

The strongest available data on technology use in adult homeless
populations comes from a 2014 systematic review (Rhoades et al., AJPH)
covering sixteen studies, and the 2018 HOPE HOME cohort study
(JMIR mHealth, n=350, Oakland adults over 50). Key findings:

- **Mobile phone access is high but unstable.** The HOPE HOME cohort
  reported 72% mobile phone access, but only 32% of phone-owners had
  smartphones, and 94% used month-to-month plans rather than annual
  contracts. Phone numbers and devices change frequently — one study
  found 56% of homeless adults had changed phones in the prior three
  months and 55% had changed phone numbers.[^1]
- **Internet access is much lower than the general population.**
  19–84% across studies, with most clustering in the 50% range.
  Compare to ~90% in the general adult US population.[^2]
- **Borrowed phones and lost devices are common.** The research
  defines "phone access" broadly — owning, borrowing long-term, or
  being able to find one in an emergency. The implication for
  software design: don't assume the device the user has today is the
  device they had last week.
- **Wi-Fi at libraries, shelters, and fast-food chains is the
  dominant connectivity model**, with prepaid limited-data cellular
  as a backstop. Both are unreliable; both produce frequent
  drop-and-resume patterns rather than fully-offline sessions.[^1]

This shapes the offline-usability question concretely: **the most
valuable offline feature is one that survives device loss / borrowed
phones, not one that requires a stable single device.** A
saved-services list that lives only in IndexedDB on one phone is
worth less to this population than a saved-services list that can
be sent to themselves over SMS.

### The three offline-usability options on the table

| Option | What it does | Build cost | User value |
|---|---|---|---|
| **A. Manual save-this-service** | "Save" button on each card. Saved services view, accessible offline. | ~3-5 days eng + UX | High, but only on the device where it was saved |
| **B. SMS-based handoff** | "Text me these results" sends names/addresses/phones as SMS | ~3-5 days eng + Twilio account | High, survives device loss/change |
| **C. Both — save locally AND send to self** | A + B | ~6-8 days combined | Highest |

#### Option A — Manual save-this-service

**What it would do.** Each service card gets a "Save" button. Saved
services persist in IndexedDB indefinitely (with a user-facing
"Clear saved" option). A separate "Saved" view shows the list, is
accessible from a tab in the chat header, and works fully offline:
addresses, phone numbers, hours, last-verified date all stored
locally.

**Why this is technically straightforward.** The IndexedDB
infrastructure is in place from the offline-cache work already
shipped. The Streetlives API already returns structured service data
that's safe to persist (no PII, public information). Per
[web.dev's offline-data guide](https://web.dev/learn/pwa/offline-data),
IndexedDB is the recommended storage for this exact use case: a
list of structured records the user can browse without a network.

**The unsolved problem with Option A alone.** If the user loses
their phone, gets a new one, switches between borrowed devices, or
clears their browser data — all common patterns in this population
— their saved services are gone. The save-locally model assumes
device continuity that doesn't hold.

**Privacy note.** A saved-services list is structurally less
sensitive than a chat transcript (it contains public service info,
not the user's situation), but it can still indirectly reveal
context. "Saved: domestic violence shelter, methadone clinic,
LGBTQ-affirming youth center" tells someone who picks up the phone
a lot about the user. This argues for an explicit clear-saved
button and possibly a default TTL (90 days?) that the user can
extend.

#### Option B — SMS-based handoff

**What it would do.** From a results card or a saved view, the user
can tap "Text me this" and enter a phone number. The server sends
an SMS with the service's name, address, phone number, and a
yourpeer.nyc URL. The number is used for delivery only and not
stored in transcripts (mirroring the existing privacy posture).

**Why SMS specifically.** In the homeless adult population, SMS is
nearly-universal where smartphones are not. The HOPE HOME study
found 76% of cohort members used text messaging.[^1] An SMS arrives
on whatever device the user has now — including a borrowed phone
they didn't have last week. An SMS persists across browser data
clears, app uninstalls, and PWA cache evictions. It survives the
user moving to a new device.

**This is the option most aligned with how the population actually
uses technology.** It treats the SMS network as the durable storage
layer and the YourPeer chat as the discovery layer. That mapping
matches reality better than treating the device as durable storage.

**Cost note.** Requires a Twilio (or similar) account and a small
amount of operational budget — Twilio's outbound SMS in the US is
around $0.0079 per message (per Twilio's published pricing as of
2026; verify before committing). At 2,000 monthly active users with
~30% sending themselves results = ~600 SMS/month = ~$5/month. Even
at 10x that scale, this is operationally trivial.

**The unsolved problem with Option B alone.** It only works if the
user has cellular SMS reception when they want to access the
information. A user in a basement library with no cell signal
cannot pull up an SMS they sent themselves. A locally-saved view
would work in that scenario. The two options are not redundant;
they cover different failure modes.

#### Option C — Both

**Recommended.** A and B together cover the two distinct failure
modes (no signal vs lost device). The combined build is roughly
6-8 engineering days — meaningful but not large — and gets the
PWA closer to honestly being able to claim "true offline
usability" for the things users actually need.

The order I'd recommend: **B before A.** SMS handoff is a one-day
win that delivers immediate value without changing the data model.
Saved-services is a larger UX project (where do they live in the
nav, how do users discover the feature, what's the empty state,
how does the "Saved" tab interact with sessions ending). Shipping
SMS first lets us learn whether users want to keep their results,
which validates the Saved feature before we build it.

### Product call #1: which of A, B, C, or none

**My recommendation: C, with B shipped first.**

If we ship neither A nor B:

- The PWA continues to deliver categories 1 and 2 only.
- All outreach language must avoid phrases like "works offline" /
  "your services stay available." Acceptable phrasing:
  *"works smoothly when your connection is unstable"* or
  *"your last results stay visible if your signal drops."*
- The current `PWA_GUIDE.md` rewrite (just landed) reflects this
  scope honestly.

If we ship B only:
- Outreach language can include *"text yourself the services you
  find so you have them when you need them."*
- This is enough to credibly claim a basic form of category 3 —
  the user can access their results without a connection (via SMS),
  on any device.

If we ship A only:
- Outreach language can include *"save services to come back to
  later."*
- We should be careful to add *"saved services live on this device
  — if you lose your phone or switch browsers, you'll need to save
  them again."* This caveat is honest but may discourage use.

If we ship C:
- Both phrasings are available. We can also describe the
  belt-and-suspenders nature: "Save them to this device or text
  them to your phone — or both."

---

## Part 2 — Outreach collateral

Adam asked about visual home-screen-install guides. This is a
design task more than an engineering one, but it has technical
implications worth flagging.

### What's needed

1. **A one-page Android install guide.** Screenshots of Chrome's
   "Add YourPeer to Home Screen" flow, with arrows pointing to the
   right buttons.
2. **A one-page iOS install guide.** Screenshots of Safari's Share
   button → "Add to Home Screen" flow. iOS is harder than Android
   for first-time installers because the share button is not
   labeled and is in a non-obvious place.
3. **A QR-code poster template.** Two QR codes per poster: one
   linking to the chat URL, one to a how-to-install URL. The
   how-to-install destination should be a simple HTML page that
   detects the user's device and shows the right instructions.

### Engineering implications

- **The how-to-install page would be a new public route.** It's
  not part of the chat itself but lives under the same domain.
  Detects user agent, shows Android instructions or iOS
  instructions based on the device.
- **QR codes that include a `?source=` parameter** would let us
  distinguish foot traffic from different outreach surfaces in
  analytics. Any reporting that segments by acquisition channel
  needs this from day one — adding it later means losing the
  early-adoption data.
- **Outreach materials should be versioned.** If we change the
  install flow or rebrand a button, printed flyers go stale. A
  single "outreach assets" page on the team intranet (or a Notion
  page) with the latest versions is worth setting up before
  printing anything in volume.

### Product call #2: who builds the visual collateral

This is design work, not engineering work. Engineering can build
the how-to-install web page and the QR-code generator. The actual
visual one-pagers need a designer (or a careful non-designer with
Figma access). Confirming who owns this is a coordination question
for the team.

---

## Part 3 — Discoverability and the AI chat's relationship to YourPeer.nyc

This is the most strategically loaded part of Adam's feedback,
and the part with the most product calls. The answers shape
analytics, reporting, SEO, and how outreach materials are written.

### The core question: is the AI chat a feature of YourPeer, or a separate experience

There are three plausible models. Each has real consequences.

#### Model A — Subdirectory feature (`yourpeer.nyc/chat`)

The AI chat lives at a path under the existing yourpeer.nyc domain.
It's part of YourPeer the way "Saved searches" or "About" would be.
Users get to it from the main YourPeer site via a prominent CTA.

**SEO consequences.** Subdirectories share the parent domain's
search authority.[^3] yourpeer.nyc has accumulated link equity over
time; a `/chat` path inherits that equity for free. A user
searching "NYC homeless services chat" or "find shelter NYC AI"
benefits from the existing domain ranking.

**Analytics consequences.** A single Google Analytics property
covers both the directory and the chat. Cross-product behavior
(user lands on a service page, then opens the chat to ask about
something else) is trivially trackable.

**SPA caveat that needs handling.** The chat is a single-page
React app. Without `pushState`-based routing inside the chat,
Google Analytics will record a single pageview for the entire
chat session.[^4] We need to fire `page_view` events on every
meaningful chat-state transition (search, results, escalation) for
analytics to be useful. This is a known and solved problem but
it's not free — see the GA4 guidance on enabling Browser History
events.[^4]

**User journey implications.** Users land on yourpeer.nyc, see the
chat as a feature alongside the directory, and choose. This means
the chat needs a clear value proposition relative to the directory:
*"chat is faster when you don't know exactly what to search for."*

#### Model B — Subdomain (`chat.yourpeer.nyc`)

The AI chat lives at a different subdomain. It's a sibling to the
main site, shares branding, but is technically separate.

**SEO consequences.** Subdomains are treated as effectively
separate sites by search engines.[^3] The chat doesn't inherit
yourpeer.nyc's link equity automatically — it has to build its
own. Google's official position is that subdomains and
subdirectories are weighted equally; SEO practitioners broadly
disagree, with most reporting that subdirectories rank
faster.[^3] For a small operation without a dedicated SEO team,
this matters.

**Analytics consequences.** Likely a separate GA property. Tracking
user journey from the directory to the chat requires cross-domain
tracking setup, which is more fragile than a single property.

**Operational consequences.** Separate Google Search Console
property, separate sitemap, separate domain authority work.

**When a subdomain makes sense.** Different infrastructure
(different deploy pipeline), different team ownership, or a desire
to evolve the chat independently. None of these strongly apply to
YourPeer.

#### Model C — Separate domain (e.g., `yourpeerchat.org`)

The chat is its own product. Different brand, different URL,
discovered separately.

**SEO consequences.** Starts from zero. No link equity, no
established rankings. Years to compete with established service-
finder sites.

**Discoverability consequences.** Realistically, users would only
find this through QR codes on outreach flyers and word-of-mouth.
Organic search would take a long time to materialize. From Adam's
note: *"I understand we are not only creating a parallel tool that
users only find through outreach materials."* This model is the
worst fit for that goal.

**The only argument for this model** is independence — if there's
a strategic reason to keep the AI chat brand-separable from
YourPeer (e.g., the chat is jointly owned with another partner, or
might spin out). Doesn't apply today.

### Product call #3: which deployment model

**My recommendation: Model A (subdirectory).** Specifically,
`yourpeer.nyc/chat`.

This is the option that:
- inherits existing search authority
- gives unified analytics
- lets users discover the chat from the main directory naturally
- requires the least net-new infrastructure work
- allows future integration (e.g., a saved-services flow that
  pulls from both the directory and the chat under one privacy
  posture)

The only real cost is the SPA-pageview tracking work (see
"Reporting" below), which we'd need to do anyway.

### Reporting: what we will and won't be able to know

Once Model A is in place, here's what reporting can tell us:

**Will be able to know:**

- Total chat sessions per day/week/month.
- Distribution across service types (shelter, food, etc.) inferred
  from the slot-extraction taxonomy.
- Drop-off points in the conversation flow (where users abandon).
- How many sessions reach a successful results screen.
- Geographic distribution at the borough level (from the location
  slots users provide), without storing precise PII.
- Acquisition channels via `?source=` parameters on QR codes /
  outreach links.
- Crisis-resource trigger frequency (a critical safety metric).
- Saved-services usage if we ship Option A.
- SMS-handoff usage if we ship Option B.

**Will NOT be able to know (and shouldn't):**

- Who the user is. The PWA stores no PII; transcripts are anonymized
  and TTL-bounded. This is by design.
- Whether a user actually reached the recommended service. We can
  only infer success from in-chat signals (did they tap "Get
  directions"?) — we can't follow them off-platform.
- Repeat-visit cohort behavior (was this user here last week?).
  Anonymous-by-default precludes this without changing the privacy
  model.

**The product call hidden in here:** if leadership wants
cohort-level analytics ("are returning users finding what they
need over time?"), we'd need to introduce some form of stable
anonymous identifier (e.g., an opt-in device-local token). This
is a non-trivial privacy decision and not in scope of the current
PR. Worth flagging because Adam asked about reporting and the
answer materially depends on whether we're willing to revisit the
"no session linkage to users" line in the design doc.

### Discoverability question that cannot be cleanly answered

**Adam asked:** *"If this is part of the existing YourPeer web app,
how will users know the AI chat exists and when to use it?"*

This is a UX question about the YourPeer.nyc directory site, not
about the AI chat itself. Deciding how prominent the chat CTA is
on the YourPeer homepage requires:

1. A design pass on the directory homepage.
2. A product call on whether the chat is positioned as
   complementary to the directory or as a primary entry point.
3. A/B testing the placement, which requires the analytics
   infrastructure described above.

**This is outstanding.** I cannot answer it from the engineering
side. It needs design input, a product call, and probably a
conversation with whoever owns the YourPeer.nyc directory's UX.
Recommend pulling Adam, design, and product into a meeting
specifically scoped to this question once the deployment model
(Model A vs others) is settled.

---

## Summary of product calls needed

| # | Call | Recommendation | Outstanding if no decision |
|---|---|---|---|
| 1 | Build saved-services / SMS handoff / both / neither | **Both, B first** | Outreach language must scope to category 1+2 only |
| 2 | Who builds visual collateral (designer? volunteer?) | Coordination, not engineering | Engineering can build the web/QR side; visual one-pagers blocked |
| 3 | Deployment model (subdirectory / subdomain / separate) | **Subdirectory (`yourpeer.nyc/chat`)** | Affects URLs, analytics setup, SEO |
| 4 | Cohort-level analytics (requires opt-in stable ID) | Defer; revisit if leadership wants cohort reporting | We'll have session-level analytics only |
| 5 | Chat CTA placement on the YourPeer.nyc homepage | Design + product question, not engineering | Cannot be answered without that meeting |

---

## What is outstanding regardless of the calls above

### Cannot be addressed from inside this codebase

- **Outreach collateral design.** Visual one-pagers for Android and
  iOS install. Engineering can build the underlying web page; the
  visuals need a designer.
- **YourPeer.nyc directory homepage CTA placement.** This is a
  decision about the directory site, not the chat.
- **SEO for the chat itself.** A chat-bot SPA is by nature less
  discoverable than a static service directory. Even with good
  technical SEO (server-side rendering, structured data), the
  chat will not rank for queries like "homeless shelter NYC" the
  way a curated location page does. The chat's discovery is
  realistically driven by outreach + the directory funnel + word
  of mouth, not organic search.[^4]

### Cannot be addressed at all without changing the privacy model

- **Cross-session user tracking.** "Did this person come back" is
  unanswerable while the system is anonymous-by-default. Changing
  this would require leadership and a privacy-impact review.
- **True attribution of outcomes.** "Did this person actually go to
  the shelter we recommended?" cannot be measured without an
  off-platform follow-up channel (e.g., the SMS-feedback flow in
  the wireframes). If we ship Option B, we have a path to this —
  the SMS could include a one-tap feedback link 24h later.

### Cannot be addressed without an iOS-specific OS update

- **Background Sync on iOS Safari.** Apple has not implemented the
  Web Background Sync API. Queued messages on iOS only send when
  the user reopens the app. We can mitigate (more prominent
  retry prompts) but cannot fix.

---

## Next steps

1. **Reply to Adam** with the honesty rewrite of `PWA_GUIDE.md` and
   this proposal. Make clear which parts of his feedback this
   document addresses and which (collateral design, homepage CTA)
   it punts to a separate conversation.
2. **Get product calls 1 and 3 made.** Calls 2 and 5 require design
   input; call 4 requires leadership privacy discussion.
3. **If C is approved**, schedule the engineering work in this
   order: SMS handoff (1 sprint), then saved-services (1-2
   sprints).
4. **Update outreach language** based on the decisions made.
   Anything currently saying "works offline" gets revised
   regardless.
5. **If Model A is approved**, plan the migration of the chat to
   `yourpeer.nyc/chat` and the SPA-pageview analytics integration
   as a separate PR.

---

## References

[^1]: Rhoades, H., Wenzel, S. L., Rice, E., Winetrobe, H., &
Henwood, B. (2017). *No Digital Divide? Technology Use among
Homeless Adults.* Journal of Social Distress and the Homeless.
[https://pmc.ncbi.nlm.nih.gov/articles/PMC6516785/](https://pmc.ncbi.nlm.nih.gov/articles/PMC6516785/)
For SMS prevalence (76% in cohort), see same study. For the
month-to-month plan and phone-turnover data, see Tan et al. (2018),
*Mobile Phone, Computer, and Internet Use Among Older Homeless
Adults*, JMIR mHealth, [https://pmc.ncbi.nlm.nih.gov/articles/PMC6305882/](https://pmc.ncbi.nlm.nih.gov/articles/PMC6305882/)

[^2]: McInnes, D. K., Li, A. E., & Hogan, T. P. (2013).
*Opportunities for Engaging Low-Income, Vulnerable Populations in
Health Care: A Systematic Review of Homeless Persons' Access to
and Use of Information Technologies*. American Journal of Public
Health. [https://pmc.ncbi.nlm.nih.gov/articles/PMC3969124/](https://pmc.ncbi.nlm.nih.gov/articles/PMC3969124/)

[^3]: Subdirectory vs. subdomain SEO consensus: while Google
formally treats them equally, practitioner consensus is that
subdirectories tend to rank faster because they inherit existing
domain authority. See e.g. [https://zupo.co/subdomain-vs-subdirectory-which-is-better-for-seo/](https://zupo.co/subdomain-vs-subdirectory-which-is-better-for-seo/)
for a recent practitioner summary.

[^4]: Single-page-application analytics and SEO: SPAs require
explicit `pushState` route changes and `page_view` events to be
tracked correctly in GA4. Default behavior is to record one
pageview per session. See [SE Ranking's SPA SEO guide](https://seranking.com/blog/single-page-application-seo/)
and Google's GA4 Enhanced Measurement documentation. SPAs also
present challenges for organic search discoverability without
server-side rendering — content not present in the initial HTML
may not be indexed. The YourPeer chat's primary discovery channels
are outreach materials and the directory funnel, not organic
search, so this is a known and accepted limitation rather than a
problem to solve.
