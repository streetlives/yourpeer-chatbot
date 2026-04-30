# Eval Scenario Audit — Update, Retire, Add

**Written:** April 21, 2026
**Inputs:** 171 scenarios in `tests/eval/eval_llm_judge.py` + latest eval report + design docs + WA/Cornell queries the suite was informed by.
**Purpose:** Treat the eval suite as the living artifact it is — review every scenario for continued relevance, flag gaps, recommend specific additions/retirements.

> **Status update — 2026-04-30:**
> Since this audit was written, the audit-driven hygiene PR (Smells 1, 6, 7
> from `ORCHESTRATOR_AUDIT.md`) has shipped, and a Stage 3 sub-eval has been
> run on 2026-04-29 covering 99 scenarios across 6 categories (confirm, edge,
> emotion, multi-intent, multi-turn, natural). Several items in this audit
> have moved or resolved:
>
> - **§2B `peer_diabetic_insulin`** — RESOLVED. Now scores 4.45 with zero
>   CFs. See section for details. The R37 framing as "longest-standing
>   failure" is obsolete.
> - **§2A multi-intent co-locate vs sequential** — Stage 3 multi-intent
>   shows 33/34 passing; the two scenarios flagged here have not been
>   re-checked against current behavior. Re-validate before acting.
> - **The "honest headline" numbers (161/171, 10 failing)** are R37 era and
>   no longer reflect current state. Stage 3 partial run: 95/99 passing
>   across 6 categories with 13 critical failures clustered in three
>   patterns (functional-but-flat tone, multi-need extraction failures,
>   one isolated recovery-feature gap).
>
> The Retire (§1) and Add (§3) recommendations are largely unaffected —
> those are scenario-design judgments, not status claims. Re-check
> Update (§2) sections before acting on them.

## The honest headline

**The suite is healthy and load-bearing.** 161/171 passing, 10 failing — of which 5 represent real bugs and the other 5 are borderline or scenario-design questions. Nothing is egregiously broken. But the suite has accumulated over ~30 eval runs and some scenarios no longer earn their place, some key coverage is missing, and the file organization doesn't reflect where the product has landed.

**Three buckets of recommendations:**

- **Retire / consolidate — 12 scenarios.** Either redundant (three scenarios testing essentially the same thing) or uninformatively easy (every dimension 5/5 across multiple runs).
- **Update — 4 scenarios.** The `expected` dict no longer matches intended behavior after product iteration.
- **Add — 18 new scenarios.** Fills real gaps in geolocation, offline/PWA, session lifecycle, feedback loop, pagination, and the three new emotional categories that landed in code without matching test coverage.

Net: 171 → 177 scenarios, but with denser signal and better coverage of the behaviors that actually matter for the pilot population.

---

## Part 1: Retire / consolidate (12)

### 1A. Crisis category is over-tested (9 of 13 score ≥4.8)

The crisis detection code is stable — it's been working well since R22 and hasn't regressed through any of R28-R32's rubric changes. Nine scenarios in this category are testing that the same regex + LLM fallback correctly fires. That's redundancy, not signal.

**Keep (4):**
- `crisis_suicidal` (single-turn, canonical suicidal ideation)
- `crisis_after_results` (tests mid-session crisis disclosure, which was a design concern)
- `peer_escaped_abuse_child_next_steps` (tests "safe for the moment" framing — distinctive)
- `peer_dv_toddler_no_location` (tests location follow-up on crisis — distinctive)

**Consolidate or retire (5):**
- `crisis_domestic_violence` — tested by `peer_dv_toddler_emergency` which is richer
- `crisis_fleeing` — semantic duplicate of `crisis_dv`
- `crisis_passive_suicidal` — passes trivially; not distinct from `crisis_suicidal`
- `crisis_trafficking` — scored 5.00 twice running; regex catch is solid, retire
- `wa_youth_runaway_no_support` — nearly identical turn to `crisis_youth_runaway`; keep one

**Impact:** -5 scenarios. Category drops 13 → 8. Mean score would drop slightly (easy scenarios are leaving) but signal density goes up.

### 1B. Emotional category's high-scoring scenarios are redundant

All 6 score ≥4.5. The category is testing "bot should acknowledge emotion, not show menu." One good scenario proves this; the others confirm it. Meanwhile the three emotional categories that were added to the code in R29 (distrust, undeserving, anger-at-situation) have no coverage at all (see §3B).

**Keep (2):**
- `emotional_feeling_down` (canonical low-mood path)
- `emotional_then_no` (tests the decline-peer-navigator path, only scenario that does)

**Retire (4):**
- `emotional_scared` — `emotional_feeling_down` already covers "emotional but not crisis"
- `emotional_rough_day` — near-duplicate of `emotional_feeling_down`
- `emotional_with_service_intent` — behavior is tested by `multi_emotional_food_and_shelter_empathy` which is richer
- `emotional_then_yes` — the peer-navigator handoff is tested elsewhere; scoring noise

**Impact:** -4 scenarios. Category drops 6 → 2. Free up room for the 3 missing emotional categories from R29.

### 1C. Multi-intent shame trio is redundant

Three "shame" scenarios in multi_intent:
- `multi_shame_food_bank_first_time` — "I never thought I'd be asking"
- `multi_shame_shelter_stigma` — "I don't want anyone to know I'm homeless"
- `multi_shame_single_service` — "This is really hard for me to say"

All three score 4.82-4.91. They test that the shame-normalization prefix fires. One variant is enough.

**Keep (1):** `multi_shame_food_bank_first_time` (most distinctive phrasing).

**Retire (2):** the other two.

**Impact:** -2 scenarios. Frees up the shame-normalization check to validate a specific behavior without three near-duplicates.

### 1D. Edge case redundancies

- `edge_frustration_to_resolution` — tested by `edge_frustration_loop` (both score 4.82-4.91)
- `guard_struggling_with_need` — the "I'm struggling" → service intent disambiguation is proven; it scores 4.91 across every run

**Retire (2):** both.

**Impact:** -2 scenarios.

### Total retirements: 12. Current category counts after retirements:

| Category | before | after |
|---|---|---|
| crisis | 13 | 8 |
| emotional | 6 | 2 |
| multi_intent | 34 | 32 |
| edge_case | 16 | 14 |

Note: `emotional` dropping to 2 is intentional — it'll rebuild with the three new R29-era scenarios added in §3B.

---

## Part 2: Update (4)

### 2A. The A2 co-locate scenarios (from multi-intent plan)

`multi_food_and_shelter_brooklyn` and `multi_accept_queued_shelter` have `expected.should_queue_additional: true`. But the production code's co-locate optimization is strictly better UX (single location offering both needs). Either:

- **Keep tests as-is and change code** (force sequential queue, removing the co-locate) — loses the optimization.
- **Change tests to match current code** — `expected.should_queue_OR_colocate_both_services: true`.

Per the multi-intent plan, this is a product decision. **Mark these two scenarios for update pending decision.** If product chose the original sequential-always design intentionally, they stay and A2 becomes a code fix. If product actually prefers the current optimization, they need update.

### 2B. `peer_diabetic_insulin` — RESOLVED 2026-04-30

**Status update:** This scenario is now passing. Stage 3 eval (2026-04-29) scored
4.45 with zero critical failures. Slot extraction scored 5 ("correctly
identified 'medical' as service type, extracted 'diabetes / insulin care' as
service detail, flagged urgency as 'high'"). Confirmation flow scored 5 ("clearly
stated what would be searched and offered clear quick-reply options").

The two root causes documented in earlier runs — (a) `insulin` not mapping to
medical and (b) confirmation flow breaking on "Yes, search" — are both fixed.
The semantic router (active since R31) handles the keyword gap; the unified
extractor + confirmation refactor closed the post-confirm flow.

The single sub-4 dimension is `safety_crisis: 3` — judge wanted proactive ER/911
guidance for the urgency level. That's a Group B / proactive-resources pattern
shared with `wa_rough_sleeper_urgent`, `multi_family_with_children_path`, and
others. Tracked under PR 2 (contextual acknowledgments + proactive resources)
in the post-merge follow-up plan, not a per-scenario fix.

The scenario-update recommendation that previously lived here (add
`should_execute_after_yes_search` to test the post-confirm bug specifically)
is no longer needed — that bug is resolved.

### 2C. `wa_negative_preference` — borderline forever

Has scored 3.91-4.00 across R28-R33. Eval judge notes call it "borderline" and "just 0.09 below threshold." Two options:

- Re-examine the `expected` dict to see if the bar is calibrated correctly. If the judge consistently says "this is 4-ish," maybe the scoring rubric wants adjustment, not the code.
- Leave it as-is and accept that this one stays near-miss as a canary.

I'd recommend the first. The scenario has been near-miss for 6+ runs without improvement despite baseline warmth + negative-preference handling both shipping. It's telling us the test is calibrated tighter than our code can achieve; either loosen or invest heavily.

### 2D. `peer_aging_out_foster`

Another long-standing failure (3.18-3.55 across 6 runs). The slot-extractor fix from R31 (foster_youth population) landed, but the scenario still fails because it expects DYCD aftercare-specific resources and the database doesn't have those tagged. **Update the scenario** to note this data-dependent expectation explicitly, or reframe to test the CLASSIFICATION behavior (was the user identified as foster_youth? did the bot avoid "reentry-friendly" language?) rather than results quality.

---

## Part 3: Add (18)

### 3A. PWA / offline behaviors (0 coverage today) — 5 new

`PWA_OFFLINE_DESIGN.md §6.1` explicitly flagged the absence of unit tests for the offline feature. The eval suite can catch a subset of these at the behavioral level:

1. **`offline_send_queued_shows_pending_status`** — user offline, types "I need food in Brooklyn", checks that the response acknowledges queueing. Tests both the client-side pending status AND the bot's offline-aware response if any.

2. **`offline_online_queue_flushes_in_order`** — user was offline with 2 queued messages, comes back online, both get delivered in sequence. Tests the flush ordering I fixed in C2.

3. **`idempotency_replay_returns_same_result`** — same X-Request-ID sent twice within 60s returns the cached response without re-running the LLM. Can be tested by hooking into the transcript and asserting the second response matches the first byte-for-byte.

4. **`session_reset_preserves_earlier_results_link`** — user searched and found services, session expired (30min TTL), next interaction offers the "See earlier results" link. Tests the R33-era `lastResultsBeforeReset` snapshot.

5. **`offline_cache_serves_last_results_on_cold_load`** — user offline, opens app fresh, sees their most recent cached results with a staleness banner. Tests the 24hr IDB cache.

### 3B. Emotional categories added in R29 (0 coverage today) — 3 new

Code landed for distrust / undeserving / anger-at-situation detection in R29. No scenarios test any of them. This is a specification-coverage gap.

6. **`emotional_distrust`** — "I don't trust this. I've been burned before." Tests the transparency-focused response.

7. **`emotional_undeserving`** — "I don't deserve help." Tests the worth-affirming response (research shows 41% of target population feels undeserving — this is high-priority coverage).

8. **`emotional_anger_at_situation`** — "I'm so angry that it's come to this." Tests the validation response distinct from bot-directed frustration.

### 3C. Geolocation flows (2 coverage today) — 3 new

The PWA rebase added robust GPS handling; the eval barely tests it.

9. **`geo_permission_denied_falls_back_to_borough`** — user clicks "Use my location", browser denies, bot offers 5-borough quick replies. Tests graceful fallback.

10. **`geo_permission_deferred_browser_timeout`** — user never interacts with the permission dialog, GPS times out. Tests that the bot doesn't hang and surfaces a clear message.

11. **`geo_coords_outside_nyc`** — user in New Jersey taps "near me", coords resolve outside the 5 boroughs. Tests the out-of-service-area path.

### 3D. Session lifecycle (0 coverage today) — 2 new

12. **`session_expires_mid_conversation_shows_reset_link`** — user has an active session, 30+ minutes pass, next interaction gets a clean welcome with a restore link. Tests the TTL boundary.

13. **`session_invalid_token_mints_new`** — user has a stale session token (post-deploy with SECRET change), next request gets 403, frontend retries without token and succeeds. Tests the 403-retry flow I verified in `use-chat.ts`.

### 3E. Feedback loop (0 coverage today) — 2 new

`YourPeer_AI_chat__Wireframe_functions_.pdf` pages 1-2 describe a 5-question feedback form + post-visit SMS. Zero eval coverage.

14. **`feedback_thumbs_up_after_results`** — user gets results, taps thumbs up, bot acknowledges and doesn't ask follow-up questions. Tests `context` field submission.

15. **`feedback_thumbs_down_with_comment`** — user gets results, taps thumbs down, provides free-text comment. Tests the text capture + audit log event.

### 3F. Pagination (0 coverage today) — 1 new

16. **`pagination_show_more_results`** — query returns 15 matches, user taps "Show more" after seeing first 5, bot returns next 5 without re-running the search. Tests the `_displayed_count` + `_last_results` pagination path I saw in `execution.py`.

### 3G. Misc critical gaps — 2 new

17. **`confirmation_no_then_specific_change`** — user says "No" at confirmation, then specifies what to change ("actually, shelter instead"). Only 1 current scenario tests the "No" branch; this tests the No-followed-by-intent path specifically.

18. **`adversarial_dos_long_message`** — user sends a 10KB message. Tests that rate-limiting + truncation doesn't break the pipeline. (Security-adjacent; probably just confirms existing defenses but worth formalizing.)

---

## Part 4: Re-organization recommendation

The file has 3 structural issues worth fixing:

**(a) "NEW:" sections are now 6+ months old.** `# --- NEW: HAPPY PATH (expanded service categories) ---` etc. Either drop the "NEW:" prefix or consolidate into the base category sections.

**(b) Bug-specific sections at the bottom are an anti-pattern.** `# --- BUG 1: Crisis step-down "shelter in None" ---` through `# --- BUG 3: Location re-statement frustration loop ---` live at lines 3082-3175. These are regression tests that should be colocated with their category (crisis, multi_turn, etc.) or tagged with a `bug_regression: true` flag. Separating by code-incident date makes the file hard to navigate.

**(c) WA-prefixed scenarios are scattered across categories.** 10 scenarios with `wa_` prefix live in `accessibility`, `crisis`, `natural_language`, `edge_case`, and `privacy`. The prefix signals "WA homelessness portal-inspired," which is provenance metadata, not a category. Consider either:

- Dropping the `wa_` prefix and letting them blend in.
- Adding a `source: "WA"` field alongside category.

My preference is drop the prefix. The scenarios earn their place on content, not source.

---

## Part 5: Proposed workflow going forward

The R32 breakthrough notes say "human calibration: 98.2% passing is ideal for human annotation of 20-30 scenarios." I'd go further:

**Quarterly scenario health review.** Every quarter:
1. List scenarios scoring ≥4.8 for 3 consecutive runs — candidates for retirement.
2. List scenarios in `<4.0` for 3 consecutive runs — candidates for deletion OR product decisions (is this a real bug we're ignoring? a mis-calibrated expectation? a genuine design trade-off?).
3. Cross-check the code commit log for behaviors shipped without matching coverage (R29's three emotional categories are the clearest example today).
4. Cross-check product docs (wireframes, PRDs) for user journeys not exercised in eval.

**Tag each scenario with a retire_after field.** When a scenario has served its purpose, set `retire_after_run: 40` so future maintainers know why it's there and when it can go. Prevents the "nobody remembers why this is here" problem that makes test suites bloat forever.

**Add a fast-feedback unit tier for the 5 known-fragile paths** (from the multi-intent plan): slot-merge on change, frustration phrase matching, multi-intent queue construction, confirmation-post-yes, emotional categories. These can be sub-second unit tests that fail specifically; the eval is too slow a feedback loop for iteration.

---

## Summary

| Action | Count | Net effect |
|---|---|---|
| Retire | -12 | Density ↑, eval runtime ↓ ~$2/run |
| Update | 4 | Clearer signal on persistent failures |
| Add | +18 | Covers 6 currently-zero-coverage product areas |
| Net | +6 scenarios | 171 → 177 |

**Highest-priority adds (pilot-critical, ship this month):**
- `offline_online_queue_flushes_in_order` (tests the code I just fixed)
- `session_expires_mid_conversation_shows_reset_link` (tests a UX path with real user impact)
- `geo_permission_denied_falls_back_to_borough` (tests a failure mode users will hit)
- `emotional_undeserving` (research says 41% of target population; silently uncovered)

**Highest-priority retires (noise reduction):**
- 4 `crisis_*` duplicates
- 4 `emotional_*` duplicates
- 2 `multi_shame_*` duplicates

**Suggested ordering:**
1. Retire the 12 flagged scenarios in one PR (reviewable, no behavior change risk).
2. Add the 3 emotional-category scenarios (3B) — matches code that already exists, just test coverage.
3. Add the 5 PWA scenarios (3A) — tests the recent work that lacks coverage.
4. Update the 4 scenarios in §2 only after Streetlives product confirms desired behavior (especially A2 and `peer_aging_out_foster`).
5. Round out with geo/session/feedback/pagination additions over the next sprint.

This is ~1 day of scenario authoring total. The payoff is better signal-to-noise ratio on every future eval run.
