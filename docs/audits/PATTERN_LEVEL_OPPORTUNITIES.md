# Pattern-Level Codebase Audit: Scalable Architecture Opportunities

**Status:** 📋 DRAFT — Audit findings, open for team discussion
**Owner:** TBD
**Created:** 2026-05-07
**Audience:** Streetlives engineering, technical leadership
**Related:** Transition-event bundle pattern design doc (companion document for one of the patterns surfaced in this audit).

---

## Executive summary

This audit identifies **eight pattern-level opportunities** in the YourPeer chatbot codebase where the current architecture forces per-scenario patches when the underlying need is structural. Each pattern has the same shape: there's a class of behavior the bot should handle uniformly, the current code handles each instance separately with hand-coded logic, and the cost of adding the next instance is roughly the same as the previous one. A pattern-level fix changes that — adding the next instance becomes data-table work, not engineering work.

Patterns are ranked by leverage (how many eval scenarios benefit), engineering effort, and structural urgency:

| # | Pattern | Leverage | Effort | Urgency |
|---|---|---|---|---|
| 8 | **Tone detection via semantic router extension** | Medium-High — improves Pattern 1's input signal; ~2% → ~15-20% tone firing rate | ~3 days | High (precursor for Pattern 1) |
| 1 | **Acknowledgment dispatch table** | High — affects every conversation | ~1 week | High |
| 2 | **Transition-event bundles** (separate design doc) | High — 7+ scenarios | ~4 weeks | High |
| 3 | **Urgency as first-class slot** | Medium — blocks transitions + crisis | ~3-4 days | High (blocks #2) |
| 4 | **Phrase-list common framework** | Medium — touches 36 lists | ~3-4 days | Medium |
| 5 | **Hard-coded org references → data layer** | Medium — 28 inline refs across 5 files | ~1 week | Medium |
| 6 | **Confirmation message composer** | Low-Medium — UX consistency | ~2 days | Low |
| 7 | **Eval-driven comment hygiene** | Low — drift prevention | ~1 day | Low |

Pattern 8 was added during the audit's open-question phase after the team observed that the production tone classifier fires on only ~2% of turns. Investigation showed the lexicon is working as designed for explicit emotional language but misses contextual/situational distress. Since the codebase already has a sentence-transformer (BERT-family) loaded for semantic routing, extending it to cover tone is a data-only change — not an infrastructure addition.

The audit also identifies **architecture that's already good** and shouldn't be touched — the 3-tier extraction cascade, `MessageContext`, the crisis detector's phrase-list structure are all exemplars that other patterns should emulate, not problems to fix.

This document is a survey, not a set of specifications. Each prioritized pattern would need its own design doc before kickoff.

---

## What "pattern-level" means

A per-scenario patch handles one specific input or eval scenario by adding a code branch:

```python
# Per-scenario patch
if "aging out of foster care" in message:
    response = "..."
elif "just got out of prison" in message:
    response = "..."
elif "just arrived from Venezuela" in message:
    response = "..."
```

A pattern-level fix changes the abstraction so adding the next case is data, not code:

```python
# Pattern-level fix
event = detect_transition_event(message)
if event:
    response = render_acknowledgment(event)
```

Where `detect_transition_event` reads from a data table and `render_acknowledgment` consumes the table's templates. The fifth foster-youth-adjacent case adds a row to the table; it doesn't add another `elif`.

The audit looks for places where the codebase has accumulated `elif` chains that should be tables, scattered detectors that should be a single dispatcher, and inline data that should be in a versioned file.

---

## Pattern 1: Acknowledgment dispatch table

**Highest-leverage pattern in the audit.** Touches every conversation; consolidates the most code; addresses persistent eval feedback ("transactional," "flat," "no acknowledgment of the user's situation").

### Current state

Three parallel mechanisms compute response prefixes/acknowledgments, each with its own dispatcher:

#### Mechanism 1: `contextual_acknowledgments.py`

5 detector functions + 5 acknowledgment functions, plus a combined dispatcher:

```
backend/app/services/chatbot/contextual_acknowledgments.py:

_is_personal_story()                  + _personal_story_acknowledgment()
_is_family_with_children_urgent_shelter()  + _path_intake_acknowledgment()
_is_rough_sleeper()                   + _rough_sleeper_acknowledgment()
_is_newcomer_to_nyc()                 + _newcomer_to_nyc_acknowledgment()
_is_substance_use_shelter()           + _substance_use_shelter_acknowledgment()

_combined_contextual_acknowledgments()  # dispatches all 5
```

Each detector is hand-coded (regex + slot inspection). Each acknowledgment function is hand-coded (string templating). 568 lines total.

#### Mechanism 2: `responses.py` `_EMOTIONAL_RESPONSES`

A 9-key dict (`shame`, `grief`, `scared`, `alone`, `sad`, `rough_day`, `undeserving`, `distrust`, `angry`) dispatched via a 9-way if/elif chain:

```python
# backend/app/services/responses.py:215-298
if _any_match(["hate asking", "humiliating", "burden", ...]):
    return _EMOTIONAL_RESPONSES["shame"]
if _any_match(["died", "passed away", ...]):
    return _EMOTIONAL_RESPONSES["grief"]
if _any_match(["scared", "afraid", ...]):
    return _EMOTIONAL_RESPONSES["scared"]
# ... 6 more branches
```

The phrase lists are inline in the if-conditions, not module-level — making them invisible to the rest of the codebase and hard to maintain.

#### Mechanism 3: `accessibility.py` immigration-specific

```
_immigration_acknowledgment()  # at line 288, dispatched from confirmation handler
```

Same shape as Mechanism 1 but lives in a different file because it's tied to the accessibility flow.

### Why it's a problem

1. **Three parallel dispatchers** each solving the same problem (compute a contextual response prefix) with different mechanisms.

2. **Each detector is one-off.** The `_is_rough_sleeper` regex has no shared infrastructure with the `_is_newcomer_to_nyc` regex. They both inspect `redacted_message` against curated patterns, both handle apostrophes, both check slot fields — but reimplemented.

3. **The eval feedback that drives new acknowledgments accumulates here.** R31 added `_is_substance_use_shelter`. R32 added research-backed emotional categories ("undeserving," 41% of homeless people report this per PMC). R42 will likely add foster-youth and reentry. Each new addition is ~80 lines of hand-coded detector + acknowledgment.

4. **Apostrophe defensiveness is duplicated.** Each detector calls `normalize_apostrophes` separately. The pattern is correct but the implementation is repeated 7+ times.

5. **No idempotency primitive.** Both Mechanism 1 and Mechanism 2 can fire in the same turn, producing double-acknowledgment ("That sounds like a really hard experience. I hear you. Let me find food in Harlem..."). There's no shared "already acknowledged this turn" flag.

### Proposed pattern: unified acknowledgment dispatcher

A single dispatcher that consumes a data table:

```python
# backend/app/data/acknowledgments.json
{
  "rough_sleeper": {
    "trigger_regex": "(?:sleeping|slept|been)\\s+(?:on the )?(?:street|outside|park|subway)",
    "trigger_slots": {},
    "negative_anchors": [],
    "response_template": "It sounds like you've been through a lot. Let me try to find {primary_service} that can really help.",
    "priority": 10,
    "review_status": "approved"
  },
  "newcomer_to_nyc": {
    "trigger_regex": "(?:just|recently)\\s+(?:moved|arrived|got) to (?:new york|nyc|the city)",
    "trigger_slots": {"newcomer": true},
    "response_template": "Welcome to New York. Navigating services here can be overwhelming at first — I'll try to make it easier.",
    "priority": 8,
    "review_status": "approved"
  },
  "shame_self_critical": {
    "trigger_regex": "(?:hate (?:asking|having to)|humiliating|burden|swallow my pride)",
    "trigger_slots": {},
    "response_template": "Asking for help takes courage — there's no shame in needing support.",
    "priority": 12,  // higher than rough_sleeper if both match
    "review_status": "approved"
  },
  // ... ~14-16 entries to cover current 9 emotional + 5 contextual + future
}
```

The dispatcher:

```python
def acknowledgment_for(message: str, slots: dict) -> Optional[str]:
    matches = []
    for ack_id, ack_def in _ACKNOWLEDGMENTS.items():
        if _matches(message, slots, ack_def):
            matches.append((ack_def["priority"], ack_id, ack_def))
    if not matches:
        return None
    matches.sort(reverse=True)  # highest priority wins
    return _render(matches[0][2], slots)
```

### Migration approach

Phase 1: stand up the dispatcher with the 5 contextual acknowledgments (Mechanism 1 entries). Verify behavioral parity via existing tests.

Phase 2: migrate the 9 emotional categories from `responses.py`. The phrase lists move into the table; the if-chain becomes a single call.

Phase 3: migrate the immigration acknowledgment from `accessibility.py`.

Phase 4: add `acknowledgment_used_this_turn` idempotency flag to prevent double-acknowledgment.

Phase 5 (optional): trauma-informed review of all templates with Streetlives staff. This becomes practical once they're in one file.

### Scope estimate

~5-6 days engineering. ~1 week if the staff review (Phase 5) is done seriously.

### Eval scenarios that benefit

Any scenario currently scoring tone≤4. Most directly:
- `peer_aging_out_foster` (already addressed by transition design — this pattern complements it)
- `peer_charge_phone_wifi` (tone=3 in R42)
- `natural_benefits_ebt` (tone=3 in R42)
- `multi_three_services_legal_benefits_food` (tone=4 in R42)

Plus future scenarios for transitions, populations, and emotional contexts.

### Decision needed

Whether to consolidate now (high engineering investment, lasting payoff) or defer until 2-3 more `_is_*` detectors land and the cost becomes more visible.

---

## Pattern 2: Transition-event bundles

**Already designed in the companion doc** (`TRANSITION_EVENT_BUNDLES.md`). Summarized here for completeness.

### Current state

Bot models user intent as one primary service request. Multi-need transition events (foster aging out, reentry, asylum, DV escape, pregnant teen, newly diagnosed, newcomer) get reduced to a single service. The bundle that should accompany the transition is silently dropped.

### Proposed pattern

3-tier detection (regex → semantic → optional LLM) that mirrors the existing slot extraction architecture. New slot fields populate at extraction time and are consumed at four downstream points (slot merge, confirmation, quick replies, results overlay).

### Scope

~4 weeks engineering across 3 phases (foundation + foster, reentry + asylum, DV + pregnancy + newcomer). Optional Phase 4 for LLM long-tail.

### Eval scenarios

7 transition scenarios (one existing — `peer_aging_out_foster`; six to add).

See `TRANSITION_EVENT_BUNDLES.md` for full design.

---

## Pattern 3: Urgency as a first-class slot

**Blocks Pattern 2.** Should ship before transitions because the transition layer needs urgency to be normalized.

### Current state

Urgency handling is scattered across 5 files with no single source of truth:

```
backend/app/services/slot_extraction/dispatch.py       (89 of 89 urgency lines: 24)
backend/app/services/slot_extraction/prompts.py        (16)
backend/app/services/slot_extraction/merge.py          (28)
backend/app/services/chatbot/contextual_acknowledgments.py  (12)
backend/app/services/chatbot/handlers/emotional.py     (9)
```

Plus 105 occurrences of urgency phrases ("tonight," "right now," "today," "this morning," "asap," "immediately") inline in code, scattered across files that aren't even on the urgency list.

The `urgency` slot field exists but is underused:
- The extractor populates it (`"high"` / `"medium"` / `"low"`).
- The crisis detector inspects message text directly for urgency phrases instead of reading the slot.
- The contextual acknowledgments inspect message text for time-of-day phrases instead of reading the slot.
- The `_handle_negative_preference` tier escalation inspects `frust_count` instead of urgency.

### Why it's a problem

1. **Same urgency signal extracted multiple times.** A user saying "I need shelter tonight" has "tonight" parsed by the extractor (sets `urgency=high`), AND by the crisis detector (inline regex), AND by the contextual acknowledgments (inline regex). Three independent parsers, three places to maintain, three places to drift.

2. **Conditional urgency depends on context.** "Next month" is high urgency for foster aging-out (cliff date) but low urgency for tax prep ("I'll need help with taxes next month"). The transition-event design surfaced this — and the right answer is for urgency to be a structured field that captures BOTH the temporal anchor AND the context-conditional priority.

3. **No `urgency_signals` list.** The current `urgency` slot is a single enum value. The temporal anchor that triggered it is lost. The transition layer needs to know `["next_month", "no_place_to_go"]` to render appropriate acknowledgments.

### Proposed pattern: structured urgency slot

Replace the single-enum `urgency` field with a structured object:

```python
{
    "urgency": {
        "level": "high",   # "low" | "medium" | "high" | "crisis"
        "signals": ["temporal:next_month", "stakes:lose_housing"],
        "source": "regex" | "semantic" | "llm" | "transition_event",
    }
}
```

Each signal is a typed string with a category prefix. Common categories:

- `temporal:tonight`, `temporal:next_week`, `temporal:no_deadline`
- `stakes:safety_risk`, `stakes:lose_housing`, `stakes:health_emergency`
- `external:eviction_notice`, `external:release_date`, `external:utility_shutoff`

The level is computed from signals via a precedence table. The signals themselves are the auditable input.

### Migration approach

Phase 1: introduce the structured field; populate alongside the existing `urgency` enum (no behavior change).

Phase 2: migrate consumers one at a time — crisis detector, contextual acknowledgments, transition layer (when it ships), tier escalation in `_handle_negative_preference`.

Phase 3: remove the legacy `urgency` enum once all consumers are migrated.

### Scope estimate

~3-4 days engineering. Lower than other patterns because the field already exists; this is a schema change + consumer migration.

### Eval scenarios that benefit

Indirect — by unblocking Pattern 2 (transitions) and Pattern 1 (acknowledgments). Direct beneficiaries:

- All transition-event scenarios (Phase 2 of #2 needs this).
- `peer_aging_out_foster` (urgency boost on "next month").
- Any scenario where the judge asks "should this be high urgency?"

### Decision needed

Whether to ship before Pattern 2 (recommended — unblocks the transition-event work) or in parallel.

---

## Pattern 4: Phrase-list common framework

**Eliminates 36 places of repeated boilerplate.** Touches detection across the whole codebase.

### Current state

36 module-level phrase/keyword lists across 7 files. Concentrated in:

```
backend/app/services/slot_extraction_regex.py   — service keywords, intent phrases
backend/app/services/phrase_lists.py             — quick replies, borough tables
backend/app/services/crisis_detector.py          — 8 crisis category lists
backend/app/services/chatbot/tone.py             — emotional/tone phrase lists
backend/app/services/chatbot/handlers/emotional.py
backend/app/services/chatbot/handlers/confirmation.py
backend/app/services/chatbot/handlers/accessibility.py
```

Each list is hand-maintained with the same defensive patterns:

#### Pattern A: apostrophe paired-listing

Every phrase containing an apostrophe is listed twice — once with curly, once stripped:

```python
_NEGATIVE_PREFERENCE_PHRASES = [
    "don't want", "dont want",
    "doesn't work", "doesnt work",
    "isn't right", "isnt right",
    "won't help", "wont help",
    # ... 11 such pairs out of 59 entries
]
```

This works because `_classify_action`'s preprocessing strips curly apostrophes via `re.sub(r"[^\w\s']", "", lower)`. But the defensive listing is repeated in every phrase list with apostrophes. If someone forgets the pair, the phrase silently doesn't fire.

#### Pattern B: negation guard

Each list that needs negation handling implements its own:

```python
# In crisis_detector.py
def _has_safety_concern(text):
    for phrase in _SAFETY_CONCERN_PHRASES:
        if phrase in text and not _is_negated(text, text.index(phrase)):
            return True
    return False
```

Reimplemented in slot_extraction_regex.py with a different signature.

#### Pattern C: negative anchors

Some lists need to suppress matches when contextual phrases are present (e.g., "aging out" matches foster youth ONLY if "high school" is NOT in the message). This is handled inline in each detector with ad-hoc `if "high school" in text: return False` logic.

### Why it's a problem

1. **Adding a new phrase list duplicates ~30 lines of boilerplate** (apostrophe pairing, negation, anchor checks) across the file.

2. **Drift between lists.** When a new apostrophe contraction enters common use, only some lists get updated. Bugs surface in scenarios that hit the un-updated lists.

3. **Crisis detector's structure is the right shape** — 8 categorized phrase lists with consistent application — but the rest of the codebase doesn't share its infrastructure. The crisis detector internalizes negation, apostrophe handling, and anchor checks into helpers (`_has_safety_concern`, `_is_negated`). The same helpers are reimplemented elsewhere.

### Proposed pattern: `PhraseList` class

A single class that encapsulates the defensive patterns:

```python
class PhraseList:
    def __init__(
        self,
        phrases: list[str],
        negative_anchors: list[str] = None,
        check_negation: bool = True,
    ):
        # Normalize apostrophes once at construction; strip versions auto-generated
        self.phrases = self._expand_apostrophes(phrases)
        self.negative_anchors = negative_anchors or []
        self.check_negation = check_negation

    def matches(self, text: str) -> bool:
        text = normalize_apostrophes(text.lower())
        if any(anchor in text for anchor in self.negative_anchors):
            return False
        for phrase in self.phrases:
            if phrase in text:
                if not self.check_negation or not _is_negated(text, text.index(phrase)):
                    return True
        return False

# Usage
_SAFETY_CONCERN = PhraseList(
    phrases=["really unsafe", "wasn't safe", "felt threatened", ...],
    negative_anchors=["I'm fine now", "all good now"],
)

if _SAFETY_CONCERN.matches(message):
    ...
```

A new phrase list becomes a 5-line construction call. The defensive patterns are applied uniformly. Apostrophe pairing is automatic (no more hand-maintained twins).

### Migration approach

Phase 1: introduce the class. Migrate `crisis_detector.py` first since it's already structured this way.

Phase 2: migrate remaining service files one by one. Tests verify behavioral parity per file.

Phase 3: delete the apostrophe-paired-listing convention. Document the new pattern in `CONTRIBUTING.md`.

### Scope estimate

~3-4 days engineering. Mostly mechanical migration; the class itself is small.

### Eval scenarios that benefit

Indirect. Reduces drift risk, makes new phrase additions cheap. No direct eval lift, but enables faster iteration.

### Decision needed

Whether to invest in the framework (high one-time cost, lasting payoff) or accept the duplication (no-op cost, ongoing drift risk).

---

## Pattern 5: Hard-coded org references → data layer

**Brittle to NYC org changes.** The bot mentions specific organizations by name in 28 places across 5 files; if Streetwork changes its name, the bot's knowledge breaks silently.

### Current state

28 hard-coded references to NYC organizations in production code:

```
backend/app/services/slot_extraction_regex.py:
   - "Streetwork", "Covenant House", "Ali Forney", "Safe Horizon", "Cabrini",
     "Make the Road", "Family Justice Center", "UnLocal", "RiseBoro" ...

backend/app/services/slot_extraction/prompts.py:
   - "Mount Sinai", "Realization Center" ...

backend/app/services/slot_extraction/merge.py:
   - 9 inline org references

backend/app/services/crisis_detector.py:
   - "Safe Horizon", FJC references in DV resources

backend/app/services/chatbot/execution.py:
   - 3 inline org references
```

These are used for:
- Recognizing org names in user input (e.g., user says "I went to Covenant House" → extract as known shelter org).
- Mentioning resources in responses ("DYCD aftercare exists for this").
- Crisis fallback ("Safe Horizon: 1-800-621-HOPE").

### Why it's a problem

1. **Org names drift.** Streetwork was historically known as "Streetwork Project." If the org rebrands, all 5 inline references need updating across 5 files.

2. **Phone numbers drift faster than names.** Crisis hotlines, DV resources, and city-service phone numbers change. There's no `last_validated_at` on inline references.

3. **Data stewards can't review them.** All 28 are buried in Python source. A data steward who notices "Cabrini Center has merged with another org" can't update without a code change PR.

4. **This is the exact gap that motivated `transition_resources.json`** in Pattern 2. Same problem, broader scope.

### Proposed pattern: `nyc_resources.json` data layer

Move all 28 references into a versioned JSON file:

```json
{
  "covenant_house": {
    "canonical_name": "Covenant House New York",
    "aliases": ["Covenant House", "Covenant"],
    "service_types": ["shelter", "youth_shelter"],
    "phone": "1-800-388-3888",
    "url": "https://www.covenanthousenewyork.org",
    "population_tags": ["youth", "transition_age_youth"],
    "last_validated_at": "2026-05-07T00:00:00Z",
    "review_status": "approved"
  },
  "safe_horizon_dv_hotline": {
    "canonical_name": "Safe Horizon Domestic Violence Hotline",
    "phone": "1-800-621-4673",
    "phone_display": "1-800-621-HOPE",
    "service_types": ["crisis_support", "dv_resource"],
    "population_tags": ["dv_survivor"],
    "last_validated_at": "2026-05-07T00:00:00Z",
    "review_status": "approved"
  }
  // ... 26 more
}
```

Loaded at startup; consumers reference by ID. Data stewards review quarterly and update timestamps. Tests verify all referenced IDs exist.

### Migration approach

Phase 1: create `nyc_resources.json` with all 28 entries. Build a loader.

Phase 2: migrate consumers one file at a time. Tests verify behavioral parity.

Phase 3: integrate with `transition_resources.json` (from Pattern 2) — they're really the same data, scoped differently.

### Scope estimate

~1 week engineering. Plus ~2 weeks of data steward review for canonicalizing the entries (can run in parallel).

### Eval scenarios that benefit

Indirect. No direct eval lift, but enables data stewards to maintain freshness. Critical for the population-resource layer in Pattern 2.

### Decision needed

Whether to do this as a standalone project, or fold it into Pattern 2's `transition_resources.json` work as a unified resource layer.

---

## Pattern 6: Confirmation message composer

**Smaller-scope pattern.** Addresses prefix-string drift across 8 call sites.

### Current state

`_build_confirmation_message(slots)` at `confirmation.py:72` is the canonical confirmation builder. It's called from 8 sites with various prefix strings concatenated:

```python
# handlers/confirmation.py:120
confirm_msg = _build_confirmation_message(ctx.existing)

# handlers/confirmation.py:838
confirm_msg = _build_confirmation_message(existing)

# handlers/confirmation.py:886
confirm_msg = f"Got it — switching to {new_label}. " + _build_confirmation_message(existing)

# handlers/confirmation.py:1114
confirm_msg = (
    "or if you were still thinking about the search. "
    + _build_confirmation_message(existing)
)

# handlers/confirmation.py:1151
confirm_msg = nudge_prefix + _build_confirmation_message(existing)

# handlers/accessibility.py:93
confirm_msg = "No problem at all. " + _build_confirmation_message(ctx.existing)
```

Each call site composes its own prefix. The prefix-context relationship is implicit — there's no shared model of "this prefix is appropriate when the user just changed service" vs "this prefix is appropriate when the user gave accessibility info."

### Why it's a problem

1. **Drift is invisible.** "Got it — switching to..." vs "No problem at all..." vs "or if you were still thinking..." are all valid prefixes for similar contexts, but the choice is per-handler. No central policy.

2. **The negative-preference fix shipped recently introduced ANOTHER prefix path** — `_negative_preference_expansion` returns a prefix-laden string that's prepended to confirmation. That's nine sites now.

3. **Confirmation copy is a known eval gap.** R42 judge feedback on `peer_charge_phone_wifi`: confirmation says "I'll look for **other services** in Midtown" instead of "phone charging and wifi spots." That's a separate bug, but it lives in `_build_confirmation_message` — a place that's already hard to refactor because of the 8 calling-context variants.

### Proposed pattern: `compose_confirmation(slots, context)` builder

```python
def compose_confirmation(
    slots: dict,
    context: ConfirmationContext,
) -> str:
    prefix = _prefix_for(context)
    body = _confirmation_body(slots)
    return f"{prefix}{body}" if prefix else body

class ConfirmationContext(Enum):
    INITIAL = "initial"            # first confirmation in a turn
    SERVICE_SWITCH = "service_switch"   # user just switched service
    ACCESSIBILITY_PROVIDED = "accessibility_provided"  # user just gave access need
    POST_NUDGE = "post_nudge"      # confirmation after a nudge prompt
    POST_NEGATIVE_PREFERENCE = "post_negative_preference"  # broaden after rejection
    THINKING_AGAIN = "thinking_again"   # user re-engaging after pause

# _prefix_for returns the appropriate string for each context.
```

Each call site passes its context; the composer produces the right prefix. Drift becomes a one-place change.

### Migration approach

Phase 1: introduce the composer with the 9 known contexts. Migrate call sites one at a time.

Phase 2: address the "specific service_detail" eval gap (e.g., `peer_charge_phone_wifi`'s "other services" → "phone charging and wifi spots"). The composer's body component can be updated in one place.

### Scope estimate

~2 days engineering.

### Eval scenarios that benefit

- `peer_charge_phone_wifi` (R42 confirmation_ux=4 due to "other services" vagueness).
- Any future scenario where the confirmation prefix should be context-aware.

### Decision needed

Low priority unless the eval feedback on confirmation specificity (`peer_charge_phone_wifi`) becomes a recurring theme.

---

## Pattern 7: Eval-driven comment hygiene

**Smallest-scope pattern.** Style/discipline issue rather than architectural.

### Current state

Production code contains 6 comments that directly reference eval scenarios or run numbers:

```
backend/app/services/slot_extraction_regex.py:150
   # Chronic conditions / medications (Run 24 eval gaps)

backend/app/services/slot_extraction_regex.py:1428
   # Run 24 eval gaps — felon/criminal record terminology

backend/app/services/chatbot/handlers/confirmation.py:382
   # Eval target: ``wa_negative_preference``. R32: 3.91 (failing). Judge feedback...

backend/app/services/chatbot/handlers/confirmation.py:388
   # Also count as frustration for escalation tiers (Run 24 eval fix)

backend/app/services/chatbot/pipeline.py:364
   # Eval target: adversarial_unrecognized_service (R28: 2.91, R32: 4.36, R41: 3.73...)

backend/app/services/phrase_lists.py:412
   # Run 24 eval fix — patterns that name the specific new service
```

Each comment ties code to a specific eval scenario or run. Some are recent (R32 from this week's PR shipped via these comments).

### Why it's a problem

1. **Runs and scenarios are ephemeral.** R24 was 12 runs ago. The "Run 24 eval gap" reference is no longer informative — the gap was fixed; the reference is fossil.

2. **Production code is the wrong place for test-coverage rationale.** The reason this code exists belongs in the test that pins its behavior. A test docstring like "Pins behavior for `peer_aging_out_foster` — see eval R32 baseline 3.55" is durable. A production comment "fix from R32 eval" goes stale immediately.

3. **The pattern is spreading.** This audit's top recommendations would each add 5-10 such comments if shipped without discipline. Pattern 1's acknowledgment table would have one comment per row referencing the eval that motivated it.

### Proposed pattern: production code is scenario-agnostic; tests pin scenarios

The production code documents the BEHAVIOR it implements ("Block confirmation when extraction landed on low-confidence other"). The TEST that exercises the behavior names the scenario it pins ("Test for `peer_aging_out_foster`'s confirmation-block behavior").

Migration is a documentation refactor:

```python
# Before (in pipeline.py)
# Eval target: adversarial_unrecognized_service (R28: 2.91, R32: 4.36, R41: 3.73)
# Conservative gate by design...

# After (in pipeline.py)
# Block confirmation when LLM gate snapped to "other" with no detail —
# the routing layer has classified this as a low-confidence unrecognized
# request. Without this guard, confirmation fires before the redirect runs.

# (in tests/integration/test_multi_turn_and_context.py)
class TestUnrecognizedServiceLLMGateGuard:
    """Pins behavior for `adversarial_unrecognized_service` (R28: 2.91,
    R32: 4.36, R41: 3.73). Tests assert observed behavior so the eval
    history doesn't bind to specific implementation details."""
```

### Scope estimate

~1 day to migrate the 6 existing comments. Plus a code-style check to prevent regression.

### Eval scenarios that benefit

None directly. Hygiene only.

### Decision needed

Whether to make this an explicit code-review checklist item or a lint rule.

---

## Pattern 8: Tone detection via semantic router extension

**Precursor pattern.** Should ship before Pattern 1 because Pattern 1's acknowledgment dispatch table consumes tone signals as input. Investigation prompted by a dashboard observation: the production tone classifier fires on only ~2% of 658 classified turns.

### Current state

`_classify_tone` in `classifier.py:228` is a lexicon-based classifier matching against four hand-curated phrase lists in `phrase_lists.py`:

```
_FRUSTRATION_PHRASES   ~60 entries  (bot-directed: "not helpful", "useless", "going in circles")
_EMOTIONAL_PHRASES    ~120 entries  (self-state: "feeling sad", "i'm anxious", "hate asking")
_CONFUSED_PHRASES      ~20 entries  (orientation: "i don't know what to do", "i'm lost")
_URGENT_PHRASES        ~20 entries  (time pressure: "right now", "asap", "tonight")
```

Matching is contains-substring on a normalized form of the message (lowercased, contractions expanded, intensifiers stripped). Returns one of `crisis | frustrated | emotional | confused | urgent | None`.

The classifier is doing exactly what it was designed to do — detect explicit emotional surface language. The 2% firing rate isn't a bug; it accurately reflects how often users of a service-finding bot use explicit emotional words in their messages.

### Why a pattern-level extension is warranted

Three signals distinct from each other, currently conflated under "tone":

| Signal | Source | Detected by |
|---|---|---|
| **Surface emotion** | What the user says explicitly | Lexicon (current); could extend to semantic |
| **Situational emotion** | What the situation implies (asylum seeker, foster youth, charging phone) | Slot extraction + population tags; addressed by Pattern 2 |
| **Baseline emotional weight** | Population is vulnerable | Architectural default; addressed by Pattern 1 |

The judge ("Opus") in the eval scores tone based on a **fusion** of all three. When `peer_charge_phone_wifi` is judged tone=3 ("transactional, flat"), it's not because the user said an emotional word the bot missed. It's because the situation (homeless person needing connectivity) carries emotional weight, and the bot's response didn't acknowledge it.

But Signal 1 (surface emotion) is genuinely under-detected by the lexicon. Phrases like *"I lost my job and don't know what to do,"* *"things have been rough,"* or *"I just need things to work out for once"* carry emotional content that fall outside the lexicon's exact-match scope. A semantic-similarity classifier captures these.

### Why the natural path is the existing semantic router

The codebase already has a sentence-transformer model loaded for Tier 2 routing:

```python
# semantic_router.py:89
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
```

This is a 22M-parameter BERT-family encoder. Already loaded at startup; already computing embeddings on every Tier-2-eligible message; already pre-embedding canonical utterances at startup time and using cosine similarity with thresholds. The infrastructure exists.

Currently the router classifies into:
- 15 `SERVICE_ROUTES` (medical, food, shelter, etc.)
- 7 `POPULATION_ROUTES` (reentry, veteran, senior, etc.)

Adding tone is a third route category with the same shape:

```python
# semantic_routes.py — add alongside SERVICE_ROUTES and POPULATION_ROUTES
TONE_ROUTES = {
    "shame": [
        "I hate asking for help",
        "I'm embarrassed to be here",
        "I never thought I'd need this",
        "this is humiliating",
        "I feel like a burden",
        "swallowing my pride",
    ],
    "scared": [
        "I'm really scared",
        "I'm terrified",
        "I don't know what's going to happen",
    ],
    "lost": [
        "I have no idea where to start",
        "everything is overwhelming",
        "I can't think straight",
    ],
    "exhausted": [
        "I'm so tired",
        "I can't keep doing this",
        "I'm at my limit",
    ],
    "hopeless": [
        "nothing ever works out",
        "I've tried everything",
        "what's the point",
    ],
    "distrust": [
        "is this real",
        "what's the catch",
        "this seems too good to be true",
    ],
    "undeserving": [
        "other people need it more",
        "I don't deserve help",
        "I'm not worth it",
    ],
    "alone": [
        "I have nobody",
        "no one to turn to",
        "I'm completely alone",
    ],
    # ~8-10 categories total, mapping to existing _EMOTIONAL_RESPONSES
}
```

These embed at startup, same as the service routes. At classify time, the **same embedding pass** that computes service-similarity also computes tone-similarity. Marginal cost: one additional dict comparison per message, microseconds.

### Why this beats other Tier-2 paths

Two alternatives considered:

**Alternative A: Load a separate emotion-finetuned model** (e.g., `j-hartmann/emotion-english-distilroberta-base`, GoEmotions-fine-tuned). Better tone-discrimination — likely 10-15% F1 advantage. But adds 250MB to the deployment image and ~30ms per inference; introduces a second model to maintain. Justified only if `all-MiniLM-L6-v2` proves insufficient (see "Empirical verification" below).

**Alternative B: Use Tier-3 LLM (Haiku) for tone classification.** Highest accuracy and the model has trauma-informed pretraining. But cost is ~$1/1M input tokens × every classified turn, and latency is ~500ms-2s. Reserve LLM for the existing slot-extraction role; tone shouldn't fire a separate LLM call.

**The existing semantic router** is the right shape: zero new infrastructure, deterministic enough to be auditable, fast enough for the existing latency budget, and the architectural pattern ("3-tier cascade: regex → semantic → LLM") generalizes cleanly to cover tone.

### Caveats

1. **`all-MiniLM-L6-v2` isn't trained specifically on emotion data.** It's trained on general semantic similarity, paraphrase, and NLI tasks. It will cluster *"I'm sad"* near *"I'm depressed"* (good), but might also cluster *"I'm worried about my interview"* near *"I'm worried about losing my apartment"* — both are anxiety, but only one is the kind of distress the bot should acknowledge. Empirical verification needed (see below).

2. **Thresholds need separate tuning.** The current `DEFAULT_SERVICE_THRESHOLD = 0.75` is calibrated for service discrimination, where false positives matter (routing to wrong service). For tone, false negatives matter more (missing a shame signal), so the threshold should probably be lower — start at 0.65 and tune against eval data.

3. **Multi-label is a new pattern for the router.** Currently `SemanticMatch` returns one `service_type` + one optional `population`. Tone is orthogonal to both — a message can simultaneously hit `shame` (tone) + `food` (service) + `foster_youth` (population). The router's interface needs an additional `tone` field, and the threshold logic needs to handle "fire all signals above their respective thresholds" rather than top-1.

### Proposed migration

Phase 1: stand up `TONE_ROUTES` data structure with ~80 canonical utterances across 8-10 categories. Initial categories should map to existing `_EMOTIONAL_RESPONSES` keys so downstream consumers don't need to change.

Phase 2: extend `SemanticMatch` dataclass with an optional `tone: str | None` field and a `tone_confidence: float` field. Update `classify()` to compute tone similarity in parallel with service/population.

Phase 3: integrate into the existing tone consumers in `responses.py` (the if-chain at lines 215-298 that maps phrase lists to `_EMOTIONAL_RESPONSES` keys). The semantic match becomes a fallback after lexicon match: lexicon hit wins for backward compatibility; semantic hit fires when lexicon misses.

Phase 4: tune thresholds against the existing eval suite. Measure firing rate change and judge-tone-score correlation.

Phase 5 (parallel): involve Streetlives staff in reviewing canonical utterances. The 80 utterances are the bot's "ear" for what each tone sounds like; staff with lived experience can validate and extend them.

### Empirical verification before committing

Before committing 3 days of engineering, validate that `all-MiniLM-L6-v2` actually clusters emotion utterances usefully (caveat #1). Cheap experiment:

1. Take 100 messages from existing eval transcripts.
2. Have 2-3 people label each for tone (`shame`, `scared`, `lost`, etc., or `neutral`).
3. Embed each message with the existing model.
4. Check if same-tone messages cluster higher than cross-tone messages (tone-internal similarity > tone-cross similarity).

Expected result: yes, with caveats around edge cases. The same model is reportedly used for emotion clustering in several open-source chatbot projects, and the architecture (sentence-level embedding) is well-suited to tone classification.

If verification fails, the fallback is Alternative A (load a separate emotion-finetuned model). Either way, the eval data tells you definitively before you commit engineering.

**Cost: ~2 hours.**

### Scope estimate

Including the empirical verification step: ~3 days engineering + ~0.5 day for verification + Streetlives staff review (parallel). Breakdown:

- Tone routes data (~80 utterances, ~10 categories): half day, but should involve Streetlives staff for voice review.
- Router interface change (multi-signal output): half day.
- Threshold calibration against existing eval: half day.
- Tests (route-firing tests, threshold guard tests, integration tests): 1 day.
- Integration into existing tone consumers: half day.

### Eval scenarios that benefit

Direct beneficiaries (where lexicon currently misses but semantic should fire):

- `peer_aging_out_foster` — situation-implied distress; semantic might catch "I have nowhere to go" → `lost` or `scared`.
- `multi_three_services_legal_benefits_food` (asylum seeker) — current tone=4; semantic might detect the implicit weight in "I need help with my asylum case."
- `peer_charge_phone_wifi` — current tone=3 ("transactional, flat"); semantic might detect the implied vulnerability.
- Future scenarios for transitions, populations, and emotional contexts.

Indirect: improves Pattern 1's input signal quality. Pattern 1 (acknowledgment dispatch) consumes tone signals; richer input means richer output.

### Decision needed

1. **Empirical verification first?** Recommend yes — 2-hour experiment validates the approach before engineering kicks off.
2. **Streetlives staff review of canonical utterances.** Same voice-review consideration as Pattern 1's acknowledgment templates and Pattern 2's transition templates. Should happen in parallel.
3. **Multi-label router interface.** Should `SemanticMatch` grow a `tone` field, or should there be a separate `tone_match()` method? Affects API consistency.
4. **Migration strategy for the existing lexicon.** Keep both (lexicon wins, semantic fallback)? Replace lexicon with semantic? Hybrid with confidence-weighted voting? Recommendation: keep both; lexicon hit fires special-case handlers that have specific copy ("It takes real strength to reach out..."), semantic hit fires general categorical responses.

---

## What's already good (don't refactor)

The audit also identifies architecture that's working well. These are exemplars to copy, not problems to fix.

### The 3-tier extraction cascade

Regex → semantic → LLM gate is the right shape. Each tier has clear performance characteristics, the cascade has explicit fallback semantics, and the `extraction_source` field provides clean telemetry. **Pattern 2 (transitions) deliberately copies this structure** rather than introducing a new one.

### `MessageContext` dataclass

Replaced 15+ closure variables in `generate_reply`. Clean dataclass, well-documented fields, late-set fields explicitly labeled. **The `confidence_reason` field added in last week's PR fits naturally here** — same pattern of adding orthogonal state cleanly.

### Crisis detector phrase-list structure

8 categorized phrase lists with consistent application via shared helpers (`_has_safety_concern`, `_is_negated`). **This is the exemplar Pattern 4 wants to generalize** to the rest of the codebase.

### Slot-merge architecture

Even though individual merge cases are complex (1244 lines in `merge.py`), the structure — early extraction, gate, merge, dispatch — is sound. The complexity is inherent to the domain (handling multi-turn slot updates with cross-references), not architectural.

### Quick-reply composition

Each handler composes its quick replies inline, but the patterns are consistent: a list of dicts with `label` and `value` keys, with `_WELCOME_QUICK_REPLIES` as a shared constant. No refactoring needed.

---

## Recommended sequencing

If the team wants to tackle these patterns systematically, this sequence minimizes blocking and maximizes learning:

```
Week 0 (precursor):
   Pattern 8: Tone detection via semantic router extension
   (unblocks Pattern 1; ~3 days incl. empirical verification)

Week 1-2:
   Pattern 3: Urgency as first-class slot
   (unblocks Pattern 2)

Week 3-6:
   Pattern 2: Transition-event bundles
   (separate design doc; existing approval needed)

Week 7-8:
   Pattern 1: Acknowledgment dispatch table
   (highest leverage; consumes Pattern 8's tone signals;
    can start in parallel with Pattern 2 Phase 3)

Week 9-10:
   Pattern 4: Phrase-list common framework
   (lower urgency; can defer)
   Pattern 5: Hard-coded org references → data layer
   (consolidate with Pattern 2's resource layer)

Ongoing:
   Pattern 6: Confirmation message composer (when next confirmation eval gap surfaces)
   Pattern 7: Eval-driven comment hygiene (next code-review pass)
```

Total: ~10-12 weeks of engineering for full pattern coverage. Each pattern can ship independently; gates are explicit.

The Week-0 placement of Pattern 8 reflects its precursor role for Pattern 1. If the empirical verification step (≤2 hours) shows `all-MiniLM-L6-v2` doesn't cluster emotion utterances usefully, the fallback (loading a separate emotion-finetuned model) adds ~1 week to the Pattern 8 timeline. Patterns 1-7 sequencing is unaffected either way.

## Risk: doing none of these

If the team continues per-scenario patches:

1. **Eval scores will keep tracking fixture state.** Each new run surfaces 2-3 new gaps; each gets a hand-coded fix; the next run surfaces 2-3 more. Velocity of "fixes shipped" stays high while "structural progress" stalls.

2. **The codebase grows linearly with eval scenarios.** 50 scenarios → 50 hand-coded acknowledgments → 50 detector functions. The architectural ceiling is reached; further scaling requires the patterns this audit proposes.

3. **Maintenance burden compounds.** Each phrase-list addition needs apostrophe-pair handling. Each new acknowledgment needs idempotency consideration. Each new org reference needs to find its way into 5 different files. The cost of additions grows even as the additions themselves stay simple.

4. **Streetlives staff review becomes impossible.** 14 acknowledgments in 4 files, 28 org references in 5 files, 7 transition events in N files — no single person can review the bot's voice end-to-end.

The patterns above are the structural tools to break the linear-cost trajectory.

## Open questions

1. **Prioritization.** This audit ranked patterns by leverage × urgency. Does the team agree, or do other constraints (Streetlives staff bandwidth, eval pressure, deployment cadence) suggest a different sequence?

2. **Pattern 2 + Pattern 5 unification.** Should `transition_resources.json` and `nyc_resources.json` be one file, two scoped files, or a unified resource layer with type tags? Affects design-doc scope for both.

3. **Pattern 1 + emotional categories.** The `_EMOTIONAL_RESPONSES` consolidation is the highest-leverage piece of Pattern 1 (touches every conversation), but it also touches the most-trafficked code path. Risk of behavior change. Worth a careful migration plan.

4. **Pattern 4 framework adoption.** The `PhraseList` class is a small infrastructure investment with broad reach. Question: does the team want to commit to it as the canonical pattern, or accept the duplication cost?

5. **Test-coverage strategy.** Several patterns (1, 2, 4, 8) involve behavioral changes that need parity testing. Should we adopt a structured "before/after eval comparison" workflow analogous to the unified-extractor migration's parallel-run approach?

6. **Eval suite expansion ownership.** Patterns 1 and 2 both want new eval scenarios (negative controls, transition variants, emotional context cases). Who owns the eval scenario library? Currently scattered across runs.

7. **Pattern 8 verification gate.** Should the empirical verification step (≤2 hours: cluster check on labeled eval messages) be a hard gate before the 3-day engineering kicks off, or a parallel validation? Recommendation: hard gate. The fallback path (load a separate emotion-finetuned model) adds infrastructure cost; better to know up front.

## Appendix A: numeric snapshot

Quantification of the patterns identified, for prioritization discussion:

| Metric | Count |
|---|---|
| Lines of code in `backend/app/services/` | 19,072 |
| Files >1000 lines | 6 |
| `_is_*` detector functions | 7 |
| `_*_acknowledgment` functions | 7 |
| `_EMOTIONAL_RESPONSES` if-branches | 9 |
| Module-level phrase/keyword lists | 36 |
| Hard-coded NYC org references | 28 |
| `_build_confirmation_message` call sites | 8 |
| Eval-target / R[N] eval comments | 6 |
| Lines mentioning specific populations | 167 |
| Lines touching urgency | 89 |
| Files touching urgency | 5 |
| Tone classifier firing rate (R42 dashboard) | ~2% of 658 turns |
| Existing semantic router routes (target for Pattern 8) | 22 (15 service + 7 population) |
| `all-MiniLM-L6-v2` parameters loaded | 22M |

These numbers are baselines for measuring pattern adoption progress.

## Appendix B: out-of-scope observations

Surfaced during the audit but not pattern-level — file as separate tickets if not already:

- **`post_results.py` (1748 lines) is the largest file in services**, and it exists at the same name as `handlers/post_results.py` (a known basename collision flagged in earlier work). Worth a dedicated decomposition.
- **`audit_log.py` (1174 lines)** has accumulated logging surface for every event type. Could benefit from event-class consolidation.
- **`slot_extraction_regex.py` (1996 lines)** is the canonical regex extractor and is reaching the size where decomposition may be needed. Not urgent.
- **`handlers/confirmation.py` (1166 lines)** has all the confirmation-flow logic; Pattern 6 would address part of it but the bulk is inherent complexity.
