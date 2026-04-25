# Unified LLM Extractor Migration

**Status:** Phase 3 SHIPPED — flag default flipped to ON. Phase 4 (legacy deletion) queued.
**Owner:** Raleigh
**Created:** 2026-04-22
**Approved:** 2026-04-23 — schema decisions (`org_name` keep + fuzzy validator; `service_detail` Option A extend + canonical-form validator; `additional_services` extended to `[{type, detail?, location?}]`); priority-hierarchy consolidation (food ≥ mental_health); 2-hour / ~$50 parallel-run eval budget.
**Revision:** 2026-04-24 (rev 16) — Phase 3 SHIPPED. `_USE_UNIFIED_EXTRACTOR` default flipped from False to True in `backend/app/services/chatbot/context.py`. The env var is now an opt-OUT: setting `USE_UNIFIED_EXTRACTOR=0` (or `false`/`no`/`off`, case-insensitive) routes back to the legacy `extract_slots_smart`. Unrecognized values default to ON so typos don't silently revert traffic. **`pipeline.py:149`'s `classify_unified` gap-filler is unchanged** — that call site returns `tone`/`action` keys that the unified extractor doesn't produce, so migrating it before Phase 4 would lose tone/action gap-fill signal. Phase 4 will delete `classify_unified` entirely; the gap-filler's continued utility (or removal) gets re-evaluated then. The 23 routing tests in `test_unified_extractor_flag.py` were updated for the new opt-out semantics; one `test_flag_is_false_when_env_unset` became `test_flag_is_true_when_env_unset`, and the truthy/falsy parametrize lists were swapped — empty string and unrecognized values now assert True. Repo-wide test suite under default env: 3,553 passing, 0 failures, 10 skipped, 3 xfailed. Verified opt-out works: `USE_UNIFIED_EXTRACTOR=0` runs legacy and the suite stays green.
**Prior revisions:** rev 15 (2026-04-24) — Phase 2 VALIDATION COMPLETE. Cross-borough carve-out and primary-location decoupling fixes shipped after rev 14's flag-on path was first exercised end-to-end. Mini-eval results: 12/15 passing (4.23 avg); migration headline scenario `multi_cross_borough_food_brooklyn_shelter_manhattan` recovered 3.36 → 4.00. Confirmation-handler orphan-file wiring corrected; orchestrator awaiting-clear guard added. Test count corrected from rev-14's claimed 4,097 to actual 3,529 passing. R37 full eval (171 scenarios, flag on, post-rev-15): 167/171 passing (97.7%), 4.59 overall, 19 critical failures — beats R36 Legacy on every headline metric. rev 14 (2026-04-24) — Phase 2 wiring shipped. rev 13 (2026-04-23) — Phase 1 COMPLETE: `backend/app/services/slot_extraction/` package shipped (1,580 LOC across 4 files) with 147 unit tests achieving 100% line + branch coverage. rev 1–12 — design iteration, audits, schema decisions, Phase 0 corpus check.
**Related:** R35 eval regression (`multi_cross_borough` 3.09 → 2.82); PR #61.

## Problem

The chatbot has two LLM-based slot extractors that overlap but are not identical:

- `classify_unified` (`llm_classifier.py`, ~250 LOC) — narrow gap-filler, single Haiku call, fires only when regex found no service intent.
- `extract_slots_smart` (`llm_slot_extractor.py`, ~800 LOC) — full pipeline with four dispatch paths, runs unconditionally on service-category messages.

`llm_classifier.py`'s header says its intent is to **replace** `extract_slots_smart`, but the migration was never completed. The two coexist, and `extract_slots_smart` can silently override correct regex extractions — as happened in R35, where its override stripped Sprint 1's cross-location binding from `multi_cross_borough`.

Every downstream fix that depends on regex output (Sprint 1's per-service-location binding, Sprint 2's `_gender` suffix, Sprint 3's foster-youth populations) is at risk of being silently erased whenever the message is complex enough to trigger LLM extraction.

## Goal

Migrate all LLM-based slot extraction through a single, unified entry point. Delete `llm_slot_extractor.py`. Preserve narrative mode, semantic-router integration, and merge logic; eliminate the destructive override pattern.

Success criteria:

1. Single entry point for LLM extraction.
2. No caller can receive an extraction that silently discards correct regex output.
3. R35's failing scenarios pass post-migration.
4. R35's passing scenarios don't regress — measured via parallel-run, not assumed.
5. `llm_slot_extractor.py` is deleted; the 8 test files referencing it are consolidated or ported.

## Where the code lives now

The new extractor is in `backend/app/services/slot_extraction/`:

- `__init__.py` — public `extract()` function and dispatch logic.
- `prompts.py` — `_SHORT_SYSTEM_PROMPT`, `_NARRATIVE_SYSTEM_PROMPT`, extended `_EXTRACT_SLOTS_TOOL` schema.
- `merge.py` — per-field trust-model merge functions and the top-level `merge()`.
- `dispatch.py` — `_is_narrative`, `_is_simple_message`, both LLM call paths, narrative regex-fallback, audit-log helpers.

Phase 1's tests live in `tests/unit/test_slot_extraction.py` (147 → now 158 tests after rev-15 carve-out + primary-location additions; 100% line and branch coverage on the four module files).

The flag `USE_UNIFIED_EXTRACTOR` is defined in `backend/app/services/chatbot/context.py`. Two call sites branch on it:

- `chatbot/orchestrator.py` (service-branch) — routes to `slot_extraction.extract()` when on.
- `chatbot/handlers/confirmation.py` (post-pending-confirmation re-extraction) — same routing, runs regex inline first since `early_extracted` isn't in scope at this call site.

A third call site, `chatbot/pipeline.py:149`'s `classify_unified` gap-filler, is accepted as-is — it has never caused observed regressions and migrates as part of Phase 4 deletion.

## Merge rules

The 13 fields in the merged result cluster into five distinct trust models. Future maintainers adding a new field should identify which model applies, not invent a new one.

### Trust model 1 — Regex has literal-match authority

Applies to: `location`, `_gender`, `service_detail` (validator-gated), `org_name` (validator-gated).

Rule: if regex returned a non-None value, use it. Else use LLM's value (validator-gated for `service_detail` and `org_name`; raw for `location` and `_gender`).

Rationale: regex matches literal text from curated tables. LLM paraphrases can drift.

Validator-gated cases:

- `service_detail` — extended into the unified tool-call schema; LLM output snapped to one of 98 `_NOTABLE_SUB_TYPES` values or dropped.
- `org_name` — LLM `org_name` accepted only if it fuzzy-matches (token-sort-ratio ≥ 85) a value in the regex's authoritative org table.

Known inherited edge cases (out-of-scope):
- `_gender` over-extraction from facility names ("women's shelter" → female).
- `location` coarseness ("near the Bronx Zoo" → "Bronx" not "East Tremont").
- `org_name` misses misspellings.

### Trust model 2 — LLM has semantic-context authority

Applies to: `age`, `urgency`, `family_status`.

Rule: if LLM returned a non-None value, use it. Else use regex's value.

Rationale: these fields are mostly expressed implicitly ("tonight" → urgency=high) or with attribution ("my son is 12" is NOT the user's age) — the LLM's context-awareness dominates regex's keyword matching.

### Trust model 3 — Set-agreement decides ordering

Applies to: `service_type` and the primary's `location`.

Rule:

```
Let R = {regex_primary} ∪ regex_additional_service_types
Let L = {LLM_primary}   ∪ LLM_additional_service_types

if R is empty:                → LLM wins
elif L is empty:              → regex wins
elif R == L:                  → LLM wins (Ext-2b)
                                EXCEPT cross-borough carve-out (see below)
else:                         → LLM wins
```

Worked examples:

| Input | R (regex) | L (LLM) | R == L? | Winner | Result |
|---|---|---|---|---|---|
| "food and shelter in Brooklyn" | {food, shelter} | {food, shelter} | yes | LLM (Ext-2b) | LLM's primary pick |
| "shower in LES, food in Chinatown" | {personal_care, food} | {personal_care, food} | yes | LLM (Ext-2b) | personal_care (first-mentioned) |
| "food in Brooklyn, shelter in Manhattan" (LLM collapses) | {food, shelter} | {food, shelter} | yes | regex (carve-out) | shelter@manhattan + food@brooklyn |
| "just got out of hospital, need somewhere safe" | {medical, shelter} | {shelter} | no | LLM | shelter |
| "I ran out of insulin" | {} | {medical} | R empty | LLM | medical |

**Ext-2b** (rev 14): when sets match, LLM's primary pick wins regardless of message length. Both LLM paths teach primary selection (narrative: full urgency hierarchy; short: first-mentioned default with safety-signal override). Regex's static priority table can't distinguish request from context or first-mentioned from priority-ordered; the prompts can.

**Cross-borough carve-out** (rev 15): when sets match AND regex detected distinct per-service locations AND the LLM did NOT preserve that cross-borough structure (its additional_services are all at the same location as its primary), regex wins on `(primary, location, additional_services)`. Detection is in `_merge_service_type_and_primary_location`; covered by `TestTrustModel3CrossBoroughCarveOut`.

**Primary-location binding** (rev 15): the primary location returned from `_merge_service_type_and_primary_location` is the source of truth for `merge()`'s final `location` field. When LLM wins primary, its location is validator-gated (catches hallucinations like "Chicago"); when regex wins, its location is used directly (already canonical). `_merge_location` is retained as the no-location fallback for the `accessibility_low_literacy` case (regex won primary but had no parseable location). Covered by `TestPrimaryLocationBinding`.

### Trust model 4 — Union with explicit FP tolerance

Applies to: `_populations`.

Rule: `union(regex_populations, semantic_router_population, LLM_populations)`.

Rationale: each source has coverage gaps. Regex catches explicit keywords ("I'm a veteran"); semantic router catches phrase embeddings regex misses ("aging out of foster care"); LLM catches implicit membership ("just got out of Rikers" → reentry).

Accepted false positive: "my brother's in jail" — regex extracts `reentry` (third-person attribution it can't detect). Causes a wrong tone prefix ("reentry-friendly ..."), not a routing failure.

### Trust model 5 — Regex-only for phrase-pattern signals

Applies to: `no_requirements`, `_contradiction`, `_is_additive`.

Rule: regex value only; LLM doesn't contribute. The unified tool schema does not declare these fields.

Rationale: tuned phrase tables. LLM could over-trigger on mild phrases. Out of scope for this migration.

### `additional_services` — hybrid

Doesn't fit cleanly into the five patterns. Rule:

For each unique service type in `winner_additional ∪ regex_additional ∪ llm_additional` (excluding the primary):
- `type`: regex wins.
- `detail`: regex's value if non-None, else LLM's.
- `location`: regex's value if non-None, else LLM's.

Implemented in `_merge_additional_services` with five behaviors documented in the source: no input mutation; schema-shape normalization at call boundary; debug-log on primary-exclusion; always-set output key (empty list when no additionals); per-field merge on duplicates.

### Per-field reference table

| Field | Trust model | Rule (one-line) |
|---|---|---|
| `location` | 1 | regex wins when present; primary-winner's location used at top level (rev 15) |
| `_gender` | 1 | regex wins when present |
| `service_detail` | 1 | regex wins; LLM fallback validator-gated to canonical 98 values |
| `org_name` | 1 | regex wins; LLM fallback validator-gated by fuzzy match |
| `age` | 2 | LLM wins when present |
| `urgency` | 2 | LLM wins when present |
| `family_status` | 2 | LLM wins when present |
| `service_type` | 3 | set-agreement (Ext-2b) + cross-borough carve-out + narrative exception |
| `_populations` | 4 | union of all three sources |
| `no_requirements` | 5 | regex only |
| `_contradiction` | 5 | regex only |
| `_is_additive` | 5 | regex only |
| `additional_services` | hybrid | regex-tuple-preserving dedup-union (see above) |

## Phase 2 Validation Findings (rev 15)

When the mini-eval was run with `USE_UNIFIED_EXTRACTOR=1` for the first time after rev 14, three issues surfaced. All three were "code shipped, but the path that exercises it never ran" problems — gaps that previous validation passes hadn't surfaced.

### Why these weren't caught earlier

The mini-eval script (`scripts/mini_eval_r36_regressions.py`) was being run without `USE_UNIFIED_EXTRACTOR=1` set in the environment. The script silently fell back to the legacy `extract_slots_smart` path. Every "Ext-2b" delta we measured during Phase 2 was actually measuring legacy-path behavior. The flag-on path was effectively un-validated for ~a month between rev 14 and rev 15.

The unit suite was green because the cross-borough integration tests in `test_multi_intent_queue.py::TestCrossBoroughExtraction` exercise `extract_slots()` (regex only), not the full `extract() → merge()` pipeline. Regex alone produces correct output for `multi_cross_borough_food_brooklyn_shelter_manhattan` — it's the LLM-plus-merge path that was broken. No unit test composed regex output with a realistic LLM response shape and disagreeing primaries, so the bugs were unreachable from the unit suite.

### Bug 1: cross-borough LLM-collapse

Ext-2b's "LLM wins primary on sets-match" rule was added on the assumption that the LLM's primary-selection logic is more accurate than regex's static URGENCY_HIERARCHY. For most sets-match cases this holds and Ext-2b helps. For one specific input shape it fails: when the user phrases their request with distinct per-service locations and the LLM's tool-call output collapses both services to one location, the LLM has discarded location information that regex correctly captured. Ext-2b would still hand the primary win to the LLM, dropping the per-service binding entirely.

Fix: a narrow carve-out that fires when regex's `additional_services` has at least one entry whose location differs from regex's primary location (regex detected cross-borough) AND the LLM's output doesn't exhibit the same structure (its additional_services are all at the same location as its primary). The carve-out fires for `multi_cross_borough_*` and does not fire for `multi_cross_neighborhood_*` (where the LLM correctly preserves per-service bindings).

### Bug 2: primary-location decoupling

`merge()` was calling `_merge_location(regex.location, llm.location)` in a separate step from the primary-service merge. Correct when both extractors agree on primary; broken when they disagree.

For `multi_cross_borough_food_brooklyn_shelter_manhattan` post-Bug-1 fix, the LLM correctly extracts `(food, "Brooklyn", [shelter+Manhattan])`. Ext-2b accepts food as primary. But then `_merge_location` runs on the raw locations: regex has `"manhattan"` (regex's primary-for-shelter location, Trust Model 1's "regex wins" applies), so the final location becomes `"manhattan"` — bound to LLM's accepted food primary. Food now at Manhattan when the user said Brooklyn.

Fix: use the primary-winner's location as the source of truth. When LLM wins primary, its location goes through `_validate_location` (drops hallucinated values like "Chicago"); when regex wins, its location is used directly (already canonical from `_KNOWN_LOCATIONS`). The fallback to `_merge_location` is preserved for the `accessibility_low_literacy` case where the primary-winner has no location.

### Bug 3: rev-14 wiring incomplete

The rev-14 deliverable described `handlers/confirmation.py` as having `_USE_UNIFIED_EXTRACTOR` branching in the post-pending-confirmation path. The branching was actually staged in `chatbot/confirmation.py` — an orphan with zero importers — never integrated into the active handler. Three test failures in `TestConfirmationHandlerFlagRouting` were tracking this gap; they sat as "3 xfailed" in test summaries and went un-investigated.

A related gap: the `_awaiting_service_after_clear` flag (the `confirm_multi_change` fix) was set in `handlers/confirmation.py`'s change-service branches but the orchestrator's read-side guard had never been merged into the active orchestrator. The flag was set every turn, never read, never popped — `confirm_multi_change` was failing at 3.64 because of this.

Both closed in rev 15: orphan content merged into active handler, orphan deleted, orchestrator guard added. Post-fix `confirm_multi_change` scores 4.73.

### What to add to the test suite to prevent the next instance

Three patterns would have caught these earlier:

- **Per-slot primary-disagreement coverage.** For every slot-merge function, test the case where regex wins primary and LLM wins primary respectively, and assert the slot value tracks whichever side won. The new `TestPrimaryLocationBinding` covers location; analogous coverage should be added for `service_detail`, `_gender`, and any slot whose value is bound to a specific extractor's primary.
- **Full-pipeline integration tests with mocked LLM.** The cross-borough tests in `test_multi_intent_queue.py` exercise regex only. Each should have a parallel test that runs through `extract() → merge()` with a mocked LLM response shape; the mock's primary should sometimes agree with regex and sometimes disagree.
- **Flag-state equivalence tests.** A small suite that runs a curated set of scenarios under both `USE_UNIFIED_EXTRACTOR=0` and `USE_UNIFIED_EXTRACTOR=1` and asserts equivalent behavior. Catches divergence between the two paths and forces drift to surface in CI rather than after a flag-flip.

## Behaviors preserved from `extract_slots_smart`

The original audit catalogued 25 behaviors of `extract_slots_smart` that the migration must preserve (semantic router as a `_populations` source, role-alternation padding for conversation history, asymmetric narrative-vs-short LLM-failure handling, `additional_services` shape transformation, urgency clue list, etc.). All 25 are now covered by unit tests in `tests/unit/test_slot_extraction.py` and verified at 100% line and branch coverage. The original behavior writeups have been removed from this doc; if you need the historical detail, see git history for rev 14 of this file.

**One behavior with ongoing relevance** — `test_service_data_llm_firewall.py` (704-line integration test) defines the LLM-firewall contract that the migration must preserve across Phase 4 deletion:

- `test_slot_extractor_uses_tool_use_not_text` — both short and narrative paths in the new module must use tool_use API. Plain-JSON or free-text responses are forbidden.
- `test_slot_extractor_system_prompt_has_no_service_data` — both `_SHORT_SYSTEM_PROMPT` and `_NARRATIVE_SYSTEM_PROMPT` must pass `"212-" not in prompt` and `"https://yourpeer" not in prompt`.
- 6 tests directly import from `app.services.llm_slot_extractor`. Phase 4 must remap these to the new module.
- Phase 4 should add a new assertion: no system prompt in the new module contains any `_NOTABLE_SUB_TYPES` canonical value (closes a leak vector introduced by the `service_detail` schema extension).

## Implementation phases

### Phase 0 — Design + corpus check (COMPLETE, 2026-04-23)

Schema decisions, priority-hierarchy consolidation, parallel-run budget all approved. Corpus check identified 4 short-path blind-spot scenarios; team approved Option 1 (accept the mispick), with Option 4 (port urgency hierarchy to short prompt) as fallback if Phase 2 surfaced regressions. Full analysis at `/mnt/user-data/outputs/phase-0-corpus-check/analysis.md`.

### Phase 1 — New unified extractor (COMPLETE, 2026-04-23)

`backend/app/services/slot_extraction/` shipped (1,580 LOC across 4 files) with 147 unit tests achieving 100% line + branch coverage. Repo-wide: 4,066 passing, 0 failures. Deliverable at `/mnt/user-data/outputs/phase-1-slot-extraction/`. No callers edited in this phase.

### Phase 2 — Feature flag + parallel-run validation (COMPLETE, 2026-04-24)

Wiring shipped in rev 14 (with the integration gap documented under "Bug 3" above, closed in rev 15). 23 routing tests in `test_unified_extractor_flag.py` cover flag env-var parsing, both-call-site routing on/off, and the no-API-key bypass.

R36 parallel-run eval (167 scenarios, both flag states):

- Legacy path: 167/171 passing (97.7%), 22 critical failures, overall 4.56.
- Unified path: 159/171 passing (93.0%), 25 critical failures, overall 4.55.
- Migration's primary target (`multi_cross_borough_food_brooklyn_shelter_manhattan`) recovered from 2.82 to 4.73 on the unified path.
- 8 scenarios flipped from passing to failing on unified; categorized in `eval-r36/r36-analysis.md`.

Rev-15 fixes (cross-borough carve-out + primary-location binding) addressed the major regressions; mini-eval rerun showed 12/15 passing and zero new regressions vs. the rev-14 unified baseline.

### Phase 3 — Flip the flag default (COMPLETE, 2026-04-24)

`_USE_UNIFIED_EXTRACTOR` default flipped from `False` to `True` in `backend/app/services/chatbot/context.py`. The env var is now an opt-OUT — `USE_UNIFIED_EXTRACTOR=0` (or `false`/`no`/`off`, case-insensitive) reverts to legacy. Unrecognized values default to ON so typos don't silently revert traffic. The 23 routing tests in `test_unified_extractor_flag.py` were updated for the new semantics: empty string and unrecognized values now assert True; only the explicit falsy set asserts False.

**`pipeline.py:149` (`classify_unified` gap-filler) deliberately unchanged.** The original Phase 3 plan called for migrating this call site to `slot_extraction.extract()`, but the gap-filler reads `tone` and `action` keys from `classify_unified`'s output that the unified extractor doesn't produce — `slot_extraction.extract()` returns slots only. Migrating before Phase 4 would lose the tone/action gap-fill signal. Phase 4 deletes `classify_unified` entirely, at which point this site needs to either be removed (if regex/early-extraction catches enough on its own) or have tone/action support added to the unified extractor.

Repo-wide test suite under default env (post-flip): 3,553 passing, 0 failures, 10 skipped, 3 xfailed. Verified the opt-out path: `USE_UNIFIED_EXTRACTOR=0` runs the legacy path and the suite stays green.

### Phase 4 — Delete old code (~1 day)

- Delete `backend/app/services/llm_slot_extractor.py`.
- Delete `backend/app/services/llm_classifier.py`.
- **Resolve `pipeline.py:149`'s `classify_unified` gap-filler**: either delete the entire `_run_llm_gate` function if regex + semantic-router coverage is sufficient on its own, or extend `slot_extraction.extract()` to produce `tone` and `action` keys and migrate the call site to use it.
- Delete the feature-flag branches in `orchestrator.py` and `handlers/confirmation.py`.
- Update `chatbot/context.py` re-exports — drop the `extract_slots_smart` and `classify_unified` conditional imports.
- Remove the `_USE_UNIFIED_EXTRACTOR` flag itself (no opt-out path needed once legacy is gone).
- Update `tests/integration/test_service_data_llm_firewall.py` — remap the 6 direct imports from `llm_slot_extractor` to the new module; preserve Layer 4 and Layer 6 asserts; add the new `_NOTABLE_SUB_TYPES`-no-prompt-leak assertion.
- Update or delete `tests/unit/test_unified_extractor_flag.py` — the routing tests are no longer meaningful once there's only one path. Either delete the file or repurpose it for tests of the unified path's specific behaviors.
- Consolidate tests:
  - `test_llm_slot_extractor.py` → split into `test_slot_extraction_dispatch.py`, `test_slot_extraction_merge.py`, `test_slot_extraction_prompts.py`.
  - `test_narrative_extraction.py` → fold into the above.
  - `test_llm_multi_service.py` → fold into merge tests.
  - `test_llm_classifier.py` → delete or fold into prompts tests.
- Update import paths across the repo.

Exit criterion: `llm_slot_extractor.py` and `llm_classifier.py` don't exist; the `_USE_UNIFIED_EXTRACTOR` flag is gone; all tests green including the 38 in `test_service_data_llm_firewall.py`; one full eval run passes.

### Phase 5 — Cleanup + retrospective (~0.5 days)

- Update this doc's status to "Implemented".
- Note any deviations from the plan.
- Short retrospective: what the parallel-run showed, surprises, recommendations for future similar refactors. The rev-15 Phase 2 Validation Findings section is the seed for this.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Flag-flip surfaces a code path not exercised by R37 in production traffic | Low | Medium | R37 covered 171 scenarios across 20 categories with 0 errors; opt-out remains available via `USE_UNIFIED_EXTRACTOR=0` for emergency rollback. Monitor production for unexpected slot-extraction errors in the first week. |
| Phase 4 deletion of `classify_unified` removes the gap-fill `tone`/`action` signal that pipeline.py:149 currently uses | Medium | Medium | Phase 4 must explicitly decide: delete `_run_llm_gate` if regex + early-extraction is sufficient, OR add `tone`/`action` to `slot_extraction.extract()` output and migrate the call site. Don't delete `classify_unified` without resolving this. |
| Phase 4 test consolidation accidentally drops one of the 38 firewall asserts | Medium | High | Phase 4 PR should diff `test_service_data_llm_firewall.py` line by line against the pre-deletion version; require a passing run before merge |
| Future migrations repeat the rev-14 wiring gap | Medium | High | Adopt flag-state equivalence tests (see "What to add to the test suite" above) — should land before the next feature-flagged migration |
| Unrelated change touches extraction during Phase 4 | Medium | High | Coordinate merge timing; complete Phase 4 in one week of focused work |

## What doesn't change

- `slot_extractor.py` (regex extraction) — untouched. Its API is exactly what the new unified extractor takes as input.
- Semantic router (`semantic_router.py`) — still called from `_run_early_extraction`, unchanged.
- PII redaction — unchanged.
- All downstream handlers — unchanged, they consume the same `merged` dict shape.

## What gets better

- Single entry point for LLM extraction; new contributors stop needing to know which of two modules to read.
- Documented merge rules — each field's behavior is stated, tested, and inspectable.
- Preserved Sprint 1/2/3 work — no more silent clobbering of correct regex output.
- Smaller surface area: 2 modules → 1, ~1,100 LOC → ~400, 10 test files → 3-4.
- Clearer cost model — single `SLOT_EXTRACTION_MODEL` constant; changing the model propagates everywhere.

## Open questions

1. Should `_contradiction` and `_is_additive` move from Trust Model 5 (regex-only) to Trust Model 2 (LLM-semantic) or 4 (union)? Haiku could plausibly detect both. Revisit after Phase 3 production data — if eval shows regex missing these signals on novel phrasings, promote to union and re-test.
2. Should narrative mode move to Sonnet for better urgency-hierarchy reasoning? Current code uses Haiku for both paths. Hypothesis: long narratives with implicit urgency benefit from Sonnet's deeper reasoning; cost is ~3× higher per narrative call. If `wa_tell_my_story`-style scenarios still underperform after Phase 3, this is the lever.
3. Is the `_is_narrative` ≥ 20-word threshold still right? It predates Haiku 4.5. Both paths use the same model today, so the tuning question is now prompt cost (narrative ~700 tokens vs short ~200) and reasoning quality, not model selection. Don't change as part of this migration; measure after Phase 3 and file follow-up.
4. The post-pending-confirmation call site (`handlers/confirmation.py`) runs extraction on messages typed at a confirmation prompt — typically short, contradiction-bearing, or filler. Should it use a narrower extractor (contradiction detection + single-slot extraction) rather than the full unified `extract()`? Out of scope for this migration; likely yes in a future iteration.

**Resolved in rev 15:**

- ~~Whether the cross-borough scenario regression from Ext-2b is acceptable.~~ Not acceptable. Carve-out shipped in rev 15.
- ~~Whether the test count discrepancy at rev 14 (4,097 claimed vs 3,521 actual) reflects an issue.~~ Yes — three test failures were tracking the orphan-file wiring gap. Closed in rev 15.
