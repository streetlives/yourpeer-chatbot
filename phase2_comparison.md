# Phase 2 Eval Comparison

**Legacy run:** 2026-04-24T11:38:59.037003  **Unified run:** 2026-04-24T12:45:43.815551

**Overall (unweighted):** 4.56 → 4.55
**Overall (weighted):** 4.54 → 4.54
**Critical failures:** 22 → 25

## Acceptance Criteria

| Criterion | Status | Detail |
|---|---|---|
| `multi_cross_borough` passes on unified | ⚠️ NOT FOUND | multi_cross_borough absent from unified report |
| No scenario >=4.5 drops below 4.2 | ❌ FAIL | 2 regression(s) |
| CF count: unified <= legacy | ❌ FAIL | 25 vs 22 |

## Watch List (Set-Equality Blind Spots)

| Scenario | Legacy | Unified | Delta | Option 4 Trigger |
|---|---|---|---|---|
| `multi_food_and_shelter_brooklyn` | 4.36 | 3.91 | -0.45 | no |
| `multi_shower_and_food_drop_in` | 4.45 | 4.0 | -0.45 | no |
| `multi_clothing_and_food_harlem` | 4.55 | 4.64 | +0.09 | no |
| `multi_cross_neighborhood_shower_les_food_chinatown` | 4.0 | 3.55 | -0.45 | no |

## Regressed Scenarios (≥0.3 drop)

| Scenario | Category | Legacy | Unified | Delta |
|---|---|---|---|---|
| `confirm_multi_change` | confirmation | 4.73 | 3.55 | -1.18 |
| `accessibility_low_literacy` | accessibility | 4.73 | 3.73 | -1.00 |
| `multi_accept_queued_shelter` | multi_intent | 4.36 | 3.82 | -0.54 |
| `natural_long_story` | natural_language | 4.45 | 3.91 | -0.54 |
| `multi_cross_neighborhood_shower_les_food_chinatown` | multi_intent | 4.0 | 3.55 | -0.45 |
| `multi_food_and_shelter_brooklyn` | multi_intent | 4.36 | 3.91 | -0.45 |
| `multi_shower_and_food_drop_in` | multi_intent | 4.45 | 4.0 | -0.45 |

## Improved Scenarios (≥0.3 gain)

| Scenario | Category | Legacy | Unified | Delta |
|---|---|---|---|---|
| `multi_cross_borough_food_brooklyn_shelter_manhattan` | multi_intent | 2.82 | 4.73 | +1.91 |
| `confirm_change_service` | confirmation | 4.09 | 4.73 | +0.64 |
| `peer_diabetic_insulin` | natural_language | 2.64 | 3.0 | +0.36 |

## Passing-Status Transitions (threshold 4.0)

**Newly passing (1):**
- `multi_cross_borough_food_brooklyn_shelter_manhattan` (multi_intent): 2.82 → 4.73

**Newly failing (9):**
- `accessibility_low_literacy` (accessibility): 4.73 → 3.73
- `confirm_multi_change` (confirmation): 4.73 → 3.55
- `multi_accept_queued_shelter` (multi_intent): 4.36 → 3.82
- `multi_cross_neighborhood_shower_les_food_chinatown` (multi_intent): 4.0 → 3.55
- `multi_food_and_shelter_brooklyn` (multi_intent): 4.36 → 3.91
- `multiturn_change_mind` (multi_turn): 4.0 → 3.91
- `natural_long_story` (natural_language): 4.45 → 3.91
- `peer_young_mom_multiple_needs` (multi_intent): 4.18 → 3.91
- `wa_substance_use_shelter` (natural_language): 4.09 → 3.91

## Verdict

❌ **Acceptance criteria violated — do not flip Phase 3.**
Return to Phase 1 and tune merge rules.
