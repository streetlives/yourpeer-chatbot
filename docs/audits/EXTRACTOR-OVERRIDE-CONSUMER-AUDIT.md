# Extractor-override consumer audit

<!-- drift:ignore-file: historical audit of the override behavior of llm_slot_extractor.py before Phase 4 deleted it -->

**Revision history:**
- 2026-04-24 (rev 2): corrected the Trust Model 3 rule direction in the
  "What 'the override' actually is" section. Earlier drafts described
  the new rule as "sets differ → regex wins, sets match → LLM wins" —
  that was backwards. The actual code (`merge.py:327`) is **sets match →
  regex priority wins, sets differ → LLM wins**. Override A/B
  descriptions rewritten to reflect the correct rule. Audit conclusion
  (no consumer depends on either override's outcome) is unchanged —
  the consumer-level analysis doesn't depend on how the rule is framed.
  Also updated eval-surface section with R36 actual outcomes.
- 2026-04-23 (rev 1): original audit.

---

**Question:** Does any downstream code rely on `extract_slots_smart`'s
destructive override behavior being "correct" — specifically, the
legacy pattern where LLM wins the primary `service_type` when regex
finds multiple services ("ambiguous regex" case)?

**Answer: No.** No production consumer has embedded assumptions about
which extractor produced the current slots, or how the primary was
selected between regex and LLM. Downstream handlers treat extractor
output as opaque: primary is primary, additionals get queued.

**Scope of audit:** All production (non-test) consumers of
`additional_services`, `_is_additive`, `_contradiction`, and
`_queued_services` in the backend, read end-to-end.

---

## What "the override" actually is

Two behaviors in legacy `extract_slots_smart` could be called overrides.
The migration changes both in subtle ways. Naming them here to be
unambiguous, and stating the new rule accurately:

**The new merge rule (Trust Model 3)**, from `slot_extraction/merge.py:327`:

```
R = {regex_primary} ∪ regex_additional_service_types
L = {llm_primary}   ∪ llm_additional_service_types

if R is empty:              → LLM wins
elif L is empty:            → regex wins
elif R == L:                → regex priority wins
else (R != L):              → LLM wins
```

**Override A — "Regex unambiguous → regex wins primary"**
`llm_slot_extractor.py:730-741`. When regex finds exactly one service_type
(no additionals) and LLM disagrees, legacy had regex win primary.

Under the new rule: R = {X} (one service), L = {Y} → sets differ →
**LLM wins** on the new path. In practice, LLM usually agrees with regex
on unambiguous single-service messages ("I need food" → both pick food),
so the outcomes coincide most of the time. But when the LLM does
disagree on a single-service regex match, behavior is now **different**
from legacy — the LLM's correction wins. This is the migration's
intended direction (prior consumer audit framing that called this
"preserved" was incorrect — the outcomes align only by common-case
agreement, not by rule).

**Override B — "Regex ambiguous → LLM wins primary"**
`llm_slot_extractor.py:742-752`. When regex finds primary + additionals
and LLM disagrees on primary, legacy deferred to LLM.

Under the new rule:

- **Sets differ** — R = {X, Y}, L = {Z}: sets unequal → LLM wins.
  Same outcome as legacy Override B. **Preserved** in this case.
- **Sets match but primaries differ** — R = {X, Y} with X primary,
  L = {X, Y} with Y primary: sets equal → regex priority wins (X).
  Legacy Override B would have let LLM pick Y. **Changed** in this
  case. This is the primary behavior the migration targets — the
  motivation for the whole migration (`multi_cross_borough`
  recovery from 2.82 → 4.73 on unified) depends on regex's correct
  primary pick when sets agree.

Legacy Override B could also silently drop a regex-found service when
the LLM's set didn't include it (final `additional_services` came from
LLM alone). Under new extractor, `_merge_additional_services` unions
both sides, so regex-found services are preserved even when LLM wins
primary. Bug reduction, not regression; worth naming since queue
behavior may visibly differ.

---

## Consumers checked

Ran `grep -r "additional_services" backend/ --include="*.py" | grep -v test`
plus adjacent flag checks (`_is_additive`, `_contradiction`,
`_queued_services`). 97 references across 12 files. Extractor-internal
files are out of scope. The 7 consumer files:

| File | Lines | What it does |
|---|---|---|
| `pipeline.py` | 162-163 | Pass-through of unified.additional_services |
| `orchestrator.py` | 404-405 | Exclude from "has_new_slots" check |
| `orchestrator.py` | 418-427 | Queue additionals; clear queue on "change of mind" |
| `handlers/confirmation.py` | 630-634 | Exclude from "pending_has_new" check |
| `handlers/confirmation.py` | 648-651 | Branch on `_is_additive` flag |
| `handlers/emotional.py` | 270-272 | Queue additionals during crisis step-down |
| `handlers/accessibility.py` | 223-278 | Walk additionals for asylum/immigration detail |
| `handlers/post_results.py` | 382-388 | Guard: `_queued_services` empty = no pending queue |

---

## Patterns that WOULD be a dependency (none found)

The specific anti-patterns that would tie a handler to legacy's
Override B outcome:

1. `if additional_services and service_type == X: <branch>` —
   treating primary as authoritative BECAUSE additionals are present.
   **Not found.**

2. `if len(additional_services) > 0: defer to <other logic>` —
   using the presence of additionals as a trust signal.
   **Not found.**

3. Reasoning about "did LLM or regex pick this primary?" —
   any post-hoc check on extractor provenance.
   **Not found.**

4. Hardcoded primary expectations in handler flow — e.g., "for shelter
   queries, do X; for medical queries, do Y" where the mapping assumes
   legacy's Override B is picking that primary.
   **Not found.**

All three `if additional_services:` / `if additional and ...:` hits
(pipeline:162, orchestrator:420, emotional:271) are identical:
"if non-empty, populate `_queued_services`." No branch depends on the
primary's identity.

No handler anywhere compares `service_type` against `_queued_services`
to reason about extractor choice. Confirmed by
`grep "service_type.*_queued|_queued.*service_type"` returning zero
hits in production code.

---

## Adjacent flags: `_is_additive`, `_contradiction`

Both flags are regex-derived in both extractors. In the new extractor,
they come through the `_merge_regex_only` path in `merge.py` —
preserved verbatim from regex output without LLM override. Consumer
code branching on these flags (confirmation.py:648, orchestrator.py:419)
behaves identically under either extractor. **No behavior change.**

---

## The one adjacent thing worth naming

`orchestrator.py:422-427` clears `_queued_services` when:
primary changed AND no new additionals AND not additive.

Under legacy Override B, the LLM might have dropped a regex-found
service, producing an output with fewer additionals. Under the new
extractor, those services are preserved. So this clearing condition
may fire LESS often under new.

That's a bug-reduction, not a regression: legacy was occasionally
wiping queues the user still wanted. No consumer relies on the queue
being wiped at a specific frequency. Worth mentioning so the Phase 2
eval comparison isn't surprised by queue-clearing rate shifts.

---

## Eval-surface vs. code-surface

The real behavioral difference from the rule change is **which
service becomes primary in multi-intent turns** when regex and LLM
disagree. That shows up as:

- Different wording in the "I'll look for X — sound good?" confirmation
- Different primary in search results
- Different service queued for follow-up

This is an **eval concern**, not a code-dependency concern:
- Tracked by the Phase 2 watch-list in `UNIFIED_EXTRACTOR_MIGRATION.md`
  (4 scenarios: `multi_food_and_shelter_brooklyn`,
  `multi_shower_and_food_drop_in`, `multi_clothing_and_food_harlem`,
  `multi_cross_neighborhood_shower_les_food_chinatown`)
- R36 parallel eval (2026-04-24) confirmed 3 of the 4 watch-list
  scenarios regressed by −0.45 each on the unified path
- Option 4 hardening at `/mnt/user-data/outputs/phase-2-option-4-hardening/`
  was applied post-R36; mini-eval shows 6/6 recovery at the LLM layer
- Option 2b (narrative-path exception) at
  `/mnt/user-data/outputs/phase-2-option-2b-narrative-exception/`
  was applied for the Category B scenario (`natural_long_story`)

No code changes gate on any of this. Handlers will happily serve
either primary.

---

## Conclusion

**Clear to proceed with Phase 1 / Phase 2 migration without pre-work
on consumer code.** (Original audit conclusion, confirmed post-R36.)

The audit checked every production consumer of `additional_services`
and adjacent flags. None has embedded assumptions that would break
under the new extractor's set-equality rule. The behavioral difference
from the rule change manifests in eval outcomes (which service is
picked as primary in specific ambiguous cases), not in code paths
that need to be rewritten.

The Phase 2 parallel-eval comparison is the right instrument to catch
any scenario-level regression. The Option 4 hardening and Option 2b
narrative-path exception were the right mitigations for the watch-list
regressions and the Category B scenario surfaced by R36.

---

## Method / reproduction

```
# Consumer file enumeration
grep -rln "additional_services" backend/ --include="*.py" \
  | grep -v /tests/ | grep -v _test.py

# Anti-pattern sweep
grep -rnE "if.*additional_services.*:|if.*len.*additional_services|if.*additional\s*and" \
  backend/ --include="*.py" \
  | grep -v /tests/ | grep -v _test.py \
  | grep -vE "slot_extract|/slot_extraction/|llm_classifier"

# Cross-reference with queue state
grep -rnE "service_type.*_queued|_queued.*service_type" \
  backend/ --include="*.py" \
  | grep -v /tests/ | grep -v _test.py
```

Reading time: ~25 minutes. Files read in full:
`orchestrator.py` 395-490, `handlers/confirmation.py` 410-660,
`handlers/emotional.py` 255-310, `handlers/accessibility.py` 210-290,
`handlers/post_results.py` 375-410, `llm_slot_extractor.py` 720-810.
