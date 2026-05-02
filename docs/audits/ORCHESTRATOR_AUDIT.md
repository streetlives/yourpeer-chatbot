# orchestrator.py audit

**File:** `backend/app/services/chatbot/orchestrator.py`
**Lines:** 605
**Function under audit:** `generate_reply` (~520 lines, 28 return points)

---

## Top finding: the planned refactor was never finished

`MessageContext` is defined in `context.py` (50+ lines, 20+ fields) and exported from the chatbot package. Its docstring says:

> *"All state produced by the classification pipeline for a single message. Built once at the start of `generate_reply`, consumed by handler functions. **Replaces the 15+ local variables that were previously shared via closure inside the monolithic generate_reply()**."*

But **no production code instantiates `MessageContext`.** The orchestrator threads each variable as a separate argument; handlers accept individual params; the class is dead weight pointing at the migration's intended end state.

The cost of not finishing this:

- 15+ handlers each accept 5–9 positional args, with **inconsistent ordering** between them. Examples:
  - `_handle_reset(session_id, redacted_message, category, tone, request_id)` — 5 args, no `existing`
  - `_handle_correction(session_id, redacted_message, existing, tone, request_id)` — 5 args, no `category`
  - `_handle_greeting(session_id, redacted_message, existing, category, tone, request_id)` — 6 args
  - `_handle_help(session_id, message, redacted_message, existing, _response_tone, category, tone, _tone_prefix, request_id)` — 9 args
- Adding a field (e.g., `extraction_source` last week) requires updating every signature in the chain. We did this through the orchestrator only, but if a downstream handler ever needs the source, the cascade widens.
- The orchestrator becomes a "thread arguments and dispatch" function whose primary job is plumbing.

**Recommended:** Adopt `MessageContext` as designed. Build it once at line ~225 (after classification completes), pass it to every handler. Handler signatures collapse from 5–9 positional args to a single `ctx: MessageContext` parameter (plus session-mutation helpers as needed).

This is a meaningful refactor — touches every handler — but the work is mechanical and the dataclass already exists. Estimated 1–2 days; reduces orchestrator.py by ~80–120 lines and improves every handler's signature.

---

## Real bugs / suspect logic

### 🟥 Bug 1: late `_emotional_context_update` can be lost on follow-up paths

**Location:** lines 516–534

**Setup:** `_compute_tone_prefix` is called twice — once early (line 275) for help/confused/emotional handlers, once late (line 516) to handle the B.2 promotion case (negative_preference → service changes `is_service_flow=False` to `True`).

**Problem:**

```python
# Line 495: saves merged with whatever emotional_context was set early
save_session_slots(session_id, merged)

# Line 516: late call may compute a different _emotional_context_update
_tone_prefix, _emotional_context_update = _compute_tone_prefix(...)

# Line 528: writes new value to merged (in-memory only)
if _emotional_context_update is not None:
    merged["_emotional_context"] = _emotional_context_update

# Line 533: re-saves ONLY if the value is *novel*
if merged.get("_emotional_context") and not existing.get("_emotional_context"):
    save_session_slots(session_id, merged)
```

The condition checks "did the late call introduce a new value where there wasn't one?" But it doesn't catch the case where the late call **changes** an existing value. Concrete scenario:

1. Turn N-1: user expressed shame, session has `_emotional_context = "shame"`
2. Turn N message: "I already tried those, I need shelter instead" (B.2 negative_preference)
3. Early `_compute_tone_prefix` at line 275: `is_service_flow=False`, returns `_emotional_context_update=None` (most prefixes don't fire on non-service-flow)
4. Line 286: `_emotional_context_update is None` so no early save
5. B.2 promotes category to "service"
6. Line 516: late call with `is_service_flow=True` returns `_emotional_context_update="frustrated"`
7. Line 528: `merged["_emotional_context"] = "frustrated"` (overwrites "shame" in memory)
8. Line 533: `merged.get(...)` truthy ("frustrated"), `existing.get(...)` truthy ("shame") → condition False → **no save**
9. If response takes the follow-up path (line 570 or 586), no further save happens. The "frustrated" update is lost; the next turn loads "shame" from session.

**Fix:** condition should be "save if the value differs from what's persisted":

```python
if merged.get("_emotional_context") != existing.get("_emotional_context"):
    save_session_slots(session_id, merged)
```

The blast radius is narrow (requires emotional context change between early and late computation AND a follow-up path response). I don't have evidence this affects an eval scenario today, but it's a real correctness bug.

### 🟧 Suspect 1: Tone prefix asymmetry between follow-up paths

**Location:** lines 569-583 vs 586-599

The first follow-up block (when `category == "service"`) prepends `_tone_prefix`:
```python
follow_up = _tone_prefix + next_follow_up_question(merged)
```

The second follow-up block (subsequent service turns where `has_new_slots and existing.service_type and not _pending_confirmation`) does NOT:
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

### 🟧 Smell 1: dead imports and dead variables

```python
# Line 20 — never referenced; also not actually re-exported via __init__.py
from app.privacy.pii_redactor import redact_pii  # noqa: F401  (re-exported below)

# Line 124 — computed, marked unused, immediately discarded
has_coords = latitude is not None and longitude is not None  # noqa: F841
```

`detect_crisis` (line 31) IS legitimately re-exported (used as `app.services.chatbot.orchestrator.detect_crisis` patch target by 6+ test files). `redact_pii` is not — no test patches `orchestrator.redact_pii` and nothing imports it from there. It's leftover from before `_redact_with_safety_warning` was extracted to pipeline.py.

**Fix:** delete both (1 line each). Keep `detect_crisis`.

### 🟧 Smell 2: heavy use of underscore-prefixed local variables

In Python, `_foo` at module scope means "private to module"; using it for function locals doesn't have a standard meaning. `generate_reply` defines ~25 underscore-prefixed locals (`_action_pre`, `_extraction_source`, `_llm_tone`, `_crisis_result`, `_response_tone`, `_pii_warning`, `_confidence`, `_tone_prefix`, `_emotional_context_update`, `_is_service_flow`, ...) alongside non-prefixed locals (`message`, `session_id`, `existing`, `merged`, `extracted`, `tone`, `category`, `action`, `pending`, `last_action`, ...).

The pattern seems to be "leading underscore = intermediate value, no underscore = primary state" — but this isn't documented and isn't applied consistently. It's noise in diffs and reviews.

**Fix:** drop the leading underscores. This is part of the MessageContext refactor anyway.

### 🟦 Smell 3: inline import in hot path

```python
# Line 459, inside the service-routing branch:
from app.services.slot_extraction import extract as extract_unified
```

Inline imports cost ~10μs per call (Python's import-cache lookup) and obscure the dependency graph. There's no circular-import risk: `slot_extraction` doesn't depend on `chatbot.orchestrator`.

**Fix:** move to module-level imports at the top.

### 🟦 Smell 4: inline constants

```python
# Line 236:
_CONSUMES_LAST_ACTION = {"confirm_yes", "confirm_deny"}

# Line 479:
_MAX_TRANSCRIPT = 20
```

These are constants in semantics but locals in scope. Each function call rebuilds them, and they're hard to discover (e.g., "what actions consume `_last_action`?" requires knowing to look inside `generate_reply`).

**Fix:** move to module scope.

### 🟦 Smell 5: awkward `if X: pass else:` structure

**Location:** lines 167–213

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

### 🟦 Smell 6: response-building boilerplate repeats 3 times

**Location:** lines 548–567, 569–583, 586–599

Three near-identical blocks build a result dict with the same 7 keys, call `_log_turn`, and return. The differences are:
- The response text source (confirmation message vs. follow-up question)
- Whether `_tone_prefix` is prepended (yes / yes / **no** — see Suspect 1 above)
- Whether `quick_replies` come from `_confirmation_quick_replies` or `_follow_up_quick_replies`
- The category passed to `_log_turn`

**Fix:** extract a helper:

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

### 🟦 Smell 7: orchestrator does session-state mutation it shouldn't

The orchestrator handles:
- Transcript append + truncation (lines 475–481)
- Queue-additional-services management (lines 483–493)
- `_awaiting_service_after_clear` flag clearing (line 445)
- `_last_action` clearing (lines 386–389)
- `_emotional_context` persistence (lines 285–287, 527–534)

These are session-state operations interleaved with routing logic. The orchestrator's job is supposedly "thread the pipeline, dispatch to handlers" — not "manage the session's queue, transcript, and emotional context."

**Possible fix:** consolidate into a `chatbot/session_helpers.py` module with functions like `_append_to_transcript(merged, redacted_message)`, `_update_queue(merged, extracted)`, `_persist_emotional_context(session_id, merged, existing)`. Reduces orchestrator by ~30 lines and makes the routing flow more visible.

### 🟦 Smell 8: mixed dispatch patterns

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

### 🟦 Smell 9: `_empty_reply` doesn't log

Line 102: empty-message guard returns via `_empty_reply` without a `_log_turn` call. Every other return path in the orchestrator logs (verified all 24 handlers do). If the team wants empty-message events in audit logs, this is a gap.

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

| Category | Count | Action |
|---|---|---|
| Real bugs | 1 (emotional context save condition) | Fix |
| Suspect logic | 1 (tone prefix asymmetry) | Investigate; document or fix |
| Dead code | 2 (`redact_pii` import, `has_coords` local) | Delete |
| Refactor opportunities | 7 (MessageContext adoption is the big one) | Plan |

**Recommended priority:**

1. **Bug 1** (emotional context save) — narrow but real, fix the condition
2. **Suspect 1** (tone prefix asymmetry) — ask the team, document or fix
3. **Smells 1, 3, 4** (dead imports, inline import, inline constants) — trivial cleanup, ~5 minute fix
4. **Smell 6** (response-building helper) — small refactor, surfaces Suspect 1 explicitly
5. **MessageContext adoption** — the big one. Mechanical work, large surface, big payoff. Schedule when the eval/feature tempo allows.
6. **Smells 5, 7, 8, 9** — fix opportunistically, not worth a dedicated PR

---

## Phase tracking — MessageContext adoption (PR-α)

The "MessageContext adoption" item from the priority list above was scoped into four phases for incremental delivery. Each phase migrates a related cluster of handlers from positional args to a single ``ctx: MessageContext`` parameter. Phases A–C are complete; Phase D is deferred.

### Phase A — meta and general handlers

**Status:** ✅ Complete.

**Scope:** ``meta.py`` (``_handle_help``, ``_handle_bot_identity``, ``_handle_bot_capability_question``, ``_handle_confused``, ``_handle_reset``, ``_handle_greeting``, ``_handle_thanks``) and ``general.py`` (``_handle_general_conversation``).

**Notes:** First phase; established the dataclass and the late-set pattern (``tone_prefix``, ``merged``). No behavior changes intended.

### Phase B — emotional and accessibility handlers

**Status:** ✅ Complete.

**Scope:** ``emotional.py`` (``_handle_emotional``, ``_handle_escalation``, ``_handle_frustration``, ``_handle_crisis``) and the two ctx-friendly accessibility handlers (``_handle_demographic_skip``, ``_handle_location_unknown``). ``_handle_spanish_detection`` deferred to Phase D — see below.

**Notes:** This phase also extracted session-mutation helpers into ``session_helpers.py`` (Smell 7 in this doc): ``_append_to_transcript``, ``_update_queued_services``, ``_persist_emotional_context_early``, ``_persist_emotional_context_late``, ``_clear_stale_last_action``, ``_consume_last_action``. Smells 1 and 4 also addressed (dead ``redact_pii`` import removed, ``_CONSUMES_LAST_ACTION`` and ``_MAX_TRANSCRIPT`` moved to module scope). Smell 6 addressed via ``_build_follow_up_response``.

### Phase C — confirmation and post-results handlers

**Status:** ✅ Complete.

**Scope:** ``confirmation.py`` (``_handle_correction``, ``_handle_negative_preference``, ``_handle_change_location_request``, ``_handle_change_service_request``, ``_handle_context_aware_confirm``, ``_handle_pending_confirmation``, ``_handle_post_pending_confirmation``, ``_promote_queued_offer``) and ``post_results.py`` (``_handle_post_results_interaction``, ``_handle_show_more``, ``_handle_sort_results``, ``_handle_post_results_question``, ``_handle_hours_for_day``).

**Architectural change:** ``MessageContext`` construction was moved up in ``generate_reply`` to happen BEFORE the queue-accept and post-results fast paths, so those handlers could also take ``ctx``. ``_compute_routing_category`` and ``_clear_stale_last_action`` moved up alongside it. The reorder is safe because (a) ``_compute_routing_category`` only depends on tone/action/has_service_intent/early_extracted/extraction_source/message — all finalized before the fast paths run, and (b) ``_clear_stale_last_action`` is a no-op for the case the post-results fast path's ``_last_action`` guard cares about. Pinned by ``test_routing_category_order.py`` and ``test_show_more_after_emotional_clears_stale_last_action`` in ``test_multi_turn_and_context.py``.

**Notes on second-arg dispatchers:** Three handlers take a second positional arg alongside ``ctx``:

* ``_handle_context_aware_confirm(ctx, last_action)`` — captured snapshot of ``existing.get("_last_action")`` from before dispatch
* ``_handle_pending_confirmation(ctx, pending)`` — captured snapshot of ``existing.get("_pending_confirmation")``
* ``_handle_post_pending_confirmation(ctx, response_tone)`` — captured pre-promotion tone

In each case the second arg is needed because the handler mutates the same key on ``ctx.existing`` and the orchestrator consumes the pre-mutation value afterward (e.g., ``_consume_last_action(session_id, existing, last_action)``). Three options were considered (re-read from ctx.existing — wrong because re-read sees post-mutation; pass as second positional — chosen; add to MessageContext — rejected as ctx-bloat for snapshot values).

Two other handlers take auxiliary runtime data: ``_handle_hours_for_day(ctx, post_intent)`` and ``_promote_queued_offer(ctx, offer, location_override=None)``.

### Phase D — Spanish detection handler (deferred)

**Status:** Not started.

**Scope:** ``_handle_spanish_detection`` is the last positional-args handler in ``handlers/``.

**Why deferred:** It has 4 direct unit-test invocations in ``tests/unit/test_chatbot_extracted_helpers.py``. Migration requires adding a ctx-construction fixture for those tests. Low impact (the handler is straightforward and currently works correctly), but mechanical work that's worth doing for consistency.

### Known-deferred items

* **Bug 1 (late ``_emotional_context`` save):** The buggy save-condition is preserved bit-for-bit in ``_persist_emotional_context_late`` with a clear note in its docstring and a regression test that pins the BUGGY behavior in ``test_late_persist_does_not_save_on_value_to_value_change``. When the fix lands (one-character change: ``and not`` → ``!=``), invert the test's ``save_recorder`` assertion.
* **Suspect 1 (tone prefix asymmetry):** Documented at the call site (orchestrator service-flow continuation block). Resolution is a UX question — whether to repeat the empathic prefix on follow-up turns within a single conversation.
* **Smells 2, 3, 5, 8, 9:** Not addressed. Audit-flagged as "fix opportunistically".
