# orchestrator.py audit

**File:** `backend/app/services/chatbot/orchestrator.py`
**Function under audit (at audit time):** `generate_reply` (~520 lines, 28 return points)

**Status (May 2026):** ✅ The audit's top finding — the unfinished `MessageContext` refactor — has been fully resolved. Phases A–D landed across multiple PRs; the snapshot-arg pattern from Phase C was simplified into ctx-resident fields in PR #76. Most code smells flagged here have also been addressed; a small number remain open and are tracked in `PHASE_AC_AFTERMATH.md` (`SMELL-2`, `SMELL-5`, `SMELL-8`, `SMELL-9`, `SUSPECT-1`). Body sections below preserve the original audit findings for traceability with status markers per item.

---

## Top finding (resolved): incomplete `MessageContext` refactor

**Status:** ✅ Resolved (Phases A–D, plus PR #76 cleanup).

**Original finding:** `MessageContext` was defined in `context.py` (50+ lines, 20+ fields) and exported from the chatbot package, but no production code instantiated it. The orchestrator threaded each variable as a separate argument; handlers accepted individual params; the class was dead weight pointing at the migration's intended end state.

**Cost (as of original audit):**

- 15+ handlers each accepted 5–9 positional args, with **inconsistent ordering** between them. Examples:
  - `_handle_reset(session_id, redacted_message, category, tone, request_id)` — 5 args, no `existing`
  - `_handle_correction(session_id, redacted_message, existing, tone, request_id)` — 5 args, no `category`
  - `_handle_greeting(session_id, redacted_message, existing, category, tone, request_id)` — 6 args
  - `_handle_help(session_id, message, redacted_message, existing, _response_tone, category, tone, _tone_prefix, request_id)` — 9 args
- Adding a field (e.g., `extraction_source`) required updating every signature in the chain.
- The orchestrator was a "thread arguments and dispatch" function whose primary job was plumbing.

**Resolution:** The four-phase migration (described in `## Phase tracking` below) walked the dispatcher tree handler-by-handler. By the end, all 27 handlers in `services/chatbot/handlers/` accept a single `ctx: MessageContext` parameter; the orchestrator constructs it once after classification and passes it through. PR #76 finished the last three accessibility handlers (`D-1`) and cleaned up the snapshot-arg shape (`D-5`) to use ctx-resident fields rather than positional second args.

**End-state shape:** `_handle_X(ctx) -> Optional[dict]` for every handler. Orchestrator dispatch is uniformly `if (result := _handle_X(ctx)) is not None: return result` (or the equivalent for category-keyed handlers). New fields are added to `MessageContext` once and read where needed; signature cascades are no longer a thing.

**Recommended:** Adopt `MessageContext` as designed. Build it once at line ~225 (after classification completes), pass it to every handler. Handler signatures collapse from 5–9 positional args to a single `ctx: MessageContext` parameter (plus session-mutation helpers as needed).

This is a meaningful refactor — touches every handler — but the work is mechanical and the dataclass already exists. Estimated 1–2 days; reduces orchestrator.py by ~80–120 lines and improves every handler's signature.

---

## Real bugs / suspect logic

### ✅ Bug 1: late `_emotional_context_update` can be lost on follow-up paths — FIXED

**Status:** Fixed in BUG-1 PR (May 2026). See `PHASE_AC_AFTERMATH.md::BUG-1` for the resolution detail.

**Location (pre-fix):** lines 516–534 of the legacy orchestrator

**Setup:** `_compute_tone_prefix` is called twice — once early for help/confused/emotional handlers, once late to handle the B.2 promotion case (negative_preference → service changes `is_service_flow=False` to `True`).

**Problem (pre-fix):**

```python
# Late call may compute a different _emotional_context_update
_tone_prefix, _emotional_context_update = _compute_tone_prefix(...)

# Writes new value to merged (in-memory only)
if _emotional_context_update is not None:
    merged["_emotional_context"] = _emotional_context_update

# Re-saves ONLY if the value is *novel*
if merged.get("_emotional_context") and not existing.get("_emotional_context"):
    save_session_slots(session_id, merged)
```

The condition checked "did the late call introduce a new value where there wasn't one?" But it didn't catch the case where the late call **changes** an existing value. Concrete scenario:

1. Turn N-1: user expressed shame, session has `_emotional_context = "shame"`
2. Turn N message: "I already tried those, I need shelter instead" (B.2 negative_preference)
3. Early `_compute_tone_prefix`: `is_service_flow=False`, returns `_emotional_context_update=None` (most prefixes don't fire on non-service-flow)
4. No early save fires
5. B.2 promotes category to "service"
6. Late call with `is_service_flow=True` returns `_emotional_context_update="frustrated"`
7. `merged["_emotional_context"] = "frustrated"` (overwrites "shame" in memory)
8. `merged.get(...)` truthy ("frustrated"), `existing.get(...)` truthy ("shame") → condition False → **no save**
9. If response takes the follow-up path, no further save happens. The "frustrated" update is lost; the next turn loads "shame" from session.

**Fix landed:**

```python
new_value = merged.get("_emotional_context")
if new_value and new_value != existing.get("_emotional_context"):
    save_session_slots(session_id, merged)
```

The `new_value and ...` truthiness guard is preserved (rather than a naked `!=`) to protect against an artificially-empty `merged` dict where existing had context — which a naked `!=` would treat as "value cleared" and save the stripped-down dict. In real usage `merged` is always the full output of `merge_slots(existing, extracted)` and carries through existing fields, so the guard is defensive against the unit-test shape rather than a production scenario.

**Verification:** 4306 tests pass (was 4304 pre-fix, +2 new regression tests pinning the truthiness and inequality guards). The pinning test was renamed `test_late_persist_does_not_save_on_value_to_value_change` → `test_late_persist_saves_on_value_to_value_change` with the assertion inverted.


### 🟧 Suspect 1: Tone prefix asymmetry between follow-up paths

**Status:** Open (UX question). Tracked in `PHASE_AC_AFTERMATH.md::SUSPECT-1`. The asymmetry is now visible at the call site after Smell 6's `_build_follow_up_response` extraction — the `tone_prefix=""` default makes the difference legible rather than hidden.

**Location:** the two follow-up branches in `generate_reply` after handler dispatch — the first-turn service branch (`category == "service"`) and the subsequent-turn branch (`has_new_slots and existing.service_type and not pending_confirmation`).

The first follow-up block (first-turn service) prepends `tone_prefix`:
```python
follow_up = tone_prefix + next_follow_up_question(merged)
```

The subsequent-turn block does NOT:
```python
follow_up = next_follow_up_question(merged)
```

There's no comment explaining the difference. Possible explanations:

1. **Intentional:** by turn 2+, the emotional acknowledgment from turn 1 has already landed; repeating it would feel rote.
2. **Accidental:** copy-paste leftover from before tone prefixes existed.

If (1), the rationale should be commented. If (2), the second block is missing a behavior change worth ~0.1 score on tone-sensitive scenarios that span multiple turns.

I'd ask the team. The eval data probably has the answer — compare `peer_pregnant_doctor_bronx`-style scenarios that reach turn 2+ and check whether the tone prefix is missing where it shouldn't be.

---

## Code smells (not bugs, but worth fixing)

### ✅ Smell 1: dead imports and dead variables — FIXED

**Status:** Closed in audit follow-ups PR. `redact_pii` import dropped; `has_coords` local removed (also removed from `MessageContext` since no reader needed it).

**Original finding:**

```python
# Line 20 — never referenced; also not actually re-exported via __init__.py
from app.privacy.pii_redactor import redact_pii  # noqa: F401  (re-exported below)

# Line 124 — computed, marked unused, immediately discarded
has_coords = latitude is not None and longitude is not None  # noqa: F841
```

`detect_crisis` (line 31 at audit time) was kept — it IS legitimately re-exported (used as `app.services.chatbot.orchestrator.detect_crisis` patch target by 6+ test files). `redact_pii` was not — no test patched `orchestrator.redact_pii` and nothing imported it from there. It was leftover from before `_redact_with_safety_warning` was extracted to pipeline.py.

### 🟧 Smell 2: heavy use of underscore-prefixed local variables — partially addressed

**Status:** Open. Many `_*` locals were eliminated when their values moved onto `ctx` during Phases A–C, but some remain. Tracked in `PHASE_AC_AFTERMATH.md::SMELL-2`.

In Python, `_foo` at module scope means "private to module"; using it for function locals doesn't have a standard meaning. At audit time `generate_reply` defined ~25 underscore-prefixed locals (`_action_pre`, `_extraction_source`, `_llm_tone`, `_crisis_result`, `_response_tone`, `_pii_warning`, `_confidence`, `_tone_prefix`, `_emotional_context_update`, `_is_service_flow`, ...) alongside non-prefixed locals (`message`, `session_id`, `existing`, `merged`, `extracted`, `tone`, `category`, `action`, `pending`, `last_action`, ...).

The pattern seemed to be "leading underscore = intermediate value, no underscore = primary state" — but this wasn't documented and wasn't applied consistently. After the MessageContext migration, many of these became `ctx.X` fields (no underscore); a residual set remains in the orchestrator's local scope.

### ✅ Smell 3: inline import in hot path — FIXED

**Status:** Closed (moved to module-level). Also tracked as `PHASE_AC_AFTERMATH.md::SMELL-3` — hoisted to module-level imports in `orchestrator.py`, `confirmation.py`, and `pipeline.py`. Switched from aliased function imports to module imports to preserve test-mockability.

**Original finding:**

```python
# Line 459, inside the service-routing branch:
from app.services.slot_extraction import extract as extract_unified
```

Inline imports cost ~10μs per call (Python's import-cache lookup) and obscure the dependency graph. There was no circular-import risk — `slot_extraction` doesn't depend on `chatbot.orchestrator`.

### ✅ Smell 4: inline constants — FIXED

**Status:** Closed in Phase B. Both constants moved to module scope in `session_helpers.py`.

**Original finding:**

```python
# Line 236:
_CONSUMES_LAST_ACTION = {"confirm_yes", "confirm_deny"}

# Line 479:
_MAX_TRANSCRIPT = 20
```

These were constants in semantics but locals in scope. Each function call rebuilt them, and they were hard to discover (e.g., "what actions consume `_last_action`?" required knowing to look inside `generate_reply`).

### 🟧 Smell 5: awkward `if X: pass else:` structure

**Status:** Open (low priority). Tracked in `PHASE_AC_AFTERMATH.md::SMELL-5`.

**Location:** the queue-accept-and-post-results pre-dispatch block in `generate_reply` (the block guarded by `tone != "crisis"`).

```python
if tone == "crisis":
    pass  # handled below in routing
else:
    # 45 lines of queue-accept and post-results checks
    ...
```

Reads more naturally as:

```python
if tone != "crisis":
    # queue-accept and post-results checks
    ...
```

Or extract the body into a helper `_check_queue_and_post_results(...) -> dict | None`.

### ✅ Smell 6: response-building boilerplate repeats 3 times — FIXED

**Status:** Closed. Helper extracted as `_build_follow_up_response` in `chatbot/result_builder.py`; `SUSPECT-1`'s tone-prefix asymmetry now visible explicitly at the call sites (the `tone_prefix=""` default with explicit overrides makes the difference legible rather than hidden).

**Original finding:**

**Location (in pre-fix orchestrator):** lines 548–567, 569–583, 586–599.

Three near-identical blocks build a result dict with the same 7 keys, call `_log_turn`, and return. The differences are:
- The response text source (confirmation message vs. follow-up question)
- Whether `_tone_prefix` is prepended (yes / yes / **no** — see Suspect 1 above)
- Whether `quick_replies` come from `_confirmation_quick_replies` or `_follow_up_quick_replies`
- The category passed to `_log_turn`

**Resolution (now landed):** extracted helper:

```python
def _build_follow_up_response(
    session_id: str,
    redacted_message: str,
    response_text: str,
    merged: dict,
    quick_replies: list,
    log_category: str,
    request_id: str,
    tone: str | None,
) -> dict:
    result = {
        "session_id": session_id,
        "response": response_text,
        "follow_up_needed": True,
        "slots": merged,
        "services": [],
        "result_count": 0,
        "relaxed_search": False,
        "quick_replies": quick_replies,
    }
    _log_turn(session_id, redacted_message, result, log_category, request_id=request_id, tone=tone)
    return result
```

The three call sites become 1–2 lines each. The tone-prefix asymmetry surfaces explicitly: a `tone_prefix=""` default with explicit overrides at the call sites makes the difference visible rather than hidden.

### ✅ Smell 7: orchestrator does session-state mutation it shouldn't — FIXED

**Status:** Closed in Phase B. Helpers extracted into `chatbot/session_helpers.py`: `_append_to_transcript`, `_update_queued_services`, `_persist_emotional_context_early`, `_persist_emotional_context_late`, `_clear_stale_last_action`, `_consume_last_action`. Orchestrator delegates to these; the routing flow is now meaningfully more legible.

**Original finding:**

The orchestrator handled:
- Transcript append + truncation
- Queue-additional-services management
- `_awaiting_service_after_clear` flag clearing
- `_last_action` clearing
- `_emotional_context` persistence

These are session-state operations interleaved with routing logic. The orchestrator's job is supposedly "thread the pipeline, dispatch to handlers" — not "manage the session's queue, transcript, and emotional context."

**Possible fix:** consolidate into a `chatbot/session_helpers.py` module with functions like `_append_to_transcript(merged, redacted_message)`, `_update_queue(merged, extracted)`, `_persist_emotional_context(session_id, merged, existing)`. Reduces orchestrator by ~30 lines and makes the routing flow more visible.

### 🟧 Smell 8: mixed dispatch patterns

**Status:** Open (low priority). Tracked in `PHASE_AC_AFTERMATH.md::SMELL-8`. After the MessageContext migration the call shape is uniformly `_handle_X(ctx)` — the difference now is just whether the orchestrator returns immediately on a non-None result vs. checks the result against `None` and continues.

The dispatch oscillates between two styles:

**Pattern A — category-driven, immediate return:**
```python
if category == "reset":
    return _handle_reset(...)
```

**Pattern B — always-call, conditionally returns:**
```python
result = _handle_demographic_skip(...)
if result:
    return result
```

Pattern B fires for ~7 handlers (`_handle_demographic_skip`, `_handle_location_unknown`, `_handle_context_aware_confirm`, `_handle_pending_confirmation`, `_handle_post_results_interaction`, `_handle_spanish_detection`, `_handle_post_pending_confirmation`). These do their own internal "should I fire?" check and return None if not.

This is acceptable for handlers whose firing conditions are too complex for a category check (e.g., `_handle_post_results_interaction` looks at session state, not category). But the inconsistency makes the flow harder to trace.

**Possible fix:** standardize on returning `(matched: bool, result: dict | None)` or `result | None`, then use the `result := _handle_X(...)` walrus pattern:

```python
if (result := _handle_demographic_skip(ctx)) is not None:
    return result
```

Uniform call shape across both patterns.

### 🟧 Smell 9: `_empty_reply` doesn't log

**Status:** Open (low priority). Tracked in `PHASE_AC_AFTERMATH.md::SMELL-9`.

empty-message guard returns via `_empty_reply` without a `_log_turn` call. Every other return path in the orchestrator logs (verified all 24 handlers do). If the team wants empty-message events in audit logs, this is a gap.

**Fix:** add `_log_turn(session_id, "", result, "empty_message", request_id=request_id, tone=None)` before returning. Or accept the gap intentionally — but document it.

---

## Things I considered and decided are fine

**The 605-line file size.** The body is genuinely doing a lot of routing for a chatbot with 24 distinct handler categories, and the code is mostly well-commented at decision points. After the recommended refactors above, the file would shrink to ~480 lines — still long, but the structure would be cleaner.

**The 28 return points.** Routers naturally have many returns; "single return" doctrine doesn't apply here. The current pattern (each routing decision returns immediately) is more readable than alternatives like setting a result variable and falling through.

**The two `_compute_tone_prefix` calls.** Genuinely necessary — B.2 promotion changes `is_service_flow`, which changes the prefix. Could be optimized (compute once, recompute only on B.2 promotion), but the current version is clearer and the perf cost is microseconds.

**The B.2 negative_preference promotion logic.** The comment explains why (compound intent — "I already tried those, I need shelter instead" carries both rejection and new request). The implementation is right.

**The order of handlers.** Order matters here (e.g., post-results check must run before service flow because it might consume the message). The comments document the ordering constraints.

---

## Summary

| Category | Count at audit | Current status |
|---|---|---|
| Real bugs | 1 (emotional context save condition) | ✅ Fixed in BUG-1 PR |
| Suspect logic | 1 (tone prefix asymmetry) | 🟧 Open (`SUSPECT-1`) — UX question, not a code bug; the helper extraction in Smell 6 made the asymmetry visible at the call site |
| Dead code | 2 (`redact_pii` import, `has_coords` local) | ✅ Both removed |
| Refactor opportunities | 7 (MessageContext adoption was the big one) | ✅ MessageContext adoption complete (Phases A–D); Smells 3, 4, 6, 7 closed; Smells 2, 5, 8, 9 remain open as low-priority polish |

**Original recommended priority (with current status):**

1. ~~**Bug 1** (emotional context save)~~ — ✅ Fixed in BUG-1 PR.
2. **Suspect 1** (tone prefix asymmetry) — 🟧 Still open as `SUSPECT-1`. Needs a UX call from the team; the helper from Smell 6 surfaces the asymmetry at the call site for whoever decides.
3. ~~**Smells 1, 3, 4** (dead imports, inline import, inline constants)~~ — ✅ All closed.
4. ~~**Smell 6** (response-building helper)~~ — ✅ `_build_follow_up_response` shipped.
5. ~~**MessageContext adoption**~~ — ✅ Phases A–C complete; Phase D landed in PR #76.
6. **Smells 5, 7, 8, 9** — Smell 7 closed (session helpers extracted). Smells 2, 5, 8, 9 remain open as opportunistic-fix items.

---

## Phase tracking — MessageContext adoption (PR-α)

The "MessageContext adoption" item from the priority list above was scoped into four phases for incremental delivery. Each phase migrates a related cluster of handlers from positional args to a single ``ctx: MessageContext`` parameter. Phases A–C are complete; Phase D is deferred.

### Phase A — meta and general handlers

**Status:** ✅ Complete.

**Scope:** ``meta.py`` (``_handle_help``, ``_handle_bot_identity``, ``_handle_bot_capability_question``, ``_handle_confused``, ``_handle_reset``, ``_handle_greeting``, ``_handle_thanks``) and ``general.py`` (``_handle_general_conversation``).

**Notes:** First phase; established the dataclass and the late-set pattern (``tone_prefix``, ``merged``). No behavior changes intended.

### Phase B — emotional and accessibility handlers

**Status:** ✅ Complete.

**Scope:** ``emotional.py`` (``_handle_emotional``, ``_handle_escalation``, ``_handle_frustration``, ``_handle_crisis``) and the two ctx-friendly accessibility handlers (``_handle_demographic_skip``, ``_handle_location_unknown``). ``_handle_spanish_detection`` was deferred to Phase D at the time of this phase — Phase D has since landed; see below.

**Notes:** This phase also extracted session-mutation helpers into ``session_helpers.py`` (Smell 7 in this doc): ``_append_to_transcript``, ``_update_queued_services``, ``_persist_emotional_context_early``, ``_persist_emotional_context_late``, ``_clear_stale_last_action``, ``_consume_last_action``. Smells 1 and 4 also addressed (dead ``redact_pii`` import removed, ``_CONSUMES_LAST_ACTION`` and ``_MAX_TRANSCRIPT`` moved to module scope). Smell 6 addressed via ``_build_follow_up_response``.

### Phase C — confirmation and post-results handlers

**Status:** ✅ Complete.

**Scope:** ``confirmation.py`` (``_handle_correction``, ``_handle_negative_preference``, ``_handle_change_location_request``, ``_handle_change_service_request``, ``_handle_context_aware_confirm``, ``_handle_pending_confirmation``, ``_handle_post_pending_confirmation``, ``_promote_queued_offer``) and ``post_results.py`` (``_handle_post_results_interaction``, ``_handle_show_more``, ``_handle_sort_results``, ``_handle_post_results_question``, ``_handle_hours_for_day``).

**Architectural change:** ``MessageContext`` construction was moved up in ``generate_reply`` to happen BEFORE the queue-accept and post-results fast paths, so those handlers could also take ``ctx``. ``_compute_routing_category`` and ``_clear_stale_last_action`` moved up alongside it. The reorder is safe because (a) ``_compute_routing_category`` only depends on tone/action/has_service_intent/early_extracted/extraction_source/message — all finalized before the fast paths run, and (b) ``_clear_stale_last_action`` is a no-op for the case the post-results fast path's ``_last_action`` guard cares about. Pinned by ``test_routing_category_order.py`` and ``test_show_more_after_emotional_clears_stale_last_action`` in ``test_multi_turn_and_context.py``.

**Notes on snapshot args (post-PR-#76 shape):** Three handlers operate on captured snapshot values that the orchestrator consumes after the dispatcher mutates the underlying session state:

* `_handle_context_aware_confirm` — uses `ctx.snapshot_last_action` (snapshot of `existing.get("_last_action")` from before dispatch)
* `_handle_pending_confirmation` — uses `ctx.snapshot_pending` (snapshot of `existing.get("_pending_confirmation")`)
* `_handle_post_pending_confirmation` — uses `ctx.snapshot_response_tone` (pre-promotion tone, captured before B.2 negative-preference promotion can reassign `tone = "frustrated"`)

In each case the snapshot is needed because the dispatcher mutates the same key on `ctx.existing` and the orchestrator consumes the pre-mutation value afterward (e.g., `_consume_last_action(session_id, existing, last_action)`). Earlier shapes considered: re-read from `ctx.existing` (wrong — re-read sees post-mutation), pass as second positional arg (Phase C shape, replaced in PR #76), add to `MessageContext` as ctx fields (chosen in `D-5`). The ctx-resident form is declarative — readers can see the snapshot was captured at construction time by reading the ctx, no control-flow indirection. `TestSnapshotArgSemantics` in `test_chatbot_extracted_helpers.py` pins the divergence between `ctx.existing.get(...)` and `ctx.snapshot_*` so the contract can't accidentally collapse.

Two other handlers take auxiliary runtime data that isn't a snapshot: `_handle_hours_for_day(ctx, post_intent)` and `_promote_queued_offer(ctx, offer, location_override=None)`.

### Phase D — accessibility handler migration + Stage 2 snapshot cleanup

**Status:** ✅ Complete (PR #76).

**Scope landed:**
- All three accessibility handlers migrated from 6-arg signatures to ctx-only: `_handle_demographic_skip`, `_handle_location_unknown`, `_handle_spanish_detection`.
- The Phase C "second positional arg" pattern for snapshot values was simplified into ctx-resident `snapshot_last_action`, `snapshot_pending`, `snapshot_response_tone` fields (`D-5`).
- `MessageContext.merged` annotation kept as `Optional[dict]` but with a new `require_merged()` accessor that raises a descriptive `RuntimeError` if read before the late-set runs (`D-3`).
- `_response_tone` local alias removed from the orchestrator; pre-promotion tone now captured into `ctx.snapshot_response_tone` immediately after construction, before the B.2 negative-preference promotion (`D-4`). Self-found regression caught and pinned by `test_b2_post_pending_uses_pre_promotion_tone`.
- Test infrastructure for the migration: `make_ctx(**overrides)` builder in `tests/conftest.py` (`D-2`); 14 new curly-apostrophe tests for `_handle_demographic_skip` (`TEST-GAP-1`); 5 `TestSnapshotArgSemantics` tests pinning the divergence between `ctx.existing.get(...)` and `ctx.snapshot_*` (`TEST-GAP-2`); `test_apostrophe_fuzz.py` wired through to the now-migrated handlers (`ENG-1`).

See `PHASE_AC_AFTERMATH.md::D-1` through `D-5`, `TEST-GAP-1`, `TEST-GAP-2`, and `ENG-1` for per-item resolution detail.

### Known-deferred items (open as of this audit refresh)

* **Suspect 1 (tone prefix asymmetry):** Documented at the call site (orchestrator service-flow continuation block). Resolution is a UX question — whether to repeat the empathic prefix on follow-up turns within a single conversation. Tracked as `SUSPECT-1` in `PHASE_AC_AFTERMATH.md`.
* **Smells 2, 5, 8, 9:** Audit-flagged as "fix opportunistically". Tracked individually in `PHASE_AC_AFTERMATH.md`.
