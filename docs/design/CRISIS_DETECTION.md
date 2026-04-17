# Crisis Detection

This document describes how the YourPeer chatbot detects and responds to crisis situations, the design decisions behind the system, and how to extend or modify it.

## Overview

Crisis detection runs on every incoming message before anything else — before slot extraction, before greeting handling, before database queries. A message that triggers crisis detection always receives immediate safety resources. It never receives a slot-filling follow-up question.

The system uses two sequential stages:

**Stage 1 — Regex pre-check** (<1ms, deterministic): A curated list of phrases covers the most common explicit crisis expressions. If any phrase matches, the response is returned immediately and the LLM is never called.

**Stage 2 — LLM classification** (1–3s, only when regex misses): Claude Sonnet classifies the message against all eight crisis categories. This catches indirect, paraphrased, and culturally specific language that cannot be enumerated in a keyword list — "I've been on the streets for months and nothing helps anymore", "no one would even notice if I disappeared", "he said he'd come back tonight".

This two-stage approach mirrors the slot extraction architecture: regex handles the common case cheaply and deterministically; the LLM handles the ambiguous case accurately.

**Performance optimization (`skip_llm`):** For short safe actions (≤4 words like "yes", "no", "start over", "hello"), the LLM crisis detection stage is skipped entirely. The regex check still runs on every message regardless. This optimization reduces Sonnet API calls by ~20% without compromising safety — messages short enough to skip are structurally unlikely to contain indirect crisis language. The threshold was tightened from 6 to 4 words after testing revealed that 6-word messages like "start over I cant take this" could contain crisis signals.

**Post-results safety (eval P10):** Crisis detection runs BEFORE the post-results question handler in `generate_reply()`. This is critical because messages like "do they even help people like me? I want to die" contain result-reference words ("they") that would match the post-results classifier. Without this ordering, the user would get "I only have the information shown on the cards" instead of crisis resources. The post-results handler never sees the message if crisis is detected.

## Categories

| Category | Description | Primary Resources |
|---|---|---|
| `suicide_self_harm` | Suicidal ideation (direct or indirect), self-harm, passive hopelessness | 988 Suicide & Crisis Lifeline, Crisis Text Line, Trevor Project |
| `domestic_violence` | Abuse by a partner or family member, threats, fleeing a dangerous home situation | National DV Hotline, NYC DV Hotline, Safe Horizon |
| `safety_concern` | Feeling unsafe, being followed, unsafe living situation (general — not runaway-specific) | 988, Crisis Text Line, 311 |
| `trafficking` | Being controlled, unable to leave, documents taken, forced labor or sex work | National Human Trafficking Hotline (1-888-373-7888) |
| `medical_emergency` | Immediate physical danger requiring emergency services | 911, Poison Control |
| `violence` | Threats to harm others, weapons | 911, 988 |
| `youth_runaway` | Ran away, kicked out by parents, unsafe at home, can't go home (Run 31+) | National Runaway Safeline (1-800-786-2929), Covenant House |
| `assault_victim` | Got beat up, was attacked, got jumped, physically assaulted (Run 31+) | Safe Horizon Victim Services (1-800-621-HOPE) |

## Fail-Open Policy

The LLM is only invoked when regex returns nothing — meaning the message was ambiguous enough that no keyword matched. That ambiguity is itself reason to err toward safety.

If the LLM call fails for any reason (API timeout, quota exceeded, malformed response, network error), the system returns a general `safety_concern` response rather than falling through to normal conversation. This is intentional and documented in the code.

The fail-open policy applies **only to messages that reached Stage 2**. Clear service requests like "I need food in Brooklyn" will not trigger the LLM crisis check at all — regex returns `None` quickly, and the message routes normally to slot extraction.

## Code Location

All detection logic and response text lives in a single file:

```
backend/app/services/crisis_detector.py
tests/test_crisis_detector.py             — 36 unit tests (29 regex, 7 LLM)
```

### Phrase lists (Stage 1 — regex)

| Constant | Line | Category |
|---|---|---|
| `_SUICIDE_SELF_HARM_PHRASES` | [L103](../../backend/app/services/crisis_detector.py#L103) | Suicidal ideation, self-harm, passive hopelessness |
| `_VIOLENCE_PHRASES` | [L133](../../backend/app/services/crisis_detector.py#L133) | Threats to harm others, weapons |
| `_DOMESTIC_VIOLENCE_PHRASES` | [L144](../../backend/app/services/crisis_detector.py#L144) | Abuse, threats, fleeing a dangerous home |
| `_SAFETY_CONCERN_PHRASES` | [L174](../../backend/app/services/crisis_detector.py#L174) | Feeling unsafe, runaway, kicked out, unsafe home |
| `_TRAFFICKING_PHRASES` | [L199](../../backend/app/services/crisis_detector.py#L199) | Controlled, unable to leave, documents taken |
| `_MEDICAL_EMERGENCY_PHRASES` | [L215](../../backend/app/services/crisis_detector.py#L215) | Immediate physical danger |

### Response strings

Each response is a static string constant — never LLM-generated. To update the text shown to a user in crisis, edit the constant directly.

| Constant | Line | Shown when |
|---|---|---|
| `_SUICIDE_RESPONSE` | [L232](../../backend/app/services/crisis_detector.py#L232) | `suicide_self_harm` detected |
| `_VIOLENCE_RESPONSE` | [L243](../../backend/app/services/crisis_detector.py#L243) | `violence` detected |
| `_DOMESTIC_VIOLENCE_RESPONSE` | [L252](../../backend/app/services/crisis_detector.py#L252) | `domestic_violence` detected |
| `_TRAFFICKING_RESPONSE` | [L263](../../backend/app/services/crisis_detector.py#L263) | `trafficking` detected |
| `_MEDICAL_EMERGENCY_RESPONSE` | [L273](../../backend/app/services/crisis_detector.py#L273) | `medical_emergency` detected |
| `_SAFETY_CONCERN_RESPONSE` | [L281](../../backend/app/services/crisis_detector.py#L281) | `safety_concern` detected |
| `_YOUTH_RUNAWAY_RESPONSE` | — | `youth_runaway` detected (Run 31+) |
| `_ASSAULT_VICTIM_RESPONSE` | — | `assault_victim` detected (Run 31+) |
| `_FAILOPEN_RESPONSE` | [L320](../../backend/app/services/crisis_detector.py#L320) | LLM unavailable (aliased to `_SAFETY_CONCERN_RESPONSE`) |

### LLM stage

| Constant | Line | Purpose |
|---|---|---|
| `_LLM_CATEGORY_RESPONSES` | [L309](../../backend/app/services/crisis_detector.py#L309) | Maps LLM-returned category names to response strings |
| `_LLM_SYSTEM_PROMPT` | [L322](../../backend/app/services/crisis_detector.py#L322) | Prompt sent to Claude Sonnet for classification |
| `_CRISIS_CATEGORIES` | [L299](../../backend/app/services/crisis_detector.py#L299) | Ordered list of `(category, phrases, response)` tuples used by the regex loop |

The LLM stage uses a lazy-initialized Anthropic client, consistent with `llm_slot_extractor.py`. It activates automatically when `ANTHROPIC_API_KEY` is present in the environment.

## Detection Flow

```
Incoming message
      │
      ▼
Stage 1: Regex pre-check
      │
      ├── Match found → return (category, response)   ← < 1ms, no LLM
      │
      └── No match
            │
            ├── ANTHROPIC_API_KEY not set → return None (normal routing)
            │
            └── ANTHROPIC_API_KEY set
                      │
                      ▼
               Stage 2: LLM classification (Claude Sonnet)
                      │
                      ├── Crisis detected → return (category, response)
                      │
                      ├── No crisis → return None (normal routing)
                      │
                      └── LLM error (timeout, API down, bad JSON)
                                │
                                └── FAIL OPEN → return ("safety_concern", response)
```

## Responses

Each category has a dedicated response. All responses share these properties:

- **Warm and non-judgmental tone** — the person is not in trouble, they are not being evaluated
- **Leads with the most relevant resource** for that crisis type
- **Includes 911** for any situation involving immediate physical danger
- **Offers peer navigator connection** as an additional human touchpoint
- **Does not clear the session** — the user can continue their service search after seeing crisis resources

Responses are static strings, not LLM-generated. This ensures they are consistent, reviewed, and do not vary based on context.

## Phrase Lists

The regex phrase lists in `_SUICIDE_SELF_HARM_PHRASES`, `_DOMESTIC_VIOLENCE_PHRASES`, `_SAFETY_CONCERN_PHRASES`, `_TRAFFICKING_PHRASES`, `_MEDICAL_EMERGENCY_PHRASES`, and `_VIOLENCE_PHRASES` are organized by subcategory with inline comments explaining additions. Key design notes:

**Substring matching, not whole-word matching.** "I want to kill myself" contains "kill myself", so it matches. This means short phrases need care — "hurt" alone would create false positives on "my foot hurts", so phrases like "hurt myself" and "hurt him" are used instead of "hurt" alone.

**Apostrophe variants.** Both `"don't feel safe"` and `"dont feel safe"` are listed. Users on mobile frequently omit apostrophes.

**Passive ideation is included.** Indirect hopelessness like `"what's the point anymore"`, `"nothing helps anymore"`, `"better off without me"` is covered in `_SUICIDE_SELF_HARM_PHRASES`. These don't mention death but signal suicidal ideation for a population experiencing chronic homelessness and crisis.

**Youth runaway is a safety concern.** Phrases like `"ran away from home"`, `"kicked out of my home"`, `"can't go home"` are in `_SAFETY_CONCERN_PHRASES`. Running away is an acute safety situation for youth even when DV isn't explicitly mentioned.

**"Kicked me out" overlaps with DV.** The DV detector also contains `"kicked me out"`, so some kicked-out scenarios will fire as `domestic_violence` rather than `safety_concern`. Both responses include hotlines and are appropriate — the overlap is intentional.

## Why LLM Instead of More Phrases

Every phrase-based fix has been reactive — a scenario fails in eval, phrases are added, the scenario passes. This is not scalable. In production with real users, language will be:

- Culturally specific and multilingual
- Indirect and contextual ("he's coming home soon and I need to get out")
- Mixed with service requests ("I need shelter and I'm scared")
- Expressed with spelling errors, abbreviations, or fragments

The LLM stage addresses this directly. A prompt-based classifier can generalize across phrasings without enumeration. The regex stage is retained for speed, auditability, and determinism on the common cases — the LLM handles everything the regex can't.

The LLM prompt explicitly asks Claude to be sensitive to indirect language and to err toward `crisis: true` when uncertain, which aligns with the system's fail-open philosophy.

## Emotional Phrase Guard

Before invoking the LLM (Stage 2), the system checks whether the message matches a known sub-crisis emotional phrase — expressions that indicate distress but not danger. Examples: "feeling scared", "I'm struggling", "rough day", "stressed out", "nobody cares."

If the message matches any of these phrases, the LLM stage is **skipped entirely** and `detect_crisis` returns `None`. The message is then handled by the emotional tone handler in the chatbot router, which provides empathetic acknowledgment and a peer navigator offer — a more appropriate response than crisis hotlines for someone having a bad day.

**Why this guard exists:** The LLM's crisis detection prompt says "when in doubt, err toward crisis=true." This is correct for genuinely ambiguous safety situations ("I've been on the streets for months and nothing helps anymore"), but it over-escalates clearly emotional-but-not-crisis messages. A user saying "I'm feeling scared" who receives suicide hotline numbers instead of empathy has a worse experience than one who receives no crisis resources at all — the mismatch signals that the bot doesn't understand them.

The guard phrase list mirrors `_EMOTIONAL_PHRASES` from `phrase_lists.py` and is defined inline in `detect_crisis()` as `_SUB_CRISIS_EMOTIONAL`.

**Note on "nobody cares":** This phrase was originally in both `_SUICIDE_SELF_HARM_PHRASES` (crisis regex) and `_EMOTIONAL_PHRASES`. The crisis regex fired first, over-escalating emotional loneliness to suicide crisis. Fixed by changing the crisis regex entry from bare `"nobody cares"` to the specific `"nobody cares if i"` — the "if i" suffix is the suicidal marker. Bare "nobody cares" now falls through to the emotional handler. The `_SUB_CRISIS_EMOTIONAL` guard still lists it to prevent the LLM stage from re-escalating.

## Crisis Step-Down

When crisis detection fires AND the user has explicit service intent (regex found a service keyword), the system applies a **step-down** that shows crisis resources while preserving the service context:

```
User: "My family kicked me out and I need shelter in Brooklyn"
                    ↓
         detect_crisis → "safety_concern" (regex match: "family kicked me out")
         extract_slots → service_type: "shelter", location: "Brooklyn"
                    ↓
         Step-down: crisis category is non-acute (safety_concern)
                    AND has_service_intent is True
                    ↓
         Response: [crisis resources] + "I can also help you find shelter
                   in Brooklyn — would you like me to search?"
         Quick replies: [✅ Yes, search for shelter] [🤝 Peer navigator]
                    ↓
         Session: service_type="shelter", location="Brooklyn" PRESERVED
                  _last_action="crisis" SET
```

**Which categories get step-down:** `safety_concern`, `domestic_violence`, `youth_runaway`, and `assault_victim` (Run 31+). These are situations where the user may need both safety resources AND practical help finding services. A 17-year-old runaway needs crisis hotline numbers AND shelter — the step-down provides both. Acute categories (`suicide_self_harm`, `medical_emergency`, `trafficking`, `violence`) always show crisis resources only — the immediate safety concern overrides everything.

**"Yes" after step-down:** Executes the service search using the preserved slots. If enough slots are present, goes directly to results. If not, asks the follow-up question for the missing slot.

**"No" after step-down:** Acknowledges gracefully ("The resources above are available anytime. If you'd like to search for services later, I'm here.") with navigator and search buttons.

**DV population injection (Phase 5):** When the crisis category is `domestic_violence`, `dv_survivor` is injected into the session's `_populations` list. This ensures that when the user confirms the service search (or starts a new search later), DV-specific services are boosted to the top of results via description-based ORDER BY ranking. The injection fires in both branches — with service intent (step-down path) and without (crisis-only path). This bridges a coverage gap: the crisis detector has 54 DV phrases (e.g., "he hits me", "afraid to go home") but the population extractor only matches 10 of them (e.g., "domestic violence", "abusive relationship"). Without the injection, a user who says "he hits me and I need shelter" would get generic shelter results instead of DV-specialized services like Safe Horizon or Family Justice Centers.

## Extending the System

### Adding a new regex phrase

Add to the appropriate list in `crisis_detector.py`. Use the most specific phrase that avoids false positives. Add a corresponding test in `test_crisis_detector.py`.

```python
# Example: add to _DOMESTIC_VIOLENCE_PHRASES
"he controls all my money",
"she won't let me have my phone",
```

### Adding a new crisis category

1. Add a phrase list constant (`_NEW_CATEGORY_PHRASES`)
2. Add a response constant (`_NEW_CATEGORY_RESPONSE`)
3. Add the tuple to `_CRISIS_CATEGORIES` and `_LLM_CATEGORY_RESPONSES`
4. Update the LLM system prompt to include the new category description
5. Add tests for detection, response content, and false positive guards

### Updating the LLM prompt

The prompt is `_LLM_SYSTEM_PROMPT` in `crisis_detector.py`. Keep it narrow and focused — the model only needs to answer yes/no with a category. The prompt intentionally does not include example messages, to avoid over-fitting to specific phrasings.

### Monitoring

The audit log records every crisis detection event via `log_crisis_detected()`. The staff admin console at `/admin` shows crisis events with timestamps and anonymized message snippets. All LLM-stage detections and fail-open events are logged at `WARNING` level with the message prefix `LLM crisis detected` or `LLM crisis detection failed`.

## Testing

The test suite in `tests/test_crisis_detector.py` has 36 tests across three areas:

**Regex detection (22 tests):** One test per major phrase group for each category, plus response content tests (verifying hotline numbers are present), false positive tests (normal service requests must not trigger), and integration tests (crisis in a longer message, crisis alongside a service request).

**P8 / P9 additions (7 tests):** Dedicated tests for passive suicidal ideation and youth runaway scenarios, added after these cases failed in eval Run 6. Includes a documented intentional false positive (`"I give up"` fires broadly by design).

**LLM stage (7 tests):** All LLM tests use mocks and run without a real API key. They verify the short-circuit (LLM not called when regex fires), the invocation path (LLM called when regex misses), correct category routing, non-crisis returns `None`, and fail-open behavior on both API errors and malformed JSON.

```bash
# Run the full crisis detection test suite
python -m pytest tests/test_crisis_detector.py -v
```
