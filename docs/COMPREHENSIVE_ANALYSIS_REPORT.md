# YourPeer Chatbot — Comprehensive Analysis Report

**Date:** April 15, 2026
**Scope:** Architecture review, industry standards comparison, YourPeer web app parity analysis, previous audit resolution, and gap identification
**Sources:** Current codebase, Streetlives API documentation, YourPeer web application (github.com/streetlives/yourpeer.nyc), previous April 2026 audit, industry research (Frontiers in Political Science 2025, OECD 2025, ACM DIS 2025, W3C WAI chatbot accessibility research, NYC MyCity chatbot, Findhelp/Aunt Bertha platform)

---

## 1. Executive Summary

The YourPeer chatbot is architecturally strong in the areas that matter most for this population: safety (zero hallucinated service data across 167 eval scenarios), crisis detection (8 categories with fail-open), and privacy (PII redaction before storage). The "Safer, Limited RAG" pattern — where the LLM handles conversation only and all service data comes from deterministic database queries — is a genuinely novel design that addresses the primary risk cited in public sector AI chatbot research: the danger of hallucinated facts in high-stakes contexts.

However, the system has significant gaps when measured against production social services platforms (Findhelp, NYC 311/MyCity) and the features already present in Streetlives' own YourPeer web application. The most critical gaps are: no offline capability (the target population has unreliable internet), no SMS channel (many rely on basic phones), no share/save functionality (results disappear when the browser closes), and limited multilingual support. The application also lacks closed-loop referral tracking — the ability to know whether someone actually received help after being shown results — which is the defining feature of production social care platforms.

This report is organized from most to least critical. Items marked "resolved" reference the previous audit.

---

## 2. Previous Audit Resolution Status

Of the 15 recommendations from the April 2026 audit, 9 have been fully resolved and 6 remain open:

| # | Issue | Status | Notes |
|---|---|---|---|
| 1.1 | "Substance use treatment" not a keyword | ✅ Resolved | Added with "treatment program", "treatment center", "inpatient", "outpatient", "sober living" |
| 1.2 | "Baby supplies" not recognized | ✅ Resolved | Moved to `other` category with stroller, car seat |
| 1.3 | Financial services unmapped | ✅ Resolved | 10+ financial keywords added, "bad with money" confirmed working |
| 1.4 | DV shelter extracts shelter but fires crisis | ✅ Resolved | Crisis step-down with `dv_survivor` population boost — correct by design |
| 1.5 | `also_available` stale taxonomy names | ⚠️ Partial | 45 taxonomy names expanded with user-friendly labels, but label mapping could be more complete |
| 3.2 | Accessibility data not surfaced | ✅ Resolved | Now queried and displayed on service cards |
| 3.4 | No stale data warning | ✅ Resolved | Amber "⚠️ Not recently verified — call ahead" at 180+ days, "⚠️ Unverified" when null |
| 4.1 | Gender identity stored in audit log | ✅ Resolved | Renamed to `_gender` (prefixed, excluded from audit log serialization) |
| 4.2 | No XSS sanitization for service data | ✅ Resolved | `SafeHtml` component with tag allowlist, attribute escaping, `javascript:`/`data:` URL blocking |
| 5.1 | No Spanish language support | ❌ Open | Design doc created (`SPANISH_LANGUAGE_DESIGN.md`) but not implemented. Bilingual acknowledgment only. |
| 5.2 | No referral tracking / closed-loop feedback | ❌ Open | Unimplemented. No mechanism to know if referrals were successful. |
| 5.3 | No SMS / phone channel | ❌ Open | Web-only. Architecture doc §14 describes CCaaS integration as future work. |
| 5.5 | No provider hours for specific days | ✅ Resolved | "Are they open Saturday?" queries `holiday_schedules` for requested weekday |
| 5.7 | No directions integration | ✅ Resolved | Google Maps deep link on service card Directions button |
| 6.1 | Confirmation step adds friction for urgent needs | ✅ Resolved | Auto-execute for high-urgency queries (skips confirmation) |

---

## 3. Architecture Strengths

These are things the chatbot does well that are worth calling out, because they represent deliberate design decisions that align with industry best practices for high-stakes public sector chatbots.

**3.1 Safer, Limited RAG is the right pattern.** A 2025 Frontiers in Political Science study on evaluating chatbot architectures for public service delivery found that hybrid RAG systems reduce hallucination rates by approximately 70% compared to pure generative approaches. YourPeer goes further — the LLM never touches service data at all. This is stricter than even NYC's MyCity chatbot, which grounded answers in approved web pages but still experienced hallucinations about restaurant tip laws that made headlines. YourPeer's template query approach makes hallucination structurally impossible for service data.

**3.2 Crisis detection with fail-open is correct.** The two-stage approach (fast regex + LLM for ambiguous language) with fail-open policy (show safety resources if LLM fails) matches the design recommended by the OECD's 2025 report on AI in public service delivery: chatbots handling sensitive populations should "err toward safety" and always provide a human escalation path.

**3.3 Trauma-informed design is evidence-based.** The SAMHSA six-principles framework, shame normalization, AVR emotional handling pattern, and strict rubric for response tone are grounded in published research (PMC, National Harm Reduction Coalition, Woebot/Wysa clinical chatbot studies). Most public sector chatbots don't consider emotional context at all.

**3.4 The eval framework is unusually rigorous.** 167 scenarios, 11 weighted dimensions, LLM-as-judge with a more capable model (Opus) than the system under test (Haiku/Sonnet). The 2025 ACM DIS conference paper on 311 chatbots explicitly calls out the lack of systematic evaluation frameworks in public sector chatbot deployments. YourPeer has one that tracks regressions across 32 runs.

**3.5 Privacy posture exceeds minimum requirements.** No PII storage, anonymous sessions, PII safety warnings when users share SSNs/phones, gender identity excluded from audit logs. This exceeds what Findhelp offers (which collects names and contact information for referral tracking). For a population that may fear that seeking help creates a record usable against them, this is the right default.

---

## 4. Critical Gaps — What's Missing for Production

### 4.1 No offline capability (HIGH)

The target population uses library WiFi, shelter WiFi, and prepaid mobile data — all unreliable. When connection drops, the entire chat history and search results disappear. A 2025 W3C chatbot accessibility study found that chatbot conversations create "sizeable conversation histories" that users need to review — losing them mid-session is a critical UX failure for this population.

**Industry standard:** Progressive Web App (PWA) with service worker caching. Findhelp's mobile app allows saving program lists for offline reference. Even basic PWA features (cache last response, show cached results when offline, sync when reconnected) would prevent the worst case: a user who found a shelter address, lost connection on the subway, and can't remember the address when they arrive at the neighborhood.

**Recommendation:** Implement a service worker that caches the last set of search results and the chat history. Add a `manifest.json` for "Add to Home Screen" on mobile. This is a 1-2 week effort and dramatically improves reliability for the target population.

### 4.2 No SMS or phone channel (HIGH)

The architecture documentation (§14-15) describes CCaaS integration and voice interface as planned features. Many in the target population rely on basic phones without reliable mobile browsers. The YourPeer wireframe documents show SMS-based follow-up ("Did you get what you needed?") as a core flow.

**Industry standard:** NYC 311 is actively expanding from web to voice AI. Findhelp supports SMS-based referrals ("Resource Connect (powered by Findhelp) has been amazing. I can send families information via text or email"). The 2025 OECD report highlights multichannel access as a key enabler for inclusive AI public services.

**Recommendation:** For pilot, integrate Twilio for SMS result delivery. When a user completes a search, offer "📱 Text me these results" which sends a formatted SMS with the top 3 service names, addresses, and phone numbers. This requires collecting a phone number (temporary, for delivery only) — document the privacy tradeoff. Estimated effort: 2-3 weeks.

### 4.3 No save/share results (HIGH)

When a user finds relevant services, there's no way to save them, text them to a friend, or generate a shareable link. On shared/public devices, closing the browser destroys everything. The `localStorage` persistence (30-minute TTL) helps for page refreshes but not for deliberate return visits.

**Industry standard:** Findhelp's app allows saving and sharing program lists. The yourpeer.nyc web application has shareable URLs for every location (`yourpeer.nyc/locations/covenant-house-hells-kitchen`). The chatbot should at minimum link to these.

**Recommendation:** Two options. Quick win: every service card already has a "Learn more →" link to yourpeer.nyc — make this more prominent and add "Share" (Web Share API on mobile, copy-to-clipboard on desktop). Larger effort: generate a shareable results page URL that doesn't require a user account.

### 4.4 No closed-loop referral tracking (MEDIUM)

The system can tell you that 2,000 users searched for services last month, but it can't tell you whether a single one of them actually received help. This is the fundamental gap between a search tool and a social care platform.

**Industry standard:** Findhelp's entire business model centers on closed-loop referrals — tracking whether a referred person actually received services, and measuring referral success rates by provider. The YourPeer architecture doc (§11) describes this as the "ultimate goal." The YourPeer wireframe doc describes post-visit SMS feedback ("Did you get what you needed?").

**Recommendation:** This is a large effort that requires either opt-in identity (phone number for SMS follow-up) or partnership with providers who can report back. For pilot, implement a simpler version: post-results feedback ("Did you visit this place? Yes/No") surfaced 24 hours after search via a notification if the user opted in. Requires the persistent disk for state beyond the 30-minute session window.

### 4.5 Full Spanish support (MEDIUM)

Documented separately in `SPANISH_LANGUAGE_DESIGN.md`. Approximately 25% of NYC's homeless population is Spanish-speaking. The current bilingual acknowledgment is insufficient — it tells Spanish speakers the bot can't help them in their language, which is worse than not detecting Spanish at all.

**Industry standard:** NYC MyCity is expanding to multiple languages. Portugal's gov.pt chatbot supports 12 languages. Findhelp's platform is fully bilingual (English/Spanish). The OECD 2025 report identifies multilingual support as a key requirement for inclusive public AI services.

---

## 5. YourPeer Web App vs. Chatbot — Feature Parity Gaps

The yourpeer.nyc web application has several features the chatbot doesn't replicate or integrate with:

### 5.1 Map-based discovery

The web app shows services on an interactive map. Users can browse geographically — "what's near me?" becomes a visual scan rather than a text query. The chatbot returns a list of cards. For users unfamiliar with NYC neighborhoods, a map view would be more intuitive than text-based location results.

**Recommendation:** Consider embedding a static map image or a "View on map" link that opens yourpeer.nyc with the search results pre-filtered. Full map integration in the chat is complex; linking out is simple.

### 5.2 Category browsing

The web app lets users browse all services by category (Food, Shelter, Clothing, etc.) without typing anything. The chatbot's quick-reply category buttons serve a similar purpose, but the web app's category pages show subcategories (Soup Kitchen, Food Pantry, Meals on Wheels) that the chatbot doesn't expose until post-results filtering.

**Recommendation:** Add subcategory quick replies after the user selects a main category. "You picked Food — any specific type? [Soup Kitchen] [Food Pantry] [Any]"

### 5.3 Review/feedback system

The yourpeer.nyc website has a feedback feature with sentiment highlights extracted by LLM (documented in `Streetlives_Feedback_Feature_-_Sentiment_Analysis_Code.docx`). The chatbot shows review highlights on service cards but doesn't contribute to the feedback loop — users can rate locations via the in-chat widget, but this data doesn't flow back to the yourpeer.nyc review system.

**Recommendation:** Integrate the chatbot's per-location feedback (Safe? Friendly? Clean? LGBTQ+ friendly?) with the yourpeer.nyc feedback pipeline so the same data powers both surfaces.

### 5.4 Full location pages

The web app's location pages show complete information: all services at a location, photos, reviews, "how to get here" with transit info, eligibility details, and required documents. The chatbot's service cards show a subset. The "Learn more →" link bridges this gap but is easy to miss — it's a small text link in the card footer.

**Recommendation:** Make the yourpeer.nyc link more prominent. Consider it a primary action alongside Call and Directions, not a footer afterthought.

---

## 6. Architecture Decisions to Reconsider

### 6.1 In-memory session store is a deployment risk

Sessions and audit logs live in a Python dictionary behind a threading lock. This works for the pilot (single instance, ~2,000 users/month) but has three risks: server restart loses all data (mitigated by `PILOT_DB_PATH` SQLite persistence), no horizontal scaling (mitigated by Render persistent disk being single-instance), and memory growth under load (mitigated by LRU eviction at 500 sessions).

For production, consider PostgreSQL-backed sessions. The Streetlives database is read-only for service data, but a separate small database (or even a new schema in the same RDS instance) could store sessions, audit logs, and eval results. This eliminates the persistent disk dependency and enables horizontal scaling.

### 6.2 The chatbot.py monolith

`chatbot.py` is ~2,500 lines with `generate_reply()` as the main router. Every message type, handler, and flow lives in one file. This makes it difficult for multiple engineers to work on different features simultaneously (merge conflicts) and hard for new engineers to understand the flow (the ONBOARDING.md guide acknowledges this: "the largest file").

**Recommendation:** Extract handlers into separate modules: `handlers/service_flow.py`, `handlers/crisis.py`, `handlers/emotional.py`, `handlers/post_results.py`, `handlers/confirmation.py`. Keep `generate_reply()` as a thin router that dispatches to handlers. This is a refactor, not a feature — schedule it when there's a natural break in feature work.

### 6.3 Regex-first classification has a coverage ceiling

The 3-tier cascade works well (85% regex, 10% semantic, 5% LLM), but every new keyword, contraction variant, and colloquial phrase requires manual addition to keyword lists. The keyword audit has already identified 25 collision-prone words needing word-boundary matching. As coverage grows, the maintenance cost of the regex layer increases while its marginal value decreases.

**Alternative to consider:** Make the semantic router the primary classifier and use regex only for exact-match shortcuts (category names, service sub-types). The embedding model handles novel phrasings, misspellings, and cross-lingual input without manual keyword engineering. This would reduce the keyword maintenance burden and improve resilience to natural language variation. The cost is ~2-5ms per message (currently zero for regex hits), which is imperceptible to users.

### 6.4 SQL template approach may not scale to new query types

The 11 pre-written SQL templates are safe and auditable but rigid. Adding a new filter (e.g., "wheelchair accessible only") requires modifying the template, adding a parameter, and threading the parameter through the chatbot → query executor → template chain. Findhelp's platform uses dynamic query construction with validated parameters — more flexible but higher hallucination risk.

YourPeer's approach is correct for the current scope (10 service categories, 5 filter types). If the system grows to support more complex queries (compound eligibility, provider ratings, appointment availability), consider a query builder pattern that validates parameters against an allowlist rather than requiring a new template for each combination. This preserves the safety of pre-reviewed queries while reducing rigidity.

---

## 7. Safety & Compliance Gaps

### 7.1 No formal accessibility audit has been performed

The codebase has strong accessibility foundations: `aria-live` regions, keyboard navigation, screen reader labels, semantic HTML. But the W3C's 2025 chatbot accessibility playbook (Stanley et al.) identified 157 unique recommendations for chatbot accessibility, many of which are chatbot-specific and not covered by standard WCAG conformance: message pacing for cognitive disabilities, conversation history navigation, focus management between messages, and alert timing for new bot responses.

**Recommendation:** Commission a formal accessibility audit of the chat interface by a specialist familiar with chatbot-specific patterns. WCAG 2.2 AA conformance should be the minimum target. Include at least one user with a screen reader and one with cognitive/learning disabilities in pilot testing.

### 7.2 No content moderation beyond rate limiting

The previous audit (§4.4) noted no abuse detection beyond rate limiting. This remains unresolved. Adversarial users could probe LLM boundaries, submit offensive content, or attempt prompt injection. While the LLM system prompt has guardrails, dedicated moderation (keyword blocklist, toxicity scoring, pattern detection for prompt injection) would add defense in depth.

**Industry standard:** NYC's MyCity chatbot added scope restrictions after hallucination incidents — redirecting out-of-scope questions rather than attempting to answer them. The chatbot already does this for service data, but general conversation via the Haiku fallback is less constrained.

### 7.3 Self-neglect not covered in crisis detection

The previous audit (§4.5) identified that crisis detection doesn't cover self-neglect — a person who hasn't eaten in days, is disoriented, or describes severe medical neglect. This remains unresolved. Self-neglect is a common presentation in the homeless population and is recognized as a form of abuse by the NYC Department for the Aging.

### 7.4 No data retention policy

The system stores audit logs indefinitely (capped at 2,000 events in memory, unlimited in SQLite). There's no documented data retention policy, no automatic deletion schedule, and no mechanism for data subject access requests. For a public-sector-adjacent application handling conversations from vulnerable populations, a formal data retention policy should be established.

**Recommendation:** Document a retention policy. For the pilot, 90 days for anonymized transcripts and 30 days for session data is a reasonable starting point. Implement automatic SQLite purging on a schedule.

---

## 8. UX Gaps Specific to the Target Population

### 8.1 Reading level not audited

The previous audit (§6.7) noted that response messages should be audited against a 6th-grade reading level. This remains unaddressed. Phrases like "matching your criteria" and "I'll hold onto your info" appear in some responses. The Flesch-Kincaid readability score of bot responses has not been measured.

**Recommendation:** Run all hardcoded messages through a readability analyzer. Target Flesch-Kincaid grade level ≤ 6. Replace complex phrases with simpler alternatives. This is a text-editing task, not engineering.

### 8.2 No "undo" for accidental resets

"Start over" immediately clears all session state with no confirmation. On mobile, this button can be accidentally tapped. No recovery is possible.

**Recommendation:** Add a 5-second "Undo" quick-reply after reset, or require confirmation ("Are you sure? This will clear your search.").

### 8.3 No progress indicators during multi-turn intake

The bot asks questions sequentially (service → location → age → family status) but doesn't tell the user how many steps remain. Research on trauma-informed design (Bickmore et al., 2018) recommends predictability — "Step 2 of 3: Where are you?" — so users know what to expect and feel in control.

### 8.4 9 category buttons may overwhelm on small screens

The welcome message shows 9 quick-reply buttons (Food, Shelter, Showers, Clothing, Health Care, Mental Health, Legal, Employment, Other). On a small phone screen, these push the welcome text off-screen. Users see buttons without context.

**Recommendation:** Show 4-5 top categories initially, with a "More options" button that reveals the rest. Alternatively, group into "Immediate needs" (Food, Shelter, Showers) and "Other help" (Legal, Employment, etc.).

---

## 9. Comparison with Industry Leaders

### vs. Findhelp (formerly Aunt Bertha)

| Feature | Findhelp | YourPeer Chatbot |
|---|---|---|
| Closed-loop referrals | ✅ Core feature | ❌ Not implemented |
| Save/share results | ✅ App + web | ❌ Not implemented |
| SMS delivery | ✅ Text/email results | ❌ Web only |
| Mobile app | ✅ iOS + Android | ❌ Web only (no PWA) |
| Multilingual | ✅ English + Spanish | ⚠️ English only (Spanish in design) |
| Conversational interface | ❌ Form-based search | ✅ Natural language |
| Crisis detection | ❌ Not applicable | ✅ 8 categories with fail-open |
| Trauma-informed design | ⚠️ Standard UX | ✅ SAMHSA-based, research-grounded |
| Hallucination prevention | ⚠️ Curated data, no AI generation | ✅ Architecturally impossible |
| Peer-verified data | ⚠️ CBO-claimed listings | ✅ Lived-experience validation |
| Provider feedback loop | ✅ Provider dashboard + analytics | ⚠️ Basic per-location rating |
| Offline access | ✅ Mobile app | ❌ None |
| Interoperability (HL7/FHIR) | ✅ Healthcare system integration | ❌ Not applicable |

YourPeer's strengths are in areas Findhelp doesn't address: conversational access for low-literacy users, crisis detection, trauma-informed design, and hallucination-proof architecture. Findhelp's strengths are in areas YourPeer hasn't built yet: multi-channel delivery, save/share, closed-loop tracking, and provider-side analytics.

### vs. NYC MyCity Chatbot

NYC's MyCity chatbot (chat.nyc.gov) had high-profile hallucination issues — it told restaurant owners they could take employee tips, which is illegal. NYC's response was to add scope restrictions and reduce the chatbot's generative capability. YourPeer's template query architecture prevents this class of failure entirely. However, MyCity supports voice interaction and is expanding to handle 311's full call volume — capabilities YourPeer hasn't built.

---

## 10. Prioritized Recommendations

### P0 — Before pilot launch

1. **Offline results caching** — PWA service worker that caches the last search results. Prevents the worst case: user finds a shelter, loses WiFi, can't remember the address. (1-2 weeks)
2. **Formal accessibility audit** — Commission a WCAG 2.2 AA audit of the chat interface with chatbot-specific patterns. (External, 1-2 weeks)
3. **Reading level audit** — Run all hardcoded messages through Flesch-Kincaid and simplify to ≤ 6th grade. (1-2 days, text-only)
4. **Data retention policy** — Document and implement automatic purging of anonymized transcripts (90 days) and session data (30 days). (2-3 days)

### P1 — First quarter post-pilot

5. **SMS result delivery** — Twilio integration for "Text me these results" — addresses the no-phone-browser gap. (2-3 weeks)
6. **Spanish language support** — Implement per `SPANISH_LANGUAGE_DESIGN.md`. (6 weeks)
7. **Share results** — Web Share API on mobile, copy-to-clipboard on desktop, for individual service cards and full result sets. (1 week)
8. **Chatbot.py decomposition** — Extract handlers into separate modules to reduce merge conflicts and improve onboarding. (1-2 weeks)

### P2 — Second quarter post-pilot

9. **Closed-loop feedback** — Post-visit "Did you get help?" follow-up via SMS (opt-in). (4-6 weeks)
10. **YourPeer link prominence** — Make the yourpeer.nyc location page link a primary action on service cards, not a footer link. (1 day)
11. **Content moderation** — Toxicity scoring and prompt injection detection beyond rate limiting. (1-2 weeks)
12. **Self-neglect crisis category** — Add crisis detection for severe self-neglect descriptions. (1 week with clinical review)
13. **Category grouping** — Reduce welcome screen button count from 9 to 4-5 with "More options" expand. (2-3 days)
14. **Undo for reset** — 5-second undo button or confirmation before clearing session. (1 day)

### P3 — Future roadmap

15. **PostgreSQL-backed sessions** — Replace in-memory + SQLite with a proper database for sessions and audit logs. Enables horizontal scaling.
16. **Voice interface** — STT → intake → TTS pipeline per architecture doc §15.
17. **Provider dashboard** — Let service providers see how many referrals they received and respond to feedback.
18. **Map integration** — Embed or link to map view for search results.
19. **Semantic-first classification** — Shift primary classification from regex to the embedding model, reducing keyword maintenance burden.
