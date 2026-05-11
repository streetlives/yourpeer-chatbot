# Phase A-C aftermath — open items tracking

**Status as of:** Phase A, B, C complete; Phase D handler migration and Stage 2 snapshot cleanup landed in PR #76; subsequent PRs closed the `LLM-*`, `COMPAT-*`, `UTIL-*`, `BUG-1`, and `SMELL-3` items (see the table at the bottom). The May 2026 SMELL bundle closes `SMELL-2`, `SMELL-5`, `SMELL-8`, and `SMELL-9` per the doc's own prioritization note (low-priority style smells closed en masse). Remaining open: `SUSPECT-1` (needs eval run, not a code change) and the two eval-cluster items `EVAL-B` / `EVAL-C` (need eval verification of score uplift). Items closed in any prior PR retain their detail sections below for historical context — see each section's status line.

Use stable IDs (e.g., `D-1`, `BUG-1`) when referencing items from PRs or commits.

---

## Quick status table

| ID | Item | Category | Effort | Priority |
| --- | --- | --- | --- | --- |
| `D-1` | Migrate `_handle_spanish_detection` to `MessageContext` | Phase D | M | ✅ Closed (PR #76) |
| `D-2` | MessageContext test fixture / builder | Phase D | S | ✅ Closed (PR #76) |
| `D-3` | Tighten `MessageContext.merged` annotation | Phase D | S | ✅ Closed (PR #76) |
| `D-4` | Investigate dropping `_response_tone` alias | Phase D | S | ✅ Closed (PR #76) |
| `D-5` | Stage 2 snapshot pattern: helper accessors for `_last_action` / `_pending_confirmation` | Phase D | M | ✅ Closed (PR #76) |
| `BUG-1` | `_persist_emotional_context_late` save-on-value-change | Bug | XS | ✅ Closed |
| `SUSPECT-1` | Tone prefix asymmetry between follow-up paths | UX question | XS | Low (needs eval) |
| `SMELL-2` | Underscore-prefixed locals in `generate_reply` | Style | M | ✅ Closed (May 2026, option b — documented convention) |
| `SMELL-3` | Inline imports of `slot_extraction.extract` | Style | XS | ✅ Closed |
| `SMELL-5` | `if X: pass else:` structure in orchestrator | Style | XS | ✅ Closed (May 2026) |
| `SMELL-8` | Mixed dispatch patterns | Style | M | ✅ Closed (May 2026) |
| `SMELL-9` | `_empty_reply` doesn't log empty-message events | Audit gap | XS | ✅ Closed (May 2026) |
| `UTIL-1` | Extend `text_normalize.py` with `normalize_contractions` + `strip_intensifiers` | Utility extraction | S | ✅ Closed |
| `UTIL-2` | Extract `format_time` to `utils/time_format.py` | Utility extraction | XS | ✅ Closed |
| `LLM-1` | Duplicate `slot_extraction.extract()` between `_run_llm_gate` and orchestrator service branch | LLM redundancy | M | ✅ Closed |
| `LLM-2` | `_handle_post_pending_confirmation` re-extracts; ignores pre-computed `ctx.early_extracted` | LLM redundancy | S | ✅ Closed |
| `LLM-3` | `classify_message_llm` fallback in `_compute_routing_category` likely redundant after `_run_llm_gate` | LLM redundancy | S | ✅ Closed |
| `COMPAT-1` | Remove or simplify `_classify_message` backward-compat wrapper | Pre-launch cleanup | S | ✅ Closed |
| `COMPAT-2` | Drop `extraction_source=None` default in `slot_extraction.extract()` | Pre-launch cleanup | XS | ✅ Closed |
| `COMPAT-3` | Drop unused `message` parameter from `merge.py` set-overlap branches | Pre-launch cleanup | XS | ✅ Closed |
| `COMPAT-4` | Standardize on 3-tuple offers throughout the merge pipeline | Pre-launch cleanup | S | ✅ Closed |
| `TEST-GAP-1` | `_handle_demographic_skip` integration coverage | Test gap | S | ✅ Closed (PR #76) |
| `TEST-GAP-2` | Snapshot-arg semantics not directly unit-tested | Test gap | S | ✅ Closed (PR #76) |
| `EVAL-B` | Cluster B eval fixes (multi_cross_borough, multi_three_services_legal_benefits_food) | Eval | L | High |
| `EVAL-C` | Cluster C eval fix (wa_negative_preference) | Eval | M | Medium |
| `ENG-1` | Mobile-input fuzz harness (apostrophe substitutions) | Engineering | S | ✅ Closed (PR #76) |

**Effort key:** XS ≤ 30min · S ≤ 2hr · M ≤ 1day · L ≤ 1week.

---

## Phase D — handler migration cleanup

The four-phase MessageContext adoption (Phases A-C in `ORCHESTRATOR_AUDIT.md`) plus the residual Phase D cleanup is complete. All `D-*` items below were closed in PR #76; the body sections are kept for historical context — each one notes how it landed.

### `D-1` — Migrate `_handle_spanish_detection` to `MessageContext`

**Status:** ✅ **Closed in PR #76.**

**Resolution:** All three accessibility handlers (`_handle_demographic_skip`, `_handle_location_unknown`, `_handle_spanish_detection`) migrated from 6-arg signatures to ctx-only. The orchestrator call sites and the 22 unit-test invocations in `tests/unit/test_chatbot_extracted_helpers.py` were updated to use the `make_ctx` builder from `D-2`.

**Original details (for historical context):**

**Original signature:** `_handle_spanish_detection(session_id, message, redacted_message, existing, has_service_intent, tone, request_id)` at `accessibility.py:157`. Returns a `(result, acknowledgment)` tuple.

**Target signature:** `_handle_spanish_detection(ctx) -> Tuple[Optional[dict], str]`.

**Was deferred because:** 4 direct unit-test invocations in `tests/unit/test_chatbot_extracted_helpers.py` used the positional form. Migration required a `MessageContext` test fixture (`D-2`) before the test calls could be cleanly updated.

### `D-2` — MessageContext test fixture / builder

**Status:** ✅ **Closed in PR #76.**

**Resolution:** `make_ctx(**overrides)` builder added in `tests/conftest.py`. Returns a `MessageContext` with sensible defaults; callers override only the fields they care about. Auto-derives `is_confirmation_action` from the `action` override. Used by the rewritten unit tests for the three accessibility handlers (`D-1`) and by the new `TEST-GAP-2` snapshot-divergence tests.

**Original details (for historical context):**

**Problem:** `MessageContext` has 18 fields (post-cleanup), several required without defaults. Constructing one in a unit test is verbose. Tests that white-box-call handlers (rather than going through `generate_reply`) couldn't easily build a ctx.

**Options considered:**
- **(a) Pytest fixture `default_ctx(**overrides)`** — returns a MessageContext with sensible defaults, callers override only the fields they care about. Simplest and most idiomatic for pytest. **Chosen approach.**
- **(b) `MessageContext.for_test(...)` classmethod** — same idea, on the class itself. Discoverable but couples production code to tests.

### `D-3` — Tighten `MessageContext.merged` annotation

**Status:** ✅ **Closed in PR #76.**

**Resolution:** Kept `merged: Optional[dict] = None` (the alternative — making it required at construction — broke the dataclass-construction-once pattern). Added `MessageContext.require_merged()` accessor that raises `RuntimeError` with a descriptive message if called before `merged` is set. `_handle_general_conversation` (in `general.py`) — the only current reader — was migrated to call the accessor instead of reading `ctx.merged` directly. Future readers that need the merged dict should use the accessor; the type annotation stays defensive.

**Original details (for historical context):**

**Concern:** A handler that reads `ctx.merged` before the late-set runs would crash. The only current reader was `_handle_general_conversation` in `general.py:66`, which runs at the end and was safe — but the contract was implicit.

**Resolution path chosen:** Keep `Optional` annotation; add `require_merged()` that fails fast and loud rather than passing `None` into downstream code that would crash with `AttributeError` later.

### `D-4` — Investigate dropping `_response_tone` alias

**Status:** ✅ **Closed in PR #76** (refactored, not dropped — alias semantics now live on the ctx).

**Resolution:** The alias was removed from the orchestrator local scope and replaced with `ctx.snapshot_response_tone`, captured immediately after `MessageContext` construction in `generate_reply` — *before* the B.2 negative-preference promotion block reassigns `tone = "frustrated"`. Both `_compute_tone_prefix` call sites and `_handle_post_pending_confirmation` now read from `ctx.snapshot_response_tone`. Pre-D4 semantics restored exactly — readers see the pre-promotion tone, not the promoted "frustrated" value.

**Self-found regression caught during the work:** an earlier draft removed the alias entirely on the assumption that `tone` was never reassigned. It was — the B.2 negative-preference block reassigns `tone = "frustrated"`, and the post-pending path was reading the post-promotion value. Pinned by `test_b2_post_pending_uses_pre_promotion_tone` in `tests/integration/test_classification_and_routing.py`, which spies on `_compute_tone_prefix` and asserts no call gets `response_tone="frustrated"` for a turn whose user-expressed tone was neutral.

**Original details (for historical context):**

**Why the alias existed:** `tone` was reassigned to `"frustrated"` in the negative_preference + service-promotion path (orchestrator:376-377: `if tone is None: tone = "frustrated"`). `_response_tone` preserved the pre-promotion value so tone-matched response text reflected what the user originally expressed, not the promoted classification.

**Three readers needed the pre-promotion value:**
- `_compute_tone_prefix(response_tone=...)` at orchestrator:332 (early prefix)
- `_handle_post_pending_confirmation(ctx, response_tone)` at orchestrator:451 — uses for `nudge_prefix` selection. If tone was None pre-promotion and "frustrated" post-promotion, the post-promotion path would pick the frustrated prefix on a user whose original message wasn't frustrated.
- `_compute_tone_prefix(response_tone=...)` at orchestrator:537 (late prefix) — would change baseline-warmth selection in negative_preference flows.

**Investigation conclusion:** the alias is intentional and preserves real semantics. The Phase D refactor moved it onto the ctx so the snapshot-arg pattern is uniform with `D-5`'s other two fields; the alias-as-local-variable was the form to drop, not the alias-as-concept.

### `D-5` — Stage 2 cleanup: helper accessors for snapshot args

**Status:** ✅ **Closed in PR #76** (taken in a different shape than originally proposed — see resolution).

**Resolution:** Three snapshot fields added directly to `MessageContext`: `snapshot_last_action`, `snapshot_pending`, `snapshot_response_tone`. Captured at ctx construction time, consumed by handlers via `ctx.snapshot_*`. Handlers no longer take positional snapshot args at all — the dispatcher signatures dropped `(ctx, last_action)` / `(ctx, pending)` / `(ctx, response_tone)` for plain `(ctx)`.

This is *cleaner* than the original "with_snapshotted_X(ctx, handler)" indirection-helper proposal — the snapshot is now declarative state on the ctx, not a control-flow indirection. Readers can see the snapshot was captured at construction time by reading the ctx, and the orchestrator's "capture before, consume after" pattern collapses to a single `ctx.snapshot_X = existing.get(...)` line in the construction block.

**Original details (for historical context):**

**Original pattern:** orchestrator captured snapshot values inline before calling dispatchers:
```python
last_action = existing.get("_last_action")
context_result = _handle_context_aware_confirm(ctx, last_action)
# ...
_consume_last_action(session_id, existing, last_action)
```

This pattern occurred three times (`_last_action`, `_pending_confirmation`, `_response_tone`).

**Original proposal — extract helpers:**
```python
def with_snapshotted_last_action(ctx, handler):
    """Capture _last_action, run handler, return (result, snapshot)."""
    snap = ctx.existing.get("_last_action")
    return handler(ctx, snap), snap
```

**Why we landed on ctx fields instead:** the indirection helper would have hidden the capture point; ctx fields make it visible at construction. The TEST-GAP-2 unit tests pin the divergence between `ctx.existing.get(...)` and `ctx.snapshot_*` so a future refactor can't accidentally collapse them.

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

**Status:** ✅ **Closed (May 2026)** via option (b) from the acceptance
list — kept as a semantic marker, with the convention documented
inline. A comment block in `generate_reply` (right after the empty-
message guard, before the PII redaction block) names the convention
explicitly: leading-underscore locals (`_pii_warning`,
`_extraction_source`, `_action_pre`, `_post_result`,
`_spanish_acknowledgment`, etc.) are transient pipeline state consumed
within `generate_reply` and NOT promoted onto `MessageContext`;
values that DO promote use `ctx` fields directly (`ctx.tone`,
`ctx.snapshot_*`). Sweep-rename (option (a) or (c)) would touch every
line of the orchestrator's main function for a low-priority style
change and was rejected on risk/payoff grounds. The audit body below
preserves the original finding.

**Original finding:** Partially addressed — many `_*` locals were eliminated when their values moved onto `ctx` during phases A-C, but some remain.

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

**Status:** ✅ **Closed (May 2026).** Flipped to `if tone != "crisis":` in
`orchestrator.py`. The else-body (queue-accept fast path + post-results
check) is now the if-body, indentation unchanged. Behavior identical;
preserved by the full test suite. Replaced the inline `pass` comment
with a header comment explaining why crisis tone skips these fast
paths (non-meaningful for active crisis; message falls through to
crisis routing below). The audit body below describes the pre-fix
state.

**Original finding:**

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

**Status:** ✅ **Closed (May 2026).** Five Pattern B dispatcher call
sites in `orchestrator.py` standardized on the walrus form
(`if (result := _handle_X(ctx)): return …`):
`_handle_post_results_interaction`, `_handle_demographic_skip`,
`_handle_location_unknown`, `_handle_context_aware_confirm`, and
`_handle_pending_confirmation`. `_handle_post_pending_confirmation`
was already called-and-returned-directly (no Pattern B form, no
refactor needed). `_handle_spanish_detection` returns a tuple, so it
stays in its current shape. All five refactored handlers return
`dict | None`, so the truthy check is equivalent to `is not None`.
Behavior preserved by the full test suite. The audit body below
preserves the original analysis.

**Original finding:**

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

**Status:** ✅ **Closed (May 2026)** via option (a). The empty-message
guard in `orchestrator.py::generate_reply` now calls `_log_turn` before
returning, with `category="empty_message"` and `tone=None`. The reply
dict is bound to a local first (`empty_reply = _empty_reply(...)`) so
the same value is both logged and returned. `_log_turn` wraps its body
in `try/except` so an audit-log failure can't break the user-facing
path. Regression test:
`tests/integration/test_classification_and_routing.py::test_empty_message_logs_audit_event`
pins that a `conversation_turn` event with `category='empty_message'`
lands in the audit feed for the empty-message path; the user-facing
contract from `test_empty_message_guard` is unchanged. The audit body
below describes the pre-fix state.

**Original finding:**

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

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Took option (a) — cache the gate's result on `MessageContext`.

- ``_run_llm_gate`` in ``pipeline.py`` now returns a 6-tuple, with the new trailing element being the full ``unified`` dict produced by ``slot_extraction.extract()`` (``None`` when the gate condition didn't fire or the call raised).
- ``MessageContext`` got a new field, ``unified_extraction: Optional[dict] = None``, with a documented contract: populated only when ``_run_llm_gate`` ran successfully; consumers must handle the ``None`` case.
- Orchestrator's service branch (``orchestrator.py``) checks ``ctx.unified_extraction is not None`` BEFORE the existing ``else`` branch's ``slot_extraction.extract()`` call. When set, it uses the cached value directly. When unset (gate didn't fire — short message, prior service intent, etc.), the existing fresh-extraction path runs.
- The two test stubs of ``_run_llm_gate`` (1 in ``test_orchestrator_guards.py`` returning a 5-tuple, 3 unpacking sites in ``test_chatbot_extracted_helpers.py``) were extended to the 6-tuple shape.
- The deferred-latency comment at ``slot_extraction/__init__.py:162-163`` that anticipated this fix was retained — the cache short-circuits the duplicate call without changing the inner extraction module's behavior, so the comment still describes that module accurately.

**New regression test:** ``tests/integration/test_llm_call_redundancy.py::TestLLM1NoDuplicateExtractionCall``. Two cases — the long-service-message scenario (asserts call count = 1; verified to fail at 2 pre-fix), and the short-simple-message sanity check (asserts call count = 0 on the regex-only fast path; verifies the cache change didn't regress the short path).

**Verification:** Full suite at 4330 passing.

### `LLM-2` — `_handle_post_pending_confirmation` re-extracts, ignoring pre-computed extraction

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Three-branch fix that reuses ctx-cached values whenever possible.

The handler in ``confirmation.py:_handle_post_pending_confirmation`` previously did:
```python
regex_result = extract_slots(ctx.message)
pending_extracted = slot_extraction.extract(
    ctx.message, regex_result, ..., extraction_source="regex",
)
```

After the fix:
```python
if ctx.unified_extraction is not None:
    # LLM-1 cache hit: gate already ran, reuse it.
    pending_extracted = ctx.unified_extraction
elif _USE_LLM:
    # Gate didn't fire — call the LLM once with ctx.early_extracted
    # (which already has regex+semantic) instead of re-running
    # extract_slots on the same message.
    pending_extracted = slot_extraction.extract(
        ctx.message, ctx.early_extracted, ...,
        extraction_source=ctx.extraction_source or "regex",
    )
else:
    # No LLM — ctx.early_extracted is the regex+semantic result.
    pending_extracted = ctx.early_extracted
```

Best case (gate fired earlier in the turn): saves 1 LLM call AND 1 regex call. Common case (gate didn't fire because regex caught service_type): saves 1 redundant regex call. ``extract_slots_short`` still runs but only on the post-pending message that genuinely needs LLM enrichment.

Forwards ``ctx.extraction_source`` to the LLM call so Trust Model 3 can give the semantic router priority when it disagrees with the LLM's pick — same behavior as the orchestrator's main service-flow call.

**New regression test:** ``tests/integration/test_llm_call_redundancy.py::TestLLM2PostPendingReusesContextValues``. Sets up a pending confirmation in session state, then sends a long message that triggers the gate. Asserts call count = 1 (verified to fail at 2 pre-fix).

**Verification:** Full suite at 4330 passing.

### `LLM-3` — `classify_message_llm` fallback removed from `_compute_routing_category`

**Status:** ✅ **Closed** (in this PR).

**Resolution:** Took option (a) — remove the fallback entirely.

The pre-fix branch in ``pipeline._compute_routing_category``:
```python
elif _USE_LLM and len(message.strip().split()) > 3:
    from app.llm.claude_client import classify_message_llm
    llm_category = classify_message_llm(message)
    if llm_category is not None:
        category = llm_category
        confidence = "medium"
    else:
        category = "general"
        confidence = "low"
else:
    category = "general"
    confidence = "low"
```

was replaced with the simpler:
```python
else:
    category = "general"
    confidence = "low"
```

The branch only fired when ALL of the following held: ``tone is None`` (regex AND gate's ``unified.tone`` both empty), ``action`` not in the early-handled list, and ``has_service_intent is False`` (regex + semantic + gate's slot extraction all empty). When all three hold, the message has no detectable signal — and calling ``classify_message_llm`` (the same model with a different routing-focused prompt) is unlikely to find a signal its sibling classifiers missed. The category outputs either overlap with already-handled branches or default to ``"general"``.

The ``classify_message_llm`` function itself remains in ``app/llm/claude_client.py`` along with its unit tests — it's not called from the orchestrator pipeline anymore but stays as a standalone utility (and the existing unit tests in ``test_claude_client.py`` still pass).

**Documentation updated:** an inline rationale comment in ``_compute_routing_category`` explains why the branch is gone (so a future contributor doesn't add it back). The routing-cascade pinning test ``test_routing_category_sequence_is_preserved`` was updated to drop the ``llm_category`` step from the expected sequence.

**Risk:** Possible eval regression if some scenarios were previously caught only by this LLM fallback. Mitigation: the branch was already conservative (returned ``"general"`` when the LLM returned ``None``, which is the same as the post-fix default), so the regression surface is just the cases where the LLM returned a non-None category and that category mattered for routing. If eval scores drop, this can be reverted in isolation.

**New regression test:** ``tests/integration/test_llm_call_redundancy.py::TestLLM3NoClassifierFallbackCall``. Patches ``classify_message_llm`` with a counter and runs a no-signal message through ``generate_reply``. Asserts call count = 0 (verified to fail at 1 pre-fix).

**Verification:** Full suite at 4330 passing.

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

**Status:** ✅ **Closed in PR #76.**

**Resolution:** `normalize_apostrophes` was added to `_handle_demographic_skip` and `_handle_location_unknown`. 14 unit tests in `tests/unit/test_chatbot_extracted_helpers.py` exercise the curly-apostrophe handling using "I don't want to say" (with U+2019). The `D-2` `make_ctx` fixture made it tractable to white-box-call these handlers with a hand-built ctx, sidestepping the integration-flow problem entirely (option (a) from the original analysis).

**Original details (for historical context):**

**Problem:** `_handle_demographic_skip` was hard to integration-test because its preconditions required a session state that's awkward to reach via natural conversation — service+location auto-sets `_pending_confirmation`, which interferes with the demographic-skip path.

**Where documented:** `tests/integration/test_classification_and_routing.py:2081-2090` — explicit "Coverage gap acknowledged" note in `test_curly_apostrophe_in_location_unknown`.

**Risk that was mitigated:** A selective revert of just `demographic_skip`'s `normalize_apostrophes` import wouldn't have been caught by the existing sibling test. The new unit tests would now catch it directly.

### `TEST-GAP-2` — Snapshot-arg semantics not directly unit-tested at dispatcher level

**Status:** ✅ **Closed in PR #76.**

**Resolution:** Added 5 tests to `tests/unit/test_chatbot_extracted_helpers.py::TestSnapshotArgSemantics`. Each test constructs a ctx where `ctx.existing.get(snapshot_key)` and `ctx.snapshot_X` deliberately diverge, and verifies the dispatcher routes by the snapshot field, not a re-read of `ctx.existing`. After the `D-5` migration to ctx-resident snapshots, the assertion shape became "uses `ctx.snapshot_X`, not `ctx.existing.get(...)`" rather than the original "uses the second positional arg".

**Original details (for historical context):**

Phase C introduced three dispatchers that took a captured snapshot as a second arg:
- `_handle_context_aware_confirm(ctx, last_action)`
- `_handle_pending_confirmation(ctx, pending)`
- `_handle_post_pending_confirmation(ctx, response_tone)`

The snapshot semantics matter because the dispatcher mutates the same key on `ctx.existing` and the orchestrator consumes the pre-mutation value afterward. After `D-5`, those positional args moved to `ctx.snapshot_*` fields, but the divergence-pinning gap was the same.

**Original coverage:**
- Integration tests covered the full path (which implicitly exercised the snapshot semantics).
- `tests/unit/test_session_helpers.py::test_uses_captured_value_not_current_dict` pinned the snapshot pattern at the helper level (`_consume_last_action`).
- No test directly exercised the dispatcher-level signature with `snapshot != ctx.existing.get(...)` to pin "uses the snapshot, not a re-read". That's what PR #76 added.

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

**Status:** ✅ **Closed in PR #76.**

**Resolution:** `tests/integration/test_apostrophe_fuzz.py` (which existed pre-PR with 13 tests but didn't meaningfully exercise the post-migration handlers due to the un-migrated accessibility handlers in `D-1`) is now wired through to the migrated `_handle_demographic_skip` and `_handle_location_unknown`. The 13 tests cover U+2019, U+02BC, U+2018, and U+0060 substitutions across representative scenarios; failure clearly identifies the (scenario, substitution) pair that regressed.

**Original details (for historical context):**

Mobile autocorrect produces curly apostrophes (U+2019) where users typed straight ones. The codebase has `normalize_apostrophes` in `app.utils.text_normalize` that handles this at all known call sites. The risk was that a future change might add a new call site that needs normalization but go untested.

The harness is parametrized: takes a list of eval scenarios with apostrophe-bearing input, substitutes each apostrophe variant, and re-runs. Asserts the same routing behavior as the straight-apostrophe input.

---

## Closed during Phase A-C, audit follow-ups, and PR #76 (for reference)

These items were resolved during Phases A-C, the audit follow-ups PR, and PR #76. Listed here so they don't get re-tracked.

| Item | Resolution |
| --- | --- |
| `MessageContext` adoption (audit's top finding) | Phases A-C: 26 of 27 handlers migrated. Final 3 accessibility handlers landed in PR #76 (`D-1`). |
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
| `LLM-1` — Duplicate `slot_extraction.extract()` | Cached gate result on `MessageContext.unified_extraction`. Orchestrator service branch short-circuits when populated. Eliminates 1 LLM call on every >8-word missed-by-regex service message. |
| `LLM-2` — `_handle_post_pending_confirmation` re-extracts | Three-branch reuse: `ctx.unified_extraction` (cache hit) → `ctx.early_extracted` as regex_result for the LLM call (gate didn't fire) → `ctx.early_extracted` directly (no LLM). Eliminates 1 redundant `extract_slots` call always; 1 redundant LLM call when gate fired. |
| `LLM-3` — `classify_message_llm` fallback | Removed from `_compute_routing_category`. The branch only fired when no signal was detectable; the second classifier on the same message couldn't recover one. Routing now defaults to `general`/`low` directly. Routing-cascade pinning test updated. |
| `D-1` — Migrate `_handle_spanish_detection` to `MessageContext` | PR #76: all three accessibility handlers (`_handle_demographic_skip`, `_handle_location_unknown`, `_handle_spanish_detection`) migrated from 6-arg to ctx-only. 22 unit-test invocations updated. |
| `D-2` — `MessageContext` test fixture | PR #76: `make_ctx(**overrides)` builder added in `tests/conftest.py` with auto-derived `is_confirmation_action`. |
| `D-3` — `MessageContext.merged` annotation | PR #76: kept `Optional`; added `require_merged()` accessor that raises `RuntimeError` if read before set. `_handle_general_conversation` migrated to use it. |
| `D-4` — `_response_tone` alias | PR #76: alias removed from orchestrator local scope; pre-promotion tone now lives on `ctx.snapshot_response_tone`. Self-found regression caught and pinned by `test_b2_post_pending_uses_pre_promotion_tone`. |
| `D-5` — Stage 2 snapshot-arg cleanup | PR #76: three `snapshot_*` ctx fields replace positional snapshot args. Dispatcher signatures dropped to `(ctx)`. |
| `TEST-GAP-1` — `_handle_demographic_skip` apostrophe coverage | PR #76: `normalize_apostrophes` added to handler; 14 unit tests with curly-apostrophe pin. |
| `TEST-GAP-2` — Snapshot-arg semantic divergence tests | PR #76: 5 `TestSnapshotArgSemantics` tests construct ctx where `ctx.existing.get(...)` and `ctx.snapshot_*` deliberately diverge, pin "uses snapshot, not re-read". |
| `ENG-1` — Apostrophe fuzz harness | PR #76: `tests/integration/test_apostrophe_fuzz.py` (13 tests) wired through to the now-migrated accessibility handlers. |
| `SMELL-2` — Underscore-prefixed locals in `generate_reply` | May 2026 SMELL bundle: option (b) — convention documented in `generate_reply` itself (comment block after the empty-message guard) rather than sweep-rename. Convention named explicitly: leading-`_` locals are transient pipeline state NOT promoted onto `MessageContext`; promoted values use `ctx` fields directly. |
| `SMELL-5` — `if X: pass else:` structure | May 2026 SMELL bundle: flipped to `if tone != "crisis":` in `orchestrator.py`. Body unchanged; comment reframed to explain why crisis tone skips the queue-accept/post-results fast paths. |
| `SMELL-8` — Mixed dispatch patterns | May 2026 SMELL bundle: five Pattern B call sites in `orchestrator.py` standardized on walrus form (`_handle_post_results_interaction`, `_handle_demographic_skip`, `_handle_location_unknown`, `_handle_context_aware_confirm`, `_handle_pending_confirmation`). All five return `dict \| None`, so truthy check is equivalent to `is not None`. |
| `SMELL-9` — `_empty_reply` doesn't log empty-message events | May 2026 SMELL bundle: option (a) — empty-message guard in `generate_reply` now calls `_log_turn` with `category='empty_message'` before returning. Pinned by new integration test `test_empty_message_logs_audit_event` alongside the existing user-facing-contract tests. |

---

## Notes on prioritization

- `EVAL-B` and `EVAL-C` are now the highest-value remaining items by user impact — they pin specific scoring failures. Acceptance requires a fresh eval run, so they're not closeable from a code change alone.
- `SUSPECT-1` remains open as a documentation/decision item; acceptance is "decision recorded" after reviewing relevant multi-turn eval scenarios — also gated on an eval run.
- All `SMELL-*` items are closed as of May 2026; see each section's status marker for the resolution path. The doc's earlier "polish PR en masse" recommendation was followed for the four-SMELL bundle.
