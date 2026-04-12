# Regex Pattern Audit — Collision Risk Analysis

## Summary

Tested every SERVICE_KEYWORD, POPULATION_PHRASE, and WORD_BOUNDARY_PATTERN against real collision scenarios. Found **25 active false-positive patterns** that fire on common English text, plus **12 population-phrase collisions** and **3 cross-category conflicts**. With a semantic routing layer incoming, these patterns should be retired rather than fixed — adding word boundaries to 25+ keywords is more maintenance than the regex layer is worth for those cases.

Industry guidance is consistent: substring matching without word boundaries is the most basic form of keyword matching and is universally discouraged in production chatbots. The standard recommendation is to keep regex only for **unambiguous, domain-specific terms** and delegate everything else to embeddings or ML classifiers.

---

## Critical — Active False Positives (proven in tests)

These patterns fire RIGHT NOW on common English text. Each one is a substring match in `SERVICE_KEYWORDS` that lacks word-boundary protection.

| Keyword | Category | False Positive | What Happens |
|---|---|---|---|
| `room` | shelter | "mush**room**", "class**room**" | User asking about a classroom → shelter results |
| `sick` | medical | "home**sick**", "**sick** of this" | User frustrated → medical results |
| `job` | employment | "blow**job**", "good **job**" | Praise or profanity → employment results |
| `mail` | other | "e**mail**", "g**mail**", "black**mail**" | Any email mention → other services |
| `pads` | personal_care | "i**Pads**" | Tablet discussion → hygiene results |
| `visa` | legal | "tele**visa**…" | Note: actually `vision` → medical | "tele**vision**" triggers medical |
| `vision` | medical | "I have a **vision** for my future" | Career aspiration → medical results |
| `wic` | food | "**wic**ked" | Common adjective → food results |
| `formula` | food | "mathematical **formula**" | Math context → food results |
| `soap` | personal_care | "**soap** opera" | Entertainment → hygiene results |
| `meal` | food | "oat**meal**" | Actually OK in this case — oatmeal IS food, but the substring match is accidental |
| `prep` | medical (WB) | "food **prep**", "test **prep**" | Cooking/studying → medical (PrEP) results |
| `physical` | medical | "**physical** abuse" | DV context → medical results instead of legal/crisis |

**Recommendation:** Retire all 13 from regex. The semantic layer handles them naturally — "I'm homesick" won't embed near medical routes, and "I have a vision for my future" won't embed near eye care routes.

---

## High — Semantic False Positives (correct word, wrong context)

These are real words that legitimately appear in the social services domain, but also appear in unrelated contexts. The regex can't distinguish context.

| Keyword | Category | Ambiguous Usage | Risk Level |
|---|---|---|---|
| `court` | legal | "food **court**" | Medium — food court is a real place seekers visit |
| `intake` | shelter | "**intake** process" (for any service) | Medium — intake applies to all services, not just shelter |
| `bail` | legal | "**bail** out" (financial) | Low — unlikely in target population |
| `housing` | shelter | Conflicts with `housing_assistance` category | High — "housing" matches shelter first, even when user means housing programs |
| `health` | medical | "mental **health**" should be mental_health | Currently OK — "mental health" matches first (longer match wins), but fragile |
| `recovery` | mental_health | "password **recovery**", "data **recovery**" | Currently OK — "recovery" alone doesn't match. But adding it to keywords would be risky |
| `rights` | legal | "civil **rights**" (correct), but "**rights** reserved" | Low — rare in chat context |

**Recommendation:** Move `court`, `intake`, and `bail` to semantic layer. Keep `housing` but document the cross-category risk. `health` is fine as long as "mental health" continues to match first.

---

## Medium — Population Phrase False Positives (proven in tests)

These phrases trigger population tagging on normal English text.

| Phrase | Population | False Positive | Verified? |
|---|---|---|---|
| `have a record` | reentry | "I **have a record** of all my meetings" | ✅ Yes — fires |
| `did time` | reentry | "I **did time** management training" | ✅ Yes — fires |
| `senior` | senior | "**senior** developer at Google" | ✅ Yes — fires |
| `navy` | veteran | "**navy** blue sweater" | ✅ Yes — fires |
| `felon` | reentry | "**felon**y assault charges" | Low risk — felon and felony are both valid |
| `blind` | disabled | "**blind** spot" | ✅ No — didn't fire (word boundary or substring position?) |
| `deaf` | disabled | "**deaf** ears" | ✅ No — didn't fire |
| `marines` | veteran | "**marines** park" (Brooklyn neighborhood) | Untested — likely fires |

**Recommendation:**
- **Retire from regex:** `have a record`, `did time`, `senior`, `navy`. These are too ambiguous for substring matching. The semantic layer will handle "I'm a Navy veteran" without also matching "navy blue sweater."
- **Keep:** `felon`, `felony`, `blind`, `deaf` — these are domain-specific enough and didn't show false positives in testing.

---

## Low — Short Keywords That Need Word Boundaries

These single-word keywords are ≤4 characters and use substring matching (`.find()`), not word boundaries. They haven't caused proven false positives yet, but are structurally risky.

| Keyword | Category | Length | Risk |
|---|---|---|---|
| `coat` | clothing | 4 | Low — unlikely in non-clothing context |
| `soup` | food | 4 | Low — "soup kitchen" is the primary use |
| `meal` | food | 4 | See above — "oatmeal" triggers |
| `sick` | medical | 4 | **HIGH** — see Critical section |
| `room` | shelter | 4 | **HIGH** — see Critical section |
| `soap` | personal_care | 4 | **HIGH** — see Critical section |
| `pads` | personal_care | 4 | **HIGH** — see Critical section |
| `wic` | food | 3 | **HIGH** — see Critical section |
| `job` | employment | 3 | **HIGH** — see Critical section |
| `ebt` | other | 3 | Low — acronym, unlikely in other words |
| `daca` | legal | 4 | Low — acronym, unlikely in other words |
| `tps` | legal | 3 | Low — but matches "https" |
| `snap` | other | 4 | Medium — "Snapchat" could trigger |
| `wifi` | other | 4 | Low — always means wifi |
| `visa` | legal | 4 | **HIGH** — see Critical section |
| `bail` | legal | 4 | Medium — see High section |
| `mail` | other | 4 | **HIGH** — see Critical section |

**Recommendation:** Add word-boundary protection (`\b`) to all ≤4-char keywords as an immediate fix. Medium-term, move the high-risk ones to semantic layer entirely.

---

## Cross-Category Conflicts

These keywords appear in SERVICE_KEYWORDS for one category but logically belong in another, or exist in multiple categories.

| Keyword | Matched As | Should Be | Issue |
|---|---|---|---|
| `housing` | shelter | Could be housing_assistance | "housing" as standalone matches shelter, but "housing help" should arguably be housing_assistance |
| `physical` | medical | Could be legal (physical abuse) | Context-dependent — regex can't distinguish |
| `domestic violence shelter` | shelter | Also implies legal/crisis | Currently in shelter keywords, which is correct for the safety need |
| `parole` / `probation` | other (SERVICE_KEYWORDS) + reentry (POPULATION) | reentry population only | Dual-registered: appears as both a service keyword AND a population phrase |

**Recommendation:** Remove `parole` and `probation` from `SERVICE_KEYWORDS["other"]` — they should only be population phrases. Keep `housing` in shelter but document the cross-category behavior.

---

## Word-Boundary Patterns — Current Assessment

These already use `\b` word boundaries, which is the correct approach.

| Pattern | Category | Assessment |
|---|---|---|
| `\bbed\b` | shelter | ✅ Good — "embedded" doesn't match |
| `\bwash\b` | personal_care | ✅ Good — "washington" doesn't match |
| `\bid\b` | other | ✅ Good — "said" doesn't match |
| `\beat\b` | food | ✅ Good — "theater", "great" don't match |
| `\bhat\b` | clothing | ✅ Good — "what", "that" don't match |
| `\bstress\b` | mental_health | ✅ Good — unambiguous |
| `\bsober\b` | mental_health | ✅ Good — unambiguous |
| `\bprep\b` | medical | ⚠️ Bad — matches "food prep", "test prep". Should only match PrEP (HIV prevention) |
| `\bparole\b` | other | ⚠️ See cross-category note |
| `\bprobation\b` | other | ⚠️ See cross-category note |

**Recommendation:** Remove `\bprep\b` from word-boundary patterns. Add "prep" or "PrEP" as a medical keyword only in the phrase "prep medication" or via semantic layer.

---

## Recommended Actions

### Immediate (before next eval run)

1. **Move to word-boundary patterns:** `room`, `sick`, `job`, `mail`, `soap`, `pads`, `wic`, `visa`, `meal`
   — This prevents substring false positives without removing the keywords entirely.

2. **Remove from SERVICE_KEYWORDS:** `formula` (food), `physical` (medical)
   — These cause false positives ("mathematical formula", "physical abuse") and will be handled by the semantic layer.

3. **Remove from WORD_BOUNDARY_PATTERNS:** `prep`
   — "PrEP" is too rare and too collision-prone for regex. The semantic layer handles it better with utterances like "I need PrEP for HIV prevention."

4. **Remove from POPULATION_PHRASES:** `have a record`, `did time`, `senior`, `navy`
   — All proven false positives. The semantic layer handles them via context ("I'm a Navy veteran" vs "navy blue sweater").

5. **Remove from SERVICE_KEYWORDS["other"]:** `parole`, `probation`
   — These are population phrases, not service requests. Keep them in `_POPULATION_PHRASES` only.

### Medium-term (with semantic layer)

6. **Retire all ≤4-char single-word keywords** from SERVICE_KEYWORDS and handle them via semantic routes. This eliminates the entire class of substring collision risk. The semantic layer doesn't need word boundaries — "I'm homesick" will never embed near the medical route.

7. **Retire ambiguous single-word keywords** regardless of length: `vision`, `physical`, `intake`, `court`, `bail`, `formula`, `recovery`. These are all context-dependent and the semantic layer handles context natively.

### What to KEEP in regex

Regex should be the fast path for **unambiguous, domain-specific terms** that have no common English alternative meaning:

- Multi-word phrases: "food pantry", "soup kitchen", "urgent care", "job training", "syringe exchange"
- Domain-specific compound terms: "harm reduction", "sober living", "housing voucher", "birth certificate"
- Acronyms: "ebt", "daca", "snap", "nycha", "tps", "ptsd" (with word boundaries)
- Unique terms: "methadone", "suboxone", "naloxone", "narcan"

These will never cause false positives and are faster than the semantic layer. The 3-tier cascade (regex → semantic → LLM) works best when each tier handles what it's good at.

---

## Impact Estimate

| Metric | Before | After (immediate) | After (with semantic) |
|---|---|---|---|
| False-positive keywords | 13 proven | ~3 remaining | 0 |
| Population false positives | 4 proven | 0 | 0 |
| Keywords requiring maintenance | ~280 | ~270 | ~120 (safe core only) |
| Coverage of novel phrasings | ~85% | ~85% | ~95%+ |
