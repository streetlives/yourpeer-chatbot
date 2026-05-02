# Phase A-C aftermath — open items tracking

**Status as of:** Phase A, B, C complete and the audit follow-ups merged. This doc enumerates everything that remains: the Phase D handler-migration cleanup, deferred bugs and design decisions, audit-flagged smells we chose not to address, eval cluster fixes, and engineering tasks that came up during the migration.

Use stable IDs (e.g., `D-1`, `BUG-1`) when referencing items from PRs or commits.

---

## Quick status table

| ID | Item | Category | Effort | Priority |
| --- | --- | --- | --- | --- |
| `D-1` | Migrate `_handle_spanish_detection` to `MessageContext` | Phase D | M | Low |
| `D-2` | MessageContext test fixture / builder | Phase D | S | Low |
| `D-3` | Tighten `MessageContext.merged` annotation | Phase D | S | Low |
| `D-4` | Investigate dropping `_response_tone` alias | Phase D | S | Low |
| `D-5` | Stage 2 snapshot pattern: helper accessors for `_last_action` / `_pending_confirmation` | Phase D | M | Low |
| `BUG-1` | `_persist_emotional_context_late` save-on-value-change | Bug | XS | ✅ Closed |
| `SUSPECT-1` | Tone prefix asymmetry between follow-up paths | UX question | XS | Low |
| `SMELL-2` | Underscore-prefixed locals in `generate_reply` | Style | M | Low |
| `SMELL-3` | Inline imports of `slot_extraction.extract` | Style | XS | ✅ Closed |
| `SMELL-5` | `if X: pass else:` structure in orchestrator | Style | XS | Low |
| `SMELL-8` | Mixed dispatch patterns | Style | M | Low |
| `SMELL-9` | `_empty_reply` doesn't log empty-message events | Audit gap | XS | Low |
| `UTIL-1` | Extend `text_normalize.py` with `normalize_contractions` + `strip_intensifiers` | Utility extraction | S | ✅ Closed |
| `UTIL-2` | Extract `format_time` to `utils/time_format.py` | Utility extraction | XS | ✅ Closed |
| `LLM-1` | Duplicate `slot_extraction.extract()` between `_run_llm_gate` and orchestrator service branch | LLM redundancy | M | Medium |
| `LLM-2` | `_handle_post_pending_confirmation` re-extracts; ignores pre-computed `ctx.early_extracted` | LLM redundancy | S | Medium |
| `LLM-3` | `classify_message_llm` fallback in `_compute_routing_category` likely redundant after `_run_llm_gate` | LLM redundancy | S | Low |
| `COMPAT-1` | Remove or simplify `_classify_message` backward-compat wrapper | Pre-launch cleanup | S | ✅ Closed |
| `COMPAT-2` | Drop `extraction_source=None` default in `slot_extraction.extract()` | Pre-launch cleanup | XS | ✅ Closed |
| `COMPAT-3` | Drop unused `message` parameter from `merge.py` set-overlap branches | Pre-launch cleanup | XS | ✅ Closed |
| `COMPAT-4` | Standardize on 3-tuple offers throughout the merge pipeline | Pre-launch cleanup | S | ✅ Closed |
| `TEST-GAP-1` | `_handle_demographic_skip` integration coverage | Test gap | S | Low |
| `TEST-GAP-2` | Snapshot-arg semantics not directly unit-tested | Test gap | S | Low |
| `EVAL-B` | Cluster B eval fixes (multi_cross_borough, multi_three_services_legal_benefits_food) | Eval | L | High |
| `EVAL-C` | Cluster C eval fix (wa_negative_preference) | Eval | M | Medium |
| `ENG-1` | Mobile-input fuzz harness (apostrophe substitutions) | Engineering | S | Medium |

**Effort key:** XS ≤ 30min · S ≤ 2hr · M ≤ 1day · L ≤ 1week.

---

## Phase D — handler migration cleanup

The four-phase MessageContext adoption (Phases A-C in `ORCHESTRATOR_AUDIT.md`) is complete except for these residual cleanup items. None of them block the migration's value; they would round out the consistency story.

### `D-1` — Migrate `_handle_spanish_detection` to `MessageContext`

**Status:** Open.

**Current signature:** `_handle_spanish_detection(session_id, message, redacted_message, existing, has_service_intent, tone, request_id)` at `accessibility.py:157`. Returns a `(result, acknowledgment)` tuple.

**Target signature:** `_handle_spanish_detection(ctx) -> Tuple[Optional[dict], str]`.

**Why deferred:** 4 direct unit-test invocations in `tests/unit/test_chatbot_extracted_helpers.py` use the positional form. Migration requires updating those test calls, which need a `MessageContext` to construct. See `D-2` below.

**Acceptance:** signature updated; test fixture from `D-2` used in unit tests; orchestrator call site updated; full test suite green.

### `D-2` — MessageContext test fixture / builder

**Status:** Open.

**Problem:** `MessageContext` has 18 fields (post-cleanup), several required without defaults. Constructing one in a unit test is verbose. Tests that white-box-call handlers (rather than going through `generate_reply`) currently can't easily build a ctx.

**Options:**
- **(a) Pytest fixture `default_ctx(**overrides)`** — returns a MessageContext with sensible defaults, callers override only the fields they care about. Simplest and most idiomatic for pytest.
- **(b) `MessageContext.for_test(...)` classmethod** — same idea, on the class itself. Discoverable but couples production code to tests.

<!-- drift:ignore: possible future files that may not exist yet -->
**Recommendation:** (a) in `tests/conftest.py` or `tests/_fixtures/ctx.py`. About 15 lines.

**Acceptance:** fixture lands; existing unit tests for migrated handlers (e.g. `_handle_negative_preference`, `_handle_correction`, `_handle_demographic_skip`) optionally rewritten to use it; `D-1` blocked on this.

### `D-3` — Tighten `MessageContext.merged` annotation

**Status:** Open.

**Current:** `merged: Optional[dict] = None` (set late, after `merge_slots` in service flow).

**Concern:** A handler that reads `ctx.merged` before the late-set runs would crash. Currently defended only by docstring ("this handler runs at the end of `generate_reply` so merged is always populated by then"). The only current reader is `_handle_general_conversation` in `general.py:66`, which runs at the end and is safe.

**Investigation needed:**
- Audit the orchestrator's late-set sequence: is there any code path where a ctx-using handler could fire AFTER ctx construction but BEFORE the `ctx.merged = merged` assignment at orchestrator:515?
- If no such path exists, `merged` could be required (no `Optional`, no default) and constructed-with-merged at the assignment point. But that breaks the dataclass-construction-once pattern.
- Alternative: keep optional but raise `RuntimeError` early in any handler that reads it before it's set, or assert via `assert ctx.merged is not None` at handler entry.

**Acceptance:** decision documented in either context.py docstring or this tracker; chosen approach implemented.

### `D-4` — Investigate dropping `_response_tone` alias

**Status:** Open investigation.

**Current state:** `_response_tone = tone` at orchestrator:183, used in 3 places:
- `_compute_tone_prefix(response_tone=_response_tone, ...)` at orchestrator:332 (early prefix)
- `_handle_post_pending_confirmation(ctx, _response_tone)` at orchestrator:451
- `_compute_tone_prefix(response_tone=_response_tone, ...)` at orchestrator:537 (late prefix)

**Why it exists:** `tone` is reassigned to `"frustrated"` in the negative_preference + service-promotion path (orchestrator:376-377: `if tone is None: tone = "frustrated"`). `_response_tone` preserves the pre-promotion value so tone-matched response text reflects what the user originally expressed, not the promoted classification.

**Question:** Could the three readers tolerate seeing the post-promotion `tone` instead?
- Tone prefix computation: probably NOT — would change baseline-warmth selection in negative_preference flows.
- `_handle_post_pending_confirmation`: uses for `nudge_prefix` selection (`if response_tone == "emotional": ...`). If tone was None pre-promotion and "frustrated" post-promotion, the post-promotion path would pick the frustrated prefix on a user whose original message wasn't frustrated. Likely unwanted.

**Recommendation:** Keep the alias. Document why (pre-promotion preservation) more clearly at the assignment site if it isn't already. This investigation can probably be closed as "alias is intentional" with a comment update.

**Acceptance:** comment at orchestrator:183 explicitly documents the pre-promotion preservation rationale; this tracker item closed as "intentional, not dropping".

### `D-5` — Stage 2 cleanup: helper accessors for snapshot args

**Status:** Open.

**Current pattern:** orchestrator captures snapshot values inline before calling dispatchers:
```python
last_action = existing.get("_last_action")
context_result = _handle_context_aware_confirm(ctx, last_action)
# ...
_consume_last_action(session_id, existing, last_action)
```

This pattern occurs three times (`_last_action`, `_pending_confirmation`, `_response_tone`). Each is a "capture before, pass to handler, consume after" cycle.

**Proposed cleanup:** Extract helpers that capture-and-pass in one step, reducing the orchestrator's exposure to the snapshot mechanic:
```python
def with_snapshotted_last_action(ctx, handler):
    """Capture _last_action, run handler, return (result, snapshot)."""
    snap = ctx.existing.get("_last_action")
    return handler(ctx, snap), snap
```

**Concern:** This adds an indirection that may obscure more than it clarifies. The current pattern is verbose but explicit; readers can see exactly when capture happens. Stage 2 cleanup is "not urgent".

**Acceptance:** decide whether to do this cleanup at all. If yes, helpers in `session_helpers.py` with tests. If no, close this item with a note.

---

## Documented bugs (deferred with explicit tracking)

### `BUG-1` — `_persist_emotional_context_late` save-on-value-change

**Status:** ✅ **Closed** (fixed in BUG-1 PR).

**Description:** When the early `_compute_tone_prefix` set `_emotional_context = "shame"` and the late call wanted `"frustrated"`, the in-memory dict updated but the save condition (`merged.get(...) and not existing.get(...)`) evaluated `True and not "shame"` → False, so the change wasn't persisted. Next turn would load `"shame"` from session despite the late computation.

**Fix landed:** condition rewritten to guard truthiness AND check inequality:
```python
new_value = merged.get("_emotional_context")
if new_value and new_value != existing.get("_emotional_context"):
    save_session_slots(session_id, merged)
```

The `new_value and ...` truthiness guard is preserved (not just a naked `!=`) to protect against an artificially-empty `merged` dict treating "value cleared" as a save trigger and nuking session state. In real usage `merged` is always the full output of `merge_slots(existing, extracted)`, but the guard is defensive against the unit-test shape and a robustness improvement.

**Test changes:**
- `test_late_persist_does_not_save_on_value_to_value_change` renamed to `test_late_persist_saves_on_value_to_value_change` with inverted assertion (`len(save_recorder) == 1`) plus content check that the persisted value is the late-computed `"frustrated"`.
- New `test_save_does_not_fire_when_merged_value_falsy` pins the truthiness guard.
- New `test_save_does_not_fire_when_value_unchanged` pins the inequality guard.

**Cleanup:**
- Module docstring in `test_session_helpers.py` updated (Bug 1 / PR-γ references removed).
- Inline comment at `orchestrator.py:547` updated to describe the now-correct semantics.
- `_persist_emotional_context_late` docstring rewritten to describe the post-fix behavior.

**Verification:** 4306 tests pass (was 4304 pre-fix, +2 new regression tests). 17 skipped, 3 xfailed — same baseline.


### `SUSPECT-1` — Tone prefix asymmetry between follow-up paths

**Status:** Documented as a UX question, not a code bug.

**Description:** First follow-up path (`category == "service"`, fresh service flow) prepends `_tone_prefix` to the follow-up question. Second follow-up path (service-flow continuation: `has_new_slots and existing.service_type and not _pending_confirmation`) does NOT. No comment explaining the difference.

**Possible interpretations:**
- (a) Intentional — by turn 2+, the empathic acknowledgment from turn 1 has already landed; repeating would feel rote.
- (b) Accidental — copy-paste leftover from before tone prefixes existed.

**Source:** `ORCHESTRATOR_AUDIT.md` — "Suspect 1".

**Where documented:** orchestrator.py:609-613, in the second follow-up block as an in-place comment noting the asymmetry is pre-existing and preserved by the refactor.

**Investigation needed:** review eval scenarios that reach turn 2+ in a single service flow with non-routine tone (e.g., `peer_pregnant_doctor_bronx`-style multi-turn). Compare the response text to the rubric's tone expectation.

**Acceptance:** decision recorded — either (a) document as intentional with rationale, or (b) add `_tone_prefix` to the second path with a regression test that exercises the multi-turn path.

---

## Audit-flagged smells (intentionally skipped per audit priority)

The audit explicitly flagged smells 5, 7, 8, 9 as "fix opportunistically, not worth a dedicated PR". Smell 7 was actually fixed during Phase A/B (helpers extracted to `session_helpers.py`). The remainder are listed here for completeness; closing them is optional and low-priority.

### `SMELL-2` — Underscore-prefixed locals in `generate_reply`

**Status:** Open. Partially addressed — many `_*` locals were eliminated when their values moved onto `ctx` during phases A-C, but some remain.

**Remaining cases:** `_action_pre`, `_extraction_source`, `_llm_tone`, `_crisis_result`, `_response_tone`, `_pii_warning`, `_confidence`, `_tone_prefix`, `_emotional_context_update`, `_is_service_flow`, `_geolocation_ready`, `_has_session_coords`, `_post_result`, `_spanish_acknowledgment`, `_spanish_result`, `_immigration_acknowledgment`, etc.

**Reason audit flagged:** Python's leading-underscore convention is for module-level "private to module" names. Using it on function locals has no standard meaning and adds noise.

**Reason it's still open:** Many of these names are 1:1 with ctx field names (e.g., `_response_tone` corresponds to `ctx.tone` at the snapshot moment). Renaming without dropping the alias entirely would just change the noise.

**Acceptance:** decide one of (a) drop all leading underscores, (b) keep as semantic marker for "pre-promotion / pre-merge / orchestrator-local snapshot", or (c) replace each with a more descriptive name (e.g., `pre_promotion_tone` instead of `_response_tone`). Whichever choice, do uniformly.

### `SMELL-3` — Inline imports of `slot_extraction.extract`

**Status:** ✅ **Closed** (in this PR).

**Locations (pre-fix):**
- `orchestrator.py:498` (inside the service-routing branch)
- `confirmation.py:734` (inside `_handle_post_pending_confirmation`)
- `pipeline.py:149` (inside `_run_llm_gate`) — third site found during the cleanup, not in the original audit.

All three did `from app.services.slot_extraction import extract as extract_unified` (or the module form) inside the function body.

**Resolution:** All three hoisted to module-level imports. The aliased form (`from ... import extract as extract_unified`) introduced a test-mocking footgun — `monkeypatch.setattr("app.services.slot_extraction.extract", ...)` patches the module attribute but the local binding in the importer is frozen at module load time. With inline imports, every call did a fresh attribute lookup so mocks worked. Switching to module imports (`from app.services import slot_extraction` then `slot_extraction.extract(...)`) preserves the lookup-at-call-time behavior AND makes the import top-of-file. Existing 8 test patches at `app.services.slot_extraction.extract` continue to work without changes.

**Verification:** 4326 tests pass.

### `SMELL-5` — `if X: pass else:` structure

**Status:** Open.

**Location:** orchestrator.py:
```python
if tone == "crisis":
    pass  # handled below in routing
else:
    # 45 lines of queue-accept and post-results checks
    ...
```

**Suggested fix:** flip to `if tone != "crisis":` or extract the body into a helper.

**Acceptance:** structure flipped or extracted; behavior unchanged.

### `SMELL-8` — Mixed dispatch patterns

**Status:** Open.

**Description:** Dispatch oscillates between two styles in the orchestrator:
- **Pattern A** — category-driven with immediate return (`if category == "reset": return _handle_reset(ctx)`)
- **Pattern B** — always-call with conditional return (`result = _handle_X(ctx); if result: return result`)

Pattern B fires for handlers whose firing conditions are too complex for a category check (`_handle_demographic_skip`, `_handle_location_unknown`, `_handle_context_aware_confirm`, `_handle_pending_confirmation`, `_handle_post_results_interaction`, `_handle_spanish_detection`, `_handle_post_pending_confirmation`).

**Suggested fix:** standardize on the walrus-operator pattern:
```python
if (result := _handle_demographic_skip(ctx)) is not None:
    return result
```

**Acceptance:** all conditional-return handlers in the orchestrator use the walrus pattern uniformly; readability improved.

### `SMELL-9` — `_empty_reply` doesn't log empty-message events

**Status:** Open.

**Description:** orchestrator.py:102 (empty-message guard) returns via `_empty_reply` without `_log_turn`. Every other return path logs.

**Fix options:**
- (a) Add `_log_turn(session_id, "", result, "empty_message", request_id=request_id, tone=None)` before the return.
- (b) Document the gap intentionally.

**Acceptance:** decision recorded; if (a), code added with test that verifies the log fires.

---

## Utility extraction candidates

Identified during a post-Phase-C cross-module audit for repeated patterns and private-helper imports across module boundaries. Same shape as the original `text_normalize.py` extraction (which fixed the curly-apostrophe duplication): pure functions reaching across the layering, ripe for promotion to `app/utils/`.

### `UTIL-1` — Extend `text_normalize.py` with `normalize_contractions` + `strip_intensifiers`

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Migrated to `app/utils/text_normalize.py` following the same pattern as the original `normalize_apostrophes` extraction.

- Moved `CONTRACTION_MAP`, `_CONTRACTION_PAIRS` (private), `INTENSIFIERS`, `_INTENSIFIER_RE` (private) from `phrase_lists.py` to `text_normalize.py`. Public-facing constants got the leading underscore dropped; compiled regex and length-sorted pairs list stayed private.
- Moved `_normalize_contractions` and `_strip_intensifiers` from `classifier.py` to `text_normalize.py` as public `normalize_contractions` and `strip_intensifiers`.
- `classifier.py` re-imports them under their existing private aliases (`from app.utils.text_normalize import normalize_contractions as _normalize_contractions, strip_intensifiers as _strip_intensifiers`) to keep call-site naming local — same pattern used for `_normalize_apostrophes` in `crisis_detector.py`.
- `responses.py` inline import dropped; module-level import added with the public names.
- Comment in `phrase_lists.py` that referenced `_normalize_contractions()` updated to point at `text_normalize.normalize_contractions()`.

**New tests:** 17 in `tests/unit/test_text_normalize.py` covering: contraction expansion (apostrophe and apostropheless variants), pronoun contractions, lowercasing-as-side-effect, longer-contractions-match-first ordering, intensifier stripping (single, multiple, word-boundary protection, case-insensitive), whitespace collapse, set/map content spot-checks, and a compose test that exercises the typical strip-then-normalize pipeline.

**Verification:** 4326 tests pass.

### `UTIL-2` — Extract `format_time` to `utils/time_format.py`

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Created `app/utils/time_format.py` exporting public `format_time(t)`. Function body unchanged — same `%I` zero-padded format with manual lstrip — so cross-platform behavior is identical. Updated both call sites:
- `app/rag/query_templates.py` — module-level `from app.utils.time_format import format_time`, two internal callers updated.
- `app/services/chatbot/handlers/post_results.py` — replaced `from app.rag.query_templates import _format_time` with `from app.utils.time_format import format_time`. Two call sites updated.

**Test changes:**
- New `tests/unit/test_time_format.py` with 9 tests: morning single-digit zero-strip (mutant kill on `lstrip("0")`), morning double-digit, afternoon PM marker, afternoon double-digit PM, midnight (12:00 AM edge case), noon, minute zero-padding, return type, and a smoke test over all 24 hours.
- Removed 6 duplicate `_format_time` tests from `tests/unit/test_query_templates.py` (superseded by the dedicated file). Left a comment pointing at the new location.

**Verification:** 4326 tests pass.

---

## LLM call redundancy

Identified during a post-Phase-C sweep of all LLM call sites. The system has 8 production LLM call paths across `claude_client.py`, `crisis_detector.py`, `slot_extraction/dispatch.py`, `post_results.py`, plus thin wrappers in `responses.py` and `meta.py`. Most are well-gated (regex tier first; LLM only on miss). Three exceptions found.

### `LLM-1` — Duplicate `slot_extraction.extract()` call between `_run_llm_gate` and orchestrator service branch

**Status:** Open. Highest-impact item in this category.

**Description:** When a user types a service request that regex misses (e.g., "I'm looking for somewhere safe to crash tonight in Brooklyn", >8 words), the LLM slot extractor is invoked twice on the same message within a single turn:

1. **First call:** `pipeline._run_llm_gate` → `slot_extraction.extract()` runs because `not has_service_intent and len(message.split()) >= 4`. The LLM finds `service_type="shelter"` and the gate mutates `early_extracted` in place to add it.
2. **Second call:** orchestrator service branch (`orchestrator.py:493`) → `slot_extraction.extract()` again. The branch is gated only on `_USE_LLM and category == "service"`. Inside `extract()`, `_is_simple_message` returns False for messages >8 words even when regex_result has full slots, so the LLM fires a second time.

The known-deferred comment at `slot_extraction/__init__.py:162-163` acknowledges the gap: *"If latency becomes a concern on these short messages, revisit; the parallel-run eval in Phase 2 will reveal whether skipping the LLM here costs score points."*

Also documented at `pipeline.py:159-160`: *"The gate condition guarantees `early_extracted.service_type is None`, so when the result has a service_type, the LLM contributed it."* — but the second call ignores that fact.

**Cost:** ~2x latency and ~2x API cost on every >8-word message that regex misses but the gate finds. This is the most common path for "narrative" service requests that aren't trivially short.

**Why it survived migration:** Phase 4 (April 2026) replaced `extract_slots_smart` with the unified `extract()`. The `_run_llm_gate` path was added separately for tone/action classification. Each path was correct in isolation; nobody noticed they fire on the same message in sequence.

**Fix options:**
- (a) **Cache the gate's result on `MessageContext`.** When `_run_llm_gate` invokes `slot_extraction.extract()` and gets back a result, store it on `ctx` (e.g., `ctx.unified_extraction`). The orchestrator service branch checks for the cached value and uses it instead of re-running. Cleanest, requires a new MessageContext field.
- (b) **Skip the second call when the gate already enriched.** Add a guard at `orchestrator.py:493` that returns `early_extracted` directly when `extraction_source == "llm_gate"`. Smaller change; correctness depends on the gate having extracted everything the second call would.
- (c) **Push the gate's call into the service branch.** Restructure so only one path invokes `extract()` per message. Larger refactor; clearer architecturally.

**Test coverage required:** new test that pins LLM call count = 1 for a missed-by-regex narrative service message. Mock the LLM client and assert `client.messages.create.call_count == 1` over the full `generate_reply` cycle.

**Acceptance:** Single LLM extraction call per message verified by test; eval scores match or exceed baseline; latency improvement measurable on the affected path.

### `LLM-2` — `_handle_post_pending_confirmation` re-extracts, ignoring pre-computed extraction

**Status:** Open.

**Description:** `_handle_post_pending_confirmation` (confirmation.py:734-741) runs its own slot extraction pipeline:

```python
from app.services.slot_extraction import extract as extract_unified
regex_result = extract_slots(ctx.message)
pending_extracted = extract_unified(
    ctx.message,
    regex_result,
    conversation_history=existing.get("transcript", []),
    api_key_available=True,
)
```

The orchestrator already ran:
- `_run_early_extraction` (regex + semantic) → `ctx.early_extracted` is populated
- `_run_llm_gate` may have run → `ctx.early_extracted` may be enriched

But this handler ignores `ctx.early_extracted` and re-runs both regex (`extract_slots`) and the unified LLM extractor. On a pending-confirmation continuation message, this is a 2nd LLM call (3rd if `LLM-1` also fires).

**Comment at L730-733** justifies the re-extraction: *"The unified extractor requires a `regex_result` parameter, so we run regex here first (cheap — the caller's `_run_early_extraction` isn't in scope at this post-pending path)."*

The justification is partially accurate — `_run_early_extraction` isn't in scope as a function call, but its output IS available via `ctx.early_extracted`. The handler could read from there instead.

**Fix:** replace lines 734-741 with:
```python
pending_extracted = ctx.early_extracted
```
... if the orchestrator's earlier extraction is sufficient for this handler's needs. Verify by tracing what fields `pending_has_new` and the downstream branches actually read.

**Caveat:** if the orchestrator's `_run_llm_gate` did NOT fire (e.g., because `has_service_intent` was already True, or the message was <4 words), `ctx.early_extracted` only has regex+semantic results. The handler may need LLM-quality extraction for the "user changed their mind during confirmation" path, where regex underfits. In that case the fix is to fire the LLM only when needed, not on every pending-confirmation turn.

**Test coverage required:** baseline test count of LLM calls on a pending-confirmation continuation message; post-fix test asserts the count dropped by 1.

**Acceptance:** No redundant slot extraction on pending-confirmation continuation; existing pending-confirmation tests still pass; eval clusters covering this path don't regress.

### `LLM-3` — `classify_message_llm` fallback likely redundant after `_run_llm_gate`

**Status:** Open. Lowest priority of the three.

**Description:** `pipeline._compute_routing_category` falls through to `classify_message_llm` (pipeline.py:267-275) when nothing else matched the message:

```python
elif _USE_LLM and len(message.strip().split()) > 3:
    from app.llm.claude_client import classify_message_llm
    llm_category = classify_message_llm(message)
```

But `_run_llm_gate` already ran on the same message earlier in `generate_reply` for tone/action classification. The unified gate uses Haiku with a richer prompt and a tool-use schema; it returns `tone` and `action` enums. If the gate returned `(None, None)` for both, that's a strong signal the message doesn't fit any classified category — and calling a SECOND LLM classifier on the same input is unlikely to succeed.

**The two classifiers have overlapping responsibilities:**
- `_run_llm_gate` (slot_extraction.extract): primary purpose is slot extraction; tone/action are advisory side outputs.
- `classify_message_llm`: dedicated message-category classifier; returns one of 17 valid category strings.

**Cost:** 1 extra LLM call per general-fallthrough message ≥4 words, which is the most common case for unrecognized chitchat.

**Fix options:**
- (a) **Remove `classify_message_llm` entirely.** Trust the gate's `action` classification as the routing signal; if both gate and regex returned None, fall to "general" with low confidence (which is what happens when `classify_message_llm` returns None anyway).
- (b) **Skip `classify_message_llm` when `_run_llm_gate` already ran and returned no action.** Pass a flag through the pipeline to indicate the gate fired.
- (c) **Keep both** and accept the cost — but document why both are needed if so.

**Why this is lowest priority:** the call only fires on the "general fallthrough" path, which is rarer than the service path. And if the cost matters, option (a) is a small change.

**Test coverage required:** verify that removing `classify_message_llm` doesn't regress any test; eval clusters covering the general/casual chat path don't regress.

**Acceptance:** decision recorded (remove vs keep); if remove, the pipeline.py:267-278 block deleted; full test suite green; no eval regression.

---

## Pre-launch backwards-compat cleanup

The codebase predates launch but has accumulated backwards-compatibility shims from internal API migrations (Phase 4 unified extractor migration, the `extract_slots_smart` removal, etc.). Since there are no external API consumers yet, these shims can be cleaned up directly without a deprecation cycle. Items below are ranked by signal-to-noise — each removes a shim that doesn't serve a purpose anymore.

**Items considered and rejected:**
- `query_executor.py:553` (`"housing_assistance": "other"` redirect) — defensive guard against deserialization of stale session data; pinned by `tests/unit/test_audit_regression.py::TestHousingAssistanceRemoval`. Stays.
- `semantic_router.py:33` ("backward compat" comment on single-intent classify_service) — comment is misleading. `classify_service` is still actively used in `pipeline.py:93` for the single-best-match semantic path. Update the comment, don't remove the function.

### `COMPAT-1` — Remove or simplify `_classify_message` backward-compat wrapper

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Took option (a) but in the lightest form — kept the function name and the leading-underscore convention, just rewrote the framing. Production has no consumers; 24 test sites use it as a one-call sanity wrapper around `_classify_action` + `_classify_tone` + `detect_crisis` + `extract_slots`. Renaming would have churned all 24 with no behavior payoff.

Changes:
- Section comment updated: `# COMBINED CLASSIFIER (backward compat)` → `# COMBINED CLASSIFIER — TEST CONVENIENCE WRAPPER`.
- Docstring rewritten to say "test-convenience wrapper" instead of "backward compatibility with existing tests and the LLM fallback" (the latter half was misleading — the LLM fallback path doesn't actually call this function).
- Comment in `tests/integration/test_classification_and_routing.py` ("backward-compatibility wrapper used by the LLM fallback path") updated to match: "test-convenience wrapper that combines the underlying classifiers".

No signature change, no behavior change.

**Verification:** 4326 tests pass.

### `COMPAT-2` — Drop `extraction_source=None` default in `slot_extraction.extract()`

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Took a middle-ground approach. The original recommendation was to make `extraction_source` required, but counting found 27 test sites pass `extract()` without the parameter (most don't care which path the source came from — they're testing merge-layer behavior). Forcing all of them to invent a real value would have been busywork.

What landed instead:
- **Production callers updated to be explicit.** `pipeline.py:150` now passes the existing `extraction_source` parameter that the gate already receives. `confirmation.py:736` now passes `extraction_source="regex"` (the regex_result passed in is computed from `extract_slots(ctx.message)` immediately above, so "regex" is the correct explicit source).
- **Docstring framing updated.** Dropped "backwards-compatible with callers that don't track source"; added "The default is `None` for test convenience — production callers should always pass an explicit value to make the source-tracking behavior visible at the call site."

Net effect: production has explicit source-tracking everywhere; test ergonomics unchanged; the docstring now accurately describes the contract.

**Verification:** 4326 tests pass.

### `COMPAT-3` — Drop unused `message` parameter from `merge.py` set-overlap branches

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Verified via code reading that `message` is genuinely unused in both `_merge_service_type_and_primary_location` and the top-level `merge()` (it was passed through unconditionally to the inner function; the inner function never read it after the Phase 4 prompt-driven primary-selection rewrite). Took option (a): removed the parameter.

Changes:
- `_merge_service_type_and_primary_location(regex_result, llm_result, message=None, extraction_source=None)` → `(regex_result, llm_result, extraction_source=None)`.
- `merge(regex_result, llm_result, message=None, extraction_source=None)` → `(regex_result, llm_result, extraction_source=None)`.
- Both production callers in `slot_extraction/__init__.py` updated.
- Docstring sections describing `message`'s semantics dropped from both functions.
- 14 test call sites in `test_slot_extraction.py` updated via Python regex script (5 patterns: 4-positional-with-None, 3-positional-with-message-var, 3-positional-with-literal-string, top-level `merge(..., message=...)` kwarg, `merge(..., message="...")` kwarg). 3 unused `message = ...` locals cleaned up.

**Verification:** 4326 tests pass.

### `COMPAT-4` — Standardize on 3-tuple offers throughout the merge pipeline

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Took option (b): kept the defensive branches in `_unpack_additional_item` (1-tuple, 2-tuple, string fallbacks) but reframed the docstring to drop the "legacy" language. The branches genuinely protect against malformed input from session deserialization or future LLM output drift; calling them "legacy" implied a deprecation path that doesn't exist.

Changes:
- `_unpack_additional_item` docstring rewritten: instead of "Accepts both legacy 2-tuples and new 3-tuples", it now reads "The canonical shape produced by current callers is a 3-tuple. The 1-tuple, 2-tuple, and string branches are defensive: they let the function tolerate malformed or partial entries that might appear in deserialized session data, third-party fixtures, or future LLM output that drops fields."
- Two test docstrings in `test_slot_extraction.py` updated similarly: "Legacy 2-tuple ... should unpack to ..." → "Defensive: a 2-tuple ... should unpack to ...".

No code change. Pure documentation.

**Verification:** 4326 tests pass.

---

## Test coverage gaps

### `TEST-GAP-1` — `_handle_demographic_skip` integration coverage

**Status:** Open. Documented in code.

**Problem:** `_handle_demographic_skip` is hard to integration-test because its preconditions require a session state that's awkward to reach via natural conversation — service+location auto-sets `_pending_confirmation`, which interferes with the demographic-skip path.

**Where documented:** `tests/integration/test_classification_and_routing.py:2081-2090` — explicit "Coverage gap acknowledged" note in `test_curly_apostrophe_in_location_unknown`.

**Risk:** A selective revert of just `demographic_skip`'s `normalize_apostrophes` import wouldn't be caught by the existing sibling test.

**Fix options:**
- (a) `D-2` MessageContext fixture lands → unit-test `_handle_demographic_skip` directly with a hand-built ctx.
- (b) Find an integration flow that naturally clears `_pending_confirmation` then triggers the demographic-skip path. May not exist.
- (c) Mock the precondition-blocker and integration-test it.

**Recommendation:** (a). Couples to `D-2`.

**Acceptance:** test exists that exercises `_handle_demographic_skip` with curly-apostrophe input; would fail if its `normalize_apostrophes` were removed.

### `TEST-GAP-2` — Snapshot-arg semantics not directly unit-tested at dispatcher level

**Status:** Open. Low impact.

**Description:** Phase C introduced three dispatchers that take a captured snapshot as second arg:
- `_handle_context_aware_confirm(ctx, last_action)`
- `_handle_pending_confirmation(ctx, pending)`
- `_handle_post_pending_confirmation(ctx, response_tone)`

The snapshot semantics matter because the dispatcher mutates the same key on `ctx.existing` and the orchestrator consumes the pre-mutation value afterward.

**Current coverage:**
- Integration tests cover the full path (which implicitly exercises the snapshot semantics).
- `tests/unit/test_session_helpers.py::test_uses_captured_value_not_current_dict` pins the snapshot pattern at the helper level (`_consume_last_action`).

**Gap:** No test directly exercises the dispatcher-level `(ctx, snapshot)` signature with `snapshot != ctx.existing.get(...)` to pin "uses the snapshot, not a re-read".

**Fix options:**
- Construct ctx where `ctx.existing.get("_last_action")` returns `None` but pass `last_action="emotional"` as the snapshot. Verify the handler still dispatches the emotional → confirm_yes path. Requires `D-2`.

**Acceptance:** unit test added that exercises the divergence between `ctx.existing.get(...)` and the snapshot arg; test would fail if a dispatcher were "fixed" to re-read from `ctx.existing` instead of using the snapshot.

---

## Eval cluster fixes

### `EVAL-B` — Cluster B: multi_cross_borough and multi_three_services_legal_benefits_food

**Status:** Open. Identified during R32-era eval analysis.

**Scenarios:**
- `multi_cross_borough_food_brooklyn_shelter_manhattan` — score 4.36, error_recovery=2.
- `multi_three_services_legal_benefits_food` — score 3.55.

**Root causes (suspected):**
- Multi-intent with cross-borough preferences not handled cleanly by the queue mechanism.
- Three-service queue with mixed locations may drop the third service or assign wrong location.
- Includes the `BUG-1` fix in `_persist_emotional_context_late` because the asymmetric save condition can affect multi-turn shame normalization on these scenarios.

**Acceptance:** both scenarios score ≥4.0 in a fresh eval run; root-cause fix(es) documented; regression tests added in the relevant `test_classification_and_routing.py` block.

### `EVAL-C` — Cluster C: wa_negative_preference

**Status:** Open. Identified during R32-era eval analysis.

**Scenario:** `wa_negative_preference` — score 3.82, error_recovery=2.

**Root causes (suspected):**
- Geographic expansion logic when the user's negative preference includes a location qualifier.
- Safety language acknowledgment missing when negative-preference context overlaps with safety signals.
- Quick-reply set may not include "search wider area" or equivalent.

**Acceptance:** scenario scores ≥4.0; fix path includes (a) widened geographic fallback when negative-preference extracts a location, (b) safety-context acknowledgment in the negative-preference response, (c) updated quick-reply set with "expand search" affordance; regression tests added.

---

## Engineering tasks

### `ENG-1` — Mobile-input fuzz harness (apostrophe substitutions)

**Status:** Open. Identified during the curly-apostrophe PR.

**Description:** Mobile autocorrect produces curly apostrophes (U+2019) where users typed straight ones. The codebase has `normalize_apostrophes` in `app.utils.text_normalize` that handles this at all known call sites. But there's no test that systematically re-runs eval scenarios with apostrophe substitutions to catch new sites that need normalization.

**Proposed harness:** parametrized test that takes a list of eval scenarios with apostrophe-bearing input, substitutes each apostrophe variant (U+2019, U+02BC, U+2018, U+0060), and re-runs. Asserts the same routing behavior as the straight-apostrophe input.
<!-- drift:ignore: possible future file that may not exist yet -->
**Implementation sketch:** ~50 LOC in `tests/integration/test_apostrophe_fuzz.py`. Reuses existing scenario definitions; iterates a small list of substitutions.

**Acceptance:** harness exists; runs against ≥10 representative scenarios; failure clearly identifies which (scenario, substitution) pair regressed.

---

## Closed during Phase A-C / audit follow-ups (for reference)

These items were resolved during Phases A-C and the audit follow-ups PR. Listed here so they don't get re-tracked.

| Item | Resolution |
| --- | --- |
| `MessageContext` adoption (audit's top finding) | Phases A-C: 26 of 27 handlers migrated. Only `_handle_spanish_detection` deferred (`D-1`). |
| Smell 1 — dead `redact_pii` import | Removed in Phase A. |
| Smell 1 — dead `has_coords` local | Removed in audit follow-ups (also dropped from `MessageContext`). |
| Smell 4 — inline constants `_CONSUMES_LAST_ACTION`, `_MAX_TRANSCRIPT` | Moved to `session_helpers.py` module scope in Phase B. |
| Smell 6 — response-building boilerplate | Extracted as `_build_follow_up_response`; `SUSPECT-1` asymmetry now visible at the call site. |
| Smell 7 — orchestrator session-state mutation | Helpers extracted: `_append_to_transcript`, `_update_queued_services`, `_persist_emotional_context_*`, `_clear_stale_last_action`, `_consume_last_action`. |
| `crisis_result` type annotation (`Optional[dict]` → `Optional[Tuple[str, str]]`) | Fixed in audit follow-ups. |
| Dead ctx fields (`spanish_detected`, `spanish_acknowledgment` write-only) | Removed `spanish_detected`; wired up `spanish_acknowledgment` as actual reader at the prefix-prepend block. |
| Doc references to "ORCHESTRATOR_AUDIT.md Phase X" | Phase tracking section appended to the audit doc. |
| Dangling `(TEST_QUALITY_PLAN.md)` and `PR-γ` references | Updated in `session_helpers.py` and `orchestrator.py`. |
| Stale source-code-under-test docstring in `test_orchestrator_guards.py` | Updated to match post-migration `_promote_queued_offer(ctx, offer, ...)`. |
| Phase C orchestrator restructure | Done. `_compute_routing_category`, `_clear_stale_last_action`, `MessageContext` construction moved before queue-accept and post-results fast paths. Pinned by `test_show_more_after_emotional_clears_stale_last_action`. |
| Phase C dispatcher migration | Done. All confirmation.py and post_results.py handlers on `ctx`. Snapshot-arg pattern for the three dispatchers that mutate ctx state. |
| `BUG-1` — `_persist_emotional_context_late` value→value save | Fixed in BUG-1 PR: condition rewritten to `new_value and new_value != existing.get(...)`. Pinning test inverted to assert save fires; new tests pin the truthiness and inequality guards. |
| `SMELL-3` — Inline imports of `slot_extraction.extract` | Hoisted to module-level imports in orchestrator.py, confirmation.py, pipeline.py. Switched from aliased function imports to module imports to preserve test-mockability. |
| `UTIL-1` — `normalize_contractions` + `strip_intensifiers` | Moved from `classifier.py`/`phrase_lists.py` to `app/utils/text_normalize.py`. Inline import in `responses.py` dropped. 17 new unit tests. |
| `UTIL-2` — `format_time` | Extracted from `query_templates.py:_format_time` to `app/utils/time_format.py`. Both call sites updated. 9 new dedicated tests; 6 duplicate tests removed from `test_query_templates.py`. |
| `COMPAT-1` — `_classify_message` "backward compat" framing | Section comment and docstring updated to say "test convenience wrapper" — no signature change (production has no callers; 24 test sites unchanged). |
| `COMPAT-2` — `extraction_source` default | Production callers (pipeline.py, confirmation.py) now pass an explicit value. Docstring framing updated to drop "backwards-compatible" language. |
| `COMPAT-3` — Unused `message` parameter in `merge.py` | Dropped from `_merge_service_type_and_primary_location` and `merge()` signatures. 14 test call sites + 3 unused locals cleaned up. |
| `COMPAT-4` — 3-tuple offers | Doc-only change: `_unpack_additional_item` docstring reframed from "legacy 2-tuples" to "defensive against malformed input". Two test docstrings updated. |

---

## Notes on prioritization

- `EVAL-B` and `EVAL-C` are the highest-value items by user impact — they pin specific scoring failures.
- `LLM-1` is the highest-impact infrastructure item: it cuts API cost and latency in half on a common path (>8-word service messages that miss regex). Worth doing before launch because the gain is real and the fix is bounded.
- `LLM-2` and `LLM-3` are smaller cost wins; bundle with `LLM-1` as a single "LLM redundancy" cleanup PR if pursuing.
- `D-1` through `D-5` are migration polish; doing them tightens the architecture but doesn't unlock new capability. Pick up when refactor budget allows.
- All remaining `SMELL-*` items (`SMELL-2`, `SMELL-5`, `SMELL-8`, `SMELL-9`) are explicitly low-priority per the original audit's own assessment. Consider closing them en masse with a single small "polish PR" rather than individual changes.
- `ENG-1` (fuzz harness) is the highest-value preventative item — catches a class of bug that mobile users have shipped to us before.
