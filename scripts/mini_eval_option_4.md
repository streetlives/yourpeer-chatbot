# `mini_eval_option_4.py` — companion doc

Prompt-level validation script for the **Option 4 hardening** applied to
`slot_extraction/prompts.py`. Confirms that the updated short-path prompt
produces the expected `service_type` primary pick across a curated set of
multi-intent messages, without needing the full eval harness or Opus judge.

## Purpose

When `_SHORT_SYSTEM_PROMPT` changed to teach "first-mentioned wins unless a
safety signal is present," we needed a fast way to verify the LLM actually
followed the new guidance. The full eval (171 scenarios, ~30-60 min, ~$15-25)
is too expensive for iterative prompt tuning. The unit tests verify the prompt
TEXT looks right but don't exercise Haiku itself.

This script sits in between: 6 live Haiku calls, 30 seconds, ~$0.01. If any
case fails, the prompt needs iteration before running the full eval.

## When to use

- After editing `_SHORT_SYSTEM_PROMPT` (any time the prompt changes)
- Before running the full unified eval, as a smoke check that the short path
  still behaves correctly on the watch-list patterns
- After applying adjacent changes that could affect short-path LLM behavior
  (e.g., Option 2b, which changed merge logic — the mini-eval confirmed the
  short path was unaffected)

## When NOT to use

- For regressions that involve multi-turn conversation state
  (the script runs single-turn extraction only)
- For regressions in the narrative path — this script only hits the short
  path; use `mini_eval_r36_regressions.py` for narrative coverage
- For end-to-end flow validation (confirmation, search, response tone) —
  that's what the full eval is for

## How to run

```bash
cd /path/to/yourpeer-chatbot
USE_UNIFIED_EXTRACTOR=1 ANTHROPIC_API_KEY=sk-... \
    python scripts/mini_eval_option_4.py
```

Optional flags:

| Flag | Effect |
|---|---|
| `--verbose` | Print full LLM response for every case, not just failures |
| `--only-watch-list` | Skip the 2 safety-override sanity cases (faster, ~20s) |

Environment requirements:

- `ANTHROPIC_API_KEY` must be set (the script calls Haiku directly)
- `USE_UNIFIED_EXTRACTOR=1` must be set (the script uses the unified extraction
  path, not the legacy `extract_slots_smart`)

## The 6 cases

### 4 watch-list cases — first-mentioned should win

These are the multi-intent patterns that regressed under unified in R36 without
Option 4. Each has **no safety signal**, so the first-mentioned service should
be primary.

| Case | Message | Expected primary | Rationale |
|---|---|---|---|
| `multi_food_and_shelter_brooklyn` | "I need food and a place to sleep in Brooklyn" | `food` | food mentioned first |
| `multi_shower_and_food_drop_in` | "Where can I get a shower and something to eat in Manhattan?" | `personal_care` | shower mentioned first |
| `multi_clothing_and_food_harlem` | "I need some clean clothes and a meal in Harlem" | `clothing` | clothing mentioned first |
| `multi_cross_neighborhood_shower_les_food_chinatown` | "I want to shower in the Lower East Side and grab food in Chinatown" | `personal_care` | shower mentioned first |

The 4th case also tests that per-service location binding works under the new
prompt — the LLM should associate shower with LES and food with Chinatown.

### 2 sanity cases — safety signal should override first-mentioned

These verify that the override rule (shelter/medical wins when safety signal
present) still fires correctly, preventing the "first-mentioned" rule from
being over-applied.

| Case | Message | Expected primary | Trigger phrase |
|---|---|---|---|
| `sanity_food_shelter_tonight` | "I need food and somewhere to sleep tonight" | `shelter` | `tonight` |
| `sanity_job_shelter_nowhere` | "I need a job but I have nowhere to go right now" | `shelter` | `nowhere to go`, `right now` |

If either of these picks the first-mentioned service, the safety override is
lost and the prompt needs fixing before shipping.

## Output interpretation

**All 6 passing:**

```
------------------------------------------------------------------------------
Result: 6/6 passed
  Watch-list (first-mentioned recovery): 4/4
  Sanity (safety-signal override):        2/2
------------------------------------------------------------------------------
✓ All cases pass. Option 4 hardening recovers the blind-spot scenarios
  and preserves the safety-signal override.
```

Proceed to the next validation step (full eval or `mini_eval_r36_regressions.py`).

**Watch-list failing:**

If any of the 4 watch-list cases fails, the LLM is not picking first-mentioned
as primary. Possible causes:
- Prompt wording is still ambiguous (the LLM is interpreting something differently
  than intended)
- Haiku non-determinism — re-run 2-3 times before declaring failure
- The example in the prompt doesn't match the test case closely enough —
  consider adding a closer worked example

**Sanity failing:**

If either sanity case fails (i.e., safety signal didn't trigger override), the
prompt's safety-signal list is incomplete or the priority rule isn't firing.
This is a more serious failure than watch-list — safety-adjacent cases are more
consequential than ordering for multi-intent lookups.

**Mixed results:**

Usually indicates prompt needs another iteration. The mini-eval is cheap enough
to run repeatedly while iterating on the prompt wording.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All cases pass |
| 1 | One or more cases failed |
| 2 | Usage or environment error (missing API key, missing flag, etc.) |

## Implementation notes

- Uses `app.llm.claude_client.extract_slots_short` directly — bypasses the
  regex stage and merge logic, so it isolates prompt behavior
- `max_tokens=500` per call — enough for the schema's 10 fields, not bloated
- No retry logic — if Haiku returns malformed output, that counts as a failure
  (the prompt needs fixing)
- Temperature is fixed at 0 in the underlying `extract_slots_short` call —
  reduces non-determinism but doesn't eliminate it

## Cost

~$0.01 per full run (6 cases × ~2000 input tokens × Haiku pricing). Negligible
compared to the full eval ($15-25).

## Related files

- `backend/app/services/slot_extraction/prompts.py` — the prompt this validates
- `tests/unit/test_slot_extraction.py::TestPromptSanity` — 4 structural tests
  on the prompt text (runs in CI, doesn't need API key)
- `UNIFIED_EXTRACTOR_MIGRATION.md` §"Phase 2 — Option 4 hardening (applied)" —
  rationale and history
- `eval-r36/r36-analysis.md` — the R36 finding that motivated Option 4
