<!-- drift:ignore-file: historical audit — see the Post-Phase-3 location note in this file for current paths of body references -->
# Multi-Service Intent — Architecture Plan

> **📎 Post-Phase-3 location note (April 2026)**: this plan was written before the `chatbot.py` → `services/chatbot/` package decomposition. Body references to `chatbot.py` in code-home attribution tables still apply conceptually; the current paths are:
> - Queue storage + offer-after-results logic → `backend/app/services/chatbot/execution.py`
> - Confirmation listing all queued services + queue-clear-on-success → `backend/app/services/chatbot/handlers/confirmation.py`
> - Co-located multi-service response message → `backend/app/services/chatbot/execution.py`
> - Top-level confirmation-message builder (unchanged location) → `backend/app/services/confirmation.py`

## Problem

The chatbot originally assumed one service type per message. Classification gated whether slot extraction even ran, leading to three ad-hoc workarounds that have now been eliminated.

## Status

| PR | Status | Description |
|---|---|---|
| PR 1 | ✅ Done | Extract all service types from a message |
| PR 2 | ✅ Done | Split classifier, extract-first routing, tone prefixes, bug fixes |
| PR 3 | ✅ Done | Service queue — offer queued services after results |
| PR 4 | ✅ Done | LLM extractor — update schema for multi-service |
| PR 5 | ✅ Done | Co-located queries — prioritize locations with all requested services |
| PR 6 (Apr 2026) | ✅ Done | Close multi-intent & frustration coverage gaps — A.4 queue-clearing on service change, B.1 negative-preference phrase expansion, B.2 compound-intent override, A.1.b immigration acknowledgment, A.1.a/A.2/A.3 eval expectation updates. See "Completed: PR 6" section below. |
| PR 7 (Apr 2026) | ✅ Done | Close bot-identity phrase gap + topic-shift disambiguation — C.1 phrase expansion (`what's your name`, `who made you`, etc.), C.2 disambiguation prompt in `_handle_post_pending_confirmation` Path 3. Defense-in-depth against surreal non-sequiturs during pending confirmations. See "Completed: PR 7" section below. |

---

## Completed: PR 1 — Extract All Service Types

**`_extract_all_service_types(text)`** scans for ALL service keywords in a message with:
- Span tracking to prevent sub-matches ("mental health" doesn't also match "health")
- Forward scanning past overlapping spans ("food stamps and food" finds both)
- Need-based priority ordering (Maslow/Housing First/SAMHSA — shelter before food, medical before clothing; text position is tiebreaker within the same tier)
- Category deduplication ("food" and "food pantry" → one "food" entry)

**Hybrid multi-intent extraction:** The semantic router now runs on every message via `classify_all_services()`, scoring all routes against the message embedding. Services that regex missed (e.g., "anywhere to sleep" → shelter) are merged into `additional_services`. The `exclude` parameter skips routes regex already found.

**`extract_slots(message)`** returns `additional_services` — a list of `(service_type, service_detail, location)` tuples beyond the primary. `merge_slots()` skips this field; `has_new_slots` checks exclude it.

**Bug fixes applied during audit:**
- `find()` only returned first occurrence — now scans forward past overlapping spans
- Order reflected keyword length, not text position — now sorted by need-based priority
- Added 7 negation phrasings to shelter keywords ("anywhere to sleep", "nowhere to go", etc.)

**Tests:** 17 covering multi-service extraction, ordering, overlap, deduplication, additional_services in extract_slots/merge_slots.

## Completed: PR 2 — Split Classifier + Extract-First Routing

### Architecture

```
message
  ↓
┌──────────────────────────┐
│ 1. extract_slots(message)│  ← always runs first (regex, <1ms)
│    has_service_intent?   │
└───────────┬──────────────┘
            ↓
┌──────────────────────────┐
│ 2a. _classify_action()   │  → reset, greeting, confirm_yes, help, escalation, etc.
│ 2b. _classify_tone()     │  → crisis, frustrated, emotional, confused, urgent, None
└───────────┬──────────────┘
            ↓
┌──────────────────────────────────────────────────┐
│ 3. Combined routing (priority order)             │
│                                                  │
│  crisis tone           → crisis resources        │
│  reset action          → clear session           │
│  confirmations/bot/etc → action handlers         │
│  service + escalation  → service only if location│
│  service + any tone    → service + tone prefix   │
│  help (no service)     → help handler            │
│  escalation (no svc)   → escalation handler      │
│  frustrated            → frustration handler     │
│  emotional             → emotional handler       │
│  confused              → confused handler        │
│  LLM (>3 words)        → LLM classification      │
│  none                  → general conversation    │
└──────────────────────────────────────────────────┘
```

### What was removed
- `_has_service_words` — eliminated entirely
- `help_slots = extract_slots()` ad-hoc guard in help handler
- `esc_slots = extract_slots()` ad-hoc guard in escalation handler
- Second-pass emotional/confused check after slot extraction
- Double slot extraction (service section now uses `early_extracted`)

### Tone prefixes
When service intent + emotional tone are both present, the confirmation and follow-up messages get an empathetic prefix:

| Tone | Prefix |
|---|---|
| emotional | "I hear you, and I want to help." |
| frustrated | "I understand this has been frustrating. Let me try something different." |
| confused | "No worries — let me help you with that." |
| urgent | "I can see this is urgent — let me find something right away." |

Also applied to pending confirmation re-show (defensive, for future tones).

### Escalation guard
Service intent only overrides escalation when BOTH service_type AND location are present. "Connect me with a navigator about food" (food but no location) stays as escalation — the user wants human help. "Navigator, client needs shelter in East Harlem" (both present) routes to service.

### Bug fixes applied during audit
- Escalation guard regression — restored location requirement
- Tone prefix used `has_service_intent` (regex only) — changed to `category == "service"` to cover LLM-detected intent
- Tone-aware nudge prefix added to pending confirmation re-show

### Tone categories (5)
Based on research from NCBI clinical studies on homeless populations, ISEAR emotion models, and the DAPHNE social needs chatbot:

| Tone | Priority | Rationale |
|---|---|---|
| crisis | 1 | Acute danger — always wins |
| frustrated | 2 | System frustration — check before urgency |
| emotional | 3 | Sadness, fear, loneliness — covers the dominant emotional states in this population |
| confused | 4 | Overwhelm, not knowing what to do |
| urgent | 5 (lowest) | Time pressure — empathy matters more when stronger tones co-occur |

**Future tone candidates** (not blocking, easy to add later):
- `shame` — "I'm embarrassed to ask" → normalizing response. Distinct from emotional.
- Anger collapses into frustrated. Distrust into privacy bot_question. Hopelessness into emotional/crisis.

**Tests:** 40+ covering split classifier functions, combined routing, tone prefixes, escalation guard, taxonomy enrichment.

---

## Completed: PR 3 — Service Queue

### Goal
After delivering results for the first service, proactively offer the next queued service. "I need food and shelter in Brooklyn" → food results → "You also mentioned shelter — want me to search for that too?"

### Design decisions

**1. When to store the queue**

Store `_queued_services` in session when `additional_services` is non-empty in the service flow. This should happen at the point where we merge slots and proceed to confirmation — not earlier (the user might change their mind during follow-up questions).

```python
# In the service section, after merge:
additional = extracted.get("additional_services", [])
if additional:
    merged["_queued_services"] = additional
```

**2. When to offer the next service**

After `_execute_and_respond` delivers results. Append the offer to the response and replace quick replies:

```python
# In _execute_and_respond, after building the result:
queued = slots.get("_queued_services", [])
if queued:
    next_service, next_detail = queued[0]
    remaining = queued[1:]

    # Update session: remove offered service, keep remaining
    slots["_queued_services"] = remaining
    save_session_slots(session_id, slots)

    label = next_detail or _SERVICE_LABELS.get(next_service, next_service)
    result["response"] += (
        f"\n\nYou also mentioned {label} — would you like me to "
        f"search for that too?"
    )
    result["quick_replies"] = [
        {"label": f"✅ Yes, search for {label}", "value": f"I need {next_service}"},
        {"label": "❌ No thanks", "value": "No thanks"},
    ]
```

**3. How "Yes, search for shelter" works**

The quick reply sends `"I need shelter"` as a new message. This enters `generate_reply` normally:
- Extracts service_type = "shelter"
- Location, age, family_status are already in session from the previous search
- Proceeds to confirmation (or straight to search if enough info)

This is the simplest approach — no special queue-consumption logic needed. The quick reply value IS the new search request.

**4. How "No thanks" works**

"No thanks" enters `generate_reply`:
- `_classify_action` → "confirm_deny"
- No pending confirmation → falls through
- `_queued_services` should be cleared
- Show a wrap-up message

Need to add: when category is "confirm_deny" and no pending confirmation but `_queued_services` is set, clear the queue and respond gracefully:

```python
if not pending and category == "confirm_deny" and existing.get("_queued_services"):
    existing.pop("_queued_services", None)
    save_session_slots(session_id, existing)
    result = _empty_reply(
        session_id,
        "No problem! Let me know if you need anything else.",
        existing,
        quick_replies=list(_WELCOME_QUICK_REPLIES),
    )
    return result
```

**5. What "start over" does to the queue**

`clear_session` already clears all session state including `_queued_services`. No changes needed.

**6. Shared slots across queued services**

When searching for the next queued service, the session retains location, age, family_status from the previous search. This is usually correct — "I need food and shelter in Brooklyn" means both in Brooklyn. But edge cases exist:

- "I need food in Brooklyn and shelter in Manhattan" — the location would be wrong for the second service. This is a pre-existing limitation (regex extracts only one location). The user can correct via "change location" during the queued service confirmation.
- Service-specific slots like `family_status` (only relevant for shelter) should not cause issues — they're ignored by non-shelter query templates.

**7. What if the queue has 3+ services?**

After the second service's results, check if more are queued. Same logic applies recursively. In practice, 3+ services in one message is rare.

**8. What if the user ignores the queue offer and types something new?**

The new message enters `generate_reply` normally. If it has a new service_type, slots are merged (overwriting the old service_type). The queue should be cleared when a new explicit service request comes in, to avoid confusing the user with stale offers.

```python
# At the top of the service section, if new service_type differs from existing:
if (extracted.get("service_type") and existing.get("service_type")
        and extracted["service_type"] != existing["service_type"]):
    existing.pop("_queued_services", None)
```

### Files to change

| File | Change |
|---|---|
| `chatbot.py` | Store queue in router, offer after results, handle "no thanks", clear on new service |
| No other files | Queue is purely a chatbot session concern |

### Test scenarios

```
"I need food and shelter in Brooklyn"
  → confirms food → results → "You also mentioned shelter..."
  → "Yes" → confirms shelter → results

"I need food and shelter in Brooklyn"
  → confirms food → results → "You also mentioned shelter..."
  → "No thanks" → clears queue, wrap-up message

"I need food, clothing, and legal help in Manhattan"
  → confirms food → results → "You also mentioned clothing..."
  → "Yes" → results → "You also mentioned legal help..."

"I need food and shelter in Brooklyn" → food results → "start over"
  → clears everything including queue

"I need food and shelter in Brooklyn" → food results
  → user ignores queue offer and types "I need medical care"
  → new service replaces food, queue cleared

"I need food and shelter in Brooklyn" → food results
  → user types "that wasn't helpful"
  → frustration handler fires, queue preserved for later
```

---

## Completed: PR 4 — LLM Extractor Updates

### Current state

The LLM extractor (`extract_slots_llm`) returns a single `service_type` string. When `extract_slots_smart` calls it, the result is supplemented with regex-extracted fields including `additional_services`. This means multi-service already partially works via LLM:

1. Regex extracts: `{service_type: "food", additional_services: [("shelter", None)]}`
2. LLM extracts: `{service_type: "food"}` (single service)
3. Supplementation: regex `additional_services` is added to LLM result

So even without LLM schema changes, `additional_services` from regex is available.

### What the LLM schema change adds

The LLM could detect services that regex misses — e.g., "I need somewhere to eat and a place to crash" might not match "shelter" in regex but the LLM would understand "place to crash" = shelter.

**Option A: Array service_type** (breaking change to schema)
```python
"service_type": {
    "type": "array",
    "items": {"type": "string", "enum": [...]},
}
```
Pro: Clean. Con: Breaks existing parsing, needs migration.

**Option B: Additional field** (additive, recommended)
```python
"additional_service_types": {
    "type": "array",
    "items": {"type": "string", "enum": [...]},
    "description": "Service types beyond the primary one."
}
```
Pro: Backward compatible. Con: Two fields for the same concept.

**Recommendation:** Option B. The LLM returns `service_type` (primary) + `additional_service_types` (extras). The smart extractor merges them into the standard `additional_services` format. No breaking changes.

### Priority

Low — regex already handles the common multi-intent cases ("food and shelter", "clothing and legal help"). The LLM adds value only for indirect multi-service requests, which are rare. PR 3 (queue handling) is more impactful and should ship first.

## Completed: PR 4 — LLM Extractor Multi-Service (Option B)

Added `additional_service_types` array field to the LLM tool schema.
The LLM returns `service_type` (primary) + `additional_service_types`
(extras). `extract_slots_smart()` merges LLM-detected additional
services with regex-detected ones, deduplicating by category. Regex
results take precedence when both detect the same service (regex
preserves detail info like "food stamps").

**What this adds over regex:** The LLM can detect services from indirect
phrasing that regex misses — e.g., "a place to crash" → shelter,
"someone to talk to" → mental_health, "somewhere to eat" → food.

**Tests:** 7 new tests covering LLM additional extraction, smart merge,
deduplication, primary exclusion, detail preservation, and key cleanup.

---

## Architecture Diagram

```
User: "I'm struggling and need food and shelter in Brooklyn"
  │
  ├─ extract_slots()
  │   → service_type: "food"
  │   → additional_services: [("shelter", None)]
  │   → location: "Brooklyn"
  │
  ├─ _classify_action() → None
  ├─ _classify_tone()   → "emotional"
  │
  ├─ Combined routing
  │   → has_service_intent + emotional tone
  │   → category: "service"
  │   → tone_prefix: "I hear you, and I want to help."
  │
  ├─ Service flow
  │   → Store _queued_services: [("shelter", None)]    ← PR 3
  │   → Confirm: "I hear you, and I want to help.
  │               I'll search for food in Brooklyn."
  │
  ├─ User confirms → _execute_and_respond
  │   → DB query: food + Brooklyn
  │   → Results: 5 food services
  │   → Append: "You also mentioned shelter —           ← PR 3
  │              would you like me to search for that?"
  │
  └─ User taps "Yes, search for shelter"
      → New message: "I need shelter"
      → Location "Brooklyn" already in session
      → Confirm → execute → shelter results
```

---

## Completed: PR 5 — Co-Located Multi-Service Queries

Instead of always searching the primary service first and then offering queued services sequentially, PR 5 tries to find **locations that have all requested services** at the same address.

### How it works

1. **Confirmation lists all services:** "I'll search for food and clothing in Brooklyn." (not "food" with clothing mentioned later)

2. **Co-located SQL filter:** A new `FILTER_BY_COLOCATED_TAXONOMY` is added as a universal optional filter in `build_query()`. When `colocated_taxonomy_names` is in params, it adds an EXISTS subquery checking for other services at the same location matching the secondary taxonomy names.

3. **Taxonomy resolution:** `query_services()` resolves each co-located service type to its template's `default_params.taxonomy_names`. For "clothing", this becomes `["clothing", "clothing pantry", "interview-ready clothing", ...]`.

4. **Smart fallback:** If the co-located query returns 0 results, `query_services()` retries without the filter and sets `colocated_fallback=True`. The chatbot then falls back to the original sequential queue-offer behavior.

5. **Response messaging:**
   - Co-located success: "I found 3 location(s) that offer both food and clothing."
   - Fallback: "I found 3 option(s) for you. ... You also mentioned clothing — would you like me to search for that too?"

### Data validation

SQL analysis of the Streetlives database confirms the value:
- **535 locations (30%)** have 2+ services
- Top locations like The Door have 19 different offerings (food, clothing, health, laundry, showers, etc.)
- Food search: 15/15 tested locations had co-located services users would never see without this feature

### Files changed

| File | Change |
|---|---|
| `query_templates.py` | New `FILTER_BY_COLOCATED_TAXONOMY` filter, universal optional filter in `build_query()` |
| `app/rag/__init__.py` | `colocated_service_types` param, taxonomy resolution, fallback retry |
| `chatbot.py` / `confirmation.py` | Confirmation lists all services, co-located response message, queue cleared on success |

### Bug fixes during implementation

- **Single-item queue decline silently dropped:** Added `_queue_offer_pending` flag so decline handler fires even when the queue was popped at offer time
- **Unrecognized co-located service type claimed success:** Added `colocated_fallback=True` when no taxonomy names could be resolved
- **`_queued_services_original` leaked across sessions:** Cleaned up in decline handler and new confirmation setup

---

## Future Improvements (not blocking)

### Eval scenarios for multi-intent
30 LLM-as-judge scenarios added in `multi_intent_eval_scenarios.py` (appended to SCENARIOS in eval_llm_judge.py):
- ✅ Two services extracted and both searched sequentially (4 core queue scenarios)
- ✅ User declines queued service (2 — formal and informal phrasing)
- ✅ User changes location mid-queue (2 — typed and button)
- ✅ Emotional + multi-service gets empathetic framing on both (5 — emotional, urgent, confused, frustrated, second-service warmth)
- ✅ Shame tone — normalizing response for embarrassment/stigma (3)
- ✅ Cross-service slot conflicts — different locations per service (2)
- ✅ YourPeer persona scenarios (6 — LGBTQ/Ali Forney, DYCD RHY, foster aging-out, asylum seeker, re-entry, family/PATH)
- ✅ Queue edge cases (2 — ignore with new request, start over)
- ✅ Three-service combos (2 — drop-in trio, asylum seeker trio)
- ✅ Complex natural language (2 — substance use narrative, outreach worker referral)

### Shame tone
Research identified shame/embarrassment as a distinct emotional state in this population ("I'm embarrassed to ask", "I never thought I'd need a food bank"). Current `emotional` tone covers it but the response should normalize rather than just empathize. Easy to add: new phrase list in `_classify_tone()`, new handler with normalizing response.

**Eval coverage:** 3 scenarios test shame/embarrassment detection and normalizing responses: `multi_shame_food_bank_first_time`, `multi_shame_shelter_stigma`, `multi_shame_single_service`.

### Cross-service slot conflicts (Completed — Run 23, Phase 4)
"I need food in Brooklyn and shelter in Manhattan" — per-service location binding implemented. `_extract_all_locations()` finds all location matches with text positions. `extract_slots()` binds each service to its nearest location. Queue offers include per-service location ("You also mentioned shelter in Manhattan"). Quick reply values include location for slot extraction on acceptance.

**Update (April 2026 — A.3 investigation):** The earlier note that `multi_cross_borough` scored 3.88 because "the binding doesn't fire in all cases" was incorrect. Investigation proved the binding does fire correctly and produces a coherent runtime state (`service_type=shelter` primary bound to `manhattan`, with `food-in-brooklyn` queued as a secondary). The eval scenario's `expected` dict was encoding a different rule ("first-mentioned wins" → `service_type=food, location=brooklyn`), inconsistent with the priority-ordered extractor behavior and with sister scenarios like `multi_asylum_seeker_food_legal`. Fix was to update the eval expectation rather than the code. See PR 6 below.

---

## Completed: PR 6 — Close multi-intent & frustration coverage gaps (April 2026)

Seven targeted fixes addressing eval scenarios that were either failing or scoring below threshold due to gaps in multi-intent coverage, frustration detection, or stale eval expectations. Landed as a single PR with seven atomic commits. Full per-fix rationale lives in the PR description; summary below.

### Production code changes

**A.4 — Clear queue state on service_type change.** `merge_slots` in `slot_extraction_regex.py` now clears `_queued_services`, `_queued_services_original`, and `_queue_offer_pending` when the user explicitly changes `service_type` (non-additive intent). Closes the bug where `"food in Manhattan"` → `"actually, shelter"` produced `"shelter AND food"` because the queue from the prior multi-intent turn persisted. The primary failure path was `_handle_post_pending_confirmation` → `merge_slots` in `handlers/confirmation.py:561`, which bypassed the orchestrator's queue-pop at `orchestrator.py:359-364`. `_is_additive` branch excluded.

**B.1 — Expand negative-preference phrase list.** 16 new phrases added to `_NEGATIVE_PREFERENCE_PHRASES` in `classifier.py` in two clusters: "already tried" variants (8) and "this isn't helpful" variants (8). Closes the gap where `"This isn't helpful at all. I already tried those places."` matched nothing and produced an apology-wrapped functional repeat. `"this isn't helpful"` variants were intentionally reclassified from `frustrated` tone to `negative_preference` action — the refine-search UX is strictly better than the frustration UI's "🔍 New search" button for this signal. See `docs/audits/PHRASE_LIST_AUDIT.md` Audit Trail for the full phrase additions.

**B.2 — Compound-intent override.** Surfaced post-B.1 as a regression: `"I already tried those, I need shelter instead"` would match B.1's phrase and route to the menu, dropping the shelter intent. Fixed with an orchestrator-level override at the `negative_preference` dispatch site: when `early_extracted.service_type` differs from existing, downgrade the action to the service flow and promote `tone="frustrated"`. Bare rejections (no new service intent) still hit the menu. See `docs/CHATBOT_BEHAVIOR.md` Negative Preference section for the full trigger truth table.

**A.1.b — Immigration context acknowledgment prefix.** New `_immigration_acknowledgment(slots)` helper in `handlers/accessibility.py`, wired into the orchestrator's `_prefix_prepend` chain alongside the existing PII warning and Spanish acknowledgment. Fires when slot state contains `service_detail in {"asylum services", "immigration services"}` AND primary `service_type != "legal"`. Emits `"You also mentioned your [asylum|immigration] case — I can help find immigration legal services after this."` Dual-reads both `additional_services` (extractor-output key) and `_queued_services` (post-merge orchestrator key) via `_QUEUE_KEYS`. See `docs/CHATBOT_BEHAVIOR.md` Response Prefix Chain section.

### Eval expectation updates (test-data only)

**A.2 — Rename `should_queue_additional` → `should_handle_additional_service`.** The old key encoded a specific implementation strategy (sequential queue); runtime correctly accepts either queueing OR co-located single-search. 22 occurrences renamed + header comment documenting the accepted-either-way semantic.

**A.3 — Cross-borough scenario expectation.** Investigation proved the extractor correctly handles `"food in Brooklyn and shelter in Manhattan"` — binds each service to its own location, picks shelter as priority-ordered primary. The eval's expected (`food, brooklyn`) encoded first-mentioned semantics. Updated expected to `shelter, manhattan`; rewrote description.

**A.1.a — Three-service asylum-seeker scenario expectation.** Same structural issue as A.3, plus an internal-inconsistency finding: the SISTER scenario `multi_asylum_seeker_food_legal` already expected `food` primary (priority-ordered). Aligned the two scenarios.

### Regression analysis

Post-implementation cross-fix analysis (seven interaction probes) surfaced INT-3 (B.1 dropping service intent in compound messages) which was resolved in-PR by B.2. Six other probes confirmed clean composition. One soft concern flagged for post-merge monitoring: triple-prefix stacking (PII + Spanish + A.1.b) can emit ~400 characters of meta-statements before the confirmation on a single compound message. Each prefix is tonally apt in isolation but stacked experience could feel heavy. See `REGRESSION_ANALYSIS.md` in the PR outputs.

### Deferred to future PRs

- **B.1 Part 2 — identical-response detector** (orchestrator-level text-similarity check as defense-in-depth against future phrase-list misses).
- **Extend semantic router to `INTENT_ROUTES`.** Current asymmetry: router covers only `SERVICE_ROUTES` and `POPULATION_ROUTES`, so intents (`negative_preference`, `frustration`, `reset`, `bot_question`) have only Tier 1 regex and Tier 3 LLM — no Tier 2 middle ground for durable coverage against novel phrasings.
- **Add `edge_compound_rejection_with_new_service_intent` scenario** to `eval_llm_judge.py` to lock B.2's behavior at eval-measurement level.

---

## Completed: PR 7 — Bot identity phrase gap + topic-shift disambiguation (April 2026)

Follow-on to PR 6, addressing two related pre-existing issues surfaced by reviewing the same failure mode (surreal non-sequitur on `"what's your name?"` during pending confirmation). Two atomic fixes:

### Production code changes

**C.1 — bot_identity / bot_question phrase expansion.** 17 phrases added across two lists (8 to `_BOT_IDENTITY_PHRASES`, 9 to `_BOT_QUESTION_PHRASES`); see `docs/audits/PHRASE_LIST_AUDIT.md` Audit Trail for the full enumeration. Intentional reclassification: `"who are you"` moved from `help` to `bot_identity`. Target bug: direct identity questions (`"what's your name?"`, `"who are you?"`) during pending confirmations silently re-nudged the prior search instead of identifying the bot. One pre-existing test updated to match.

**C.2 — Topic-shift disambiguation in `_handle_post_pending_confirmation` Path 3.** New helper `_looks_like_topic_shift_question(message)` + new branch before the re-nudge. When a pending confirmation exists and the user sends a substantive question (≥3 words, wh-word or auxiliary-verb opener, OR ≥4 words ending in `?`), the bot emits a disambiguation prompt and adds a `"💬 I was asking something else"` quick reply — rather than re-nudging blindly. Target bug: novel question phrasings that slip past classifier phrase lists produced the same surreal-non-sequitur UX. Conservative heuristic by design — 2-word questions, fragments, and confirmation-shaped utterances still fall through to the re-nudge path.

### Why both fixes (defense-in-depth)

C.1 closes the common case at Tier 1 (classifier-level). C.2 closes the novel-phrasing case at Tier 3 (fallback-level). Either alone would leave a gap: C.1 only covers phrases we've anticipated, and phrase lists are by definition incomplete. C.2 only fires at the fallback layer, which is a worse user experience than a clean bot_identity response. Together they form a layered guarantee that pending-confirmation state is never silently re-asserted against a clear topic shift.

### Verification

- Full suite: 3845 passed, 17 skipped, 3 xfailed (was 3829 pre-C; net +16 tests).
- Regression-guard discipline proven: stripping C.1 phrases causes `test_c1_regression_guard_whats_your_name_during_pending_confirmation` to fail (falls through to C.2, which catches it with the disambiguation prompt instead of a bot_identity response — exactly the defense-in-depth behavior). Stripping the C.2 branch causes `test_c2_novel_off_topic_question_during_pending_gets_disambiguation` to fail with the expected evidence: `"Got: Just to make sure — I'll look for food in Brooklyn — sound good? Tap 'Yes, search' to go, or you can change the details."` — the literal surreal non-sequitur.

### Paired follow-ups also deferred

- **Add semantic router coverage for bot_identity / bot_question.** Same asymmetry issue as mentioned above for negative_preference — the three-tier cascade skips Tier 2 for intent routes.
- **Add `edge_off_topic_question_during_pending_confirmation` eval scenario.** LLM-judge coverage for C.2's disambiguation behavior. Current test coverage is at the integration layer only.
- **Audit `_handle_post_pending_confirmation` Path 3 for other silent-retention bugs.** This same pattern (retain pending state, nudge with the stale intent) might be wrong in other contexts too — for instance, if the user sent an emotional message during pending, the current code relies on the `response_tone == "emotional"` nudge-prefix branch to soften the re-nudge, but the re-nudge itself may still be the wrong move.
