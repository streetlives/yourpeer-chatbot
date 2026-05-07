# Transition-Event Bundle Pattern

**Status:** 📋 DRAFT — Open for team discussion
**Owner:** TBD
**Created:** 2026-05-07
**Audience:** Streetlives engineering, data stewards, community-information specialists
**Related:** `peer_aging_out_foster` (R32: 3.55, persistent failure across R28-R32 fix attempts); `wa_negative_preference` (R32: 3.91 → R42: 4.45); `multi_three_services_legal_benefits_food`; future scenarios for reentry, asylum, DV escape, pregnant teen, newcomer.

---

## Executive summary

The chatbot currently models user intent as **one primary service request** with optional multi-intent additions. For users in a **transition event** — a life-status change that creates simultaneous needs across multiple service categories with time-sensitive components — that model is structurally wrong. The bot defaults to a single service and silently drops the implicit bundle.

This document proposes a **transition-event detection layer** that runs at extraction time and populates new slot fields consumed at four downstream points (slot merge, confirmation, quick replies, results overlay). The pattern targets at least seven recognized transition events: foster youth aging out, reentry from incarceration, asylum seeker arrival, domestic violence escape, pregnant teen, newly diagnosed (HIV/disability), newcomer to NYC.

The work is structured as four phases over ~4 weeks of engineering, with explicit gates between phases for Streetlives staff review and data steward sign-off on resource curation.

This is a structural commitment, not a single-eval patch. The motivation is that R28→R32 attempts to fix `peer_aging_out_foster` (population tag, aftercare resources, tone routing) have all hovered at 3.4-3.6 because each bolts context onto the single-service model rather than changing the model.

## Problem

### Single-service mental model breaks for transition events

The current pipeline assumes the user has one primary service need. The slot extractor returns a primary `service_type` plus optional `additional_services`. The flow is built around confirming the primary, dispatching a search, and offering follow-ups for the additional services as a queue.

This works well for "I need food in Brooklyn" or even "I need food and shelter in the Bronx" — concrete service requests where the user can name what they want.

It breaks for messages like:

- *"I'm aging out of foster care next month and have nowhere to go"*
- *"I just got out of prison last week, I need to get back on my feet"*
- *"I just arrived from Venezuela seeking asylum, I don't know where to start"*
- *"I left my husband last night with my kids, we're at a friend's"*
- *"I'm 17 and just found out I'm pregnant, I don't know what to do"*

In each case, the user is naming a **transition event**, not a service. The bot extracts the closest single service (usually shelter), confirms, and dispatches. The bundle that should accompany the transition (employment, benefits, legal, population-specific resources, trauma-informed acknowledgment) is silently dropped.

### Eval evidence

The persistence of `peer_aging_out_foster` across runs is the clearest signal:

| Run | Score | Fix attempted |
|---|---|---|
| R26 | 4.12 | (passing) |
| R28 | 3.36 | new dimensions (Opus, dignity, cultural) — exposed the gap |
| R29 | 3.45 | tone improvements |
| R30 | 3.18 | warm reframe |
| R31 | 3.55 | foster_youth population tag, aftercare resources |
| R32 | 3.55 | (no fix) |

The judge's R32 feedback names the structural issue directly:

> "The system failed to recognize 'aging out of foster care' as a multi-need, high-urgency situation requiring a broader response than a single shelter search. The expected behavior — offering multiple service categories (shelter, employment, benefits, legal) and acknowledging the transition — was not achieved."

Same pattern across multiple dimensions:

- **Slot Extraction 2/5** — "should have recognized this as a 'I don't know what I need' scenario and offered multiple categories"
- **Safety & Crisis 3/5** — "system treated it as a medium-urgency shelter search rather than recognizing the cliff of lost services"
- **Cultural Responsive 3/5** — "made no effort to explain what resources exist specifically for this population (DYCD, ACS aftercare)"
- **Dignity 3/5** — "no acknowledgment of their resilience, no normalization of seeking help, and no recognition that this is a systemic issue rather than a personal failing"

Tactical fixes (a foster-specific response augmentation) earn ~0.5 points on the single scenario. The same gap will surface for the other six transition events when scenarios for them are added to the eval suite.

### The pattern across transition events

Each transition event shares five structural characteristics:

1. **Multi-category need bundle** — usually 3-5 services that go together
2. **Magnitude** — life-altering, not transactional
3. **Time-sensitive** — there's often a date (release, arrival, eviction, due date) that creates urgency
4. **Population-specific resources** — programs designed for this transition (DYCD aftercare for foster, reentry services, asylum legal clinics)
5. **Trauma-informed framing matters** — the user is at a vulnerable moment

A pattern-level fix addresses all seven. A scenario-level fix addresses one and creates seven incomplete special cases.

## Goals

1. **Detect transition events** at extraction time with high precision.
2. **Populate an implicit service bundle** without auto-queuing searches the user didn't ask for.
3. **Surface population-specific resources** (DYCD aftercare, asylum legal clinics, etc.) that may not exist in Streetlives DB.
4. **Acknowledge the magnitude** with trauma-informed framing reviewed by Streetlives staff.
5. **Generalize** — adding a new transition is data-table work, not architectural work.
6. **No regression** on the ~95% of conversations that aren't transition events.

## Non-Goals

- **Replacing the existing service flow.** Single-need users hit the same path they always did. The transition layer is additive.
- **Auto-searching across all bundle services.** Bundle surfaces as opt-in quick replies; the existing queue offer doesn't auto-fire.
- **Complete population taxonomy.** The static table covers high-frequency, high-stakes transitions. Long-tail transitions either land in Tier 3 (Phase 4, optional) or get filed for promotion when usage shows them.
- **Crisis detection redesign.** DV escape overlaps with crisis but is handled via coordination, not consolidation.
- **A confidence_reason consumer.** The transition layer is independent of the routing-confidence work shipped in May 2026.

## Architecture

### Detection: 3-tier matching, mirrors existing extraction

Reuses the regex / semantic / LLM cascade so the transition layer doesn't introduce a new pattern engineers have to learn.

| Tier | Mechanism | When it fires | Coverage |
|---|---|---|---|
| 1 | Regex against `_TRANSITION_TRIGGERS` table | Always | ~80% of common phrasings |
| 2 | Sentence-transformer embeddings against canonical utterances | Tier 1 misses | ~15% additional (synonyms, paraphrases) |
| 3 | LLM extraction tool gains optional `transition_event` parameter | Tier 1+2 miss AND message has multi-need signals | Long-tail (Phase 4, optional) |

Identical structure to the regex/semantic/llm_gate cascade for `service_type`. Same telemetry shape, same `extraction_source` field semantics.

### Slot schema additions

Three new fields on the `Slots` typed dict and `MessageContext`:

```python
{
    "transition_event": Optional[str],
    # Canonical ID — "foster_youth_aging_out", "reentry_from_incarceration",
    # "asylum_seeker_arrival", "dv_escape", "pregnant_teen",
    # "newly_diagnosed", "newcomer_to_nyc". None when no transition detected.

    "transition_urgency_signals": list[str],
    # Parsed temporal anchors from message — "next_month", "in_2_weeks",
    # "tonight", etc. Used to boost urgency conditionally on the transition
    # context (so "next month" is high urgency for foster aging-out but
    # not for an unrelated service request).

    "transition_implicit_bundle": list[str],
    # Service types that go with this transition. Sourced from the
    # transitions table, not LLM-derived. Used to populate quick replies
    # and (optionally) populate additional_services for opt-in queuing.
}
```

All three default to None / empty. Backward compatibility: existing handlers that don't read these fields are unaffected.

### Consumption: four integration points

The detection layer's outputs are consumed at four points in the pipeline:

#### 1. Slot merge (`merge_slots`)

When a transition is detected, `additional_services` is populated from the bundle **as candidates, not auto-queued**. The user sees them as quick replies in step 2 and 3 below; only an explicit click queues a follow-up search.

This avoids the "bot is overcommitting me to multiple searches" UX failure (see Risk #2).

#### 2. Confirmation message (`_build_confirmation_message`)

When `transition_event` is set, a transition acknowledgment is prepended to the confirmation:

> *"Aging out of foster care is a major transition. Beyond shelter, you'll likely also need employment support, benefits enrollment, and legal help — programs like DYCD aftercare exist specifically for this. Let me start with shelter — does that sound right?"*

The acknowledgment template is sourced from the transitions table. Each template:
- Names the magnitude
- Names the bundle
- Names a population-specific resource
- Defers to user agency ("let me start with X")

Idempotent across the session: shown once, not repeated on follow-up turns.

#### 3. Quick replies

The bundle surfaces as visible affordances:

```
[💼 Employment]  [🏛 DYCD aftercare]  [🏥 Benefits]  [⚖️ Legal]  [🤝 Peer navigator]
```

Up to 4 bundle buttons + standard navigator fallback. Order matches the bundle list in the transitions table (curated by Streetlives staff).

#### 4. Results overlay (`_render_service_cards`)

When the search returns results AND `transition_event` is set, population-specific resources from `transition_resources.json` appear at the top of the list with a `[Population-specific]` flag.

This is the primary mechanism for surfacing DYCD aftercare, ACS aftercare, asylum legal clinics, and similar resources that may not be in Streetlives DB or may not be discoverable via the standard taxonomy filters.

### Data tables

Two new data files with clear ownership:

```
backend/app/data/transitions.json           # detection rules + acknowledgments
backend/app/data/transition_resources.json  # curated population-specific cards
```

Both are version-controlled, both have `review_status` fields, both reviewed by data stewards before promotion to production.

#### `transitions.json` schema

```json
{
  "foster_youth_aging_out": {
    "trigger_regex": "(?:aging\\s+out|leaving\\s+(?:foster|the\\s+system))",
    "trigger_phrases_strict": [
      "aging out of foster care",
      "out of the system",
      "leaving foster care"
    ],
    "trigger_phrases_loose": ["aging out", "leaving foster"],
    "negative_anchors": ["high school", "school", "college"],
    "primary_service": "shelter",
    "implicit_bundle": ["shelter", "employment", "benefits", "legal"],
    "population_tags": ["foster_youth", "transition_age_youth"],
    "urgency_temporal_anchors": {
      "regex": "in\\s+(\\d+)\\s+(?:weeks?|months?)|next\\s+month",
      "boost_to": "high"
    },
    "acknowledgment_template": "Aging out of foster care is a major transition. Beyond {primary_service}, you'll likely also need {bundle_summary} — programs like DYCD aftercare exist specifically for this. Let me start with {primary_service} — does that sound right?",
    "trauma_informed": true,
    "review_status": "draft"
  }
}
```

Notable fields:

- **`negative_anchors`** prevents over-fire. "I'm aging out of high school next month" doesn't fire foster youth because "high school" appears.
- **`urgency_temporal_anchors`** boosts urgency conditionally on the transition. "Next month" is high urgency for foster aging-out (cliff date), neutral for unrelated requests.
- **`trauma_informed`** flag toggles trauma-informed response mode (slower pace, more validation, less direct questioning).
- **`review_status`** gates production behavior. Only `"approved"` entries fire in production. `"draft"` and `"reviewed"` are dev/staging only.

#### `transition_resources.json` schema

Same shape as a Streetlives DB service card, plus:

- **`population_tag`** — used to overlay onto results when the matching transition fires.
- **`source`** — DYCD, ACS, NYC official site, etc.
- **`last_validated_at`** — ISO timestamp of last data steward review.

Example:

```json
{
  "id": "dycd-aftercare-rhy",
  "name": "DYCD Runaway and Homeless Youth Aftercare",
  "phone": "1-800-246-4646",
  "website": "https://www.nyc.gov/dycd/...",
  "description": "Transitional support for foster youth and homeless youth ages 16-24.",
  "population_tag": "foster_youth",
  "source": "DYCD",
  "last_validated_at": "2026-05-07T00:00:00Z",
  "review_status": "approved"
}
```

Long-term, these cards should migrate into Streetlives DB with `population_specific` flags so the existing data steward workflow covers them. The JSON is a bridge for transitions where the resource doesn't yet exist in DB.

### How the layers interact

```
User message
    │
    ▼
┌────────────────────────┐
│  Slot extraction       │
│  (existing 3-tier)     │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Transition detection  │
│  (NEW — 3-tier)        │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  merge_slots           │
│  - existing logic      │
│  - bundle → additional │
│    services candidates │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Confirmation render   │
│  - transition ack      │
│    prepended           │
│  - bundle quick replies│
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Search dispatch       │
│  - existing logic      │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Results overlay       │
│  - population-specific │
│    resources at top    │
└────────────────────────┘
```

Each integration point reads the new slot fields and is otherwise unchanged. The transition layer is additive — handlers that don't care continue to work.

## Phased rollout

### Phase 1 — Foundation + foster youth (~1 week)

**Scope:**
- Slot schema additions to `Slots` typed dict and `MessageContext`.
- `transitions.json` with one entry: `foster_youth_aging_out`.
- Tier-1 regex detection wired into the extraction pipeline.
- `merge_slots` updated to populate `additional_services` from the bundle (deferred, not auto-queued).
- Confirmation acknowledgment rendering with template substitution.
- Quick-reply bundle composition (4 buttons max + navigator).
- `transition_resources.json` with DYCD aftercare + ACS aftercare cards.
- Results overlay logic.
- ~30 unit tests, ~10 integration tests.
- Negative tests (over-fire prevention).

**Success criteria for Phase 2 promotion:**

1. `peer_aging_out_foster` scores ≥4.3 in eval.
2. Zero regression on existing scenarios in the eval suite.
3. Streetlives staff review and approve the foster acknowledgment template.
4. Data stewards verify DYCD aftercare and ACS aftercare cards against current programs.
5. Telemetry shows `transition_event` fires on appropriate inputs and not on negative-control phrases.

### Phase 2 — Reentry + asylum (~1 week)

**Scope:**
- Add `reentry_from_incarceration` and `asylum_seeker_arrival` to `transitions.json`.
- Tier-2 semantic embedding training data — collect ~30 utterances per transition for embedding-based matching.
- Curated cards for reentry programs (Fortune Society, parole-specific resources) and asylum legal clinics in `transition_resources.json`.
- Eval scenarios added: `peer_reentry_recently_released`, `peer_asylum_first_week`.
- Crisis-overlap audit — neither cleanly overlaps with current crisis detection, but worth verifying.

**Success criteria:**

1. New eval scenarios score ≥4.3.
2. No regression on `peer_aging_out_foster` or other Phase 1 scenarios.
3. No false positives in `multi_*` scenarios that mention legal/employment incidentally.
4. Streetlives staff approve acknowledgment templates for both transitions.

### Phase 3 — DV escape, pregnancy, newcomer (~1.5 weeks)

These three need extra care:

- **DV escape** overlaps with the existing crisis detection (`_SAFETY_CONCERN_PHRASES` in `crisis_detector.py`). Crisis detection must take priority for the immediate response; transition framing applies to the recovery/search step that follows. Implement as a `transition_event_pending` slot that surfaces on the next non-crisis turn.
- **Pregnant teen** overlaps with the existing pregnancy slot tag in the slot extractor. Verify the new field complements rather than conflicts.
- **Newcomer to NYC** overlaps with the existing `_is_newcomer_to_nyc` detector in `contextual_acknowledgments.py` — should consolidate. This is also a refactor opportunity to prove the transition layer can absorb existing scattered population-detection logic.

This is also when we tighten the **`transition_acknowledgment_used` idempotency flag** — once shown, doesn't repeat across the session.

**Success criteria:**

1. Three new eval scenarios score ≥4.3.
2. DV escape: crisis fires correctly on the immediate turn, transition framing applies on the recovery turn, no regression on existing crisis scenarios.
3. Pregnant teen: doesn't conflict with existing pregnancy slot tag.
4. Newcomer: existing `_is_newcomer_to_nyc` consolidated into the transition layer with no behavior change.

### Phase 4 — LLM fallback for long-tail (~1 week, optional)

For transitions outside the static table (newly disabled, recent loss of caregiver, recent eviction, etc.), the existing slot-extraction tool gains an optional `transition_event` parameter the LLM can populate.

**Defer until Phase 1-3 are shipped and usage data shows the static + semantic tiers are missing common cases.** Don't build speculatively.

**Success criteria for kicking off Phase 4:**

1. Production telemetry shows ≥X% of conversations would benefit from a transition that's not in the static table (X TBD based on usage).
2. The candidate transitions identified are reproducible (not one-off phrasings).

## Risks & mitigations

### Risk 1: Over-fire on innocuous mentions

A user saying "I'm aging out of high school" shouldn't trigger foster youth. A user saying "I just got out of a meeting" shouldn't trigger reentry.

**Mitigations:**
- `negative_anchors` field in the transitions table.
- Regex requires multi-word phrase match, not single-word.
- Negative-control eval scenarios for each transition (e.g., `negative_aging_out_of_high_school`, `negative_just_got_out_of_meeting`).
- Telemetry tracks transition-event firing rate per session; spikes investigated.

### Risk 2: Bundle queue UX overwhelm

Auto-populating `additional_services` triggers the multi-intent queue offer. For a stressed foster youth, "I'll search shelter, then employment, then benefits, then legal" feels like the bot is overcommitting them to four searches.

**Mitigation:**
- Surface the bundle as **opt-in quick replies**, not auto-queued.
- User clicks `💼 Employment` only if they want it; the next-turn intent fires the queue normally.
- The acknowledgment text names the bundle so the user knows what's available; the quick replies make each component actionable.

### Risk 3: Crisis-overlap on DV escape

A user saying "I just escaped my abusive partner with my kids" should fire `crisis_detector._SAFETY_CONCERN_PHRASES` AND the transition layer.

**Mitigation:**
- Crisis takes priority on the immediate response (safety hotline + 911 framing).
- Transition framing applies to the **next turn** when the user re-engages for service search.
- Implement as a `transition_event_pending` slot that surfaces on the next non-crisis turn.
- Eval scenario `peer_dv_escape_with_kids` verifies: turn 1 fires crisis, turn 2 fires transition framing.

### Risk 4: Population-specific resource staleness

DYCD program names, phone numbers, and eligibility rules drift over time. Hard-coded JSON entries silently become wrong.

**Mitigations:**
- `last_validated_at` field per card in `transition_resources.json`.
- Quarterly review with data stewards.
- Eval scenarios verify the cards still appear correctly.
- **Long-term:** migrate cards into Streetlives DB with `population_specific` flags so the existing data steward workflow covers them. The JSON is a bridge, not a destination.

### Risk 5: Acknowledgment language drift toward patronizing or clinical

"Aging out of foster care is a major transition" is fine. "We understand you're going through a difficult time" is bad. The line is real but subjective.

**Mitigations:**
- Each acknowledgment template reviewed by Streetlives staff with lived experience before merge.
- `review_status` field in transitions.json — `"draft"`, `"reviewed"`, `"approved"`. Only `"approved"` entries fire in production.
- A/B test acknowledgment variants if usage signals support it.
- Templates checked against a content review checklist (named magnitude, named bundle, named resource, deferred to user agency).

### Risk 6: Trigger-table maintenance burden

As the field expands, regex patterns drift, semantic embeddings need re-training, new transitions emerge.

**Mitigations:**
- Clear contribution guide for `transitions.json` documented.
- Tier-3 LLM fallback (Phase 4, optional) catches transitions the table misses, surfacing them as candidates for promotion to the static table.
- Periodic audit of telemetry to identify high-frequency phrases that aren't matching.

### Risk 7: Over-attribution

A scenario that's NOT a transition (e.g., a single-need adult food request) might accidentally match a phrase pattern.

**Mitigations:**
- Conservative regex anchors with multi-word match.
- Eval coverage including negatives — scenarios that should NOT trigger any transition.
- Telemetry tracks transition-event firing rate for monitoring; spikes investigated.

### Risk 8: Acknowledgment fatigue

If the user starts a transition flow but then asks several follow-up questions, repeating the acknowledgment becomes noise.

**Mitigation:**
- `transition_acknowledgment_used` session flag — set to True after the first render, prevents re-rendering on subsequent turns.

## Eval scope expansion

Current suite has `peer_aging_out_foster`. Phases 1-3 should add:

| Scenario ID | Phase | Tests |
|---|---|---|
| `peer_aging_out_foster` (existing) | 1 | Targeted improvement from 3.55 to ≥4.3 |
| `negative_aging_out_of_high_school` | 1 | Over-fire prevention |
| `peer_reentry_recently_released` | 2 | Bundle detection, reentry resources |
| `negative_just_got_out_of_meeting` | 2 | Over-fire prevention |
| `peer_asylum_seeker_first_week` | 2 | Bundle detection, asylum legal clinics |
| `peer_dv_escape_with_kids` | 3 | Crisis priority + transition framing on turn 2 |
| `peer_pregnant_teen_first_visit` | 3 | Bundle detection, prenatal + family services |
| `peer_newcomer_brooklyn_orientation` | 3 | Existing newcomer detector consolidation |
| `peer_recently_disabled_back_injury` | 4 | LLM-only path, long-tail |

Each scenario tests:

1. Bundle detection (correct transition_event populated)
2. Acknowledgment quality (template renders, reads as trauma-informed)
3. Population-specific resource delivery (correct cards overlay at top)
4. Urgency boosting (when temporal anchor present)
5. Tone (judge dimension scores ≥4)

## Success metrics

For each phase, success requires all of:

1. **Eval scores** for the targeted transition scenarios cross 4.3.
2. **Zero regressions** on existing scenarios (`multi_*`, single-service, baseline warmth, etc.).
3. **Telemetry**: % of conversations where `transition_event` fires. Should be small (~2-5% of total volume) initially, climbing as coverage expands. Spikes above 15% investigated for over-fire.
4. **Critical failure count** stays at 0 across all transitions.
5. **Streetlives staff approval** of acknowledgment text and resource curation per phase.

## Schema and code touch points

### New files
<!-- drift:ignore: future planned files that don't exist yet -->
- `backend/app/services/transition_detection.py` — 3-tier detection (regex/semantic/llm)
<!-- drift:ignore: deletion-of-files historical references -->
- `backend/app/data/transitions.json` — detection rules + acknowledgments
<!-- drift:ignore: deletion-of-files historical references -->
- `backend/app/data/transition_resources.json` — curated population-specific cards
<!-- drift:ignore: deletion-of-files historical references -->
- `tests/unit/test_transition_detection.py`
<!-- drift:ignore: deletion-of-files historical references -->
- `tests/integration/test_transition_flow.py`

### Existing files modified

- `backend/app/services/slot_extraction/dispatch.py` — wire transition detection into the extraction cascade
- `backend/app/services/slot_extraction/merge.py` — populate bundle in `additional_services`
- `backend/app/services/chatbot/context.py` — three new fields on `MessageContext`
- `backend/app/services/confirmation.py` — render acknowledgment in confirmation message
- `backend/app/services/chatbot/handlers/post_results.py` — overlay population-specific cards
- `backend/app/services/phrase_lists.py` — add bundle quick-reply templates
- `tests/conftest.py` — `make_ctx` defaults for new fields

### Existing logic to consolidate (Phase 3)

- `backend/app/services/chatbot/contextual_acknowledgments.py::_is_newcomer_to_nyc` — folds into transitions table as `newcomer_to_nyc` entry. Existing detector becomes a thin wrapper or is deleted entirely.

## Open questions

1. **Where does DYCD aftercare data live?** If DYCD aftercare programs are not in Streetlives DB, we need either a data-model addition or hard-coded cards. Worth a 30-minute conversation with data stewards before Phase 1 kickoff. Same question applies to: ACS aftercare, asylum legal clinics, reentry programs, Safe Horizon DV resources, NYC newcomer orientation programs.

2. **Acknowledgment language voice review.** "Aging out of foster care is a major transition. Beyond shelter, you'll likely also need..." — does that voice match how a community-information specialist with lived experience would actually open a conversation with a young person in this situation? Could be too clinical. Could be just right. Needs Streetlives staff review with feedback session before Phase 1 lock-in.

3. **Phase 4 commit decision.** Static + semantic might be sufficient for a long time. Defer Phase 4 entirely until usage data shows the long-tail is meaningful?

4. **`transition_event_pending` lifecycle.** Phase 3 introduces this for DV-escape crisis coordination. How long does it persist? One turn? Until a non-crisis turn arrives? Until session ends? Needs a clear lifecycle spec.

5. **Bundle quick-reply count.** With 4 bundle buttons + peer navigator + the existing affordances, mobile users could see 8-10 buttons. Acceptable? Or should we trim the existing affordances when transition is active?

6. **Telemetry granularity.** What firing-rate threshold triggers an over-fire investigation? 15% global? 5% within a category? TBD with the analytics review.

7. **Anchor scenario validation.** Is `peer_aging_out_foster` the right anchor scenario, or should we add a more comprehensive scenario before Phase 1 kickoff to make sure we're solving for the right user intent?

## Total scope estimate

| Phase | Engineering | Data steward / staff review |
|---|---|---|
| Phase 1 (foster, foundation) | ~1 week | ~0.5 week |
| Phase 2 (reentry, asylum) | ~1 week | ~0.5 week |
| Phase 3 (DV, pregnancy, newcomer) | ~1.5 weeks | ~0.5 week |
| Phase 4 (LLM long-tail, optional) | ~1 week | minimal |

**Total Phases 1-3:** ~4 weeks engineering + ~1.5 weeks staff review (parallel where possible).

**Total with Phase 4:** ~5 weeks engineering.

## What I'd want before kickoff

Before committing engineering to Phase 1:

1. **Streetlives staff sign-off** on the acknowledgment language approach. The pattern is "name the magnitude, name the bundle, name a population-specific resource." Confirm that's the right voice before writing seven of them.
2. **Data steward conversation** on `transition_resources.json`. Specifically: do DYCD aftercare programs / asylum legal clinics live in Streetlives DB today? If yes, what are their `service_taxonomies` so the existing search can surface them? If no, the curated JSON is the bridge.
3. **Confirmation that `peer_aging_out_foster` is the right anchor scenario.** It's in the eval suite — but the Streetlives team should confirm the conversational expectations match how community-information specialists would actually talk to a young person in this situation.
4. **Decision on Phase 4.** Static + semantic might be sufficient for a long time. Defer or commit?
5. **Owner assigned.** This is structural work that needs a single accountable engineer through all phases.

## Appendix A: alternative architectures considered

### Alternative 1 — Tactical foster-youth-specific augmentation

Detect `populations` containing `"foster_youth"` (existing slot field) and augment the existing shelter response with a bundle prepend.

**Why rejected:**
- Single-scenario fix.
- Same architectural mismatch persists for the other six transitions.
- The throwaway code becomes the first row of the bundle table anyway.
- Earns ~0.5 eval points but doesn't address the underlying deficiency.

Could ship as Phase 0 if the team wants quick eval movement before Phase 1 lands. Tradeoff: writes ~80 lines of throwaway code.

### Alternative 2 — LLM-only transition classification

Use the LLM to classify whether a message describes a transition event in a single shot. Skip the static table.

**Why rejected:**
- Higher cost (extra LLM call or augmented prompt).
- Less predictable behavior — review burden increases.
- Harder to audit by data stewards.
- Doesn't preserve the existing 3-tier extraction architecture's properties.

The hybrid (regex + semantic + optional LLM fallback) keeps Tier 1 deterministic for the high-frequency cases.

### Alternative 3 — Population-tag taxonomy with auto-bundle expansion

Treat transitions as just population tags (extending the existing `_populations` slot field) and let downstream handlers consume the tags for bundle expansion.

**Why rejected:**
- Population tags are static descriptors of who the user is, not events that just happened.
- Bundles depend on the event ("just got out of prison" implies different bundle than "I have a record"), not the population.
- The temporal/urgency dimension doesn't fit the population-tag schema.

Population tags remain useful and orthogonal — a foster_youth might be aging out (transition) or might already be aged out and needing ongoing support (population only). The two layers complement each other.

## Appendix B: open follow-up tickets

Filed during the design phase, to be addressed alongside or after this work:

- **Borough expansion data table.** `_NEARBY_BOROUGHS_BY_SERVICE["food"]["Manhattan"]` returns `["Brooklyn", "Queens"]` for density reasons; geographically Bronx is closer to Harlem. Surfaced by R42 `wa_negative_preference` judge feedback. Not blocking transitions work; file as separate ticket.
- **Confirmation copy reflects user's specific need.** `peer_charge_phone_wifi` confirmation says "I'll look for **other services** in Midtown" — should say "phone charging and wifi spots in Midtown." Surfaced repeatedly in eval feedback. Not blocking; file as separate ticket.
- **Location persistence after gibberish input.** `adversarial_nonsense_service` loses prior `location=Bronx` after "flurpledurple" input. Not blocking; file as separate ticket.
- **Streetlives DB schema review for `population_specific` flag.** Long-term destination for `transition_resources.json` cards. Conversation with data stewards needed.
