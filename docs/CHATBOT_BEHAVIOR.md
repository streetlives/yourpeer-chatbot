# Chatbot Behavior — Capabilities, Guardrails & Limitations

This document describes exactly how the YourPeer chatbot processes messages, what it can and cannot do, and the guardrails that govern its behavior. It serves as a reference for staff reviewing conversations, engineers extending the system, and stakeholders evaluating the chatbot's design.

For crisis detection details, see [CRISIS_DETECTION.md](CRISIS_DETECTION.md). For service card rendering and database queries, see [FEATURES.md](FEATURES.md).

---

## Message Routing Pipeline

Every incoming message passes through five stages: slot extraction, semantic routing (when regex misses), unified LLM classification (when both miss), split classification, and combined routing. Slots are always extracted first so service intent is known before routing decisions.

### Stage 1 — Slot Extraction (always runs first)

Regex-based slot extraction runs on every message before classification. This extracts service type(s), location, age, urgency, family status, and service detail. The result determines `has_service_intent` — whether the message contains a service request.

When multiple services are detected with different locations (e.g. "food in Brooklyn and shelter in Manhattan"), per-service location binding matches each service to its nearest location by text position. The primary service gets the first-mentioned location; queued services get their bound locations stored as 3-tuples `(service_type, detail, location)`.

### Stage 1a — Semantic Routing (when regex finds no service_type)

When regex finds no `service_type`, the semantic router (`semantic_router.py`) embeds the user's message using `all-MiniLM-L6-v2` (a 22M-parameter sentence embedding model running locally on CPU) and compares it against pre-embedded example utterances for each service category. The closest match above a confidence threshold (default 0.75, with per-route overrides) determines the service type.

This eliminates the "missing keyword" class of failures. "I ran out of insulin" has no keyword overlap with the medical phrase list, but its embedding is semantically close to "I need my medication" and "where can I get a prescription filled." The semantic router also detects population context (veteran, reentry, disabled, etc.) at a lower threshold (0.70) and merges it with regex-detected populations.

For short messages (≤8 words), a semantic match skips the LLM entirely (~2-5ms total). For longer messages, the semantic router sets the `service_type` and the LLM still runs to extract location, age, urgency, and other slots — with the semantic service type preserved by the regex-override logic.

Route definitions live in `semantic_routes.py` — 10–20 example utterances per service category. Adding new utterances requires no code changes, no model retraining, and no deployment — just edit the file and restart. The model generalizes immediately from examples.

Runtime cost: ~2-5ms per message (embedding + cosine similarity). Model load: ~1-2 seconds at startup (amortized). Memory: ~100 MB. The semantic layer handles ~15% of messages — those where regex misses but the intent is clearly within a known category.

### Stage 1b — Unified LLM Classification Gate (Run 23+)

When regex finds **no service_type AND no action AND no tone AND 4+ words**, a single Haiku call (`classify_unified()` in `llm_classifier.py`) returns all classification dimensions in one JSON response: service_type, location, additional_services, tone, action, urgency, age, family_status, populations.

This replaces two separate LLM calls (Phase 2 slot enrichment + LLM category fallback) with one combined call. It fires on ~25% of messages — the ones where regex has nothing useful. The other ~75% are handled by regex alone at zero cost.

The gate skips for short messages and messages where regex already found something useful (confirmations, greetings, resets, etc.). If the LLM call fails, the system falls through to regex results.

The prompt instructs the LLM to distinguish **intent from mention**: "I saw a doctor on TV" → null (not medical), "I need to see a doctor" → medical. This addresses the 50% false positive rate found in the regex audit.

### Stage 2 — Split Classification

Two independent classifiers run in parallel:

**`_classify_action(text)`** — what the user wants to DO:

| Action | Trigger | Example |
|---|---|---|
| `reset` | "start over", "new search", "cancel" | "start over" |
| `correction` | "not what I meant", "you misunderstood", "that's wrong", "try again" | "not what I meant" |
| `negative_preference` | "none of those", "not what I need", "I don't want any of those", "those don't help" | "none of those work" |
| `bot_identity` | "are you a robot", "am I talking to a person" | "are you AI?" |
| `bot_question` | "why can't you", "how does this work", "is this private", "can ICE see" | "is this safe?" |
| `escalation` | "peer navigator", "talk to a person", "speak with someone", "transfer me", "can someone call me", "actual person" | "connect with peer navigator" |
| `confirm_change_service` | "change service", "different service" | "change service" |
| `confirm_change_location` | "change location", "different area" | "change location" |
| `confirm_yes` | "yes", "sure", "go ahead", "yes search", "bet", "aight", "word", "fasho", "say less", "that works", "sounds good", "for sure", "lets go" | "bet" |
| `confirm_deny` | "no", "nah", "nope", "not yet", "i'm good", "nah i'm good", "all good", "no need", "i don't want to", "i don't want that" | "nah i'm good" |
| `greeting` | "hi", "hello", "hey" (only if ≤3 words) | "hello" |
| `thanks` | "thank you", "thanks" (no continuation) | "thanks" |
| `help` | "what can you do", "list services"; "help" (word-boundary, excludes "helpful"/"not helpful") | "help" |

**`_classify_tone(text)`** — how the user FEELS:

| Tone | Trigger | Example |
|---|---|---|
| `crisis` | Regex + LLM crisis detection (see [CRISIS_DETECTION.md](CRISIS_DETECTION.md)) | "I want to hurt myself" |
| `frustrated` | "not helpful", "waste of time", "already tried", "you're no help", "going in circles", "whatever", "smh", "this is bs", "this ain't working" | "that wasn't helpful" |
| `emotional` | "feeling down", "rough day", "I'm scared", "nobody cares", "can't take it anymore", "end of my rope", "crying all day", "I hate my life", "what's the point", "I'm broken", "feel empty inside", "giving up" | "I'm feeling really down" |
| `confused` | "I don't know what to do", "I'm overwhelmed" | "I'm lost" |
| `urgent` | "right now", "tonight", "nowhere to go", "on the street", "desperate", "kicked out today" | "I need shelter right now" |

**Key difference from the old architecture:** Tone detection has **no service-word gating**. "I'm struggling and need food" detects both emotional tone AND food service intent. The old `_has_service_words` guard that suppressed emotional detection when service words were present has been eliminated.

**`_classify_message(text)`** — backward-compatible wrapper that combines both classifiers into a single category string. Used by existing tests and the LLM fallback stage.

### Stage 3 — Combined Routing

The routing priority is:

| Priority | Condition | Route |
|---|---|---|
| 0 | Post-results: `_last_results` set + message is a follow-up question | Post-results handler (deterministic, no LLM). New-request phrases, new locations, frustrated tone, and negative_preference messages escape to normal routing. Unmatched name → disambiguation prompt |
| 1 | `tone == crisis` | Crisis handler (always wins). Step-down for `safety_concern`, `domestic_violence`, and `youth_runaway` — shows crisis resources AND offers service search when `has_service_intent` is true |
| 2 | `action == reset` | Reset handler |
| 2a | `action == correction` ("not what I meant") | Correction handler — clears pending state, shows alternatives |
| 2b | `action == negative_preference` ("none of those") | Acknowledges rejection, offers alternative service categories + peer navigator |
| 3 | `action` is confirmation/bot/greeting/thanks | Action handler |
| 4 | `has_service_intent == True` | **Service flow** (with tone prefix if emotional/frustrated/confused/urgent). Exceptions: (a) escalation + service without location stays as escalation; (b) `bot_question` overrides service intent — privacy questions like "do they get my info?" are NOT swallowed by the service flow |
| 4a | Location-unknown phrases + service_type set + no location | Location-unknown handler (geolocation + borough buttons) |
| 5 | `action == help` | Help handler |
| 6 | `action == escalation` | Escalation handler |
| 7 | `tone == frustrated` | Frustration handler |
| 8 | `tone == emotional` | Emotional handler |
| 9 | `tone == confused` | Confused handler |
| 10 | LLM classification (>3 words) | LLM-determined category (confidence: medium) |
| 11 | None of the above | General conversation (confidence: low) |

The key insight is **row 4**: when service intent is present, it wins over help/escalation/emotional/confused. Those become tone modifiers on the service flow, not separate routes. This eliminates the ad-hoc guards that previously re-checked slots inside the help and escalation handlers.

**Escalation exception:** Service intent only overrides escalation when BOTH service_type AND location are present. "Connect me with a navigator about food" (food but no location) stays as escalation — the user wants human help. "Navigator, client needs shelter in East Harlem" (both present) routes to service — the user is making a request on behalf of someone.

**Tone prefixes applied in the service flow:**

| Tone | Prefix on confirmation/follow-up |
|---|---|
| `emotional` | "I hear you, and I want to help." |
| `frustrated` | "I understand this has been frustrating. Let me try something different." |
| `confused` | "No worries — let me help you with that." |
| `urgent` | "I can see this is urgent — let me find something right away." |
| None | (no prefix) |

### LLM Fallback (secondary safety net)

The unified LLM classification gate (Stage 1b) handles most messages where regex fails. If the unified gate didn't fire (message too short, or regex already found something), and no other route matches, Claude Haiku classifies the message into one of the standard categories as a last-resort fallback. This catches edge cases the unified gate missed.

If the LLM is unavailable or returns an unrecognized category, the system falls back to `general`.

---

## Category Behaviors

### Crisis

Crisis resources are shown immediately. The session is NOT cleared — the user can continue their service search afterward. See [CRISIS_DETECTION.md](CRISIS_DETECTION.md) for full details on the two-stage detection pipeline, six crisis categories, and fail-open policy.

**Crisis step-down:** When crisis fires on a non-acute category (`safety_concern` or `domestic_violence`) AND the user has explicit service intent (regex found a service keyword like "shelter"), the bot shows crisis resources AND preserves the extracted service slots in session. The response appends: "I can also help you find [service] in [location] — would you like me to search?" with quick replies for "Yes, search" and "Peer navigator." For `domestic_violence` crises, `dv_survivor` is injected into the session's `_populations` so that the subsequent search boosts DV-specific services (shelters, legal aid, counseling) via description-based ORDER BY ranking. This injection bridges the gap between the crisis detector's 54 DV phrases and the population extractor's 10 — ensuring phrases like "he hits me" (which the crisis detector catches but the population extractor doesn't) still trigger the DV service boost. The injection also fires in the non-step-down branch (no service intent) so that if the user later asks for a service, the boost is already in session. This handles cases like "I was kicked out and need shelter in Brooklyn" where both safety resources and practical help are appropriate. Acute crisis categories (suicide, medical, trafficking, violence) always show crisis resources only.

**Emotional phrase guard:** Before invoking the LLM for crisis detection (Stage 2), the system checks whether the message matches a known sub-crisis emotional phrase (e.g., "feeling scared", "I'm struggling", "rough day"). If so, the LLM stage is skipped entirely — these are handled by the emotional tone handler, not the crisis handler. This prevents the LLM's "when in doubt, err toward crisis=true" instruction from over-escalating clearly emotional-but-not-crisis messages.

### Reset

Clears all session state (slots, transcript, pending confirmation). Shows a fresh welcome message with service category buttons. Triggered by "start over", "new search", "begin again", or "cancel".

### Correction ("Not what I meant")

Triggered by phrases like "not what I meant", "you misunderstood", "wrong thing", "I didn't ask for that" (15 phrases total). Clears `_pending_confirmation`, `_last_action`, and `_last_results` to prevent the user from re-entering the wrong flow, but preserves `service_type` and `location` so they don't lose their search context entirely.

Shows a context-aware response: if the user had an active search, acknowledges it ("Sorry about that! I was searching for food in Brooklyn."). Presents the full service menu plus a peer navigator button. Logged with `confidence="low"` for ambiguity tracking.

The "❌ Not what I meant" quick reply button appears on responses where the system's confidence is medium or low (LLM-classified messages and unrecognized service redirects). Tapping it sends "not what I meant" which triggers this handler.

### Disambiguation

Triggered when a message is ambiguous between a post-results follow-up question and a new service request. Specifically, when:
1. The user has results displayed (`_last_results` is set)
2. The post-results classifier matched a `specific_name` pattern (e.g., "What about X?")
3. But the name doesn't match any displayed result

Instead of silently falling through to normal routing (which would lose the user's context about why the response changed), the bot asks: "I'm not sure if you're asking about the results I showed, or if you'd like to search for something new. Which would you prefer?" with three quick-reply buttons: "🔍 Search for [query]", "📋 More about results", "🔍 New search".

This follows the industry-standard "clarification-before-classification" pattern. Messages with clear new-request signals ("I need X", "where can I go", "looking for") bypass disambiguation entirely via the post-results escape hatch — they're unambiguously new requests.

### Negative Preference

Detects when the user rejects all offered options — "none of those", "I don't want any of those", "those don't help", "not what I need". Instead of falling through to the general handler or frustration handler (which wouldn't acknowledge the specific rejection), the bot explicitly acknowledges that the options aren't right, offers alternative service categories via `_WELCOME_QUICK_REPLIES`, and includes a peer navigator option. Sets `_last_action = "negative_preference"`.

This is distinct from frustration (which is about the bot failing) and correction (which is about the bot misunderstanding). Negative preference is: "you understood me, but the results aren't what I need."

### Bot Identity

Responds honestly that the user is talking to an AI assistant, offers to connect with a real person (peer navigator). Shows service category buttons so the user can continue.

### Bot Question

Answers questions about the bot's capabilities directly and honestly. Uses an LLM prompt loaded with detailed factual information about the system, including session context (current search state, whether geolocation is active). The prompt includes specific details about service categories, geolocation failure reasons, NYC-only coverage (suggests 211 for elsewhere), and comprehensive privacy facts (no ICE connection, no law enforcement sharing, no impact on benefits).

Privacy questions are specifically classified into this category, including concerns about ICE, police, case workers, benefits impact, anonymity, recording, and data deletion. This ensures the population served — who may avoid seeking help due to surveillance fears — gets direct, reassuring answers.

When the LLM is unavailable, `_static_bot_answer()` provides pattern-matched fallbacks for 7 topic areas: geolocation failures, NYC coverage, service categories, immigration/ICE privacy, law enforcement privacy, benefits/provider privacy, identity/anonymity, data deletion, and general privacy. Unknown questions get a useful generic answer rather than silence.

This category catches messages like "why weren't you able to get my location?" that previously misrouted to the frustration handler.

### Escalation

Provides contact information for Streetlives peer navigators and crisis resources. Clears any pending search confirmation so the user doesn't accidentally re-enter the search flow on their next message. Sets `_last_action = "escalation"` so that "no" on the next message is interpreted as "no, I don't need the navigator" rather than "no, don't run the search."

### Confirmation Actions

Four confirmation categories handle the user's response to a pending search confirmation:

- **confirm_yes** — Executes the database query and returns service cards.
- **confirm_deny** — Clears the confirmation, keeps slots, offers options (change service, change location, new search, peer navigator).
- **confirm_change_service** — Clears the service type slot and asks what they need.
- **confirm_change_location** — Clears the location slot and offers borough buttons.

Context-aware "yes" and "no": after an escalation or emotional response, "yes" and "no" refer to the peer navigator offer, not to a pending search. "Yes" after escalation or emotional shows the peer navigator contact info. "No" after escalation gives a gentle "I'm here if you change your mind." "No" after emotional gives "That's okay. I'm here whenever you're ready." This prevents a user who just shared something vulnerable from accidentally triggering a search confirmation.

### Greeting

Returns a warm welcome. If the user has existing slots from an earlier search, acknowledges this and asks if they want to continue or start over.

### Thanks

Returns a brief "you're welcome" message with service buttons in case the user wants to search for something else.

### Frustration

Acknowledges the frustration empathetically without being defensive. Uses a persistent `_frustration_count` counter (not `_last_action` check) so frustration tracking survives intermediate messages.

**3-tier response escalation:**

| Count | Response style | Buttons |
|---|---|---|
| 1st | Full empathetic `_FRUSTRATION_RESPONSE` — acknowledges frustration, validates feelings, suggests trying a different search or peer navigator | "🔍 Try different search" + "👤 Peer navigator" |
| 2nd | Shorter, more direct — acknowledges the bot isn't finding what they need, strongly recommends peer navigator, mentions 311 | "🤝 Peer navigator" + "🔄 Start over" |
| 3rd+ | Immediate navigator offer — very short, apologetic, navigator-only | "🤝 Talk to a person" only |

Sets `_last_action = "frustration"` so "yes" connects to a peer navigator.

### Emotional

Follows the **Acknowledge-Validate-Redirect (AVR)** pattern established in the clinical chatbot literature (see [Emotional Handling Design](#emotional-handling-design) below). Uses a **static-first approach** — the LLM is NOT called for emotional messages, because it frequently steers toward service-finding mode despite prompt instructions. Instead, `_pick_emotional_response(message)` selects from 6 emotion-specific static responses keyed by detected emotion:

| Emotion | Trigger words | Response focus |
|---|---|---|
| `scared` | scared, afraid, frightened, terrified, fear | Normalizes fear, offers navigator |
| `sad` | feeling down/sad/bad, depressed, not okay | Validates courage to speak up |
| `rough_day` | rough/bad/tough/hard day, falling apart | Acknowledges heaviness |
| `shame` | embarrassed, ashamed, pathetic, failure | Removes stigma, affirms strength |
| `grief` | died, passed away, grieving, mourning | Acknowledges loss |
| `alone` | alone, no one, nobody, no friends/family | Affirms visibility and courage |
| (default) | Other emotional phrases | Generic warm acknowledgment |

None of the emotion-specific responses mention services — they focus purely on empathy. The generic fallback also avoids listing specific services.

Shows only one button — "🤝 Talk to a person" — not the service menu or "New search". This prevents the experience of sharing something vulnerable and immediately being shown task-oriented options. Sets `_last_action = "emotional"` so that "yes" on the next message routes to the peer navigator, and "no" gives a gentle non-pushy response with only a navigator button.

**Phrase matching** uses three techniques to maximize recall without false positives: (a) contraction normalization ("I'm" → "I am"), (b) intensifier stripping ("I'm really scared" → "I'm scared"), and (c) post-normalization variants ("i am scared" in the phrase list alongside "i'm scared").

### Location-Unknown

When the bot has just asked for the user's location (service_type is set, location is missing, no pending confirmation) and the user responds with uncertainty ("I don't know", "idk", "not sure", "no clue"), indifference ("anywhere", "wherever", "doesn't matter"), or self-location ("where I am", "here", "right here"), the bot offers geolocation and borough buttons instead of falling into the confused handler.

The handler recognizes 19 phrases via substring matching plus 2 exact-match phrases ("here" and "right here" — exact match prevents false positives on "here's what I need" or "there"). Guards ensure this only fires when the user has a service_type set, no location yet, and no pending confirmation. Without those guards, "I don't know" would always be intercepted rather than routing to the confused handler for users who genuinely don't know what they need.

Response: "No problem! You can share your location and I'll find what's nearby, or pick a borough:" with a "📍 Use my location" button followed by the five borough buttons.

### Confused

Shows gentle guidance for users who don't know what they need. Lists common things people look for in plain language (not service category labels). Shows the full welcome menu plus a "🤝 Peer navigator" option. Sets `_last_action = "confused"` so "yes" connects to a peer navigator and "no" gives gentle encouragement with a navigator button. This handler is intentionally NOT sent to the LLM, which would misinterpret "I don't know what to do" as a mental health service request.

### Help

Returns a static response describing what the bot can do: find free services in NYC, the categories available, and how to get started. Shows service category buttons.

### Context-Aware Yes/No

After emotional, escalation, frustration, or confused responses, "yes" and "no" are interpreted in context rather than as search confirmations:

| After | "Yes" means | "No" means |
|---|---|---|
| Emotional | Connect to peer navigator | Gentle response + navigator button only (no service menu) |
| Escalation | Connect to peer navigator | "No problem" + service menu buttons |
| Frustration | Connect to peer navigator | "No worries" + navigator button |
| Confused | Connect to peer navigator | Gentle encouragement + navigator button |
| Crisis (step-down) | Execute service search with preserved slots | Acknowledge + navigator button |

The `_last_action` tracker is cleared after any non-yes/no message so it doesn't persist indefinitely.

### Service

Extracts structured slots (service type, service detail, location, age, urgency, gender, family status, organization name, walk-in preference) from the message. If slots are complete, shows a confirmation prompt. If incomplete, asks a follow-up question for the missing slot.

**Organization name search:** When a user mentions a known organization name ("tell me about Covenant House", "Safe Horizon in Harlem"), the `org_name` slot is extracted. Organization name alone is sufficient to search — no location or service type required. The confirmation message adapts: "I'll search for services at Covenant House" or "I'll search for legal help at Safe Horizon in Harlem."

**Auto-execute for urgent queries:** When urgency is "high" and all required slots are filled (e.g., "I need a bed tonight in Brooklyn"), the confirmation step is skipped and the search executes immediately. Medium urgency and incomplete slots still go through normal confirmation.

**Walk-in filter:** When a user says "walk-in only", "no referral needed", or similar (20 phrases), the `no_requirements` slot is set. This excludes services requiring membership or referral from the results.

**Family composition:** For shelter searches, the chatbot asks "Are you on your own, or do you have family or children with you?" after collecting age. The `family_status` slot (with_children, with_family, alone) is shown in the confirmation and used to enrich shelter taxonomy queries.

**Population context:** The `_populations` slot extracts cross-cutting identity attributes (veteran, disabled, reentry, dv_survivor, pregnant, senior) that modify ALL service searches. Veterans get taxonomy-based sort boost; other populations get description-based sort boost via dynamic ORDER BY. Senior is auto-inferred from age ≥ 62. Confirmation messages show context-aware prefixes ("I'll search for veteran-friendly food in Brooklyn"). Populations are merged via set union across conversation turns (a user who mentions being a veteran in one message and disabled in the next gets both). Stored with `_` prefix to exclude from audit log serialization.

**Multi-service extraction:** The slot extractor detects all service types in a message (e.g., "food and shelter" → both extracted). The primary type drives the current search; additional types are stored in `additional_services` for future queue handling.

**Service flow continuation:** When a user already has a `service_type` in session and their new message extracts fresh slot data (location, age, family_status, etc.) but isn't classified as a "service" message by the classifier, the system treats it as a continuation of the service flow rather than falling through to the LLM. This handles replies like "near me", "close by", "I'm 25", or "with my kids" to follow-up questions — messages that contain slot data but no service keyword. Without this, these messages would route to the general conversation handler and the user would lose their search context.

**Narrative extraction:** Long messages (20+ words) are detected as narratives by the slot extractor and processed with urgency-aware extraction. When multiple service types are mentioned in a narrative (e.g., "I just got out of the hospital and need somewhere to stay and something to eat"), the system prioritizes based on an urgency hierarchy: shelter > medical > food > employment > other. The primary service drives the current search; additional services are queued. Regex fallback handles narrative extraction when the LLM is unavailable.

### General

Catch-all for messages that don't fit any other category. Uses Claude Haiku for a natural conversational response. The prompt adapts based on context:

- **With service intent** (user has partially filled slots): gently reminds them they can continue their search.
- **Without service intent**: just responds naturally without pushing services.
- **Unrecognized service request**: detects "I need X" / "find me X" patterns where X doesn't map to any known service type, even on the first turn without a location. Also catches the existing case of location set + no service type after 2+ turns. Both redirect gracefully with "I'm not sure I can help with that specifically" and show the service menu. This handles requests for impossible services (e.g., "unicorn ride", "helicopter") that would otherwise loop indefinitely.

**Conversational awareness guard:** Casual chat patterns ("how are you", "just wanted to chat", "good morning") are detected via `_CASUAL_CHAT_RE` and suppress service category buttons. Without this guard, "hey how are you doing today" would show the full service menu on first turn, which the eval judge flags as "pushing the service menu repeatedly."

Service category buttons are only shown on the first conversational turn when the user has no service intent AND the message is not casual chat. After that, general responses have no buttons to avoid feeling transactional.

---

## LLM Usage

Three LLM-powered features are used in production, each with a specific model assignment:

| Feature | Model | When It Runs | Output |
|---|---|---|---|
| Conversational fallback | Haiku | Message classified as `general` | 1–3 sentence response |
| Slot extraction | Haiku | Complex service messages (regex handles simple ones) | JSON: service_type, location, age, urgency, gender, family_status, populations |
| Message classification | Haiku | Messages >3 words that regex can't classify | Single category name |
| Bot question answer | Haiku | Messages classified as `bot_question` | 2–3 sentence factual answer |
| Crisis detection (Stage 2) | Sonnet | Ambiguous messages where regex didn't fire | JSON: {crisis: bool, category: string} |

See the Model Analysis tab in the admin console for cost/capability analysis and the rationale for each model assignment.

---

## Confidence & Ambiguity Handling

Every routing decision is tagged with a confidence level, stored in the audit log for analysis:

| Confidence | When it's set | What it means |
|---|---|---|
| `high` | Regex classification match, service keyword extracted, confirmation action | The system is confident about the user's intent — standard handling |
| `medium` | LLM classification | The system used the LLM to determine intent — correct in most cases but may misinterpret indirect language |
| `low` | General fallback, unrecognized service redirect, correction handler | The system is uncertain — the response may not match what the user wanted |
| `disambiguated` | Disambiguation prompt shown | The system detected ambiguity and asked the user to clarify |

When confidence is `medium` or `low`, the response includes a "❌ Not what I meant" quick reply button so the user can recover immediately if the system misinterpreted their message.

The post-results handler has its own ambiguity detection: when a message pattern-matches as a post-results question (e.g., "What about X?") but the extracted name doesn't match any displayed result, the system presents a disambiguation prompt rather than guessing. This prevents users from getting trapped in the post-results flow when they're trying to start a new search.

Confidence data in the audit log enables tracking misclassification rates across routing categories. Messages logged with `confidence="low"` or `confidence="disambiguated"` can be reviewed in the admin console to identify patterns that need additional phrase coverage or routing logic.

---

## Guardrails

### The bot must never:

- **Fabricate service information.** All service names, addresses, phone numbers, hours, fees, and eligibility rules come from the Streetlives database via parameterized SQL templates. The LLM never generates service data.

- **Give specific medical, legal, psychological, or financial advice.** The bot does not diagnose conditions, suggest treatments, recommend legal strategies, or advise on financial decisions. For anything requiring professional judgment, it directs the user to a peer navigator.

- **Make promises about service availability or eligibility.** Service data comes from the database but may be outdated. The bot shows what's in the database without guaranteeing the service is currently operating or that the user qualifies.

- **Encourage specific life decisions.** The bot does not tell users what they should do — it helps them find resources so they can make their own decisions.

- **Share personal opinions or take sides.** The bot is neutral and supportive. It does not comment on a user's situation, judge their choices, or express opinions.

- **Store personally identifiable information.** PII (names, phone numbers, SSNs, emails, addresses) is redacted from all stored transcripts before they reach the audit log. Session state has a 30-minute TTL. When `PILOT_DB_PATH` is set, sessions and audit events are persisted to SQLite and survive server restarts.

- **Generate crisis resources from the LLM.** All crisis hotline numbers and resources are static strings embedded in the code, never LLM-generated. This ensures they are accurate, reviewed, and consistent.

### The bot should always:

- **Acknowledge emotions before steering.** When a user shares something emotional, the bot validates their feeling first. It does not immediately redirect to services.

- **Offer the peer navigator.** For situations requiring human judgment (complex needs, emotional distress, frustration with the system), the bot proactively offers to connect the user with a peer navigator.

- **Fail open on safety.** If the LLM is unavailable during crisis detection, the system returns a general safety response with hotline numbers rather than falling through to normal conversation.

- **Confirm before querying.** The bot always shows the user what it will search for (service type + location) and waits for explicit confirmation before executing a database query.

- **Be honest about its limitations.** When asked what it can and can't do, the bot answers directly rather than deflecting.

---

## Conversation Modes

The bot operates in two implicit modes based on whether the user has expressed a service need:

### Service search mode

Entered when slot extraction detects a service type, location, or other structured field. The bot focuses on completing the intake: asking follow-up questions for missing slots, confirming search parameters, executing the query, and presenting results. Quick-reply buttons guide the user through each step.

### Co-located multi-service queries

When a user asks for multiple services ("I need food and clothing in Brooklyn"), the system first tries a **co-located query** — a SQL filter (`FILTER_BY_COLOCATED_TAXONOMY`) that restricts results to locations where both services exist. The confirmation message lists all services: "I'll search for food and clothing in Brooklyn." If co-located results are found, the response says "I found 3 location(s) that offer both food and clothing" and the queue is cleared. If no co-located results exist, the system falls back to searching the primary service only, then offering the additional service sequentially ("You also mentioned clothing — would you like me to search for that too?"). The fallback preserves the original queue-offer behavior. Unrecognized co-located service types are silently skipped and fall back to the queue offer.

### Post-results question handler

After search results are displayed, follow-up questions are answered deterministically from the stored service card data — no LLM calls. The `post_results.py` module classifies questions into 7 intent types:

| Intent | Example | Handler |
|---|---|---|
| `filter_open` | "are any of them open now?" | Filters to open services |
| `filter_free` | "are any free?" | Filters to services with "Free" fees |
| `specific_index` | "tell me about the first one" | Shows detailed view of that service |
| `specific_name` | "tell me about The Door" | Fuzzy matches service by name |
| `ask_field` | "what's the phone number?" | Answers the field question |
| `ask_hours_day` | "are they open Saturday?" | Queries DB for that day's schedule |
| `unknown_about_results` | "do they take walk-ins?" | Honest "I don't have that info" |
| No match | (new service request) | Falls through to normal routing |

**Sort options:** After results, a "🕐 Sort by recently verified" quick reply is offered. Sort patterns ("sort by recently verified", "sort by most services", etc.) re-sort `_last_results` in Python and return the re-ordered cards.

**Pagination / show more:** Initial query fetches 25 results but displays the first 10. When undisplayed results exist, a "📋 Show N more results" quick reply is offered. "Show more", "more results", "any others", "what else" (10 patterns) return the undisplayed remainder.

**Day-specific hours:** When the user asks about a specific day ("are they open Saturday?"), the system detects the day name, queries `holiday_schedules` for that weekday (ISO DOW: Monday=1, Sunday=7), and returns per-service hours. Weekend queries fetch both Saturday and Sunday.

**Safety requirement (eval P10):** Crisis detection always runs BEFORE the post-results handler. Messages like "do they even help? I want to die" contain result-reference words ("they") that would match the post-results classifier. Without this ordering, the crisis handler would never fire.

**Stored results lifecycle:** Results are stored in `_last_results` after a successful search (up to 25 from the initial query). `_displayed_count` tracks how many the user has seen. Cleared when: the user starts a new search, resets, or the confirmation step begins for a different service. Sort and show-more operations modify `_last_results` but do not clear service slots.

### Just chatting mode

Active when the user hasn't expressed a service need (or has finished a search and is continuing the conversation). The bot responds naturally without pushing services. It can handle 2–3 conversational turns without trying to convert them into a service search. Service category buttons are not shown repeatedly — they appear on the welcome message, after a reset, or when the user explicitly asks what's available.

The transition between modes is automatic: any message containing a service keyword or slot data switches to service search mode.

---

## Emotional Handling Design

### The Acknowledge-Validate-Redirect (AVR) Pattern

The chatbot's emotional handling follows the **Acknowledge-Validate-Redirect** pattern, the dominant design approach across clinically validated mental health chatbots. This pattern has three steps:

1. **Acknowledge** the emotion immediately ("I hear you")
2. **Validate** it ("You don't have to have everything figured out")
3. **Redirect** gently — to a human (peer navigator) or practical help, but only after steps 1-2

This pattern is informed by research across several domains relevant to our population:

**Clinical chatbot research.** Woebot (CBT-based, RCT-validated) and Wysa (CBT/DBT, hybrid human+AI) both prioritize empathetic acknowledgment before any task-oriented response. A 2025 JMIR study analyzing 13,700 utterances across 8 commercial chatbots found that social chatbots excelling at empathy (SimSimi, Replika) detected emotional tone before processing intent, and that users expected chatbots to provide "a safe space to express emotions" before receiving information or advice (Chin et al., *JMIR Formative Research*, 2025).

**Social services chatbot research.** The DAPHNE chatbot (Dialog-Based Assistant Platform for Healthcare and Needs Ecosystem), designed for social need screening with low-income families, found that "fostering user confidence" was a crucial design factor and that users valued empathetic conversational design over task efficiency (Sezgin et al., *JMIR Human Factors*, 2024).

**Emotional distress in vulnerable populations.** A PMC study on chatbot use across 8 countries found that 75% of depression-related chatbot interactions involved expressing feelings rather than seeking strategies, suggesting users find relief in being heard before being helped (Chin et al., *PMC*, 2023). A Frontiers study on empathic chatbots demonstrated that empathetic responses significantly improved mood after social exclusion compared to merely acknowledging responses (Ischen et al., *Frontiers in Psychology*, 2019).

### Design Principles Applied

Based on this research, the following principles govern emotional handling in the YourPeer chatbot:

1. **Emotion detection runs before intent classification.** The split classifier (`_classify_tone()` in `classifier.py`) runs independently of slot extraction. This ensures emotional phrases like "I'm feeling scared" are recognized even when service keywords are also present.

2. **Don't push services on emotional users.** When someone expresses distress ("I'm feeling really down"), the response shows empathy and offers a peer navigator — no service category buttons. The emotional deny handler ("no" after emotional response) also avoids the full service menu, showing only a navigator button.

3. **Emotional phrases are not service keywords.** Words like "struggling", "having a hard time", and "stressed" were removed from the `mental_health` service keyword list. "stress" uses word-boundary matching (`\bstress\b`) so "I need help with stress" matches but "I'm stressed out" does not. This prevents emotional expressions from being misrouted to service search.

4. **Sub-crisis emotional phrases skip LLM crisis detection.** The LLM crisis detector is instructed to "err toward crisis=true" for ambiguous messages — appropriate for genuine safety ambiguity, but over-escalatory for clearly emotional phrases. A guard in `crisis_detector.py` checks for known sub-crisis emotional phrases before invoking the LLM, routing them to the emotional handler instead.

5. **Graduated response levels.** The system has three tiers of emotional response:
   - **Pure emotional** (no service intent): Full AVR response with navigator offer, no service buttons
   - **Emotional + service intent**: Empathetic tone prefix ("I hear you, and I want to help.") followed by service confirmation
   - **Crisis + service intent**: Crisis resources shown with step-down offer to search for the mentioned service

6. **Always offer human escalation.** Every emotional response includes a path to a peer navigator. This aligns with Wysa's hybrid model and the APA's guidance that AI chatbots should "complement—not replace—human-led" support.

### References

- Chin, H., Song, H., et al. (2025). Chatbots' Empathetic Conversations and Responses: A Qualitative Study of Help-Seeking Queries on Depressive Moods Across 8 Commercial Conversational Agents. *JMIR Formative Research*, 9(1), e71538. https://formative.jmir.org/2025/1/e71538
- Chin, H., Peng, H., et al. (2023). The Potential of Chatbots for Emotional Support and Promoting Mental Well-Being in Different Cultures. *Journal of Medical Internet Research*, 25, e48592. https://pmc.ncbi.nlm.nih.gov/articles/PMC10625083/
- Sezgin, E., et al. (2024). Chatbot for Social Need Screening and Resource Sharing With Vulnerable Families: Iterative Design and Evaluation Study. *JMIR Human Factors*, 11, e57114. https://humanfactors.jmir.org/2024/1/e57114
- Ischen, C., et al. (2019). Effectiveness of an Empathic Chatbot in Combating Adverse Effects of Social Exclusion on Mood. *Frontiers in Psychology*, 10, 3061. https://www.frontiersin.org/articles/10.3389/fpsyg.2019.03061
- American Psychological Association. (2025). Health Advisory: Use of Generative AI Chatbots and Wellness Applications for Mental Health. https://www.apa.org/topics/artificial-intelligence-machine-learning/health-advisory-chatbots-wellness-apps

---

## Known Limitations

### Conversational

- **English only.** Multi-language support (Spanish minimum) is planned but not implemented.
- **No memory across sessions.** Each session is independent. The bot cannot reference previous visits.
- **Emotional detection phrase coverage.** Common emotional phrases ("feeling down", "I'm scared", "rough day") are caught by regex. Indirect or culturally specific expressions fall through to the LLM classifier. Without an API key (regex-only mode), only the explicit phrase list is active. The emotional phrase guard in `crisis_detector.py` prevents known sub-crisis emotional phrases from being over-escalated to crisis by the LLM. Keywords that could collide between emotional expressions and service requests (e.g., "stress" matching "stressed out") use word-boundary matching to prevent false positives.
- **Shame tone not yet a distinct handler.** Shame/stigma phrases ("embarrassed to ask", "never thought I'd need help") are now detected and routed to the emotional handler with AVR acknowledgment. A dedicated shame handler with normalizing responses (e.g., "Lots of people use these services — there's nothing to be ashamed of") is planned for a future iteration.

### Search & Results

- **NYC only.** The bot cannot search for services outside New York City's five boroughs.
- **Schedule data is sparse.** Most service categories show "Call for hours" because the database lacks schedule coverage. Open/closed filtering is intentionally disabled to avoid silently excluding services.
- **No real-time availability.** Service data is as fresh as the last database update. The bot cannot verify whether a service is currently operating or has capacity.
- **Result ordering is approximate.** Results are sorted by open-now, then recently verified, then name. Proximity sorting is available only when browser geolocation is provided.

### Technical

- **Session and audit persistence.** When `PILOT_DB_PATH` is set, sessions and audit events are persisted to SQLite and survive server restarts. When unset (default), data is in-memory only and resets on restart. Chat history persistence (localStorage) mitigates display loss for the user regardless of backend mode.
- **LLM dependency for nuanced cases.** When `ANTHROPIC_API_KEY` is not set, the bot runs in regex-only mode: no LLM classification, no LLM slot extraction, no LLM conversational responses, and crisis detection is limited to regex patterns only.

---

## Extending the Chatbot

### Adding a new routing category

1. Add a phrase list constant in `phrase_lists.py` (e.g., `_NEW_CATEGORY_PHRASES`)
2. Add detection in `_classify_action()` or `_classify_tone()` in `classifier.py` at the appropriate priority level
3. Add a response constant or LLM prompt builder
4. Add routing in `generate_reply`
5. Add the category to `_CLASSIFY_SYSTEM_PROMPT` and the `valid` set in `claude_client.py`
6. Add tests: classification tests, routing tests, false-positive guards

### Adding a new service category

1. Add keywords to `SERVICE_KEYWORDS` in `slot_extractor.py`
2. Add 10–20 example utterances to `SERVICE_ROUTES` in `semantic_routes.py` — use full phrases representing how real users describe this need, not single keywords
3. Add a SQL template in `query_templates.py`
4. Add a service label in `phrase_lists.py`
5. Verify route alignment: the key in `SERVICE_ROUTES` must match the key in `SERVICE_KEYWORDS`
6. Add tests: keyword extraction, semantic route matching, query template, end-to-end routing

### Expanding semantic route coverage

To improve classification for a specific scenario (e.g., a user phrasing that currently misroutes):

1. Review Tier 3 (LLM) fallback logs — any message that reached the LLM but should have been Tier 2 is a candidate utterance
2. Add the message as an utterance to the appropriate route in `semantic_routes.py`
3. Restart — re-embedding is automatic (<2 seconds), no code changes needed
4. Run the eval suite to verify the improvement and check for regressions

**Do not** add the same utterance to multiple routes (causes nondeterministic routing). **Do not** use single keywords as utterances — use full phrases (≥3 words) that represent natural user language.

### Adding new emotional phrases

Add to `_EMOTIONAL_PHRASES` in `phrase_lists.py`. Avoid phrases that overlap with service keywords ("feeling hungry" should route to food service, not emotional acknowledgment). Add a test case to `test_emotional_classification`. Thanks to contraction normalization, you only need the expanded form — e.g., adding "not okay" will automatically match "isn't okay", "wasnt okay", "aren't okay", etc.

### Contraction normalization and intensifier stripping

`_normalize_contractions()` in `classifier.py` expands 37 common contractions (e.g., "isn't" → "is not", "i'm" → "i am") before phrase matching in `_classify_tone()` and the help negators in `_classify_action()`. This means phrase lists only need the expanded "not" form to match all contraction variants. Normalization is NOT applied to crisis detection — crisis uses explicit enumeration for safety. To add a new contraction, add it to `_CONTRACTION_MAP` in `phrase_lists.py` and add a test in `test_contraction_normalization.py`.

`_strip_intensifiers()` (in `classifier.py`) removes 19 common intensifier adverbs (really, very, so, super, extremely, just, kinda, etc.) from text before phrase matching. This allows "I'm really scared" to match "i'm scared" in the phrase list without needing every intensifier×emotion combination. Both `_classify_tone()` and `_classify_message()` check four variants of each message: cleaned, normalized (contractions expanded), stripped (intensifiers removed), and stripped_normalized (both). Intensifier stripping is NOT applied to crisis detection.

**Post-normalization phrase variants.** Because contraction normalization converts "I'm" → "I am", the `_EMOTIONAL_PHRASES` list (in `phrase_lists.py`) includes both forms: "i'm scared" and "i am scared". Without these, "I'm scared" → normalized "i am scared" → no match. The post-normalization variants cover scared, sad, down, anxious, lonely, hopeless, depressed, stuck, stressed, and pathetic.

### Modifying guardrails

LLM guardrails are embedded in three prompt builders in `responses.py`:
- `_build_conversational_prompt()` — general conversation
- `_build_empathetic_prompt()` — emotional acknowledgment
- `_build_bot_question_prompt()` — capability questions

Each prompt contains a "STRICT RULES" or "Guidelines" section that instructs the LLM on what to avoid. Changes to guardrail language should be tested by running the LLM-as-judge eval suite to verify they don't cause regressions.

### Testing

Conversation routing is covered by 193 tests in `integration/test_chatbot.py`, 56 context routing tests in `integration/test_context_routing.py`, 31 post-results boundary tests in `unit/test_post_results_boundary.py`, 26 ambiguity handling tests in `integration/test_ambiguity_handling.py`, 29 integration scenario tests in `integration/test_integration_scenarios.py`, 28 structural fix tests in `integration/test_structural_fixes.py`, 41 phrase audit tests in `unit/test_phrase_audit.py`, 19 contraction normalization tests in `unit/test_contraction_normalization.py`, 29 edge-case tests in `unit/test_edge_cases.py`, 36 crisis detection tests in `unit/test_crisis_detector.py`, 39 gender extraction tests in `unit/test_gender_extraction.py`, 34 PII redaction tests in `unit/test_pii_redactor.py`, and 88 population tests in `unit/test_populations.py` (extraction, merge, false positives, query boosts, DV crisis injection, ORDER BY generation, confirmation prefixes). Use `assert_classified(message, category)` from `conftest.py` for classification tests and `send(message)` for full routing tests.

```bash
# Run conversation tests
pytest tests/integration/test_chatbot.py -v

# Run a specific category
pytest tests/integration/test_chatbot.py -k "emotional" -v

# Unit tests only (fast)
pytest tests/unit/ -q

# Full suite
pytest tests/ -q
```
