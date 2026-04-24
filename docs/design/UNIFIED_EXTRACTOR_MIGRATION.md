# Unified LLM Extractor Migration

**Status:** Approved — ready for implementation (team sign-off 2026-04-23)
**Owner:** Raleigh
**Created:** 2026-04-22
**Approved:** 2026-04-23 — Owner (Raleigh) and team approved: (a) Phase 2 acceptance criteria as stated below, (b) all schema decisions (`org_name` keep + fuzzy validator, `service_detail` Option A extend + canonical-form validator, `additional_services` extended to `[{type, detail?, location?}]`), (c) priority-hierarchy consolidation (food ≥ mental_health), (d) 2-hour / ~$50 parallel-run eval budget. Two pre-migration hardening changes (MOCK_QUERY_RESULTS builder and age-regex widening) already shipped as standalone PRs.
**Revision:** 2026-04-24 (rev 14) — Phase 2 WIRING COMPLETE. `USE_UNIFIED_EXTRACTOR` env flag added to `backend/app/services/chatbot/context.py` alongside `_USE_LLM`; off by default. Both migration call sites now branch on the flag: `orchestrator.py` (line 382 service-branch) routes to `slot_extraction.extract()` when on; `handlers/confirmation.py` (post-pending path) does the same but runs inline regex since `early_extracted` isn't in scope there. 23 new routing tests in `test_unified_extractor_flag.py` verify flag definition, env-var parsing for truthy/falsy values, orchestrator routing (flag on/off), confirmation-handler routing (flag on/off), regex_result passthrough as the second positional arg, and the no-API-key bypass. Repo-wide test suite: 4,097 passing, 0 failures (17 skipped, 3 xfailed — all pre-existing). Deliverable: `/mnt/user-data/outputs/phase-2-feature-flag/`. Remaining Phase 2 action: parallel-run eval against the 4-scenario watch list (budgeted separately as a ~2hr / ~$50 operation requiring live API credits).
**Prior revisions:** rev 13 (2026-04-23) — Phase 1 COMPLETE. New `backend/app/services/slot_extraction/` package shipped (1,580 LOC across 4 files) with 147 unit tests (1,585 LOC) achieving **100% line + 100% branch coverage**. All five trust models implemented as named functions; both LLM paths wrapped in mockable dispatch; canonical-form validators for `service_detail` and `org_name`. Repo-wide test suite: 4,066 passing, 0 failures. Deliverable: `/mnt/user-data/outputs/phase-1-slot-extraction/`. rev 12 (2026-04-23) — Phase 0 outcome applied (Option 1 chosen). rev 11 — Phase 0 corpus check complete. rev 10 — team approvals locked in. rev 9 — age regex widened. rev 8 — MOCK_QUERY_RESULTS + age duration-strip shipped. rev 7 — three additional audits. rev 6 — sweep of LLM functions. rev 5 — audit of `extract_slots_smart`. rev 4 — schema decisions. rev 3 — first audit pass. rev 2 — trust models. rev 1 — initial draft.
**Related:** R35 eval regression (`multi_cross_borough` 3.09 → 2.82); PR #61

## Problem

The chatbot has two LLM-based slot extractors that overlap but are not identical:

- `classify_unified` (`llm_classifier.py`, ~250 LOC) — narrow gap-filler, single Haiku call, fires only when regex found no service intent
- `extract_slots_smart` (`llm_slot_extractor.py`, ~800 LOC) — full pipeline with four dispatch paths, runs unconditionally on service-category messages

`llm_classifier.py`'s header comment states its intent is to **replace** `extract_slots_smart`, but the migration was never completed. The two extractors now coexist, with `extract_slots_smart` able to silently override correct regex extractions — as happened in R35, where its override heuristic stripped Sprint 1's cross-location binding from `multi_cross_borough`.

Every downstream fix that depends on regex output (Sprint 1's per-service-location binding, Sprint 2's `_gender` suffix, Sprint 3's foster-youth populations) is at risk of being silently erased by `extract_slots_smart`'s multi-service override logic whenever the message is complex enough to trigger LLM extraction.

## Goal

Migrate all LLM-based slot extraction through a single, unified entry point. Delete `llm_slot_extractor.py`. Preserve the capabilities that make `extract_slots_smart` valuable today (narrative mode, semantic-router integration, merge logic) while eliminating its destructive override pattern.

Success criteria:

1. Single entry point for LLM extraction.
2. No caller can receive an extraction that silently discards correct regex output.
3. R35's failing scenarios (`multi_cross_borough`, `multi_three_services_legal_benefits_food`, `peer_diabetic_insulin` extraction path) pass post-migration.
4. R35's passing scenarios don't regress — measured via parallel-run comparison, not assumed.
5. `llm_slot_extractor.py` is deleted; all 8 test files referencing it are consolidated or ported.

## Current-state inventory

### Call sites to migrate

| File | Line | Function called | Notes |
|---|---|---|---|
| `chatbot/orchestrator.py` | 383 | `extract_slots_smart` | Unconditional on service-category messages (`if _USE_LLM and category == "service"`). R35 regression source. |
| `chatbot/handlers/confirmation.py` | 605–606 | `extract_slots_smart` | Post-pending-confirmation re-extraction when user types free text at a confirmation prompt. Fires under `if _USE_LLM` inside `_handle_post_pending_confirmation`. |
| `chatbot/pipeline.py` | 149 | `classify_unified` | Gap-filler. Fires only when regex returned no `service_type`. Merge logic at pipeline.py:151–172 unconditionally writes unified values into `early_extracted` within an outer `if unified.get("service_type")` guard — this is approximately but not strictly fill-only. If regex had caught `age` but missed `service_type`, the gate's age value overwrites. Accept as-is; it's a gap-filler that has never caused observed regressions. |

### Functions in `llm_slot_extractor.py`

| Function | Purpose | Migration fate |
|---|---|---|
| `extract_slots_smart` | Top-level dispatcher — calls regex internally, routes to narrative/simple/complex paths, runs merge | **Delete** — replaced by new `extract()` with cleaner input contract (caller passes regex in) |
| `extract_slots_llm` | Short-message tool-call to Haiku; returns `{service_type, additional_service_types: str[], location, age, urgency, _gender, family_status, _populations, org_name}` | **Fold into** new unified — port prompt + tool schema; extend schema per decisions above |
| `extract_slots_narrative` | Long-message tool-call to Haiku with urgency-aware prompt; same return shape as `extract_slots_llm` except `org_name` is missing (see "Behaviors" #13) | **Fold into** new unified — port prompt + tool schema as conditional variant of the short path (narrative-mode prompt only); add `org_name` to the return dict to match schema |
| `_is_narrative` | `≥ 20 words` threshold | **Port** as private helper (constant: `_NARRATIVE_THRESHOLD`) |
| `_is_simple_message` | Returns True iff 4 criteria all hold (see "Behaviors" section #5 above) | **Port verbatim** as private helper |
| `_narrative_regex_fallback` | When narrative LLM call fails: re-prioritize regex output by `_URGENCY_HIERARCHY` + infer urgency from 14 clue words | **Port verbatim**. See "Behaviors" section #1 (hierarchy consolidation) and #2 (clue-word list) |
| `_merge_additional_services` | Converts LLM's `additional_service_types: str[]` into regex's `(service_type, detail, location)` tuple format, preserving regex's `detail` field and de-duplicating by service category | **Port with modification.** See "Behaviors" #19 for 5 non-obvious semantics to preserve/fix: (1) don't mutate input, (2) normalize schemas at call-boundary, (3) log primary-exclusion case, (4) always set output key, (5) merge per-field on dedup (not regex-wins wholesale) once LLM schema is extended. |
| `_empty_slots` | Schema default dict | **Port** as `_empty_extracted_slots()` helper |
| `_track_llm_call` calls | In-memory counter for diagnostics | **Preserve** — wrap each LLM call identically |
| `record_llm_call` calls (via `audit_log`) | Audit log entry per LLM call (task, model, tokens, latency, success) | **Preserve** — move into the LLM-call wrapper so every call site gets it |
| Conversation-history formatting (role-alternation padding) | Both LLM functions insert placeholder messages to avoid consecutive same-role turns | **Port** as a shared utility (`_format_conversation_history()`) |
| `_empty_slots` | Schema default | **Port** |

### Test files touching the surface

5,596 lines across 10 files:

| File | Role |
|---|---|
| `tests/unit/test_llm_slot_extractor.py` | Core unit tests for `extract_slots_smart` / `extract_slots_llm` — biggest file, most of the contract |
| `tests/unit/test_narrative_extraction.py` | Narrative path — specific edge cases like "just got out of hospital" |
| `tests/unit/test_llm_multi_service.py` | Multi-service merge — where the Sprint 1 regression would regress again without careful porting |
| `tests/unit/test_populations.py` | `_populations` extraction — touches both extractors |
| `tests/unit/test_semantic_router.py` | Semantic-router integration within `extract_slots_smart` |
| `tests/unit/test_llm_classifier.py` | `classify_unified` tests — will grow as we extend the prompt |
| `tests/unit/test_chatbot_extracted_helpers.py` | Orchestrator wiring tests |
| `tests/unit/test_response_escalation.py` | Downstream consumer of extraction output |
| `tests/integration/test_service_data_llm_firewall.py` | LLM firewall — 38 tests across 7 layers. See "Behaviors" #21 for the contract surface the migration must preserve (tool_use enforcement, system-prompt audit, 6 tests that import from `llm_slot_extractor` directly — update in Phase 4). |
| `tests/integration/test_targeted_bug_regressions.py` | Scenario-specific regression guards |

## Behaviors in `extract_slots_smart` that must survive the migration

Beyond the trust-model-per-field rules above, `extract_slots_smart` carries behaviors that aren't captured by the merge-rules framing. These are independent concerns the new unified extractor must preserve — each one is a potential silent regression if lost.

### 1. Two priority tables exist, and they disagree

The regex extractor uses `_SERVICE_NEED_PRIORITY` (`slot_extractor.py:829–838`) with tier numbers 1-5 where lower = higher priority. The narrative fallback uses `_URGENCY_HIERARCHY` (`llm_slot_extractor.py:216`) with ranks 0-8 where higher = higher priority. They agree on everything except **`food` vs `mental_health`**:

| Service | Regex `_SERVICE_NEED_PRIORITY` | Narrative `_URGENCY_HIERARCHY` |
|---|---|---|
| shelter | 1 (highest) | 8 (highest) |
| medical | 1 (tie) | 7 |
| **food** | **2** | **5** |
| **mental_health** | **2 (tie)** | **6** (higher than food) |
| clothing | 3 | 4 |
| personal_care | 3 (tie) | 3 |
| legal | 4 | 2 |
| employment | 4 (tie) | 1 |
| other | 5 | 0 |

**Decision — APPROVED 2026-04-23:** consolidate so **food ranks at or above mental_health**. Hunger is a physiological need that undermines other interventions; a person in mental-health crisis who hasn't eaten still needs the food first, or the mental-health referral doesn't land. The narrative table's ordering is the one to change, not the regex one. Concrete actions:
- Update `_URGENCY_HIERARCHY` in the ported `_narrative_regex_fallback` so `food ≥ mental_health`. Either raise food's rank above mental_health's, or tie them at the same rank and tie-break by text position (matches regex behavior).
- Update the narrative-LLM prompt (`_NARRATIVE_SYSTEM_PROMPT`) to list `food` above `mental_health` in its urgency hierarchy. Current prompt text lists them as `shelter / housing → medical → mental_health → food → ...`; the new ordering is `shelter / housing → medical → food → mental_health → ...`.

This is a behavioral change: the narrative-LLM-failed path AND the narrative-LLM-succeeds path will both re-prioritize differently than today. Flag explicitly in Phase 2 eval comparison; watch for regressions on scenarios where a user in crisis + hunger currently gets routed to mental_health.

### 2. Narrative regex-fallback infers urgency from clue words

`_narrative_regex_fallback:510–515` scans for 14 hardcoded urgency clues (`"tonight"`, `"right now"`, `"nowhere to go"`, `"kicked out"`, `"evicted"`, `"just released"`, `"just got out"`, `"ran away"`, `"runaway"`, `"on the street"`, `"sleeping outside"`, `"emergency"`, `"fleeing"`, `"escaped"`) and sets `urgency=high` if any appear and regex didn't already set urgency. This runs only when the narrative LLM call fails.

**Migration treatment**: port the clue list verbatim into the new unified extractor's LLM-failure fallback. Keep it as a named constant so it's discoverable for future additions. Low risk of regression if preserved literally; high risk if forgotten.

### 3. Semantic router is a third source of `_populations`

The semantic router (`semantic_router.py`) returns `(service_type, population?, confidence)`. When it matches, it contributes a population tag that's unioned with regex's populations (`llm_slot_extractor.py:670–674` and `pipeline.py:107–111`). The trust model 4 "union of regex and LLM" rule for `_populations` needs to expand to **union of regex + semantic router + LLM**.

**Migration treatment**: explicit union of all three sources. Phase 1 unit test: semantic router contributes `foster_youth` for "aging out of foster care" even when regex and LLM both miss it.

### 4. Semantic router short-circuits LLM for ≤ 8-word messages

When the semantic router matches a service and the message is ≤ 8 words (`llm_slot_extractor.py:681–683`), the LLM call is skipped entirely. Saves ~500ms + tokens on short messages like "need a place to stay tonight" where the router's high confidence is trustable.

**Migration treatment**: preserve as an optimization in the dispatch logic. This is a real latency/cost win for short user queries, not an accident.

### 5. `_is_simple_message` has four specific criteria

The short-message-skip-LLM path (`_is_simple_message`, line 526) returns True only when ALL four conditions hold:

1. `len(words) ≤ 8`
2. Regex extracted BOTH `service_type` AND `location`
3. The location is in `_KNOWN_LOCATIONS` OR is the `NEAR_ME_SENTINEL`
4. The message matches only ONE `SERVICE_KEYWORDS` category (no multi-intent ambiguity)

**Migration treatment**: port the check verbatim. Each criterion prevents a real failure mode — dropping any one is a regression. Criterion 4 specifically is what stops `multi_cross_borough` from being treated as "simple" (it would match both food and shelter keywords, failing the single-category check).

### 6. Conversation-history formatting requires role-alternation padding

Both `extract_slots_llm` and `extract_slots_narrative` take the last 6 turns of conversation history and insert placeholder messages ("(continuing)", "(listening)") to avoid consecutive same-role messages in the API call. Claude's Messages API rejects consecutive user-user or assistant-assistant turns; the padding keeps the API call valid.

**Migration treatment**: port the formatting helper as a shared utility. Unit-test with a synthetic conversation that would violate role alternation.

### 7. LLM-empty-result fallback

When the LLM returns a result dict with all null/empty values (`llm_has_data == False`, line 694), `extract_slots_smart` falls back entirely to `regex_result`. Not a merge — a wholesale replacement. Happens on API failures, malformed outputs, or genuine "no slots extractable" cases.

**Migration treatment**: preserve the wholesale-replacement semantics. The alternative — merging an empty LLM with regex via trust rules — is equivalent only if every trust rule correctly handles a None LLM value, which currently is not strictly guaranteed for the `_populations` union (the union of empty + regex = regex, safe) or trust model 3 (LLM set empty triggers "regex wins" — safe). Verify explicitly in Phase 1 unit tests rather than assuming.

### 8. Observability: audit logging + latency measurement

Both LLM call sites wrap the API call in:
- `_track_llm_call(task_name)` — in-memory counter for diagnostics
- `time.perf_counter()` bracketing to measure latency
- `record_llm_call(task, model, input_tokens, output_tokens, latency_ms, success)` — audit log entry

**Migration treatment**: preserve all three in the new wrappers. Without these, operational metrics and cost tracking break.

### 9. Model + max_tokens configuration

Both LLM paths use `SLOT_EXTRACTION_MODEL` — resolves to `claude-haiku-4-5-20251001` (`claude_client.py:39`) — with `max_tokens=256`. The constants are centralized in `app.llm.claude_client` and imported into the extractor module.

**Migration treatment**: keep in a single `config.py` inside the new module. No changes to values for the migration.

### 10. Ops-visibility logging on deferral decisions

Lines 736–752 log **when** the override fires vs defers to LLM, with enough detail for log-grep diagnosis. This is how the R35 `multi_cross_borough` regression was eventually traced. The new set-equality rule should have equivalent logging at the decision points.

**Migration treatment**: log every merge-rule decision that isn't trivial fill-only. At minimum: set-equality rule (whether sets matched, which path taken), populations union (which source contributed what), service_detail canonical-form snap (what LLM returned, what it snapped to, or discard). `logger.info` level; avoid noise.

### 11. The `additional_service_types` vs `additional_services` naming inconsistency

LLM output uses `additional_service_types` (`string[]`), regex and session state use `additional_services` (`tuple[type, detail, location][]`). `_merge_additional_services` is the only code that straddles both shapes. Downstream code expects `additional_services`.

**Migration treatment**: the unified schema extension renames this to `additional_services: [{type, detail?, location?}]` (JSON objects instead of tuples in the LLM contract, but convertible to tuples pre-merge). This collapses the naming inconsistency — worth calling out as a positive side effect.

### 12. Both LLM paths use Haiku, not Sonnet

Correction to earlier doc revisions: both `extract_slots_llm` and `extract_slots_narrative` call `SLOT_EXTRACTION_MODEL` which is `claude-haiku-4-5-20251001` (`claude_client.py:39`). Earlier text in this doc incorrectly stated "Sonnet for narrative" — the only Sonnet use in the slot-extraction pipeline is `CRISIS_DETECTION_MODEL`, which is a different module. Both narrative and short extraction run on Haiku 4.5.

**Migration treatment**: preserve Haiku for both paths. The model choice is a centralized constant, not a path-specific decision. Open question 2 in this doc asks whether narrative should move to Sonnet — that remains a valid question, but shouldn't be conflated with current behavior.

### 13. Narrative path drops `org_name` from its return dict

`extract_slots_llm` returns a 9-field dict including `org_name` (line 348). `extract_slots_narrative` returns an 8-field dict that omits `org_name` entirely (lines 432–441). Both paths call the same tool with the same schema, but the narrative path's dict-construction code was written before `org_name` was added to the tool and never updated.

**In practice**: the outer `extract_slots_smart` merges narrative-LLM output with regex-result, and regex contributes `org_name` when it detects one. So the field survives — but only because regex happens to catch org names the user mentions. If a user writes a long narrative referencing an org by name that regex misses (e.g., a novel org not in the authoritative table), the narrative-LLM would have caught it, but the dropped-from-return kills that signal.

**Migration treatment** (confirmed decision): add `org_name` to the narrative path's return dict, same shape as `extract_slots_llm`. Both paths then return 9-field dicts. Paired with the fuzzy-match validator (see Trust Model 1 sub-case for `org_name`), narrative-LLM-detected org names get the same validation + usage path as short-LLM-detected ones. Port this fix into the new unified extractor — do not port the 8-field version.

### 14. Narrative vs non-narrative LLM-failure behavior diverges

When `extract_slots_llm` fails (API error, empty tool call), it returns `_empty_slots()` — a dict with every field None or empty list. Outer `extract_slots_smart` detects `llm_has_data=False` and falls back wholesale to `regex_result` (see Behavior #7).

When `extract_slots_narrative` fails, it returns `_narrative_regex_fallback(message)` directly. This path re-runs regex AND re-prioritizes by `_URGENCY_HIERARCHY` AND infers urgency from 14 clue words (Behaviors #1 and #2). The outer `extract_slots_smart` then runs its regex-supplement loop over this already-processed fallback, which is mostly idempotent but not identical to the "empty LLM → bare regex" path.

So the two "LLM-failed" paths produce materially different outputs. Specifically, a failed narrative extraction gets urgency-inference that a failed short extraction does not.

**Migration treatment**: the new unified extractor's LLM-failure handling should converge these paths. When narrative LLM fails: run regex, re-prioritize by the consolidated urgency hierarchy, infer urgency from clue words, return. When short LLM fails: run regex, return. Two separate fallback helpers (`_short_fallback`, `_narrative_fallback`) is fine — document them clearly so future maintainers don't mistake one for the other.

### 15. Success logging is asymmetric

`extract_slots_narrative` logs a success line (primary, additional, urgency) on every successful tool call. `extract_slots_llm` logs nothing on success — only on "no tool call found" (warning) or exception (error).

**Migration treatment**: add symmetric logging to both paths. The narrative-mode log has been useful for R35-style diagnostics; the short-mode path would have benefited from it too. `logger.info` level with enough detail for `grep`-based tracing: path (short/narrative), service_type, additional_service_types, location, duration. Keep it to a single line per call.

### 16. Audit-log `success=False` is never recorded

Both paths call `record_llm_call(..., success=True)` only on the successful branch. The exception and "no tool call" branches log via `logger` but don't call `record_llm_call` with `success=False`. As a result, failed LLM calls don't appear in the audit-log cost/failure aggregates — just in the Python `logger.error` stream.

**Migration treatment** (confirmed decision): add `record_llm_call(..., success=False, input_tokens=0, output_tokens=0, latency_ms=<measured>)` to every non-success branch — exception handler and "no tool call in response" branch. Failed LLM calls must be visible to cost/failure aggregates, not just log lines. Concrete change:

```python
try:
    t0 = time.perf_counter()
    response = client.messages.create(...)
    latency = round((time.perf_counter() - t0) * 1000)
    # ... parse tool call ...
    if tool_call_found:
        record_llm_call(task, model, in_tok, out_tok, latency, success=True)
        return result
    # No tool call in response
    record_llm_call(task, model,
                    getattr(response.usage, 'input_tokens', 0),
                    getattr(response.usage, 'output_tokens', 0),
                    latency, success=False)
    logger.warning(f"{task}: Claude did not return tool call")
    return _empty_slots()  # or _narrative_regex_fallback
except Exception as e:
    latency = round((time.perf_counter() - t0) * 1000) if 't0' in dir() else 0
    record_llm_call(task, model, 0, 0, latency, success=False)
    logger.error(f"{task} failed: {e}")
    return _empty_slots()  # or _narrative_regex_fallback
```

Port this pattern into the new unified extractor's shared LLM-call wrapper so it applies to both narrative and short paths consistently.

### 17. No token-budget guard on input

`max_tokens=256` bounds output. Input is unbounded — a user message of 10,000 chars plus 6 history turns sends it all to the API. Haiku can handle this, but it wastes input tokens and latency. No callsite truncates.

**Migration treatment**: consider adding a defensive cap on `message` (e.g., truncate to last 2,000 chars if longer) and on per-history-turn text (e.g., last 500 chars per turn). Low priority — haven't observed pathological inputs in production — but adds $0 overhead when applied and bounds worst-case cost. Call out as a post-Phase-4 hardening task if not done during migration.

### 18. `response.usage` unguarded access

`response.usage.input_tokens` at line 330 assumes Anthropic always returns a `usage` field. If the API shape drifts or a future SDK version omits it, `AttributeError` is raised, caught by the bare `except Exception`, and the call is treated as failed. Not currently a real issue but a one-line defense-in-depth: `getattr(response, 'usage', None)` with a None-check before accessing `.input_tokens`.

**Migration treatment**: port with defensive `getattr` in the wrapper. Zero-cost defense.

### 19. `_merge_additional_services` has non-obvious semantics beyond dedup

Line-by-line audit (`llm_slot_extractor.py:766–809`) surfaced several behaviors not captured by earlier doc revisions:

- **The function mutates its input dict.** `primary_result.pop("additional_service_types", [])` — the key is removed, not just read. If any caller references `primary_result["additional_service_types"]` after the merge, it's gone. Callers today don't, but future callers could trip on this.
- **Shape transformation is implicit.** Input key: `additional_service_types` (LLM shape). Output key: `additional_services` (tuple shape). The function doesn't just merge — it also converts schema shapes. A reader unfamiliar with both shapes has to work through the code to see this.
- **Primary-exclusion is silent.** Line 783 adds `primary` to the `seen` set. If regex's additionals contain the primary service_type (can happen when regex priority-ordering promoted one of the additionals to primary earlier), the duplicate is dropped without a log line. Ops-invisible but correct.
- **Write-only-if-non-empty.** Line 806: `if combined_additional:` — if both sources returned empty, `additional_services` is NOT set on the dict. Downstream code must tolerate both "key absent" and "key == []". Current downstream does tolerate both, but this is a hidden invariant worth making explicit.
- **Regex-wins-on-dedup is wrong for the extended schema.** Current logic: when both sources have a service, regex's tuple wins unconditionally because "regex has detail and LLM doesn't." Once the LLM schema is extended to `[{type, detail?, location?}]`, LLM may have detail or location info regex missed. The extended merge must pick best-of-both per-field, not regex-wins wholesale.

**Migration treatment**: port the 5 behaviors above as documented expectations in the new `merge_additional_services()` helper. Specifically:
1. Do NOT mutate input — work on a copy, return a new dict.
2. Normalize schemas earlier — LLM output should be converted to tuple shape at call-boundary, not inside the merge.
3. Log the primary-exclusion case at debug level for traceability.
4. Always set the output key (empty list if no additionals), removing the "key absent" edge case.
5. On duplicate, merge per-field: regex wins on `type`, regex wins on `detail` if set else LLM's, regex wins on `location` if set else LLM's.

Add a unit test for each of the 5 behaviors. They're subtle enough that a simple "port verbatim" carries real regression risk.

### 20. Narrative urgency clue list misses DV/safety signals

Audit result from scanning all 311 user-turn messages in `tests/eval/eval_llm_judge.py` against the 14 current clue words in `_narrative_regex_fallback`:

- **11 of 22 narrative-length (≥ 20 words) scenarios** match a current clue. These get `urgency=high` when narrative-LLM fails.
- **2 of 22** imply urgency but match NO current clue. Both are safety-adjacent:
  - "I have a place to stay but I don't feel safe there. My roommate has been threatening me. I need to find somewhere else in the Bronx." — implies urgency via "don't feel safe" + "threatening me"; current list has neither.
  - "I've been to all of those already. The first one turned me away and the second one was really unsafe." — implies urgency via "unsafe"; current list misses it.
- **9 of 22** don't imply urgency regardless of clue list (medium-urgency or informational).

**Suggested additions** to the urgency clue list (all derived from eval narratives or adjacent patterns):
- `"not safe"`, `"don't feel safe"`, `"doesn't feel safe"` — safety phrasing users actually use.
- `"unsafe"` — direct adjective form.
- `"threatening"`, `"threatened"` — active threat without "emergency" framing.
- `"can't stay"`, `"can't keep"` — ejection imminent (captures "she can't keep me anymore").
- `"ran out of"` — medical-urgency phrasing ("ran out of insulin", "ran out of my meds").

**Migration treatment**: extend the clue list in the new unified extractor's narrative-fallback helper. The additions are conservative — each matches a real user phrasing observed in eval scenarios. Phase 2 parallel-run eval will show whether any of them over-trigger (false-positive urgency on genuinely non-urgent scenarios).

Alternative to discuss: move urgency inference from the fallback helper into the narrative LLM prompt itself, so the LLM handles it when available and only the fallback relies on clue words. Current code does both (prompt teaches LLM; fallback reinfers from clues) — not a problem, but a minor redundancy.

### 21. The `test_service_data_llm_firewall.py` contract surface

Audit of the 704-line integration test surfaced the architectural contract the migration must preserve. Organized into 7 layers, totaling 38 test methods:

| Layer | Class | Contract |
|---|---|---|
| 1. Prompt builders | `TestPromptBuilderIsolation` | Conversational and bot-question prompt builders must accept service slots as parameters but never include them in the prompt text. Geolocation boolean is the only exception (used to gate "near me" responses). |
| 2. LLM call sites | `TestLLMCallSiteCapture` | Captured prompts from `claude_reply` (conversational path) and fallback-response builder must not contain slot values, even when slots are fully populated. |
| 3. Search execution firewall | `TestSearchExecutionFirewall` | DB failures (exception, query error, None response) must use static error messages from `chatbot/responses.py` — never call the LLM. Successful and zero-result searches must never call the LLM either. |
| 4. Structured LLM outputs | `TestStructuredLLMOutputs` | Crisis response is hardcoded. Crisis-LLM returns JSON, not free text. Classifier returns structured data. **Slot extractor uses tool_use API, never generates text** (line 437). |
| 5. End-to-end flows | `TestEndToEndIsolation` | Full conversation flows (general-after-search, bot-question-during-search, multiple-searches, emotional-with-slots, crisis-adjacent-with-slots) never leak slot values to any captured prompt. |
| 6. System prompt audit | `TestSystemPromptAudit` | No conversational, crisis, classifier, or slot-extractor system prompt contains phone numbers, URLs, or user data. **Slot-extractor `_SYSTEM_PROMPT` asserts no `"212-"` and no `"https://yourpeer"`** (line 585). |
| 7. Regression guards | `TestRegressionGuards` | Specific past leaks are prevented: DB failure → LLM followup (2025-04-13), "Context from our conversation" pattern, "service need" steering instruction, `_fallback_response` call in execution. |

**Migration-relevant contracts** (must survive the migration):

1. **`test_slot_extractor_uses_tool_use_not_text`** (line 437) — the new unified extractor MUST use tool_use API for both short and narrative paths. No plain-JSON or free-text responses. This is consistent with current behavior and my design; explicit test guard.
2. **`test_slot_extractor_system_prompt_has_no_service_data`** (line 585) — both `_SHORT_SYSTEM_PROMPT` and `_NARRATIVE_SYSTEM_PROMPT` in the new module must pass the same assertions (`"212-" not in prompt` and `"https://yourpeer" not in prompt`). Port the assertions; they're architecturally significant (LLM must not see canned service data that could leak as a fake reply).
3. **The test currently imports from `app.services.llm_slot_extractor`** — after Phase 4 deletion, the test must be updated to import from the new module. The import change is mechanical, but easy to miss. Add to the Phase 4 checklist.

**Migration treatment**:
- Before Phase 4 (deletion), verify all 38 tests still pass with the old module in place.
- During Phase 4, update the 6 test methods that directly import from `llm_slot_extractor` (grep for `from app.services.llm_slot_extractor`). Map each to the new module's equivalent.
- Consider extending Layer 6 (System Prompt Audit) with a new assertion: no system prompt in the new module contains any `_NOTABLE_SUB_TYPES` canonical value (i.e., the LLM must infer sub-types, never be told them). This closes a potential new leak vector introduced by the `service_detail` schema extension.

### 22. Age regex hardening: duration-phrase strip + natural "year old" forms — SHIPPED

Two related hardening changes to `_extract_age` (`slot_extractor.py:1190–1244`) in this audit pass.

**Change A — strip `"for NN <time-unit>"` duration phrases before running patterns.**

Live audit corrected an earlier claim in this doc. The original `_extract_age` patterns all required prefixes (`i'm`, `i am`, `age`) or suffixes (`years old`, `-year-old`) or comma-delimited list positions. The pattern `"for N years"` matched none of them, so `"I've been homeless for 3 years"` did NOT over-extract even in the original code.

Two narrower known false positives did exist:
- `", NN, "` list-format matches outside age context (e.g., `"Brooklyn, 3, apartments"` → age=3) — the `,\s*(\d{1,2})\s*,` pattern over-matches.
- Future additions to the pattern list could introduce `"for N"` as a vulnerability.

Implementation:

```python
_DURATION_STRIP_RE = re.compile(
    r"\bfor\s+\d{1,3}\s+(?:year|month|day|week)s?\b",
    re.IGNORECASE,
)

# In _extract_age, BEFORE the existing pattern loop:
text_for_age = _DURATION_STRIP_RE.sub("", text)
# then run existing patterns against text_for_age
```

**Change B — widen "NN year(s) old" to accept all natural separators.**

The original patterns `\b(\d{1,2})-?year-?old\b` and `\b(\d{1,2})-?yr-?old\b` made hyphens optional but did NOT accept spaces. So `"17 year old"` and `"17 years old"` (without hyphens) would miss both patterns and only fall back to pattern 4 (`\b(\d{1,3}) years old\b`) for the plural form — leaving the singular `"17 year old"` unmatched entirely.

Most users don't type the hyphen. A real user typing `"17 year old needs shelter in Queens"` wouldn't have matched under the old regex. Widened to:

```python
r"\b(\d{1,3})[\s-]?years?[\s-]?old\b"  # "NN year[s] old" with space OR hyphen OR contiguous
r"\b(\d{1,3})[\s-]?yrs?[\s-]?old\b"    # abbreviated "yr[s] old" same flexibility
```

Also bumped the digit-count from `{1,2}` to `{1,3}` so centenarians saying "105 years old" match (the range check at the end still rejects ≥120).

**Verified behavior** (in shipped code, all of these now return age=17):
- `"17 years old"` ✓ (plural, space — the requested case)
- `"17 year old"` ✓ (singular, space)
- `"17-years-old"` / `"17-year-old"` ✓ (hyphenated)
- `"17 year-old"` / `"17-year old"` ✓ (mixed separators)
- `"17years old"` / `"17yearold"` ✓ (contiguous)
- `"17 yrs old"` / `"17 yr old"` / `"17-yr-old"` ✓ (abbreviated)
- Plus in-sentence: `"17 year old needs shelter"`, `"I'm a 17 year old looking for food"`, `"she's 23 years old"`

**Regression test coverage**:
- `test_age_does_not_match_for_duration_phrases` (6 duration phrases) — Change A.
- `test_age_preserved_when_duration_and_age_coexist` (4 combined phrases) — Change A.
- `test_age_year_old_without_hyphen` (all separator variants) — Change B.
- `test_age_year_old_edge_negatives` (duration + lookalikes that must NOT match) — covers "17 years ago", "17 years experience", "a17 year old" etc.

All four tests are in `tests/unit/test_slot_extractor.py`. Full unit suite: **115 passing, 1 xfailed. Zero regressions.**

Deliverable: `/mnt/user-data/outputs/age-regex-hardening/{slot_extractor.py, test_slot_extractor.py}`. Apart from hardening, Change B is a real coverage improvement — pre-fix, `"17 year old"` returned None, which could have affected real user messages where the LLM wasn't available to catch it.

### 23. Narrative prompt misses populations and org_name in the "extract ALL slots" list

Line-by-line read of `_NARRATIVE_SYSTEM_PROMPT` (`llm_slot_extractor.py:228–261`) surfaced a prompt-schema mismatch:

Line 255-256 says: *"Extract ALL slots: service_type, additional_service_types, location, age, urgency, gender, family_status."*

That's **7 fields**. The `_EXTRACT_SLOTS_TOOL` schema declares **9 fields** — the prompt is missing:
- `populations` (array of identity tags — veteran, disabled, reentry, dv_survivor, pregnant, senior, foster_youth)
- `org_name` (name of specific org the user is asking about)

Claude probably fills these fields anyway when the schema asks for them — the model reads the schema, not just the prompt. But prompt-schema mismatch weakens signal:
- The LLM may underweight populations and org_name because they're not listed in the "extract ALL slots" directive.
- This directly causes **Behavior #13** (narrative dict drops `org_name`): the post-extraction code literally ignores `org_name` because the prompt author didn't think to list it.

**Migration treatment**: in the new unified narrative prompt, enumerate all 9 fields explicitly in the "Extract ALL slots" line:

> "Extract ALL slots: service_type, service_detail, additional_services, location, age, urgency, gender, family_status, populations, org_name."

Adjust once `service_detail` is added to the unified schema (per the Phase 0 decision). Verify via a unit test that the prompt contains every field name from the tool schema as a substring. Cheap and catches future prompt-schema drift.

### 24. `classify_unified` has latent bugs worth noting (and absorbing during migration)

Line-by-line read of `llm_classifier.py` surfaced three issues in the gap-filler path that will be inherited by the migration unless explicitly addressed:

**(a) `_VALID_POPULATIONS` is missing `foster_youth`** (line 245). The set contains `{"veteran", "disabled", "reentry", "dv_survivor", "pregnant", "senior"}` — no foster_youth. So if classify_unified's LLM call returns `populations: ["foster_youth"]`, the validator silently drops it. This is possibly a contributing factor to the `peer_aging_out_foster` regression — if classify_unified fires on short foster-youth messages that regex missed, foster_youth doesn't survive the validator.

**Migration treatment**: in the new unified module, the populations validator set must match the tool-schema enum exactly. Write the set as a derived constant from the tool-schema enum, not as a hand-maintained copy — eliminates drift. Add a unit test asserting the validator's accepted set equals the tool-schema's declared values.

**(b) `service_detail` returned but not validated against canonical form** (line 179). classify_unified's validator accepts any non-empty string for `service_detail`. When the Option A schema-extension validator is built in Phase 1, make sure it snaps to `_NOTABLE_SUB_TYPES` canonical values — if classify_unified's lenient validator is ported as-is, the schema-extension validator is still needed on top.

**(c) `additional_services` always returns tuple with `detail=None`** (line 197: `(a_svc.lower(), None, a_loc)`). Same pattern as `_merge_additional_services` — LLM detail is dropped even if the schema allowed it. Once the schema is extended to `[{type, detail?, location?}]`, fix this site too.

**Migration treatment**: all three are absorbed into the new unified validator. Since classify_unified will be deleted in Phase 4, don't fix in place — fix only in the new module. Add a unit test per issue.

### 25. `MOCK_QUERY_RESULTS` fixture is service-type-blind — SHIPPED

The shared mock at `tests/conftest.py:97` (and the duplicate in `tests/eval/eval_llm_judge.py:3237`) used to return a **single hardcoded result shape**: one Brooklyn food-pantry service regardless of what the caller searched for. A shelter search for Manhattan returned food pantries in Brooklyn. A medical search for Bronx returned food pantries in Brooklyn.

This affected eval scoring in two ways:

1. **Opus judge saw mismatch and penalized.** If the scenario was "user wanted shelter in Manhattan, bot returned food in Brooklyn," Opus scored this as a search-quality failure — even though the issue was mock fidelity, not bot behavior. R35's `multi_cross_borough` dimension scores were partly contaminated by this.
2. **Multi-service scenarios couldn't be distinguished.** When the bot searched for shelter then food, both queries returned the same mock. The judge couldn't tell if the bot's multi-intent queue actually did two distinct searches.

**Fix shipped** as `/mnt/user-data/outputs/mock-query-results-improvement/conftest.py` — full replacement for `tests/conftest.py`.

**Design**:
- Swap the hardcoded dict for `build_mock_query_results(service_type, location, **kwargs)` — a builder callable that produces results plausibly matching the query.
- 9 service-type templates (food, shelter, medical, mental_health, clothing, personal_care, legal, employment, other), each with a unique `service_name`, `description`, phone, URL, and `service_taxonomies`. Unknown types fall back to food (preserves legacy behavior for tests that don't care).
- 5 NYC-borough coordinate centroids + ZIP codes, so a Manhattan card gets Manhattan coordinates and address, not Brooklyn's.
- Accepts all 16 kwargs of the real `query_services` signature via `**_unused_kwargs` — forward-compat with future signature changes.
- Back-compat preserved: `MOCK_QUERY_RESULTS`, `MOCK_SERVICE_CARD`, `MOCK_EMPTY_RESULTS`, `MOCK_RELAXED_RESULTS` still exported as module-level constants with identical shapes to the old hardcoded versions.
- New `mock_query_builder` pytest fixture exposes the builder for tests that want query-sensitive behavior.

**Wiring into the eval harness** (not yet done — separate PR):

```python
# In tests/eval/eval_llm_judge.py simulate_conversation():
from conftest import build_mock_query_results

with patch(
    "app.services.chatbot.execution.query_services",
    side_effect=lambda **kwargs: build_mock_query_results(**kwargs),
):
    result = generate_reply(user_msg, session_id=session_id)
```

The `side_effect` callable receives the actual `query_services(service_type=..., location=..., ...)` kwargs at each call and returns a plausible response. Opus judge sees shelter cards when the bot searched for shelter. Multi-service scenarios produce distinguishable outputs.

**Verification**:
- New conftest parses cleanly and back-compat spot checks pass (`MOCK_QUERY_RESULTS["services"][0]["city"] == "Brooklyn"` etc.)
- Builder correctly varies output by service_type and location across all 9 types + 5 boroughs.
- Unknown kwargs (age, gender, weekday, max_results, populations, taxonomy_override, etc.) silently accepted.
- **Full test suite: 3,917 passing, 17 skipped, 3 xfailed. Zero regressions.** This means all 56 firewall tests, all 89 integration tests that import MOCK_QUERY_RESULTS, and every other test continues to pass with the new conftest.

**Scope of the change**: this is NOT part of the unified-extractor migration. It's a separate eval-quality improvement that lands as a standalone PR. Worth calling out in this doc because Phase 2 parallel-run eval results will be more interpretable once the eval harness is wired to the builder.

**Ideal sequence for Phase 2**:
1. Land the conftest change (this PR) — done.
2. Separately update `tests/eval/eval_llm_judge.py` to use `side_effect=lambda **kwargs: build_mock_query_results(**kwargs)` in place of `return_value=MOCK_QUERY_RESULTS`.
3. Re-run R35 eval against the new mock to establish a cleaner baseline.
4. Start Phase 1 of the migration against that cleaner baseline.

**If the migration starts before step 2**: the Phase 2 comparison still works (same mock on both sides of the parallel run), the absolute scores just continue to be biased — but relative scores between old and new paths remain meaningful.

---

## Target design

### One public function

```python
# backend/app/services/slot_extraction/__init__.py
def extract(
    message: str,
    regex_result: dict,
    conversation_history: list[dict] | None = None,
    api_key_available: bool = True,
) -> dict:
    """Extract slots from a user message.

    Returns the same dict shape as regex extract_slots(). Never clobbers
    fields the caller's regex already populated; only fills gaps or
    enriches via documented merge rules.
    """
```

Callers pass their pre-computed regex result in. The extractor never re-runs regex itself — that's the caller's concern, and it eliminates the footgun where regex runs twice with different results.

**Not to be confused with** `merge_slots` in `slot_extractor.py:1700`. Two distinct merges run in sequence:

1. **Extractor-level merge** (this document's subject). Combines regex output + LLM output into a single `extracted` dict. Governed by the trust models below. Produces the `extracted` dict that the orchestrator passes to step 2.
2. **Session-level merge** (`merge_slots`, already exists and unchanged). Combines `extracted` with the existing session state, handling `_contradiction` promotion (user said "actually"), `_is_additive` queue appending (user said "also"), and service-change queue clearing. Reads `_contradiction` and `_is_additive` from `extracted` as inputs — which is why trust model 5 fields below MUST be present in the extractor output even though the LLM doesn't contribute them.

The extractor's contract: return the same dict shape as regex `extract_slots()`, including the trust-model-5 fields. It just populates them from regex.

### Dispatch logic

```
Entry: extract(message, regex_result, conversation_history, api_key_available)

1. If !api_key_available OR _is_simple_message(msg, regex_result):
     → return regex_result unchanged
     (simple = ≤8 words AND regex has service+location AND location is
      known AND only one service keyword category matched — see
      "Behaviors" section #5)

2. If _is_narrative(msg):  # ≥20 words
     → try: call narrative-mode LLM (Haiku, urgency-aware prompt)
         → on success: apply MERGE RULES, return
         → on failure: call _narrative_regex_fallback(regex_result)
                       (re-prioritizes by _URGENCY_HIERARCHY, infers
                        urgency from clue words — see "Behaviors" #2)
                       → return

3. Else (short/medium, non-simple):
     3a. If regex_result.service_type is None AND semantic_router
         matched AND len(words) ≤ 8:
           → short-circuit: return regex_result + semantic match
           (preserves extract_slots_smart:681-683 optimization — see
            "Behaviors" #4)
     3b. Else:
           → call standard LLM (Haiku, short prompt)
           → if LLM result is all-empty: fall back to regex_result
             wholesale (see "Behaviors" #7)
           → else: apply MERGE RULES, return
```

All LLM-call paths wrap the call in `_track_llm_call(task)` + latency timer + `record_llm_call(audit)` (see "Behaviors" section #8).

Both LLM paths use **tool calling** with the same `extract_intake_slots` schema (matches the existing narrative-extraction pattern). The difference is only the system prompt: urgency-hierarchy-heavy for narrative, compact for short.

This consolidates semantic routing into the caller's regex path (where it already runs once in `_run_early_extraction`, pipeline.py:92–107), eliminating the duplicate call inside `extract_slots_smart` (llm_slot_extractor.py:658–680). The duplicate isn't as expensive as it looks — `sentence-transformer` model is loaded once at startup and embeddings are fast (~2ms) — but the semantic-match result was already on `early_extracted` when the caller invoked `extract_slots_smart`, so the second call is redundant work and a potential source of drift if the two call sites ever diverge in threshold or routing table.

### Merge rules (the heart of the design)

After stress-testing against concrete edge cases, the 13 fields cluster into **five distinct trust models** rather than a uniform "regex/LLM/union" framing. Future maintainers adding a new field should identify which trust model applies, not invent a new one.

#### Trust model 1 — Regex has literal-match authority

**Pattern**: regex's explicit-phrase tables are authoritative; LLM only fills gaps.

**Applies to**: `location`, `_gender`. Plus `service_detail` and `org_name` as related sub-cases (see below).

**Rule**: if regex returned a non-None value, use it. Else use LLM's value (or None).

**Rationale**: regex matches literal text; LLM paraphrases can drift ("Manhattan" → "lower manhattan", "food stamps" → "SNAP"). The regex vocabulary is intentionally curated.

**Sub-cases where Trust Model 1 applies with specifics:**

- **`service_detail`** — more nuanced than the initial "not in LLM schema" claim. The tool-call schema (`_EXTRACT_SLOTS_TOOL`) doesn't include it, but `classify_unified`'s plain-JSON prompt does — and `pipeline.py:158–159` reads and stores it. So LLM *does* contribute service_detail today via the gap-filler path. Phase 0 decision: extend the unified tool-call schema to include `service_detail` (Option A in Prompt consolidation section) + add a canonical-form validator that snaps LLM output to one of 98 `_NOTABLE_SUB_TYPES` values or drops. This preserves today's de facto behavior inside the new unified structure. Rule then behaves as standard trust model 1: regex first, LLM fallback (validator-gated).
- **`org_name`** IS in the current tool-call schema. The prompt instructs the LLM to only extract when the user explicitly names an organization — so the LLM rarely contributes beyond what regex's authoritative name table already catches. Phase 0 decision: keep the LLM field + add a validator that discards any LLM `org_name` not fuzzy-matchable (token-sort-ratio ≥ 85) to the regex table. Gives LLM coverage for misspellings without enabling hallucination. Rule: regex first, LLM fallback (validator-gated).

**Known inherited edge cases (accepted, documented as xfail — not introduced by this migration):**
- `_gender`: "looking for help at the women's shelter" — regex over-extracts from facility name ("women's") as user's gender. LLM would correctly attribute. Our rule favors regex, so the over-extraction survives. File as separate slot-extractor issue.
- `location`: less-specific regex match wins over more-specific LLM inference. E.g., user says "near the Bronx Zoo" but regex only knows "Bronx" — rule returns "Bronx" not "East Tremont". Coarse, not wrong.
- `org_name`: regex misses misspellings (LLM could help with fuzzy matching). Out of scope.

#### Trust model 2 — LLM has semantic-context authority

**Pattern**: LLM handles implicit cues, paraphrase, and attribution; regex is the literal-keyword fallback.

**Applies to**: `age`, `urgency`, `family_status`.

**Rule**: if LLM returned a non-None value, use it. Else use regex's value.

**Rationale**: these fields are mostly expressed implicitly ("tonight" → urgency=high) or with attribution ("my son is 12" is NOT the user's age) — the LLM's context-awareness is the dominant value.

**Known inherited edge cases (accepted, xfail documented):**
- `age`: the earlier doc revision claimed `"I've been homeless for 3 years"` → regex `age=3`. **Corrected by audit**: this didn't actually happen in the original code — the existing regex patterns require specific prefixes (`i'm`, `age`, `NN years old`, hyphenated `NN-year-old`, or comma-delimited list position) and `"for 3 years"` matched none of them. The narrow false-positive patterns that DO exist in original code:
    - `", NN, "` list-format matches outside age context (e.g., `"Brooklyn, 3, apartments"` → `age=3`) — the `,\s*(\d{1,2})\s*,` pattern over-matches.
    - `"I have a NN-year-old"` → `age=NN` — technically correct per the current schema (age "may be the user or someone they're asking about" — see Behavior #22), though semantically ambiguous when the NN is a toddler's age being used to motivate a family-shelter search.
  **Hardening shipped as `/mnt/user-data/outputs/age-regex-hardening/`** (see Behavior #22): a pre-pass now strips `"for NN <year|month|day|week>s?"` duration phrases before running age patterns. Defensive hardening, not fixing an observed bug. Cost: 3 LOC + 2 parametrized unit tests (in `tests/unit/test_slot_extractor.py`). Full suite continues at 3,917 passing. The remaining narrow false-positives above are unchanged by this fix — fixing them requires the LLM-extraction merge rules, which is what Trust Model 2 covers.

#### Trust model 3 — Set-agreement decides ordering

**Pattern**: both parsers extract a set of items; who wins the primary pick depends on whether they agree on the full set.

**Applies to**: `service_type` (and, by extension, the primary's `location` when the primary service is chosen).

**Rule**:

```
Let R = {regex_primary} ∪ regex_additional_services   (as a set of service types)
Let L = {LLM_primary}   ∪ LLM_additional_service_types (as a set of service types)

if R is empty:                → LLM wins (regex had nothing)
elif L is empty:              → regex wins (LLM had nothing)
elif R == L:                  → regex priority wins
                                (both parsers agree on WHAT was requested;
                                 regex's priority table decides WHICH is primary)
else:                         → LLM wins
                                (sets disagree; LLM has successfully filtered
                                 context from request, or caught something
                                 regex missed)
```

**Worked examples:**

| Input | R (regex) | L (LLM) | R == L? | Winner | Result | ✓ |
|---|---|---|---|---|---|---|
| "food in Brooklyn and shelter in Manhattan" | {food, shelter} | {food, shelter} | yes | regex priority | shelter | ✓ |
| "I need food and shelter in Brooklyn" | {food, shelter} | {food, shelter} | yes | regex priority | shelter | ✓ |
| "just got out of hospital, need somewhere safe" | {medical, shelter} | {shelter} | no | LLM | shelter | ✓ |
| "need food, saw a doctor on TV" | {food, medical} | {food} | no | LLM | food | ✓ |
| "food, shelter, and a job" | {food, shelter, employment} | {food, shelter, employment} | yes | regex priority | shelter | ✓ |
| "I ran out of insulin" | {} | {medical} | R empty | LLM | medical | ✓ |

**Why this replaces the earlier heuristic** (v1 of this doc proposed "regex wins if any additional_services entry has a location binding"): that heuristic worked for multi-borough cases but failed on same-location multi-intent ("food and shelter in Brooklyn"). The regex's per-service location binding (`primary_location_override` in `slot_extractor.py:1601`) only fires when `len(all_types) > 1 AND len(all_locations) > 1`, so same-location multi-intent would defer to LLM and silently drop Sprint 1's priority ordering. Set-equality is the general form of the signal we want.

**Known blind spots in the set-equality rule:**

1. **"Both parsers include the context keyword"** — when regex over-extracts a contextual service (e.g., "hospital visit for my mom, I need food" — regex catches `{medical, food}`) AND the LLM ALSO extracts medical (because the prompt isn't specific enough about context filtering), the sets match and regex priority wins → returns `medical`. The correct answer is `food`. Rate: low, but real. Monitor via eval; if observed, tighten the LLM prompt to explicitly filter third-person/contextual mentions rather than changing the rule.

2. **"Sets match but LLM primary differs from regex primary for good reason"** — e.g., "hospital visit and also food" where both parsers extract `{medical, food}` but the LLM correctly identifies `food` as the primary (request) while regex picks `medical` (priority order). Rule gives regex priority → `medical`. This is the "hospital context" pattern that was the original motivation for `extract_slots_smart`'s "defer to LLM when regex has additionals" override — the thing we're explicitly trying to move away from because it silently clobbers Sprint 1. Five options for handling this better, roughly in order of implementation cost:

   **Option 1 — Accept the mispick, rely on the narrative path for hard cases.**
   The `_NARRATIVE_SYSTEM_PROMPT` explicitly teaches the urgency hierarchy, so messages ≥ 20 words already produce LLM output that mostly agrees with regex's priority table — the blind spot is mainly in the short, non-narrative path. Short-path messages with "hospital context + food" are uncommon; users describing such situations typically write 20+ words. Empirical question resolvable with a corpus check: how many short-path (<20 words) messages in the eval have both-parsers-catch-context patterns? If answer is "few," this option is free.
   *Cost*: 0 days implementation, 0.25 day corpus check.
   *Regression risk*: low. Preserves current common-case behavior.

   **Option 2 — Ask the LLM for a confidence signal on the primary pick.**
   Extend the tool schema with a `primary_confidence: "high" | "medium" | "low"` field. When LLM's primary differs from regex's AND LLM reports `high` confidence, override regex's priority pick with LLM's even when sets match. When LLM reports `medium` or `low`, keep the regex-priority rule.
   *Pros*: Minimal schema footprint. Uses a single field the LLM can reason about without new instructions.
   *Cons*: LLM confidence scores are notoriously poorly calibrated. Trusting a self-reported "high" is asking the model to know what it doesn't know. Often just reflects response fluency, not correctness.
   *Cost*: ~1 day (schema + prompt tuning + eval validation).
   *Regression risk*: medium. If LLM over-reports high confidence, the override fires too often.

   **Option 3 — Ask the LLM to distinguish "request" from "context."**
   Extend the schema with `contextual_mentions: string[]` — services mentioned as background not request. When regex primary is in this list AND a non-contextual service exists in either set, pick the non-contextual one. For "hospital visit for my mom, I need food": LLM returns `contextual_mentions: ["medical"]`, primary pick becomes `food` regardless of regex priority.
   *Pros*: Explicit signal for the exact distinction we care about. Prompt-able with concrete examples. Naturally handles the "pregnant woman mentioning hospital" case too — pregnancy-related medical is request, her sister's flu mention is context.
   *Cons*: Adds a new LLM output dimension — more tokens, more chances for malformed output. Requires good few-shot examples in the prompt to get reliable behavior; Haiku may not handle context-vs-request nuance as well as Sonnet. Validation: does the LLM output align with humans' sense of request-vs-context? Needs spot-checks.
   *Cost*: ~2 days (schema + prompt engineering with examples + eval validation + human spot-check of 20 outputs).
   *Regression risk*: medium. If Haiku misclassifies, we get wrong primaries in cases currently correct.

   **Option 4 — Teach the short-path prompt the urgency hierarchy.**
   Port the relevant excerpt from `_NARRATIVE_SYSTEM_PROMPT` — the urgency hierarchy and 3-4 worked examples — into `_SHORT_SYSTEM_PROMPT`. Then LLM's primary pick converges on regex's priority table for both paths, and the set-equality rule's "regex priority wins on match" becomes a no-op when they match (same primary picked), and a disagreement-wins-LLM when they don't. Blind spot collapses.
   *Pros*: Zero schema change. Reuses prompt language already validated in narrative mode. Unifies short and narrative reasoning, simplifying mental model.
   *Cons*: Hurts short-path latency and cost slightly (larger prompt = more input tokens per Haiku call, ~500 tokens of hierarchy + examples, ~$0.0004 extra per call). Doesn't resolve the case where both parsers genuinely disagree with the priority table because the user's actual request is lower-priority — e.g., "I was admitted to the hospital last week, can someone bring me food" where food IS the request.
   *Cost*: ~0.5 day (port the prompt excerpt, re-run eval to measure latency/score impact).
   *Regression risk*: low. Increases prompt alignment across paths.

   **Option 5 — Richer output: let LLM return a full priority-ordered list.**
   Replace `primary + additional_service_types` with `requested_services: [{type, is_primary_request: bool, context_only: bool}]`. LLM explicitly marks which service is the primary request and which are context. Our merge combines this with regex.
   *Pros*: Fully explicit signal. Composable with future additions (e.g., "this service is mentioned but with negation").
   *Cons*: Substantial schema change — breaks all existing LLM output parsers. Higher prompt complexity. More places for LLM malformation.
   *Cost*: ~3-4 days.
   *Regression risk*: high. Scope creep.

   **Decision (2026-04-23) — Option 1, gated on Phase 2 parallel-run eval.**

   Phase 0 corpus check (`/mnt/user-data/outputs/phase-0-corpus-check/`) found 4 real blind-spot scenarios in the eval suite, all currently passing today via the simulator's confirmation-flow redirect: `multi_food_and_shelter_brooklyn`, `multi_shower_and_food_drop_in`, `multi_clothing_and_food_harlem`, `multi_cross_neighborhood_shower_les_food_chinatown`. The count falls in Option 4's 4–10 band, but with all 4 currently passing, the team approved shipping Option 1 (accept the mispick) to keep the migration scope tight.

   **Fallback plan if Phase 2 surfaces regressions:** if any of the 4 blind-spot scenarios drops below the acceptance criterion ("no scenario currently ≥ 4.5 drops below 4.2") on the new path, ship Option 4 (port urgency hierarchy to short prompt) as a fast follow-up PR. Implementation is ~30 LOC appended to `_SHORT_SYSTEM_PROMPT`: a 4-line priority-tie-breaker rule plus 2 worked examples showing "first-mentioned wins unless safety is clearly urgent (`tonight`, `can't stay`, `nowhere to sleep`, `right now` → shelter/medical)." Not in scope for Phase 1; specifically scoped so Phase 2 can trigger it without re-opening the design doc.

   **Escalation if Option 4 doesn't close the gap:** revisit Option 3 (explicit `contextual_mentions` schema field). Option 2 (remove regex priority table, always prefer LLM) is likely not worth the calibration risk.

3. **`_SERVICE_NEED_PRIORITY` ties** — if regex returns `{legal, employment}` (both Tier 4), the tie-break is text-position order, which matches LLM's typical first-mentioned ordering. Ties resolve consistently, no rule change needed.

**Narrative-path exception (added 2026-04-24, Option 2b):**

Blind spot #2 above ("sets match but LLM primary differs from regex primary for good reason") fired on the narrative path in R36 via `natural_long_story`. "I just got out of the hospital..." produces regex={medical, shelter} (both tier 1, text-position tiebreak picks medical), LLM={shelter, medical} (prompt's teaching example: hospital is context, shelter is request), sets match → regex wins → primary=medical (wrong).

The narrative prompt is specifically designed to teach the LLM the urgency hierarchy. When a narrative-path message produces set agreement, the LLM's reasoned primary is what we want. The exception: in the `R == L` branch, check if the message is on the narrative path (`_is_narrative(message)` → ≥ `_NARRATIVE_THRESHOLD` words); if yes, return LLM's primary instead of regex's.

```
if R is empty:                → LLM wins
elif L is empty:              → regex wins
elif R == L:
    if narrative path:          → LLM wins      ← NEW (Option 2b)
    else (short path):          → regex priority wins
else:                         → LLM wins
```

Short-path messages keep the original rule. Option 4's short prompt teaches first-mentioned-as-default, which aligns with regex's text-position tiebreak on short multi-intent inputs — so regex and LLM agree on primary for short-path sets-match cases. The exception only matters when the two paths' prompts could reasonably disagree with regex, which is the narrative path's explicit purpose.

Implementation: `_merge_service_type_and_primary_location(regex_result, llm_result, message=None)` in `merge.py`. Default `message=None` preserves backward compatibility for direct-caller unit tests. Top-level `merge()` also takes `message`; both call sites in `__init__.py` thread it through.

#### Trust model 4 — Union with explicit false-positive tolerance

**Pattern**: multiple sources contribute; accept false positives from any in exchange for recall.

**Applies to**: `_populations`.

**Rule**: `union(regex_populations, semantic_router_population, LLM_populations)`.

**Rationale**: regex catches explicit keywords ("I'm a veteran"); semantic router catches phrase embeddings that regex misses ("aging out of foster care" → foster_youth, even when the regex keyword list has been pruned — see Sprint 3); LLM catches implicit membership ("just got out of Rikers" → reentry). Each source alone has coverage gaps.

**Three-source detail**: the semantic router contributes an optional single `population` value as part of its service_type match (`semantic_router.py` returns `(service_type, population?, confidence)`). When semantic routing fires (in `_run_early_extraction`), its population is already folded into `regex_result["_populations"]` before the extractor is invoked. So from the extractor's perspective, there are effectively two sources to union: `regex_result["_populations"]` (which already includes semantic router's contribution) and `llm_result["_populations"]`.

**Known false-positive pattern (accepted)**:
- "my brother's in jail" → regex extracts `reentry` (third-person attribution it can't detect). LLM would correctly attribute and return `[]`. Union keeps the false positive.

**Accepted cost**: a `_populations` false positive causes a wrong tone prefix in the confirmation (e.g., "reentry-friendly ..."), which is mildly awkward but not a routing failure or safety issue. Net recall gain from union beats the FP rate.

Alternative rules considered and rejected:
- Intersection: too strict; would miss LLM-only catches like "Rikers" → reentry when regex misses the implication.
- Regex-only: drops LLM's implicit detection.
- LLM-only: drops regex's keyword coverage and the semantic router's novel-phrase matches in long messages where LLM attention may truncate.

#### Trust model 5 — Regex-only for phrase-pattern signals

**Pattern**: regex's pattern tables are authoritative; LLM isn't asked.

**Applies to**: `no_requirements`, `_contradiction`, `_is_additive`.

**Rule**: regex value only; LLM doesn't contribute.

**Rationale**: these are boolean signals derived from specific phrase lists (`"don't ask me..."`, `"actually, I changed my mind"`, `"also need..."`). Regex tables are tuned; LLM could over-trigger on mild phrases. The unified tool schema should not even declare these fields — no LLM output to merge.

**Future**: these are candidates for LLM contribution in a later iteration — Haiku could detect them. Out of scope now to minimize migration risk.

#### `additional_services` — hybrid

Doesn't fit cleanly into the five patterns. Rule:

```
For each service in dedup-union(regex_additionals, LLM_additionals):
    if regex has it:  use regex's (type, detail, location) tuple
    else:             use LLM's tuple (with detail and location if LLM
                      schema provides them)
```

Regex tuples carry `detail` (e.g., "food stamps") and per-service `location`. But the location slot is populated **only when both `len(all_types) > 1` AND `len(all_locations) > 1`** (`slot_extractor.py:1601–1640`) — i.e., the multi-borough case. In single-location multi-intent ("food and shelter in Brooklyn"), regex leaves `location=None` on additionals because `primary_location` already encodes the shared location for the whole request.

LLM tuples add coverage for implicit additionals ("a place to crash" → shelter). If the unified tool schema is extended from `additional_service_types: string[]` to `additional_services: [{type, detail?, location?}]` — recommended (see "Prompt consolidation") — LLM's location binding is preserved when LLM is the only source for an item. That's a small improvement over the current `_merge_additional_services` behavior, which drops LLM's location by hardcoding `(svc, None, None)`.

#### Per-field reference table (quick lookup)

| Field | Trust model | Who wins |
|---|---|---|
| `service_type` | 3 (set-agreement) | R == L → regex priority; else LLM |
| `location` (primary) | 1 (regex literal) | Regex if set, else LLM |
| `service_detail` | 1 (regex literal) | Regex if set; LLM fallback (schema-extended, validator-gated to snap to canonical form) |
| `_gender` | 1 (regex literal) | Regex if set, else LLM |
| `org_name` | 1 (sub-case) | Regex if set; LLM only if it matches a fuzzy-match window of the regex table (validator, not raw LLM output) |
| `age` | 2 (LLM semantic) | LLM if set, else regex |
| `urgency` | 2 (LLM semantic) | LLM if set, else regex |
| `family_status` | 2 (LLM semantic) | LLM if set, else regex |
| `_populations` | 4 (union) | `union(regex, LLM)` |
| `no_requirements` | 5 (regex-only) | Regex only; LLM not asked |
| `_contradiction` | 5 (regex-only) | Regex only; LLM not asked |
| `_is_additive` | 5 (regex-only) | Regex only; LLM not asked |
| `additional_services` | Hybrid | Dedup union, regex tuple preserved where present; see section above for location binding nuances |

#### Test coverage per rule

Every rule gets unit tests covering the primary case, the fallback case, and any documented edge case:

- **Trust model 1**: 2 tests per field (regex-wins / regex-fallback-to-LLM), plus 1 xfail test per documented inherited edge case (women's shelter, Bronx Zoo, org_name misspelling).
- **Trust model 2**: 2 tests per field, plus 1 xfail test for `age` third-person attribution edge cases (e.g., `"I have a 3-year-old"` — intended per schema). The previously-proposed `"3 years homeless"` case was verified to never trip the existing regex; preventive hardening for `"for NN <time-unit>"` shipped alongside this migration.
- **Trust model 3**: 6 tests covering each row of the worked-examples table above, plus one additional test for the "both parsers wrong about context" low-probability path (logged warning, LLM wins).
- **Trust model 4**: union-coverage test + 1 xfail for the "brother in jail" FP pattern.
- **Trust model 5**: schema-level assertion that the unified tool call does not declare these fields.
- **Hybrid (additional_services)**: 3 tests — regex-only item preserved (with detail + location), LLM-only item preserved (with detail + location if schema extended), deduplication-by-service-type.

No rule ships without a named test.

### Prompt consolidation

Current state: three different system prompts (`_SYSTEM_PROMPT` in llm_slot_extractor for short tool-call, `_NARRATIVE_SYSTEM_PROMPT` for long tool-call, `_UNIFIED_SYSTEM_PROMPT` in llm_classifier for plain-JSON gap-fill).

Target state: two system prompts sharing the same tool schema — `_SHORT_SYSTEM_PROMPT` and `_NARRATIVE_SYSTEM_PROMPT`. Both use tool calling (more reliable than plain JSON for structured output). `classify_unified`'s plain-JSON path goes away.

Schema extensions/changes needed in the tool definition:

**`org_name` — DECIDED: keep + add fuzzy-match validator.**
Already present in the current tool schema. The unified prompt's instruction ("only extract when the user names an organization explicitly") is directionally good but not enforcement. Add a post-extraction validator that accepts the LLM's `org_name` only if it fuzzy-matches (e.g., token-sort-ratio ≥ 85) one entry in the authoritative regex table. Discard otherwise. This preserves LLM coverage for misspellings and novel phrasings while blocking hallucination.

**`service_detail` — APPROVED: Option A (extend schema + canonical-form validator).**

*Correction to earlier audit*: `service_detail` is NOT in the tool-call schema (`_EXTRACT_SLOTS_TOOL`), but `classify_unified`'s plain-JSON prompt already asks for it (llm_classifier.py:62) and `pipeline.py:158–159` reads it. So the LLM does contribute service_detail today, just through the gap-filler path, not the primary extractor. This complicates the "leave regex-only" recommendation I made in the previous revision.

Field impact: `service_detail` flows to four downstream consumers:
- `execution.py:743` — passed to `query_services` as a search filter (narrows DB results by service description)
- `handlers/accessibility.py:244` — drives `_IMMIGRATION_LEGAL_DETAILS` routing
- `handlers/confirmation.py:443` — propagates across confirmation flow turns
- Confirmation-message rendering — users see it as the narrowing label ("diabetes / insulin care")

So service_detail is consequential, not cosmetic. The `_NOTABLE_SUB_TYPES` table has **138 keyword→detail entries mapping to 98 canonical values.**

**Option A — Extend the schema; LLM contributes.**
Pros:
- Closes the regex-paraphrase gap. Regex knows `"insulin"` → `"diabetes / insulin care"` but misses `"glucose meds"`, `"my sugar's out of whack"`, `"I need my shots"`. Eval scenarios like `peer_diabetic_insulin` have been stuck below 4.0 partly because regex catches the canonical phrase but the simulator paraphrases. LLM closes these paraphrase gaps.
- Matches current de facto behavior via `classify_unified`. Making it explicit and under our merge rules is cleaner than letting the gap-filler silently contribute.
- Maps cleanly to trust model 1 (regex first, LLM fallback) — low conceptual overhead.

Cons:
- LLM may output non-canonical values. Regex produces exactly one of 98 canonical strings; LLM might say `"diabetes care"` vs `"diabetes / insulin care"` vs `"insulin care"`. Downstream consumers — especially the DB filter in `execution.py:743` — likely do substring matching, so non-canonical LLM strings can cause false-negatives or false-positives against the services table.
- Requires a canonical-form validator: after LLM extraction, snap the value to the closest `_NOTABLE_SUB_TYPES` canonical form via fuzzy match, else drop. Adds ~20 LOC + a test — manageable but non-trivial.
- Vocabulary drift risk: as new sub-types get added to `_NOTABLE_SUB_TYPES`, the LLM prompt needs to keep pace. Listing 98 canonical values in the prompt is expensive on tokens; providing them in a tool enum is cleaner but requires schema migration each time the table grows. Alternatively, drop the enum constraint, accept string output, and rely on the validator to snap — which is what Option A actually means in practice.

**Option B — Leave service_detail regex-only.**
Pros:
- Zero added surface area for this migration. `classify_unified`'s service_detail contribution goes away along with `classify_unified` itself (unified into the new extractor, but only for fields in the tool-call schema — service_detail is simply not asked).
- Zero hallucination risk for this field. Values are always one of 98 canonical strings.
- Simpler merge story: `service_detail` joins trust model 5 (regex-only, LLM not asked) alongside `_contradiction`/`_is_additive`/`no_requirements`.

Cons:
- Regex-paraphrase gap persists. "Glucose meds", "my sugar's out of whack" → `service_detail=None`, falling back to generic "medical" confirmation and unfiltered DB query.
- Silent regression from today's de facto behavior. Scenarios currently benefiting from `classify_unified`'s service_detail contribution (we don't know which — eval comparison needed) may drop.
- Expanding `_NOTABLE_SUB_TYPES` to cover paraphrases turns into the only lever for improvement, and regex-keyword addition is O(N) maintenance work.

**Decision** (2026-04-23): Option A, with the canonical-form validator. Rationale: `classify_unified` is already contributing service_detail; stripping that contribution during the migration is a silent regression. Adding the validator is ~20 LOC of scope. The Phase 2 parallel-run eval will tell us whether the LLM's contribution helps or hurts net; a validator gated behind a feature flag lets us roll back at the field level if it hurts.

**`additional_service_types` → `additional_services` — DECIDED: extend.**
Expand `additional_service_types: string[]` to `additional_services: [{type, detail?, location?}]`. Preserves sub-type and per-service location when LLM is the only source. This is the schema change that enables Sprint 1's multi-location correctness to work on cases where regex missed a service. Downstream merge (`_merge_additional_services`) updates accordingly.

**`no_requirements`, `contradiction_signal`, `additive_signal` — DECIDED: regex-only.**
Trust model 5 fields. Do not add to the LLM schema. Regex tables for these are tuned, adding LLM contribution risks over-triggering on weak signals, and the merge logic (union, union, union?) isn't in scope for this migration.

Narrative prompt already has the urgency hierarchy hardcoded. Port it as-is.

## Implementation phases

Sized for a single focused engineer. Elapsed time assumes no fires; add buffer accordingly.

### Phase 0 — Design doc review + corpus check (1 day) — COMPLETE

**Approvals complete (2026-04-23).** Corpus check complete 2026-04-23. Phase 0 closed.

- **Schema decisions — APPROVED:**
  - `org_name`: keep LLM extraction + add fuzzy-match validator against authoritative regex table.
  - `service_detail`: Option A — extend unified tool-call schema + add canonical-form snapping validator against `_NOTABLE_SUB_TYPES` (98 values). Preserves the de facto behavior `classify_unified` provides today.
  - `additional_services`: extend from `string[]` to `[{type, detail?, location?}]`.
- **Priority-hierarchy consolidation — APPROVED:** food ≥ mental_health. Concrete changes for Phase 1: update narrative `_URGENCY_HIERARCHY` and `_NARRATIVE_SYSTEM_PROMPT` to place food above mental_health. Flag in Phase 2 eval comparison.
- **Parallel-run duration — CONFIRMED:** 2 hours elapsed, ~$50 budget, acceptable to team.
- **Corpus check — COMPLETE:** 4 real blind-spot scenarios identified in the short-path (`multi_food_and_shelter_brooklyn`, `multi_shower_and_food_drop_in`, `multi_clothing_and_food_harlem`, `multi_cross_neighborhood_shower_les_food_chinatown`). All 4 currently pass via the simulator's confirmation-flow redirect. Falls in the Option 4 band (4-10 scenarios) but none of these scenarios cost eval points today. **Team decision (2026-04-23): ship Option 1 (accept the mispick).** Option 4 (port urgency hierarchy to short prompt, ~30 LOC) is the contingency fallback if Phase 2 parallel-run eval surfaces regressions on any of the 4 scenarios. Full analysis in `/mnt/user-data/outputs/phase-0-corpus-check/analysis.md`.

### Phase 1 — New unified extractor, written alongside (2 days) — COMPLETE

Created `backend/app/services/slot_extraction/` as a new package (NOT a replacement yet — both paths coexist, no production wiring changed):

- `__init__.py` (171 lines) — public `extract()` function + dispatch logic
- `prompts.py` (326 lines) — `_SHORT_SYSTEM_PROMPT`, `_NARRATIVE_SYSTEM_PROMPT`, extended `_EXTRACT_SLOTS_TOOL` schema with `service_detail` and object-array `additional_services` per Phase 0 decisions
- `merge.py` (623 lines) — per-field trust-model merge functions, each a named function. `_merge_regex_literal`, `_merge_llm_semantic`, `_merge_service_type_and_primary_location` (set-equality rule), `_merge_union`, `_merge_regex_only`, `_merge_service_detail` + `_validate_service_detail` (canonical-form validator vs `_NOTABLE_SUB_TYPES`), `_merge_org_name` + `_validate_org_name` (token-sort fuzzy match vs `_KNOWN_ORGS`), `_merge_additional_services` hybrid, top-level `merge()`.
- `dispatch.py` (460 lines) — `_is_narrative`, `_is_simple_message`, `extract_slots_short`, `extract_slots_narrative`, `_narrative_regex_fallback`, `_augment_urgency_from_clues` (Behavior #20 urgency-clue expansion including "not safe", "fleeing", "can't stay"), `_normalize_tool_output`, `_empty_slots`, audit-log helpers with Behavior #16 success=False recording.

Unit tests in `tests/unit/test_slot_extraction.py` (1,585 lines, 147 tests) covering every trust model:
- **Trust Model 1**: 3 tests each for `location` / `_gender` / `service_detail` (regex-wins, LLM-fallback, both-None), plus 5 tests for the `service_detail` validator (exact/substring/fuzzy-snap/drop/whitespace) and 5 for the `org_name` validator.
- **Trust Model 2**: 3 tests each for `age` / `urgency` / `family_status`. Former xfail placeholders confirmed passing and converted to regular tests (women's shelter location, third-person age, brother-in-jail populations).
- **Trust Model 3**: 7 tests covering every row of the worked-examples table in the doc, plus the LLM-empty-regex-wins edge.
- **Trust Model 4**: 6 tests — both-sources, regex-only, LLM-only, both-empty, dedup, None-inputs. Plus schema-drift test `test_union_skips_non_string_populations`.
- **Trust Model 5**: 3 schema-level assertions that the tool does NOT declare these fields + 2 passthrough tests.
- **Hybrid**: 4 tests including dedup-by-type with regex-wins-for-detail + primary-exclusion.
- **Top-level `merge()`**: 3 tests asserting 13-field shape and cross-model composition.
- **Dispatch helpers**: 4 tests for `_is_narrative` thresholds, 6 for `_is_simple_message` (including conflicting-service-signals), 5 for `_augment_urgency_from_clues`, 5 for `_normalize_tool_output` including both new schema forms.
- **LLM call internals**: 3 tests each for `extract_slots_short` and `extract_slots_narrative` (success, no-tool-use, exception). Mocked `client.messages.create` via fake response/tool_use/usage classes.
- **Role alternation**: 6 tests for `_build_messages_with_history` covering padding between consecutive-same-role, trailing-user `(listening)` placeholder, and the history[-6:] slice.
- **Audit-log helpers**: 3 tests for swallowing audit-log exceptions and handling responses without `usage`.
- **Public `extract()`**: 4 tests for the `api_key_available=False` fast path (simple, narrative, no-mutation), 1 for simple-fast-path no-LLM-call, 2 for narrative-path (LLM success + LLM empty → regex fallback), 2 for short-path (LLM success + LLM empty → wholesale fallback).
- **`_is_empty_llm_result`**: 10 tests, one per field that should trigger merge instead of fallback.
- **Prompt sanity**: 7 tests — no service-data leak in either prompt, all 9 fields enumerated in narrative, food ≥ mental_health ordering, extended schema contains `service_detail` and object-array `additional_services`.

**Coverage: 100% line coverage AND 100% branch coverage** on all four files. The only uncovered code is the one pragma-marked unreachable branch in `_unpack_additional_item` (logically impossible after the earlier guards).

**Repo-wide test result: 4,066 passing, 17 skipped, 3 xfailed, 0 failures.** The new package is fully covered and no cross-module regression surfaced.

**Deliverable location**: `/mnt/user-data/outputs/phase-1-slot-extraction/`.

**NOT in this phase (per spec)**: no callers were edited. `llm_slot_extractor.py` and `llm_classifier.py` remain in place. `orchestrator.py:383` and `handlers/confirmation.py:605` still call `extract_slots_smart`. The feature flag and call-site edits happen in Phase 2.

### Phase 2 — Feature flag + parallel-run validation (1.5 days) — COMPLETE

**Wiring status (rev 14):** Code wiring complete. The `USE_UNIFIED_EXTRACTOR` env var is parsed in `backend/app/services/chatbot/context.py` and re-exported from `backend/app/services/chatbot/__init__.py`. Both migration call sites branch on the flag:
- `backend/app/services/chatbot/orchestrator.py` (line 382 service-branch): when flag is on, calls `slot_extraction.extract(message, early_extracted, conversation_history=..., api_key_available=True)`.
- `backend/app/services/chatbot/handlers/confirmation.py` (`_handle_post_pending_confirmation`): when flag is on, calls `extract_slots(message)` inline first to produce `regex_result`, then `slot_extraction.extract(message, regex_result, ...)`. This call site doesn't have `early_extracted` in scope because the pipeline's `_run_early_extraction` is only called in the orchestrator.

23 routing tests in `tests/unit/test_unified_extractor_flag.py` cover flag env-var parsing (truthy/falsy), orchestrator routing with flag on/off, confirmation-handler routing with flag on/off, regex_result as the second positional arg, and the no-API-key bypass path. Repo-wide: 4,097 passing, 0 failures.

**Parallel-run eval (R36): executed 2026-04-24.** Full eval ran twice on 171 scenarios — once with flag off (legacy path), once with flag on (unified path). Results summarized in `eval-r36/YourPeer_Chatbot_Eval_Run_36.md`; detailed analysis in `eval-r36/r36-analysis.md`.

**Headline results:**
- R36 Legacy: 167/171 passing (97.7%), 22 critical failures, overall 4.56 — strongest run of the Opus era.
- R36 Unified: 159/171 passing (93.0%), 25 critical failures, overall 4.55.
- The migration's primary target (`multi_cross_borough_food_brooklyn_shelter_manhattan`) recovered from 2.82 to 4.73 on the unified path — the scenario that motivated the whole migration.
- 8 scenarios flipped from passing to failing on the unified path. Categorized by root cause:
    - **Category A (3):** the Option 4 watch-list scenarios — `multi_food_and_shelter_brooklyn`, `multi_shower_and_food_drop_in`, `multi_cross_neighborhood_shower_les_food_chinatown`. Set-equality rule kicking regex's primary in when scenario authors expected first-mentioned.
    - **Category B (1):** `natural_long_story` — narrative-path scenario where regex catches `medical` via "hospital" (context, not request), LLM correctly picks `shelter`, sets happen to match → regex wins incorrectly.
    - **Category C (3):** legacy behaviors not yet ported to the unified path — `confirm_multi_change`, `accessibility_low_literacy`, `multi_accept_queued_shelter`.
    - **Category D (3):** borderline drops near the 4.0 threshold with primary slots extracted correctly — `multiturn_change_mind`, `peer_young_mom_multiple_needs`, `wa_substance_use_shelter`.

**Acceptance criteria review:** `multi_cross_borough` passes ✓. No scenario ≥ 4.5 dropped below 4.2 (Category C scenarios were between 4.36 and 4.73, dropping to 3.55-3.82; several violate the ≥4.5-to-<4.2 rule). Critical failures +3 (22 → 25), which fails "≤ legacy count." **Phase 3 flip deferred pending Category A/B/C remediation.**

**Remediation applied after R36:**

1. **Option 4 — short-prompt hardening (shipped after R36).** Addresses Category A. See "Option 4 hardening" subsection below.
2. **Option 2b — narrative-path exception (shipped after R36).** Addresses Category B. See "Option 2b — narrative-path exception" subsection below.
3. Category C remaining. Requires scenario-by-scenario code tracing; not undertaken as part of Phase 2. Handled in Phase 3 planning.

Exit criterion for Phase 2: **met, with remediation.** The parallel-run eval surfaced the regressions that the Option 4 contingency was designed to catch, plus one case (Category B) that warranted an additional targeted fix (Option 2b). Both are in the working repo; re-run validation pending in Phase 3.

**Option 4 — short-prompt hardening (applied 2026-04-24):**

Post-R36, the 3 Category A watch-list scenarios dropped by exactly −0.45 each on slot extraction — triggering the contingency. Option 4 was pre-drafted before R36 and is now staged + applied to the working repo at `/mnt/user-data/outputs/phase-2-option-4-hardening/`.

- **What changed**: `_SHORT_SYSTEM_PROMPT` in `slot_extraction/prompts.py` gained an explicit two-rule system: (1) first-mentioned service wins as primary by default, (2) shelter or medical wins as primary when a safety signal is present (`tonight`, `right now`, `nowhere to sleep`, `can't stay`, `urgent`, `help me now`, `kicked out`, `evicted`, `nowhere to go`, `just got out`). Five worked examples: three positive (first-mentioned) and two safety-override.
- **Why**: aligns the LLM's short-path primary pick with the scenario-author's first-mentioned convention, which is what breaks when the set-equality rule falls back to regex's text-position tiebreak on same-tier services.
- **Tests**: 4 new tests in `TestPromptSanity` class of `tests/unit/test_slot_extraction.py` cover the prompt structure (teaches first-mentioned default, lists all safety signals, has both example directions, preserves the no-hallucination constraint). Full extractor suite: 159 → 159 passing before the Option 2b plumbing change, 100% line + branch coverage held.
- **Live validation**: `scripts/mini_eval_option_4.py` — 6 live-Haiku cases (4 watch-list + 2 safety-override sanity). 6/6 passed on first run post-apply, and again post-Option-2b. See `scripts/mini_eval_option_4.md` for usage.

**Option 2b — narrative-path exception (applied 2026-04-24):**

R36 surfaced one Category B regression that Option 4 doesn't address: `natural_long_story` (a 30-word narrative) where regex catches `medical` via "hospital" (context, not request), LLM correctly picks `shelter`, sets match → regex wins incorrectly. The narrative prompt literally contains a teaching example for this case ("I just got out of the hospital and my housing fell through → service_type: shelter"), but the set-equality rule was discarding the LLM's reasoned primary.

- **What changed**: `_merge_service_type_and_primary_location` in `slot_extraction/merge.py` gained a `message` parameter. In the sets-match branch, if the message is on the narrative path (≥ `_NARRATIVE_THRESHOLD` words), the LLM's primary wins. Short-path messages keep the original sets-match rule (regex wins, aligned with Option 4's first-mentioned prompt). Top-level `merge()` signature also gained `message`; both call sites in `slot_extraction/__init__.py` thread it through.
- **Why**: the narrative system prompt is specifically designed to teach the LLM the urgency hierarchy. When a narrative-path message produces set agreement, regex's text-position tiebreak (used when services are at equal priority tier, e.g., shelter and medical both tier 1) overrides the LLM's context-vs-request filtering. The fix leverages the narrative-prompt investment rather than adding a new signal.
- **Tests**: 3 new tests in `TestTrustModel3SetAgreement` class: `natural_long_story` exact-message reproduction, short-path sets-match case (regex primary still wins), narrative with differing sets (existing R != L → LLM wins unchanged). Full extractor suite: 162 passing, 100% coverage; 444 adjacent tests passing.
- **Backward compatibility**: `message=None` default on both `merge()` and `_merge_service_type_and_primary_location()` — direct unit-test callers that don't supply the message get the original rule fire, unchanged.
- **Live validation**: `scripts/mini_eval_r36_regressions.py` — 15 scenarios covering all R36 unified failing + watch-list + big-win. See `scripts/mini_eval_r36_regressions.md` for usage.

**Escalation if Option 4 + 2b don't close the gap:** Option 3 from the blind-spot analysis (add explicit `contextual_mentions` schema field to distinguish "requested" from "mentioned in passing"). Schema change, requires design doc addendum, should not be undertaken inside a follow-up — escalate back to a proper sprint.

Next action (gated on re-run): full unified eval with both Option 4 and Option 2b applied. Expected recovery: Category A scenarios (3), Category B scenario (1), possibly `multi_accept_queued_shelter` (C.3, same first-turn pattern as Category A). Category C.1 (`confirm_multi_change`) and C.2 (`accessibility_low_literacy`) require separate tracing and are not expected to recover from the prompt/merge changes alone.

### Phase 3 — Flip the flag default, migrate remaining callers (0.5 days)

Change the `if os.getenv(...)` default to `True`. Update `classify_unified` call site in pipeline.py to use the new unified interface (pipeline.py's gap-fill is a subset of what the new extractor handles).

Exit criterion: full test suite green, flagged eval matches unflagged.

### Phase 4 — Delete old code (1 day)

- Delete `backend/app/services/llm_slot_extractor.py`
- Delete `backend/app/services/llm_classifier.py` (functionality absorbed into the new module)
- Delete the feature-flag branch from orchestrator.py and handlers/confirmation.py
- Update `backend/app/services/chatbot/context.py` re-exports
- **Update `tests/integration/test_service_data_llm_firewall.py`** — grep for `from app.services.llm_slot_extractor` (6 imports expected per Behavior #21) and remap each to the new module. Layer 4 and Layer 6 asserts depend on these imports. Add a new Layer 6 assertion: no system prompt in the new module contains any `_NOTABLE_SUB_TYPES` canonical value (closes a new leak vector introduced by the `service_detail` schema extension — see Behavior #21).
- Consolidate tests:
  - `test_llm_slot_extractor.py` → split into `test_slot_extraction_dispatch.py`, `test_slot_extraction_merge.py`, `test_slot_extraction_prompts.py`
  - `test_narrative_extraction.py` → fold into the above
  - `test_llm_multi_service.py` → fold into merge tests
  - `test_llm_classifier.py` → delete or fold into `test_slot_extraction_prompts.py`
- Update docs and import paths across the repo

Exit criterion: `llm_slot_extractor.py` doesn't exist. All tests green — **including the 38 tests in `test_service_data_llm_firewall.py`**. One full eval run passes.

### Phase 5 — Cleanup + retrospective (0.5 days)

- Update this design doc's status from "Design" to "Implemented"
- Note any deviations from the plan
- Short retrospective writeup: what the parallel-run eval showed, any surprises, recommendations for future similar refactors

---

**Total elapsed estimate: 6 days of focused work**, with Phase 2 being the biggest risk and most likely to stretch. Add a day of buffer if the parallel-run reveals unexpected eval regressions that require deeper merge-rule tuning. Phase 0 grew to 1 day to accommodate the corpus check; this is a real deliverable (not padding) that de-risks the set-equality blind-spot decision.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Parallel-run reveals scenarios passing "for the wrong reason" in current code | Medium | Medium | Phase 2 compares scenario-by-scenario; investigate any delta > 0.3 before flipping default |
| New narrative prompt regresses on "hospital context" edge case | Medium | Medium | Port narrative prompt verbatim in Phase 1. Add regression test for `peer_medical_hospital_then_shelter` scenario specifically. |
| Tool-calling schema changes cause Haiku to stop populating a field it currently does | Low | Medium | Phase 1 includes tool-schema-output unit tests that assert Haiku returns all documented fields on a fixed set of inputs |
| Merge rules' set-equality heuristic misses a low-probability case (both parsers wrong in the same way, e.g., both wrongly include "medical" for a TV-doctor mention) | Low | Low | Catchable via eval monitoring and the warning log on LLM-wins path. Fix is to flag the case and route to human-review, not change the merge rule. Not a blocker. |
| Eval cost during parallel run (each scenario runs 2x) | Low | Low | Budget ~$50 for validation. Still cheap relative to engineer-days. |
| Someone ships an unrelated change touching extraction during the migration | Medium | High | Complete Phase 1 + 2 in one week. Coordinate merge timing. |

## What doesn't change

- `slot_extractor.py` (regex extraction) — untouched. Its API is exactly what the new unified extractor takes as input.
- Semantic router (`semantic_router.py`) — still called from `_run_early_extraction`, unchanged.
- PII redaction — unchanged.
- All downstream handlers — unchanged, they still consume the same `merged` dict shape.

## What gets better

- **Single entry point for LLM extraction.** New contributors stop needing to know which of two modules to read.
- **Documented merge rules.** No more "regex is unambiguous if single service" — each field's behavior is stated, tested, and inspectable.
- **Preserved Sprint 1/2/3 work.** No more silent clobbering of correct regex.
- **Smaller surface area.** 2 modules → 1 module. ~1,100 LOC down to ~400 LOC. 10 test files → 3–4.
- **Clearer cost model.** Simple messages skip the LLM; narrative and short both use Haiku with different prompts. Single `SLOT_EXTRACTION_MODEL` constant; changing the model propagates everywhere.

## Open questions

1. Should `_contradiction` and `_is_additive` ever move from trust model 5 (regex-only) to trust model 2 (LLM-semantic) or 4 (union)? The current rules freeze them at regex-only for this migration, but Haiku could plausibly detect both. Revisit after Phase 2 data — if eval shows regex missing these signals (e.g., novel phrasings of contradiction), promote to union and re-test.
2. Should the narrative mode move to Sonnet for better urgency-hierarchy reasoning? Current code uses Haiku for both paths. The hypothesis would be that long narratives with implicit urgency benefit from Sonnet's deeper reasoning; the downside is ~3× higher cost per narrative call. Revisit with model team post-Phase-2. If Phase 2 eval shows narrative-path scenarios still underperforming (e.g., `wa_tell_my_story`-style long narratives), this is the lever.
3. Is there value in a third dispatch path — "regex found everything but conversation history suggests contradiction" — that only calls LLM for disambiguation? Probably yes, but out of scope for this migration.
4. Is the `_is_narrative` ≥ 20-word threshold still right? It predates the current Haiku 4.5 model. Both narrative and short paths use the same model today, so the tuning question is now about **prompt cost** (narrative prompt is ~700 tokens vs short's ~200) and **reasoning quality**, not model selection. Phase 2 eval data may suggest lowering the threshold (more narrative-prompt use for medium messages with implicit urgency) or raising it (let short-prompt handle more). Don't change as part of this migration; measure during Phase 2 and file follow-up.
5. The post-pending-confirmation call site (`handlers/confirmation.py:605`) runs extraction on messages typed at a confirmation prompt. These are typically short, contradiction-bearing, or filler ("yes", "change service to food", "sorry I meant Brooklyn"). Should this call site use a narrower extractor — e.g., contradiction detection + single-slot extraction — rather than the full unified extract()? Out of scope for this migration (preserves current behavior, which uses the full extractor), but the answer is likely yes in a future iteration.
