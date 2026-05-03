# Implementation Plan: Misspelling Tolerance for YourPeer Chat

**Date:** April 21, 2026
**Scope:** Improve the chatbot's ability to understand misspelled service requests from users with low literacy, non-standard English, or mobile keyboard errors — without introducing false corrections that harm AAVE speakers, Spanish-English code-switchers, or users of NYC-specific terminology.
**Approach:** Three complementary layers (A + D + F), shipped in order of effort and risk.

---

## The problem

YourPeer's chatbot accepts free-text input for the first time in the product's history. The existing yourpeer.nyc web app uses category buttons — users tap "Food," "Shelter," "Clothing" — so misspelling was never a concern. The chatbot deliberately chose conversational input for a more natural experience, but that design choice means the system must now handle messages like:

- "I need **fod** in Brooklyn" (food)
- "where can I get **cloths**" (clothing)
- "**sheltr** near me" (shelter)
- "I need **halp** with my **imigration** case" (help, immigration)
- "**showwr** in manhattan" (shower)
- "need to see a **docter**" (doctor → health care)

The target population has higher-than-average rates of low literacy, uses mobile devices with small keyboards, and includes speakers of AAVE, Spanish-English bilingual speakers, and people with learning disabilities. Misspelling is not an edge case — it's a baseline expectation.

### How the current system handles misspellings

The chatbot's three-tier classification pipeline has different levels of typo tolerance:

**Tier 1 — Regex (sub-millisecond, ~80% of messages).** Zero typo tolerance. Keyword lists in `slot_extraction_regex.py` use exact substring matching. "sheltr" does not match "shelter." A misspelled service keyword falls through to Tier 2.

**Tier 2 — Semantic Router (all-MiniLM-L6-v2, ~5ms, ~5% of messages).** Partial typo tolerance. BPE tokenization provides some resilience — "shelterr" still tokenizes into sub-words related to "shelter." But research shows sentence transformers suffer 15-21% accuracy degradation on character-level noise. The 15 pre-embedded routes currently contain only clean utterances, so the model has no training signal that "sheltr" should match the shelter route.

**Tier 3 — Claude Haiku LLM (~1-2s, ~15% of messages).** Excellent typo tolerance. LLMs are trained on vast amounts of noisy internet text and handle misspellings naturally. But each call costs ~$0.25/1M input tokens and adds 1-2 seconds of latency. This is the current safety net for misspelled messages — it works, but it's the slowest and most expensive tier.

### What this means in practice

A user who types "I need fod in queens" follows this path: Tier 1 regex misses "fod" (not in keyword list) → Tier 2 semantic router may or may not match depending on how severely "fod" distorts the embedding → if Tier 2 misses, Tier 3 LLM catches it but at 1-2s latency and API cost. The goal of this plan is to catch most misspellings before they reach Tier 3, without introducing false corrections.

---

## Approach: three layers, shipped in order

### Why three layers instead of one

No single approach handles all misspelling types well:

- **Mild typos** ("shelterr", "foood") — the semantic router can handle these with augmented examples.
- **Severe typos** ("shltr", "fod") — need explicit fuzzy matching against known keywords.
- **Prevention** — some misspellings can be avoided entirely with autocomplete UI.

Each layer catches what the previous layer misses, and each has a different risk profile. Shipping in order (A → D → F) means each layer can be measured independently before adding the next.

---

## Layer A — Augment Semantic Router Utterances

**What:** Add misspelled variants of service keywords to the example utterances in `semantic_routes.py`.

**Why:** The semantic router (Tier 2) uses pre-computed embeddings of example utterances to match incoming messages. Adding misspelled examples teaches the embedding space that "I need fod" is semantically close to "I need food." The embedding model generalizes — once it sees "fod" near "food," it also handles "fud," "fodd," and other nearby variants better.

**Effort:** 15-30 minutes. Zero risk. No new dependencies. No runtime cost.

**Priority:** P0 — ship immediately.

### Implementation

In `semantic_routes.py`, each route has a list of example utterances. Add 3-5 misspelled variants per service category, focusing on the most common misspelling patterns (missing letters, swapped letters, phonetic spelling):

```python
# shelter route — existing utterances plus misspelled variants
{
    "route": "shelter",
    "utterances": [
        # ... existing clean utterances ...
        "I need a place to sleep tonight",
        "where can I find shelter",
        # Misspelled variants
        "I need sheltr",
        "sheltar near me",
        "shelta in brooklyn",
        "I need a shleter",
        "emergancy shelter",
    ]
}

# food route
{
    "route": "food",
    "utterances": [
        # ... existing ...
        # Misspelled variants
        "I need fod",
        "where can I get fud",
        "fod pantry near me",
        "Im hungray",
        "food pantree",
    ]
}

# clothing route
{
    "route": "clothing",
    "utterances": [
        # ... existing ...
        "I need cloths",
        "clothig near me",
        "where can I get clotes",
    ]
}

# health_care route
{
    "route": "health_care",
    "utterances": [
        # ... existing ...
        "I need to see a docter",
        "helth care",
        "I need medcal help",
        "docter near me",
    ]
}

# shower route
{
    "route": "shower",
    "utterances": [
        # ... existing ...
        "I need a showwr",
        "where can I showor",
        "showr near me",
    ]
}

# legal route
{
    "route": "legal",
    "utterances": [
        # ... existing ...
        "I need leagal help",
        "imigration lawyer",
        "legul aid",
    ]
}

# benefits route
{
    "route": "benefits",
    "utterances": [
        # ... existing ...
        "fod stamps",
        "how do I get benifits",
        "SNAPP application",
    ]
}
```

### How to generate the misspelled variants

Don't guess — use data. Three sources:

1. **Common English misspelling databases.** Words like "accommodation," "necessary," and "receive" have well-documented misspelling patterns. For service keywords, check common misspelling lists for each word.

2. **Keyboard proximity errors.** On a mobile QWERTY keyboard, "food" → "foid" (o and i are adjacent), "shelter" → "shelyer" (t and y are adjacent). Simulate by shifting each character to its keyboard neighbor.

3. **Phonetic misspellings.** People spell words how they sound: "docter" (doctor), "sheltar" (shelter), "emergancy" (emergency), "helth" (health).

Start with 3-5 variants per category. After a few weeks of production data, review Tier 3 LLM traffic for messages that contain service intent — those are messages the semantic router missed, and any misspelled ones should be added as new utterances.

### Validation

Run the eval suite's `natural_language` and `accessibility` categories before and after. Confirm:
- No passing scenario regresses.
- The semantic router's hit rate on misspelled inputs improves (add 2-3 temporary eval scenarios with misspelled inputs to measure).

### Limitations

Layer A helps Tier 2 but doesn't help Tier 1 (regex). A message like "fod in queens" still won't match the Tier 1 food keyword list — it'll fall through to the augmented Tier 2, which is fine for routing but means the regex-based slot extraction (`_extract_service_type`) still misses. The slot extractor may need to run again after the semantic router identifies the route, or Layer D (below) handles this gap.

---

## Layer D — Fuzzy Matching on Service Keywords (RapidFuzz)

**What:** Add a targeted fuzzy matching step that compares individual words in the user's message against the ~50 known service keywords. Words that closely match a keyword get normalized before Tier 1 regex runs.

**Why:** This catches severe misspellings that even the augmented semantic router misses ("shltr," "fod," "hlth") without the risks of full-message spell correction. By limiting the fuzzy matching target to ~50 known service keywords, the false-positive surface is tiny — a word only gets "corrected" if it's very close to a specific service keyword, not to any English word.

**Effort:** 1-2 days. Low risk. One new dependency (RapidFuzz, MIT license, pure C++/Python).

**Priority:** P1 — ship next sprint, after Layer A is validated.

### Why RapidFuzz over SymSpell

SymSpell is designed for full-text spell correction against a large dictionary (~80,000+ English words). It's extremely fast for that purpose. But for YourPeer, full-text correction is the wrong approach — it would "correct" valid words the dictionary doesn't know:

| Input | SymSpell output (standard dict) | Actual intent |
|---|---|---|
| "methadone" | "methane" ❌ | substance use treatment |
| "DYCD" | "dyed" ❌ | NYC youth services |
| "Bushwick" | "bushwhack" ❌ | Brooklyn neighborhood |
| "finna" (AAVE) | "final" ❌ | "going to" |
| "im finna go" | "im final go" ❌ | "I'm going to go" |

A custom dictionary could mitigate this, but maintaining it is an ongoing burden and every new term, neighborhood, or abbreviation that isn't added becomes a potential false correction.

RapidFuzz, by contrast, does targeted comparison: "is this word similar to any of these 50 specific service keywords?" If not, the word passes through untouched. No dictionary maintenance. No risk of correcting AAVE, Spanish, neighborhood names, or abbreviations — those words simply don't match any service keyword at the threshold.

RapidFuzz is the industry standard for fuzzy string matching in Python. It's MIT-licensed (unlike FuzzyWuzzy which is GPL), 40% faster than alternatives, and written in C++ with Python bindings.

### Implementation

Create a new function in `slot_extraction_regex.py` (or a new module `services/fuzzy_keywords.py`):

```python
from rapidfuzz import fuzz, process

# The known service keywords — same list the regex uses, flattened
SERVICE_KEYWORDS = {
    "shelter": "shelter",
    "housing": "shelter",
    "food": "food",
    "meal": "food",
    "pantry": "food",
    "clothing": "clothing",
    "clothes": "clothing",
    "shower": "shower",
    "health": "health_care",
    "medical": "health_care",
    "doctor": "health_care",
    "dental": "health_care",
    "legal": "legal",
    "lawyer": "legal",
    "immigration": "legal",
    "benefits": "benefits",
    "stamps": "benefits",
    # ... ~50 total entries
}

# Words to NEVER fuzzy-match (protected vocabulary)
PROTECTED_WORDS = {
    # NYC boroughs and neighborhoods
    "manhattan", "brooklyn", "queens", "bronx", "staten",
    "harlem", "bushwick", "astoria", "soho", "tribeca",
    "chelsea", "midtown", "williamsburg", "bedstuy",
    # Common AAVE and informal terms
    "finna", "gonna", "wanna", "gotta", "tryna",
    "aint", "yall", "bruh", "lowkey",
    # Service-specific terms that look like misspellings
    "methadone", "suboxone", "narcan", "medicaid",
    "dycd", "snap", "ebt", "tanf", "wic",
    "dhs", "path", "hra",
}

SIMILARITY_THRESHOLD = 85  # 0-100 scale

def fuzzy_normalize_service_keywords(message: str) -> str:
    """Replace misspelled service keywords with their canonical forms.

    Only modifies words that closely match a known service keyword
    AND are not in the protected vocabulary. All other words pass
    through unchanged.
    """
    words = message.lower().split()
    normalized = []

    for word in words:
        # Skip short words (too ambiguous) and protected words
        if len(word) <= 2 or word in PROTECTED_WORDS:
            normalized.append(word)
            continue

        # Skip words that are already exact matches
        if word in SERVICE_KEYWORDS:
            normalized.append(word)
            continue

        # Fuzzy match against service keywords
        match = process.extractOne(
            word,
            SERVICE_KEYWORDS.keys(),
            scorer=fuzz.ratio,
            score_cutoff=SIMILARITY_THRESHOLD,
        )

        if match:
            matched_keyword, score, _ = match
            # Log the correction for monitoring
            _log_fuzzy_correction(word, matched_keyword, score)
            normalized.append(matched_keyword)
        else:
            normalized.append(word)

    return " ".join(normalized)
```

### Where it runs in the pipeline

Insert fuzzy normalization as a "Tier 0.5" step — after the raw message is received but before Tier 1 regex runs:

```python
# In orchestrator.py or pipeline.py, before regex extraction:
normalized_message = fuzzy_normalize_service_keywords(user_message)
# Tier 1 regex now runs on the normalized message
slots = extract_slots(normalized_message)
```

The original (un-normalized) message is preserved for:
- Display in the chat UI (show what the user actually typed)
- Transcript storage (store the original, not the correction)
- LLM context (if the message reaches Tier 3, the LLM sees the original)

Only the slot extraction pipeline sees the normalized version.

### Monitoring and tuning

Add an audit log event for every fuzzy correction:

```python
def _log_fuzzy_correction(original: str, corrected: str, score: float):
    audit_log.log_event(
        event_type="fuzzy_keyword_correction",
        data={
            "original_word": original,
            "corrected_to": corrected,
            "similarity_score": score,
        }
    )
```

After 1-2 weeks of production traffic, review the log for:
- **False positives** — words that were corrected but shouldn't have been. Add them to `PROTECTED_WORDS`.
- **False negatives** — misspelled service keywords that weren't caught. Lower the threshold or add the misspelling pattern to Layer A's semantic routes.
- **Threshold tuning** — if false positives are rare, consider lowering from 85 to 80 for broader coverage. If false positives are common, raise to 90.

### Dependency

Add to `requirements.txt`:
```
rapidfuzz>=3.0.0
```

RapidFuzz has no transitive dependencies beyond the C++ build tools (which are standard in the Docker build environment). Package size is ~2MB. No model files to download.

### Tests

Unit tests:

```python
# Corrections that should fire
def test_fod_corrects_to_food():
    assert fuzzy_normalize("I need fod") == "i need food"

def test_sheltr_corrects_to_shelter():
    assert fuzzy_normalize("sheltr near me") == "shelter near me"

def test_docter_corrects_to_doctor():
    assert fuzzy_normalize("I need a docter") == "i need a doctor"

def test_cloths_corrects_to_clothes():
    assert fuzzy_normalize("where can I get cloths") == "where can i get clothes"

# Corrections that should NOT fire
def test_harlem_not_corrected():
    assert fuzzy_normalize("food in harlem") == "food in harlem"

def test_methadone_not_corrected():
    assert fuzzy_normalize("I need methadone") == "i need methadone"

def test_finna_not_corrected():
    assert fuzzy_normalize("I finna need shelter") == "i finna need shelter"

def test_dycd_not_corrected():
    assert fuzzy_normalize("dycd youth center") == "dycd youth center"

# Edge cases
def test_short_words_not_corrected():
    assert fuzzy_normalize("I am ok") == "i am ok"

def test_exact_match_passes_through():
    assert fuzzy_normalize("I need food") == "i need food"

def test_multiple_corrections():
    assert fuzzy_normalize("I need fod and sheltr") == "i need food and shelter"
```

### Eval scenarios

Add 3-4 misspelling-specific eval scenarios to `eval_llm_judge.py`:

```python
{
    "id": "misspelling_severe_food",
    "name": "Severely misspelled food request",
    "category": "accessibility",
    "message": "I need fod in queens",
    "expected": {"service_type": "food", "location": "queens"},
}

{
    "id": "misspelling_severe_shelter",
    "name": "Severely misspelled shelter request",
    "category": "accessibility",
    "message": "sheltr near me tonight",
    "expected": {"service_type": "shelter"},
}

{
    "id": "misspelling_aave_preserved",
    "name": "AAVE phrasing preserved, service keyword corrected",
    "category": "equity_of_access",
    "message": "im finna need some cloths",
    "expected": {"service_type": "clothing"},
    # Key: "finna" should NOT be corrected, "cloths" SHOULD be
}
```

### Limitations

Layer D normalizes individual words, not phrases. A user who types "fod stamps" gets "food stamps" (correct). But "food stmaps" gets "food stamps" only if "stmaps" fuzzy-matches "stamps" (it does at ~83%, which is below the 85% threshold). Lowering the threshold catches more but risks false positives. The semantic router (Layer A) and LLM (Tier 3) cover the remaining gap.

Layer D also doesn't handle missing-space errors ("Ineedfood") or extra-space errors ("I need f ood"). SymSpell has a word segmentation feature for the former; the latter is rare enough to defer.

---

## Layer F — Client-Side Autocomplete Suggestions

**What:** As the user types in the chat input, show a dropdown of matching service categories. Typing "she" shows "🏠 Shelter" and "👔 Clothing." Typing "foo" shows "🍽️ Food." Tapping a suggestion inserts the canonical term.

**Why:** Prevention is better than correction. Autocomplete catches misspellings before they're submitted, works offline (the keyword list is static and cached by the service worker), and doubles as a discoverability feature — users who don't know they can search for "benefits enrollment" or "legal aid" see those options surface as they type.

**Effort:** 2-3 days (frontend only). Zero backend changes. Zero risk to existing functionality.

**Priority:** P2 — ship after Layer D is validated. Can run in parallel with Layer D since it's frontend-only.

### Implementation

Create a new component `src/components/chat/autocomplete-suggestions.tsx`:

```tsx
const SERVICE_SUGGESTIONS = [
  { keyword: "food", label: "Food & Meals", emoji: "🍽️",
    aliases: ["food", "meal", "pantry", "hungry", "eat"] },
  { keyword: "shelter", label: "Shelter & Housing", emoji: "🏠",
    aliases: ["shelter", "housing", "bed", "sleep", "homeless"] },
  { keyword: "clothing", label: "Clothing", emoji: "👔",
    aliases: ["clothing", "clothes", "wear", "jacket", "coat"] },
  { keyword: "shower", label: "Showers & Hygiene", emoji: "🚿",
    aliases: ["shower", "hygiene", "wash", "laundry"] },
  { keyword: "health care", label: "Health Care", emoji: "🏥",
    aliases: ["health", "medical", "doctor", "clinic", "dental", "hospital"] },
  { keyword: "legal help", label: "Legal Help", emoji: "⚖️",
    aliases: ["legal", "lawyer", "immigration", "asylum", "court"] },
  { keyword: "benefits", label: "Benefits & SNAP", emoji: "📋",
    aliases: ["benefits", "snap", "ebt", "stamps", "wic", "food stamps"] },
  { keyword: "mental health", label: "Mental Health", emoji: "💚",
    aliases: ["mental", "counseling", "therapy", "depression", "anxiety"] },
  { keyword: "substance use", label: "Substance Use Help", emoji: "🤝",
    aliases: ["substance", "detox", "rehab", "methadone", "addiction", "recovery"] },
];
```

### Matching logic

The autocomplete should trigger when the user has typed at least 2 characters and the input matches either a keyword or an alias. Use a simple prefix match with a fuzzy fallback:

```typescript
function getSuggestions(input: string): Suggestion[] {
  const query = input.toLowerCase().trim();
  if (query.length < 2) return [];

  // Extract the last word being typed
  const lastWord = query.split(/\s+/).pop() || "";
  if (lastWord.length < 2) return [];

  return SERVICE_SUGGESTIONS.filter(s =>
    s.aliases.some(alias =>
      alias.startsWith(lastWord) ||           // prefix match
      levenshteinDistance(alias, lastWord) <= 2 // fuzzy fallback
    )
  ).slice(0, 3); // show max 3 suggestions
}
```

A lightweight Levenshtein implementation (10 lines of TypeScript) is sufficient here — no need for RapidFuzz on the frontend. The keyword list is ~50 entries and the comparison runs on every keystroke, so it must be fast, but 50 Levenshtein comparisons at ~10 characters each is trivially fast in any browser.

### UX design

The autocomplete dropdown appears above the input field (not below, which would be hidden by the mobile keyboard). It shows up to 3 suggestions, each as a tappable pill with emoji + label. Tapping a suggestion:

1. Replaces the last word in the input with the suggestion's keyword.
2. Appends a space so the user can continue typing (e.g., "I need " + tap "Shelter" → "I need shelter ").
3. Dismisses the dropdown.

Pressing Enter/Send while the dropdown is visible sends the message as-is (the user may not want any suggestion). The dropdown dismisses on blur or when the input is empty.

Visual style: match the existing quick-reply button styling (amber/yellow pills) for consistency. Keep the dropdown lightweight — no shadows, no animations, no loading states. It should feel like part of the input, not a modal.

### Accessibility

- Each suggestion has an `aria-label` with the full text ("Shelter and Housing").
- Arrow keys navigate between suggestions.
- Enter selects the highlighted suggestion (if navigated via keyboard).
- Screen readers announce "3 suggestions available" when the dropdown appears.
- The dropdown is a `role="listbox"` with `aria-activedescendant` tracking.

### Offline behavior

The `SERVICE_SUGGESTIONS` array is bundled in the JavaScript — it's not fetched from the server. The service worker caches the JS bundle on first load. This means autocomplete works fully offline, which is exactly when it's most valuable: a user typing on a spotty connection benefits from being guided to the canonical keyword rather than submitting a misspelled message that can't be sent until connectivity returns.

### Limitations

Autocomplete only helps if the user pauses while typing and looks at the suggestions. Someone who types quickly and hits send without looking at the dropdown won't benefit. This is a supplement to Layers A and D, not a replacement.

Autocomplete also doesn't help with voice input. The architecture doc (§15) describes a voice interface as a future feature. Voice-to-text transcription typically produces correctly spelled words (the ASR model handles this), so misspelling tolerance is primarily a text-input concern.

---

## Approaches explicitly not recommended

### SymSpell full-message normalization

SymSpell corrects every word in the message against an 80,000-word English dictionary. This creates unacceptable false-positive risk for this population:

| Input | SymSpell output | Problem |
|---|---|---|
| "I need methadone" | "I need methane" | Drug name not in standard dictionary |
| "DYCD youth center" | "dyed youth center" | NYC acronym mangled |
| "food in Bushwick" | "food in bushwhack" | Neighborhood name mangled |
| "im finna need help" | "im final need help" | AAVE verb form "corrected" |
| "I stay in bedstuy" | "I stay in bed stew" | Neighborhood name mangled |

A custom dictionary mitigates some of these, but:
- It must be maintained as the service evolves (new neighborhoods, new programs, new terminology).
- It must include AAVE vocabulary, which varies by community and generation.
- It must include every drug name, program acronym, and shelter name — a long tail that's hard to enumerate.
- Even with a custom dictionary, novel proper nouns (people's names) risk correction.

The targeted approach (Layer D — fuzzy match against ~50 service keywords only) achieves the same benefit for service keyword correction with a dramatically smaller false-positive surface.

### TextBlob / pyspellchecker

Both libraries use dictionary-based spell correction similar to SymSpell but slower. They share the same false-positive risks and don't add any benefit that SymSpell doesn't already provide faster. TextBlob has a documented tendency to autocorrect person and location names.

### Full phonetic matching (Soundex/Metaphone)

Phonetic algorithms convert words to a sound-based code ("shelter" → "XLTR" in Soundex) and match on the code. This catches homophones ("shelta" sounds like "shelter") but has two problems: the encoding is English-centric and works poorly for non-English speakers, and it produces surprising matches between unrelated words that happen to sound similar. For a ~50-word service keyword list, the incremental value over RapidFuzz's edit-distance matching is minimal. Can be added later inside the Layer D implementation if the data shows a class of sound-alike typos that fuzzy matching misses.

---

## Delivery sequence

### Week 1 — Layer A (semantic route augmentation)

- Add 3-5 misspelled variants per service category to `semantic_routes.py`.
- Run eval on `natural_language` and `accessibility` categories.
- Add 2-3 temporary misspelling eval scenarios to measure improvement.
- Ship as a single PR. No new dependencies.

**Acceptance criteria:** Misspelled eval scenarios route correctly at Tier 2. No existing scenarios regress.

### Week 2-3 — Layer D (RapidFuzz fuzzy matching)

- Install `rapidfuzz` dependency.
- Implement `fuzzy_normalize_service_keywords()` with protected vocabulary.
- Wire into the pipeline as Tier 0.5 (before regex extraction).
- Add unit tests (corrections that should fire + corrections that should NOT fire).
- Add 3-4 eval scenarios (misspelled food, shelter, AAVE preserved).
- Add audit logging for fuzzy corrections.
- Ship as one PR.

**Acceptance criteria:** "fod in queens" extracts `service_type=food, location=queens` at Tier 1. "im finna need cloths" extracts `service_type=clothing` without modifying "finna." All existing eval scenarios pass. Audit log records fuzzy corrections for monitoring.

### Week 3-4 — Layer F (client-side autocomplete)

- Build `autocomplete-suggestions.tsx` component.
- Integrate into `chat-container.tsx` input field.
- Add Levenshtein distance helper (10 lines TypeScript).
- Test on mobile (Android Chrome, iOS Safari) for keyboard interaction.
- Test offline (autocomplete works with cached JS bundle).
- Ship as one PR. No backend changes.

**Acceptance criteria:** Typing "she" shows shelter suggestion. Typing "foo" shows food suggestion. Tapping suggestion inserts keyword. Works offline. Accessible via keyboard and screen reader.

### Week 5 — Monitor and tune

- Review 2 weeks of fuzzy correction audit logs.
- Identify false positives → add to `PROTECTED_WORDS`.
- Identify false negatives → add to Layer A semantic routes or lower Layer D threshold.
- Review Tier 3 LLM traffic for remaining misspelling-caused escalations.
- Write up findings and decide whether phonetic matching (Soundex/Metaphone) is needed as a Layer D enhancement.

---

## Effort and risk summary

| Layer | Effort | Risk | Dependencies | Backend | Frontend |
|---|---|---|---|---|---|
| A — Semantic route augmentation | 30 min | None | None | ✅ | — |
| D — RapidFuzz fuzzy matching | 1-2 days | Low (protected vocabulary guards against false positives) | rapidfuzz>=3.0.0 | ✅ | — |
| F — Client-side autocomplete | 2-3 days | None (frontend only, no backend changes) | None | — | ✅ |

**Total: ~4-5 engineering days over 4 weeks.**

The three layers can be built independently. A and D are backend-only. F is frontend-only. A developer working on the frontend can build F in parallel with someone building D on the backend.

---

## How this integrates with the eval quality plan

The misspelling work connects to two items in the EVAL_QUALITY_ENGINEERING_PLAN:

**B.3 — Coverage additions.** The 3-4 misspelling eval scenarios proposed here can be included in the B.3 batch of new scenario additions. They naturally belong in the `accessibility` or `equity_of_access` categories.

**D.2 — Classifier phrase coverage tests.** The unit tests for Layer D (fuzzy keyword corrections that should and shouldn't fire) follow the same pattern as the classifier phrase tests: parametrized test cases asserting that specific inputs produce specific classifications. They can share the same test infrastructure.

The misspelling work does not conflict with any other workstream in the eval quality plan and can be scheduled independently.

---

## How this works within the PWA

All three layers are PWA-compatible:

**Layer A** runs on the backend. Messages sent from the PWA (whether online or queued offline) are processed identically. No PWA-specific considerations.

**Layer D** runs on the backend. Same as Layer A — the fuzzy normalization happens when the message reaches the server. Queued offline messages get normalized when they flush.

**Layer F** runs entirely on the frontend. The `SERVICE_SUGGESTIONS` array is bundled in the JavaScript, which the service worker caches on first load. Autocomplete works fully offline — it doesn't need any server communication. This is especially valuable for offline users: being guided to the correct keyword before sending reduces the chance that a queued message contains a misspelling that the backend would need to resolve later.

---

*YourPeer AI Chat — Streetlives — April 2026*
