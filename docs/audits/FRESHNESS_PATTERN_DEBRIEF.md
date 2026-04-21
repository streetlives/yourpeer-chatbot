# Bot-Knowledge Module Refresh + Freshness-Guard Pattern

**Date:** 2026-04-21
**Scope:** `backend/app/services/bot_knowledge.py` + drift-detection tests
**Test count impact:** +7 tests (net +2 to full suite after 5 redundant tests were consolidated during review)
**Full suite after:** 3,853 passed, 0 failed, 17 skipped, 3 xfailed
**Docs linter:** no drift detected

## The specific staleness

`bot_knowledge.py` is the single source of truth for what the bot tells users about itself. It feeds two code paths:

1. `answer_question(message)` — keyword-matched static fallback for bot questions (when the LLM is unavailable or the message matches a specific topic pattern).
2. `build_capability_context()` — produces the "Facts about yourself" prompt block the LLM receives when answering bot_question-categorized messages.

Both paths have topic-specific `answer` strings and capability-list entries that were **hand-maintained** despite the module also containing `_get_*` helpers that live-source counts (service categories, PII types, location count) from actual code. The hand-maintained strings drifted silently over ~3 months while the helpers stayed accurate.

### What was stale

| Claim | Truth | Location |
|---|---|---|
| "English only right now. Multi-language support is planned." | Partial Spanish shipped in PR 6 (Spanish keywords + bilingual acknowledgment) | `TOPICS["language"]["answer"]` |
| "Automatic redaction of 8 PII types" | `_PLACEHOLDERS` has 9 keys (includes `gender` for trans/NB user safety) | `TOPICS["privacy_general"]["summary"]` |
| "phone number, name, SSN, email, address, date of birth, credit card, or URLs" | Missing the 9th redacted category (`gender`) | `TOPICS["privacy_general"]["answer"]` |
| "suicidal ideation, domestic violence, medical emergencies, trafficking" | 8 categories in `_CRISIS_CATEGORIES` — also youth_runaway, assault_victim, safety_concern, violence | `build_capability_context()` crisis line |
| "English only currently" | (same as language topic) | `build_capability_context()` limitations line |
| 10× `"source": "chatbot.py → ..."` | `chatbot.py` doesn't exist post-Phase-3; everything is in `chatbot/<submodule>.py` | `TOPICS["*"]["source"]` | <!-- drift:ignore: documenting the historical stale claim as a worked example -->

### User-facing impact

The `language` topic is the most concerning — a Spanish-speaking user asking `"hablas español?"` could have received an outdated English-only answer via the static fallback, even though the surrounding infrastructure (`_handle_spanish_detection`) correctly handles their service requests bilingually.

The PII-type omission is subtler but safety-sensitive: the redactor was already stripping gender-identity terms (important for trans/NB users avoiding surveillance), but the bot's privacy explanation didn't advertise this protection — so a user specifically worried about gender-identity disclosure couldn't learn from the bot that they were already covered.

### Fixes applied

- `language` topic rewritten to accurately describe partial Spanish support + peer-navigator fallback for other languages + note about provider-language service-card field.
- `privacy_general` answer updated to list all 9 PII types including gender-identity terms.
- `privacy_general` summary bumped `8` → `9`.
- `build_capability_context()` refactored to use a new `_get_crisis_categories()` live-source helper + `_format_crisis_categories()` formatter with a `_FRIENDLY` name map. All 8 live categories now surface in the LLM prompt in natural prose.
- `build_capability_context()` limitations line rewritten to describe the partial Spanish state instead of claiming English-only.
- All 10 stale `"chatbot.py → ..."` source refs rewritten to current package paths (`chatbot/orchestrator.py`, `chatbot/handlers/meta.py`, `responses.py`, etc.) per the Phase 3 decomposition.

One interesting catch during verification: **the service-data firewall test fired correctly** on an initial version of my capability-context rewrite. I'd added specific hotline names (`"988 Lifeline, Crisis Text Line, Safe Horizon, National Runaway Safeline, Covenant House, National Trafficking Hotline"`) to the crisis-description line, and the firewall test — which asserts the LLM prompt never contains service-provider names — correctly rejected "Safe Horizon" as a service-data leak. Final version describes hotline categories generically and notes that specific numbers come from the crisis-response path, preserving the firewall boundary.

## The freshness-guard pattern

The deeper question the refresh surfaced: **how does hand-maintained code/documentation drift away from the concepts it describes, and how do we prevent it?**

### The problem shape

A module contains claims *about* another module's live state. Examples in this codebase:

- `bot_knowledge.py` claims the bot can detect N crisis categories; `crisis_detector.py` is the actual authority.
- `TESTING.md` claims test counts per file; the test files are the actual authority.
- `PHRASE_LIST_AUDIT.md` claims phrase-list sizes; `phrase_lists.py` is the actual authority.
- `CHATBOT_BEHAVIOR.md` claims "Spanish support is not implemented"; `handlers/accessibility.py` is the actual authority.

When the authority changes and the claim doesn't, drift. The claims are usually in natural-language prose (answers, summaries, documentation), which is why tests don't typically catch them — pytest asserts code behavior, not prose accuracy.

### The general pattern for guarding against drift

Every freshness guard has the same structure:

1. **Live-source the authority.** Pull the ground-truth concept from the code that owns it (e.g., `len(_PLACEHOLDERS)`, `SERVICE_KEYWORDS.keys()`).
2. **Extract the claim from the consumer.** Parse the hand-maintained prose for the claim (regex on count numbers, substring checks on enumerated items).
3. **Assert equality with an actionable error message.** On mismatch, name *what* changed, *where* to update it, and *what value* to use. The failure message is the contract documentation.

### The four guard templates this module uses

**1. Enumerated-items claim.** A prose answer lists N concrete items pulled from a code collection. Guard asserts every live item appears in the prose (with a friendly-name mapping for when code keys differ from user-facing phrasing).

```python
def test_privacy_general_answer_mentions_every_pii_type(self):
    friendly = {"phone": "phone", "ssn": "ssn", ..., "gender": "gender"}
    missing_from_mapping = set(_PLACEHOLDERS) - set(friendly)
    assert not missing_from_mapping, (
        f"New key in _PLACEHOLDERS: {missing_from_mapping}. Add to "
        f"both this mapping AND the privacy_general answer."
    )
    missing_from_answer = [k for k in _PLACEHOLDERS if friendly[k] not in answer]
    assert not missing_from_answer, f"Answer omits: {missing_from_answer}"
```

**Applies to:** any doc or prompt that enumerates a code-owned list (service categories, crisis categories, PII types, etc.).

**2. Count claim.** Prose states "N X" where X is live-sourceable. Guard regex-extracts N, compares to live count.

```python
def test_privacy_general_summary_count_matches_placeholders(self):
    match = re.search(r"(\d+)\s+PII\s+types", TOPICS["privacy_general"]["summary"])
    claimed = int(match.group(1))
    assert claimed == len(_PLACEHOLDERS), (...)
```

**Applies to:** "N phrases", "N categories", "N tests", "N boroughs" — any count claim with a code authority. The docs linter already implements this pattern for test counts; the bot_knowledge version extends it to module-internal prose.

**3. Name-map completeness.** A local translation dict (e.g., snake_case → user-facing) must have an entry for every live key. Guard extracts keys via regex on the function source, compares to live set.

```python
def test_friendly_crisis_name_map_covers_live_categories(self):
    src = inspect.getsource(bot_knowledge._format_crisis_categories)
    keys = set(re.findall(r'"([a-z_]+)"\s*:\s*"', src))
    live = set(_get_crisis_categories())
    assert not (live - keys), f"Missing friendly names: {live - keys}"
```

**Applies to:** any `_FRIENDLY` / `LABEL_MAP` / `DISPLAY_NAMES` dict that translates code identifiers to user-facing strings. Prevents falling back to `snake_case.replace('_', ' ')` auto-formatting on new additions.

**4. Specific-regression guard.** A particular staleness that bit us once; assert the exact string never sneaks back in.

```python
def test_no_english_only_claim_in_module(self):
    src = path.read_text().lower()
    assert "english only" not in src, (
        "The specific staleness that caused the April 2026 refresh. "
        "If Spanish support is genuinely removed, update this test."
    )
```

**Applies to:** anywhere a specific wrong claim has been identified and fixed. Prevents regression without preventing intentional future changes (the test can be updated if the claim genuinely becomes true again).

### Proving the guards work (strip discipline)

Each guard was verified by reintroducing its target staleness and confirming the test fails with a targeted, actionable message. Example from the `privacy_general` answer guard:

```
AssertionError: privacy_general answer omits PII type(s): ['gender'].
Update bot_knowledge.py TOPICS['privacy_general']['answer'] to
mention: ['gender']
```

A future engineer who adds a 10th PII type to `_PLACEHOLDERS` without updating the privacy_general answer gets this exact message in CI within seconds. They don't need to read the bot_knowledge source to understand what to change — the assertion text tells them.

## Where else should this pattern apply?

Candidate follow-up audits in this codebase (NOT done in this PR, but worth tracking):

### High-value (prose claims about code-owned lists)

- **`CHATBOT_BEHAVIOR.md` — "Crisis" section.** Lists crisis categories in prose; the test should assert every `_CRISIS_CATEGORIES` entry is named.
- **`FEATURES.md` — service category list.** Hand-maintained enumeration of service types; should assert against `SERVICE_KEYWORDS`.
- **`ONBOARDING.md` §6 Key Concepts entries** that describe slot keys, prefix chain components, etc. Probably too prose-y to test directly, but the specific `_SERVICE_NEED_PRIORITY` tiers (1–5) could be asserted.

### Medium-value (configuration claims)

- **`README.md` + `DEPLOY.md`** claims about required environment variables. Already partially covered by the docs linter's env-var check against `render.yaml`.
- **Model ID strings** in prose (e.g. `"Claude Haiku"`, `"Claude Sonnet"`). Already covered by the docs linter's model-ID check against `claude_client.py` constants.

### Low-value (prose-heavy, hard to test)

- **Architecture diagrams + module descriptions** — too subjective to test; best handled by the docs linter's internal-link check to ensure referenced files exist.
- **Rationale paragraphs** (the "why" behind a design decision) — inherently hand-maintained and not tied to live state; no drift risk in the same sense.

### Implementation note for future audits

The freshness-guard pattern lives naturally alongside the existing docs linter (`scripts/check_docs.py`). The split is:

- **Docs linter** handles cross-file drift: test counts in TESTING.md vs. actual test functions, model IDs in README vs. constants, env vars in DEPLOY.md vs. render.yaml, broken internal links, dead file refs.
- **Freshness pytest guards** handle module-internal prose-vs-live-code drift: answer strings vs. their underlying data, friendly-name maps vs. live enum keys, specific-regression guards.

When adding a new freshness guard, ask: "If a future engineer changes the code authority without updating the prose, what should tell them *specifically what to fix*?" The guard's failure message is the answer.

## Deliverables

```
bot-knowledge-refresh/
├── FRESHNESS_PATTERN_DEBRIEF.md      ← you are here
├── bot_knowledge.py                  ← full refactored module
├── test_bot_knowledge_freshness.py   ← new TestBotKnowledgeFreshness class
└── TESTING.md                        ← updated per-file count + freshness row
```

## Merge checklist

- Full suite: 3,853 passed, 0 failed (was 3,846 before this pass).
- Docs linter: no drift detected (33 files, 483 refs, 16 checks).
- Every freshness guard proven: strip its target, test fails with the expected targeted message, restore.
- No service-data firewall bypass: specific hotline provider names deliberately NOT included in the capability context (categories are listed generically; specific numbers live in the crisis-response path where the firewall doesn't apply).
- User-facing accuracy: `answer_question("hablas español?")` now returns a partial-Spanish description; `answer_question("is this safe?")` now lists all 9 redacted PII categories including gender-identity terms.
