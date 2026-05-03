# Phrase List Audit Report

**Date:** 2026-04-08 (inventory updated 2026-04-21 for B.1 additions)
**Scope:** All regex phrase lists in `phrase_lists.py`, `classifier.py`, `crisis_detector.py`, and `slot_extraction_regex.py`
**Methodology:** Cross-referenced against C-SSRS (Columbia Suicide Severity Rating Scale), ISEAR emotion model, DAPHNE social needs chatbot research, Woebot/Wysa clinical patterns, NYC homeless population service terminology, and NLP suicide detection literature.

---

## Current Inventory

| List | File | Count | Purpose |
|---|---|---|---|
| Suicide/self-harm | crisis_detector.py | 52 | Crisis detection — direct, indirect, and passive ideation |
| Domestic violence | crisis_detector.py | 50 | Crisis detection — abuse, threats, fleeing |
| Safety concern | crisis_detector.py | 45 | Crisis detection — unsafe situations, runaways |
| Trafficking | crisis_detector.py | 25 | Crisis detection — forced labor/sex work |
| Medical emergency | crisis_detector.py | 18 | Crisis detection — immediate physical danger |
| Violence | crisis_detector.py | 16 | Crisis detection — threats to others |
| Emotional | phrase_lists.py | 135 | Sub-crisis distress routing → AVR handler. Includes "i'm X", "im X", "i am X", "i feel X", and "i am feeling X" forms for 13 emotional states, plus intensifier variants |
| Frustration | phrase_lists.py | 40 | System frustration → 3-tier escalation handler |
| Confused | phrase_lists.py | 25 | Overwhelm → gentle guidance handler |
| Negative preference | classifier.py | 35 | Rejection of offered options → alternative service categories. **Expanded from 19 to 35 in B.1 (2026-04-21)** — added 16 phrases covering "already tried" and "this isn't helpful" variants. See B.1 entry under Audit Trail below |
| Escalation | phrase_lists.py | 13 | Human handoff requests |
| Help | phrase_lists.py | 15 | Capability questions |
| Bot question | phrase_lists.py | 74 | Privacy/capability/attribution meta-questions. **Expanded from 65 to 74 in C.1 (2026-04-21)** — added 9 attribution/origin phrases ("who made you", "who built you", "who created you", etc.) routing to the LLM-answered bot_question handler. See C.1 entry under Audit Trail |
| Bot identity | phrase_lists.py | 23 | "Am I talking to a robot?" + direct identity questions. **Expanded from 15 to 23 in C.1 (2026-04-21)** — added 8 phrases ("what's your name" variants, "who are you", "tell me about yourself", etc.) to catch identity questions that would otherwise fall through to the re-nudge path during pending confirmations. See C.1 entry under Audit Trail |
| Service keywords | slot_extraction_regex.py | 220 | Service type extraction (9 categories) |
| Word-boundary keywords | slot_extraction_regex.py | 6 | Collision-prone service keywords |
| **Total** | | **~792** | |

---

## Audit Trail

### C.1 — bot_identity / bot_question phrase expansion (2026-04-21)

Traced back from two user-reported failure modes:

1. **Surreal non-sequitur during pending confirmation.** Session transcript: user `"I need food in Brooklyn"` → bot confirms → user `"what's your name?"` → bot `"Just to make sure — I'll look for food in Brooklyn. Tap 'Yes, search' to go…"`. Root cause: `"what's your name?"` wasn't in any classifier phrase list, so `_classify_action` returned None, the orchestrator hit `_handle_post_pending_confirmation` Path 3, and the re-nudge fallback fired silently. Adding identity phrases catches the question at Tier 1.
2. **Pre-existing classifier gap on attribution/origin questions.** `"who made you"` / `"who built you"` / `"who created you"` fell through the phrase list and reached the LLM classifier with low confidence. The LLM sometimes returned `bot_question`, sometimes `general`, inconsistently.

**8 phrases added to `_BOT_IDENTITY_PHRASES`:**
- `"what's your name"`, `"whats your name"`, `"what is your name"`
- `"do you have a name"`
- `"who are you"`, `"what are you"`
- `"tell me about yourself"`, `"introduce yourself"`

**9 phrases added to `_BOT_QUESTION_PHRASES`:**
- `"who made you"`, `"who built you"`, `"who created you"`, `"who developed you"`, `"who designed you"`
- `"where do you come from"`, `"where did you come from"`
- `"what company made"`, `"what organization made"`

**Intentional reclassification.** `"who are you"` previously lived in `_HELP_PHRASES` and routed to the help handler (which listed service categories). It's semantically an identity question, not a help request; moved to `_BOT_IDENTITY_PHRASES` where the handler introduces the bot. One pre-existing test (`test_classify_help`) had its input phrases split: identity prompts now assert `bot_identity`, and the bot's capability phrases already asserted `bot_question` separately.

**Rejected as too broad.** The bare phrase `"your name"` was considered and rejected — it's a substring of too many unrelated messages (e.g., `"I'd rather not share your name with the shelter"` which is about privacy, not identity).

**Paired with C.2 (topic-shift disambiguation).** C.1 alone closes the common case but novel phrasings (`"can you speak Spanish?"`, `"do you remember me?"`) still slip through. C.2 catches them at `_handle_post_pending_confirmation` Path 3 with a disambiguation prompt. Defense-in-depth: C.1 at Tier 1 (classifier), C.2 at Tier 3 (fallback). See `CHATBOT_BEHAVIOR.md` § Confirmation Actions for the C.2 heuristic.

### B.1 — Negative preference expansion (2026-04-21)

The `edge_frustration` eval scenario input `"This isn't helpful at all. I already tried those places."` matched no existing phrase, routed as a normal service request, and produced an apology-wrapped functional repeat of the prior confirmation. Root cause: the phrase list was missing two common semantic clusters for rejection-with-context.

**16 phrases added to `_NEGATIVE_PREFERENCE_PHRASES` in `classifier.py`:**

Cluster 1 — "already tried" variants (8):
- "already tried those", "already tried them", "already tried all"
- "already tried those places", "already tried that"
- "i've already tried", "ive already tried"
- "been there already"

Cluster 2 — "this isn't helpful" / "not helping" variants (8):
- "this isn't helpful", "this isnt helpful"
- "this is not helpful", "this is not helping"
- "not helping me", "isn't helping", "isnt helping"

**Intentional reclassification:** `"this isn't helpful"` variants previously routed to `frustrated` tone via `_FRUSTRATION_PHRASES`; they now route to `negative_preference` action. The negative-preference handler opens the refine-search UX (service menu + peer navigator), strictly better for this specific signal than the frustration UI. One pre-existing test (`test_frustrated_first_buttons`) had its input phrase updated accordingly.

**Compound-intent override (B.2).** At the orchestrator dispatch site, when `early_extracted.service_type` differs from existing, the `negative_preference` action is downgraded to the service flow with `tone="frustrated"` so compound messages like `"I already tried those, I need shelter instead"` honor the user's stated pivot rather than showing a menu. See `CHATBOT_BEHAVIOR.md` Negative Preference section for full behavior.

---

## Structural Findings

### 1. Cross-List Collision: "nobody cares"

**Issue:** "nobody cares" appears in both `_EMOTIONAL_PHRASES` and `_SUICIDE_SELF_HARM_PHRASES` (as part of "nobody cares" / "no one cares if i"). The crisis regex fires first, so "nobody cares" always routes to crisis, never to emotional.

**Impact:** The emotional phrase guard in `crisis_detector.py` catches "nobody cares" (it's in `_SUB_CRISIS_EMOTIONAL`), so the LLM stage is skipped. But the regex stage matches first. The phrase "nobody cares" in the suicide list will always win over the emotional list.

**Recommendation:** This is acceptable. "Nobody cares" IS a passive suicidal ideation marker per C-SSRS research. The crisis handler's step-down mechanism will preserve any service intent. However, consider changing the suicide phrase to the more specific "nobody cares if i die" or "nobody would care if i was gone" to avoid over-triggering on the standalone "nobody cares" (which is more commonly an expression of loneliness than suicidal ideation in this population). Keep "no one cares if i" as-is — the "if i" suffix is the suicidal marker.

**Risk:** MEDIUM. False positive rate on "nobody cares" is likely high (emotional, not suicidal), but the crisis response includes useful resources and doesn't clear the session.

### 2. Redundant Phrases (substring within same list)

Found 10 redundant pairs where a shorter phrase is a substring of a longer phrase in the same list. Examples:
- "not doing ok" / "not doing okay" (emotional)
- "in danger" / "i'm in danger" (safety)
- "not helpful" / "still not helpful" (frustration)

**Impact:** The shorter phrase always matches, so the longer phrase is technically redundant. However, keeping both is harmless and aids readability/maintainability.

**Recommendation:** No action needed. The redundancy doesn't affect behavior.

### 3. Contraction Coverage

The existing phrase lists have inconsistent contraction coverage. Some entries include all three forms (e.g., "i'm not okay" / "im not okay" / "i'm not ok"), while others only have the apostrophe form.

**Recommendation:** Audit each list for consistent contraction coverage. The `cleaned` variable preserves apostrophes (`re.sub(r"[^\w\s']", "", lower)`), so both "i'm" and "im" forms are needed.

---

## Gap Analysis by Category

### CRITICAL: Suicide/Self-Harm — 29 gaps

Compared against C-SSRS severity levels and NLP suicide detection research (Arowosegbe & Oyelade, PMC 2023; Ji et al., IEEE 2021).

**C-SSRS Level 1 — Wish to be dead (HIGH PRIORITY):**

| Phrase | C-SSRS Mapping | Risk if Missing |
|---|---|---|
| "wish i was never born" | Wish to be dead | HIGH — common passive ideation |
| "wish i wasn't alive" / "wish i wasnt alive" | Wish to be dead | HIGH |
| "wish i could disappear" / "want to disappear" | Wish to be dead | MEDIUM — could be emotional, not suicidal |
| "don't want to wake up" / "dont want to wake up" | Wish to not wake up | HIGH — C-SSRS screener question |
| "go to sleep and never wake up" | Wish to not wake up | HIGH — C-SSRS screener question |

**C-SSRS Level 2 — Non-specific active thoughts (HIGH PRIORITY):**

| Phrase | Notes | Risk |
|---|---|---|
| "thinking about ending it" | Active ideation without plan | HIGH |
| "thinking about killing myself" | Active ideation | MEDIUM — "kill myself" already covered |
| "thoughts of suicide" | Clinical phrasing | MEDIUM |

**Indirect / Passive ideation (MEDIUM PRIORITY):**

| Phrase | C-SSRS Mapping | Risk |
|---|---|---|
| "i'm a burden" / "im a burden" | Perceived burdensomeness (Joiner IPT model) | HIGH — strong predictor |
| "everyone would be fine without me" | Perceived burdensomeness | HIGH |
| "the world would be better without me" | Similar to existing "better off without me" | LOW — partially covered |
| "life isn't worth it" / "life isnt worth it" | Not worth living | MEDIUM |
| "i can't take it anymore" / "i cant take it anymore" | Threshold exhaustion | HIGH — very common |
| "just want the pain to stop" | Pain termination | HIGH |
| "i don't belong here" / "i dont belong here" | Thwarted belongingness (Joiner IPT) | MEDIUM |
| "make it all stop" / "wish it would all stop" | Pain termination | MEDIUM |

**⚠️ Caution phrases (add with care):**

| Phrase | Concern |
|---|---|
| "i'm done" | Very ambiguous — "I'm done eating" / "I'm done with this search" are not suicidal. Only add the extended form "im done with everything" or rely on LLM for this. |
| "what's the point" | Currently in suicide list as "what's the point anymore". Without "anymore", this is extremely common in non-suicidal contexts. Do NOT add the short form. |
| "want to disappear" | Could be flight from DV, not suicidal. Consider adding to safety_concern instead. |

### HIGH: Domestic Violence / Safety — 21 gaps

**Partner control (commonly missed in text-based DV detection):**

| Phrase | Notes | Priority |
|---|---|---|
| "he controls everything" / "she controls everything" | Coercive control | HIGH |
| "controls my money" / "takes my money" | Financial abuse | HIGH |
| "won't let me leave the house" | Physical control | HIGH |

**Youth/family violence (common in NYC homeless youth population):**

| Phrase | Notes | Priority |
|---|---|---|
| "my parents hurt me" / "my family hurts me" | Youth abuse | HIGH |
| "being hit at home" | Physical abuse | HIGH |
| "no safe place to go" / "nowhere safe" | Acute safety | HIGH |

**Fleeing without DV language:**

| Phrase | Notes | Priority |
|---|---|---|
| "hiding from someone" | Could be DV, stalking, or trafficking | MEDIUM |
| "someone looking for me" / "he's looking for me" | Stalking/pursuit | MEDIUM |
| "had to leave home fast" / "left home suddenly" | Flight indicators | MEDIUM |

### MEDIUM: Emotional — 30 gaps

**Shame/stigma (identified in MULTI_INTENT_PLAN as common in this population):**

| Phrase | Notes | Priority |
|---|---|---|
| "embarrassed to ask" / "ashamed to ask" | Help-seeking stigma | HIGH — this is the #1 barrier |
| "never thought i'd need help" | First-time seeker | HIGH |
| "feel like a failure" | Self-stigma | MEDIUM |
| "ashamed" / "ashamed of myself" | General shame | MEDIUM |
| "i'm pathetic" / "im pathetic" | Self-deprecation | MEDIUM |

**Grief/loss:**

| Phrase | Notes | Priority |
|---|---|---|
| "lost someone" / "someone died" / "my friend died" | Bereavement | HIGH — common trigger for homelessness |
| "grief" / "grieving" / "in mourning" | Bereavement | HIGH |

**⚠️ Note:** "grief" and "grieving" were previously in `mental_health` SERVICE_KEYWORDS. They were retained after our Fix 1 cleanup. If moved to `_EMOTIONAL_PHRASES`, they should be removed from SERVICE_KEYWORDS to avoid the collision we fixed with "struggling."

**Isolation (major factor in homeless population):**

| Phrase | Notes | Priority |
|---|---|---|
| "nobody understands" / "no one understands" | Perceived isolation | MEDIUM |
| "completely alone" / "i have no one" | Acute isolation | MEDIUM |
| "no friends" / "no family" | Social network loss | MEDIUM |

**Despair without suicidality:**

| Phrase | Notes | Priority |
|---|---|---|
| "everything is falling apart" / "my life is falling apart" | General despair | MEDIUM |
| "nothing ever works out" / "things keep getting worse" | Hopelessness | MEDIUM |
| "i can't catch a break" | Frustration-adjacent | LOW |

**⚠️ Boundary concern:** "what's the point" / "whats the point" is in the SUICIDE list (with "anymore" suffix). The bare form WITHOUT "anymore" is more commonly emotional than suicidal. **Recommendation:** Add "what's the point of trying" and "what's the point of anything" to emotional, but keep "what's the point anymore" in suicide. The "anymore" suffix is the key suicidal marker.

### MEDIUM: Frustration — 26 gaps

**Missing contractions (same pattern as the "wasn't helpful" fix):**

| Phrase | Notes |
|---|---|
| "hasn't helped" / "hasnt helped" | Missing contraction variant |
| "isn't working" / "isnt working" | Missing contraction variant |
| "isn't useful" / "isnt useful" | Missing contraction variant |
| "can't help me" / "cant help me" | Missing contraction variant |
| "won't work" / "wont work" | Missing contraction variant |

**Stronger frustration / anger:**

| Phrase | Notes | Priority |
|---|---|---|
| "this is ridiculous" / "this is stupid" | Strong frustration | MEDIUM |
| "you're not listening" / "youre not listening" | Directed at bot | HIGH |
| "you don't understand" / "you dont understand" | Directed at bot | HIGH |
| "same thing every time" | Repetition frustration | MEDIUM |

**Resignation (bordering on abandonment):**

| Phrase | Notes | Priority |
|---|---|---|
| "forget it" / "whatever" | Resignation | MEDIUM |
| "this is pointless" | Resignation | MEDIUM |
| "i give up on this" | Resignation (NOT "i give up" which is in suicide list) | MEDIUM |

**⚠️ Collision risk:** "never mind" is already in `_RESET_PHRASES`. "forget it" could also be interpreted as a reset. **Recommendation:** Add "forget it" to _FRUSTRATION_PHRASES only if the frustration handler is more appropriate than the reset handler. Since frustration offers empathy + escalation while reset just clears, frustration is better for resignation. But ensure the routing priority (reset before frustration) means "nevermind" and "never mind" still route to reset.

### LOW: Confused — 11 gaps

| Phrase | Priority |
|---|---|
| "where do i even go" / "who do i talk to" | MEDIUM |
| "this is confusing" / "too many options" | MEDIUM |
| "everything is too much" / "it's all too much" | MEDIUM |
| "i can't think straight" / "i cant think straight" | MEDIUM |
| "so much going on" | LOW |

### LOW: Service Keywords — 17 NYC-specific gaps

**Food:**
- "baby formula", "wic", "diapers" — common requests from families with infants

**Shelter:**
- "path center" — NYC DHS intake center, well-known to this population
- "dhs intake" — NYC Department of Homeless Services

**Medical (harm reduction):**
- "methadone", "suboxone" — medication-assisted treatment
- "narcan" / "naloxone" — overdose reversal

**Other:**
- "voter registration", "replacement id" — commonly needed documents
- "tax prep" / "free tax" — VITA programs common in this population

---

## Regex Pattern Recommendations

### 1. Add word-boundary matching for "grief" / "grieving"

Currently in `SERVICE_KEYWORDS["mental_health"]`. These are both service requests ("I need grief counseling") and emotional expressions ("I'm grieving"). Recommend: move to word-boundary matching like "stress", or add "grief counseling" as the service phrase and move bare "grief"/"grieving" to emotional.

### 2. Consider stemming for common patterns

Many gaps are contraction variants ("isn't" / "isnt" / "wasn't" / "wasnt"). Instead of enumerating every contraction, consider a preprocessing step that normalizes contractions before matching:

```python
def _normalize_contractions(text):
    """Expand common contractions for consistent matching."""
    replacements = {
        "isn't": "is not", "isnt": "is not",
        "wasn't": "was not", "wasnt": "was not",
        "doesn't": "does not", "doesnt": "does not",
        "didn't": "did not", "didnt": "did not",
        "can't": "can not", "cant": "can not",
        "won't": "will not", "wont": "will not",
        "hasn't": "has not", "hasnt": "has not",
        "i'm": "i am", "im": "i am",
        "i've": "i have", "ive": "i have",
    }
    lower = text.lower()
    for contraction, expansion in replacements.items():
        lower = lower.replace(contraction, expansion)
    return lower
```

This would eliminate ~40% of the gaps by matching "is not helpful" against "not helpful" regardless of the contraction form. **Tradeoff:** Increases complexity and could have unintended side effects. Recommend implementing as a preprocessing step ONLY for frustration and emotional matching, not for crisis detection (where false negatives are more costly than false positives, and the LLM handles ambiguity).

### 3. Spanish language phrases

The population served includes significant Spanish-speaking users. While full multi-language support is deferred, consider adding high-priority crisis phrases in Spanish:

- "me quiero morir" (I want to die)
- "quiero matarme" (I want to kill myself)
- "me siento sola/solo" (I feel alone)
- "necesito ayuda" (I need help)
- "tengo miedo" (I'm scared)

These would only be added to the crisis regex list to ensure safety coverage. Full Spanish support would be handled by the LLM stage.

---

## Priority Implementation Order

| Priority | Category | Phrases to Add | Effort | Impact | Status |
|---|---|---|---|---|---|
| P0 | Suicide — C-SSRS gaps | 28 phrases | Low | Safety-critical | ✅ Done |
| P1 | DV/Safety — control & youth | 17 phrases | Low | Safety for vulnerable subgroups | ✅ Done |
| P1 | Emotional — shame/stigma | 10 phrases | Low | Highest-impact emotional gap | ✅ Done |
| P2 | Frustration — contractions + stronger | 20 phrases | Low | Fix systematic contraction gap | ✅ Done |
| P2 | Emotional — grief/isolation/despair | 18 phrases | Low | Common triggers in population | ✅ Done |
| P3 | Service keywords — NYC-specific | 18 keywords | Low | Coverage for common requests | ✅ Done |
| P3 | Confused — expanded | 9 phrases | Low | Minor coverage improvement | ✅ Done |
| P4 | Contraction normalization | 1 function + 37 mappings | Medium | Eliminates future contraction gaps | ✅ Done |
| P4b | Intensifier stripping | 1 function + 20 intensifiers | Medium | Eliminates intensifier×emotion gaps (109→0) | ✅ Done |
| P5 | Spanish crisis phrases | ~5 phrases | Low | Safety for Spanish speakers | Deferred |
| P6 | Bot question phrase expansion | 13 privacy/info phrases | Medium | Catches "what happens to my information" variants | ✅ Done |
| P7 | Emotional enhancement blocklist | 56 items (15 service + 11 soft-push + 7 steering + 6 adjacent + 10 vague hints + 7 other) | High | Prevents LLM from inserting service-push into emotional responses | ✅ Done |
| **P8** | **Track 1 — Regex Audit Quick Wins (Run 23)** | **~90 phrases across 7 lists** | **Low** | **Addresses 52-100% miss rates found in comprehensive regex audit** | **✅ Done** |

### Track 1 Additions (Run 23 — Regex Audit)

A comprehensive regex audit tested 133 natural-language inputs and found miss rates of 52% (slot extraction), 100% (emotional), 82% (confirm_yes), 58% (frustration), 50% (escalation), and 82% (crisis regex-only). Track 1 added phrases to close the most critical gaps:

**Confirm yes (+11 exact, +7 startswith):** NYC youth slang — bet, aight, ight, word, fasho, fo sho, facts, say less, yea, cool, absolutely, that works, sounds good, lets go, for sure, yea search.

**Frustration (+12):** you're no help, going in circles, keeps asking the same thing, this is bs, whatever, smh, bruh, this ain't working, this aint working, yo this trash, this is trash.

**Emotional (+16):** can't take it anymore, at the end of my rope, crying all day, been crying, i hate my life, have nothing left, what's the point, i feel broken, i'm broken, feel empty inside, giving up, everything is so hard.

**Escalation (+10):** speak with someone, talk to a human, transfer me, can someone call me, person i can call, actual person, get me a person, real human.

**Crisis — suicide (+8):** don't want to be here anymore, nobody would miss me, want to disappear, life isn't worth living, wouldn't matter if i was gone.

**Crisis — DV (+10):** chokes me, choked me, strangled me, strangles me, grabs me by the neck, put his/her hands on me, threatens to kill me.

**Crisis — medical emergency (+9):** took too many pills, can't stop bleeding, bleeding really bad, medical emergency, need an ambulance, call 911.

**Confirm deny — narrowing:** "i don't want" → "i don't want to" / "i don't want that" (prevents shame false positive on "I don't want anyone to know").

**Confirm deny — additions (+6):** i'm good, im good, nah i'm good, nah im good, all good, no need.

**Slot extractor — shelter (+1):** "somewhere to stay" (was missing; "place to stay" and "somewhere to sleep" existed).

**Slot extractor — employment (+5):** finding work, help finding work, help with work, finding a job, help finding a job.

**Total phrases added:** ~90 across phrase_lists.py, classifier.py, crisis_detector.py, and slot_extraction_regex.py. Post-Track 1 detection rates on audit test set: confirm_yes 18%→100%, frustration 42%→100%, emotional 0%→100%, escalation 50%→100%, crisis suicidal ideation 18%→82%, crisis DV 50%→100%, crisis medical 0%→100%.

**Implementation note:** 130+ phrases added across 7 files. Total phrase inventory: 770 → 850+. Three systematic preprocessing functions eliminate future gaps: contraction normalization (37 mappings), intensifier stripping (20 adverbs), and emotional enhancement validation (56-item blocklist). Bot self-knowledge module (`bot_knowledge.py`) provides 15 topic entries with keyword matching for static bot question answers. Zero test regressions (939 passed locally, same 4 pre-existing feature gaps).

---

## References

- **C-SSRS:** Posner, K., et al. (2011). The Columbia-Suicide Severity Rating Scale. *American Journal of Psychiatry*, 168(12), 1266-1277. https://cssrs.columbia.edu
- **Joiner IPT model:** Joiner, T. (2005). *Why People Die by Suicide*. Harvard University Press. (Perceived burdensomeness + thwarted belongingness → suicidal desire)
- **NLP suicide detection:** Arowosegbe, A. & Oyelade, T. (2023). Application of NLP in Detecting and Preventing Suicide Ideation. *IJERPH*, 20(2), 1514. https://pmc.ncbi.nlm.nih.gov/articles/PMC9859480/
- **DAPHNE:** Sezgin, E., et al. (2024). Chatbot for Social Need Screening. *JMIR Human Factors*, 11, e57114.
- **ISEAR:** Scherer, K.R. & Wallbott, H.G. (1994). Evidence for universality and cultural variation of differential emotion response patterning. *Journal of Personality and Social Psychology*, 66(2), 310-328.
- **Woebot RCT:** Fitzpatrick, K.K., et al. (2017). Delivering CBT to Young Adults Using a Fully Automated Conversational Agent. *JMIR Mental Health*, 4(2), e19.
