# `mini_eval_r36_regressions.py` — companion doc

Targeted-subset eval runner that executes the 15 scenarios from R36 that
either failed, regressed, or were identified as watch-list/headline-win
scenarios requiring monitoring. Produces the same JSON report shape as the
full eval (compatible with `compare_eval_reports.py`) but in ~3-6 minutes
instead of 30-60.

## Purpose

After applying changes targeted at specific R36 failures (Option 4 for
Category A watch-list, Option 2b for Category B `natural_long_story`, future
fixes for Category C), we want fast feedback on whether those scenarios
actually recovered before committing to a full eval run.

A full eval is the gold standard for signal, but it costs ~$15-25 and takes
~30-60 minutes. For most iteration cycles, running the 15 highest-signal
scenarios is enough to decide whether to proceed to full eval or iterate
further.

## When to use

- After applying a targeted fix (Option 4, Option 2b, future C.1/C.2/C.3 ports)
- As a smoke check before running the full unified eval
- When investigating regressions — run `--subset failing` to get fast feedback
  on just the scenarios that were <4.0 in R36
- As a regression guard when touching anything in the extraction or merge
  pipeline

## When NOT to use

- For scenarios not in the curated list (e.g., a newly-discovered failure) —
  add the scenario to `_TARGETS` first, or use `eval_llm_judge.py
  --scenario-id X` directly
- As a replacement for the full eval before Phase 3 flip — acceptance criteria
  require full-eval numbers, not mini-eval numbers
- For rapid prompt iteration on short-path behavior only — use
  `mini_eval_option_4.py` (no Opus judge call, ~30s, ~$0.01)

## How to run

```bash
cd /path/to/yourpeer-chatbot
USE_UNIFIED_EXTRACTOR=1 ANTHROPIC_API_KEY=sk-... \
    python scripts/mini_eval_r36_regressions.py
```

Three subset modes:

| Invocation | Scenarios | Time | Cost |
|---|---|---|---|
| Default | 15 (all target scenarios) | ~3-6 min | ~$2-4 |
| `--subset option-4` | 5 (4 watch-list + big-win) | ~60-90s | ~$0.50 |
| `--subset failing` | 12 (R36 unified <4.0) | ~3-5 min | ~$2-3 |

Optional flags:

| Flag | Effect |
|---|---|
| `--output FILE.json` | Save JSON report in the same shape as the full eval |
| `--verbose` | Print the Opus judge's `overall_notes` for each scenario |

Environment requirements:

- `ANTHROPIC_API_KEY` must be set (calls Haiku for the chatbot, Opus for the
  judge — same as the full eval)
- `USE_UNIFIED_EXTRACTOR=1` must be set (the script's baselines are from R36
  Unified, so it's meaningless to run against legacy — the script will refuse
  to start without this flag)

## The 15 scenarios (target set)

Organized by category. R36 Legacy and R36 Unified baselines are hardcoded in
the script's `_TARGETS` list for at-a-glance comparison.

### [A] Watch-list (Option 4 targets) — 4 scenarios

| Scenario | R36 Leg | R36 Uni | Note |
|---|---|---|---|
| `multi_food_and_shelter_brooklyn` | 4.36 | 3.91 | First-mentioned food expected |
| `multi_shower_and_food_drop_in` | 4.45 | 4.00 | First-mentioned personal_care expected |
| `multi_cross_neighborhood_shower_les_food_chinatown` | 4.00 | 3.55 | Cross-neighborhood binding test |
| `multi_clothing_and_food_harlem` | 4.55 | 4.64 | Already passing — verify no regression |

### [B] Narrative (Option 2b targets) — 1 scenario

| Scenario | R36 Leg | R36 Uni | Note |
|---|---|---|---|
| `natural_long_story` | 4.45 | 3.91 | 30-word narrative, hospital=context |

### [C] Port needed — 3 scenarios

| Scenario | R36 Leg | R36 Uni | Note |
|---|---|---|---|
| `confirm_multi_change` | 4.73 | 3.55 | C.1 — button-click "Change service" flow |
| `accessibility_low_literacy` | 4.73 | 3.73 | C.2 — "were food broklyn free" typo tolerance |
| `multi_accept_queued_shelter` | 4.36 | 3.82 | C.3 — may recover from Option 4 as side effect |

### [D] Borderline (near threshold) — 3 scenarios

Primary slots extracted correctly; drops are from dimension-score variance
near the 4.0 threshold. Worth tracking because they may drift back up if
the underlying multi-intent primary is now stable under Option 4.

| Scenario | R36 Leg | R36 Uni |
|---|---|---|
| `multiturn_change_mind` | 4.00 | 3.91 |
| `peer_young_mom_multiple_needs` | 4.18 | 3.91 |
| `wa_substance_use_shelter` | 4.09 | 3.91 |

### [H] Held from legacy — 2 scenarios

Were failing before the migration and are still failing — not a regression
from the migration, but on the list so we notice if they shift further.

| Scenario | R36 Leg | R36 Uni |
|---|---|---|
| `wa_negative_preference` | 3.82 | 3.82 |
| `peer_aging_out_foster` | 3.73 | 3.55 (worse) |

### [L] Long-standing failure — 1 scenario

`peer_diabetic_insulin` is a known confirmation-flow bug that predates the
migration. Tracked so we know if it recovers incidentally or worsens.

| Scenario | R36 Leg | R36 Uni |
|---|---|---|
| `peer_diabetic_insulin` | 2.64 | 3.00 (improving) |

### [W] Migration headline win — 1 scenario

The scenario that motivated the whole migration. Must stay ≥ 4.5; anything
below indicates a regression in the core set-equality rule that Option 4 or
Option 2b may have accidentally disturbed.

| Scenario | R36 Leg | R36 Uni |
|---|---|---|
| `multi_cross_borough_food_brooklyn_shelter_manhattan` | 2.82 | 4.73 |

## Output interpretation

### Per-scenario output

```
[1/15] [A] multi_food_and_shelter_brooklyn (R36 uni=3.91) ... ✅ 4.73/5.0  Δ=+0.82▲▲  (8.1s)
```

Fields:
- `[1/15]` — progress counter
- `[A]` — category letter (see table above)
- `R36 uni=3.91` — baseline from R36 Unified
- `✅` — pass emoji (`⚠️` for 3.0-3.99, `❌` for <3.0)
- `4.73/5.0` — this run's average score
- `Δ=+0.82▲▲` — delta vs R36 Unified; `▲▲` flags ≥ +0.30, `▲` flags ≥ +0.05,
  `·` negligible, `▼` / `▼▼` regressions

### Summary table

After the per-scenario output, a category-grouped summary shows legacy,
unified, and current scores side-by-side:

```
[A] watch-list (Option 4)
---------------------------------------------------------------------
scenario                                    R36 L   R36 U    this  vs U   pass?
---------------------------------------------------------------------
multi_food_and_shelter_brooklyn              4.36    3.91    4.73 +0.82▲▲    ✓
...
```

### Aggregate stats

- `Total / Passing / Failing / Critical failures` — headline counts
- `Recovered` — were failing, now passing (key success metric)
- `New regressions` — were passing, now failing (key failure metric)

## Decision tree after running

| Outcome | Action |
|---|---|
| All 15 passing, no new regressions | Run full unified eval for complete signal |
| Recovered A + B, C still failing, no new regressions | Run full eval; address C after per R36 action plan |
| Any NEW regressions (was ≥ 4.0, now < 4.0) | STOP — investigate before full eval |
| Watch-list fails (A scenarios still < 4.0) | Re-run 1-2× to rule out Haiku flake; if persistent, Option 4 needs iteration |
| `multi_cross_borough` drops below 4.5 | STOP — migration's headline win is at risk |
| `natural_long_story` still < 4.0 after Option 2b | Check if narrative-path branch is firing correctly; verify test coverage |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All 15 (or subset) scenarios pass |
| 1 | One or more scenarios failing |
| 2 | Usage or environment error (missing API key, missing flag, invalid subset name) |

Note: exit code 1 fires if ANY scenario is <4.0. This includes scenarios in
[H] and [L] that are expected to still be failing (held from legacy, long-
standing). Look at the category breakdown to separate expected-failing from
unexpected-failing. The mini-eval is intentionally noisy about failures because
its purpose is surfacing regressions, not gating releases.

## Integration with `compare_eval_reports.py`

The `--output FILE.json` flag produces a report in the same shape as the full
eval. You can feed this into the comparison script:

```bash
python phase-2-feature-flag/scripts/compare_eval_reports.py \
    --legacy /path/to/eval_report_legacy.json \
    --unified mini_eval_r37.json \
    --markdown mini_eval_r37_comparison.md
```

The comparison will only include the 15 scenarios — partial signal but easier
to reason about than full-eval diffs during iterative work.

## Extending the target list

When a new regression class surfaces, add to `_TARGETS` in the script. Each
entry is a tuple:

```python
("scenario_id", "category_letter", r36_legacy_score, r36_unified_score, "short note")
```

Category letter conventions:
- `A` — watch-list (Option 4 addresses)
- `B` — narrative (Option 2b addresses)
- `C` — legacy behavior not yet ported
- `D` — borderline / threshold variance
- `H` — held from legacy (was failing pre-migration)
- `L` — long-standing known issue
- `W` — migration headline win

For categories that don't fit, add a new letter + entry in `_CAT_NAMES`.

## Cost

- Default (15 scenarios): ~$2-4 (15 × 1-3 Haiku conversation turns + 1 Opus
  judge call each). Cost dominated by Opus.
- `--subset option-4` (5 scenarios): ~$0.50
- `--subset failing` (12 scenarios): ~$2-3

Cheap enough to run multiple times per day during active iteration.

## Related files

- `scripts/mini_eval_option_4.py` / `.md` — prompt-only validation, no Opus
- `tests/eval/eval_llm_judge.py` — the full eval runner this script calls into
- `phase-2-feature-flag/scripts/compare_eval_reports.py` — pairs the JSON
  output with another eval report for side-by-side comparison
- `eval-r36/YourPeer_Chatbot_Eval_Run_36.md` — the R36 results this script's
  baselines reflect
- `eval-r36/r36-analysis.md` — the R36 diagnosis that drove scenario selection
- `UNIFIED_EXTRACTOR_MIGRATION.md` §"Phase 2 outcomes" — context on what the
  mini-eval is validating against
