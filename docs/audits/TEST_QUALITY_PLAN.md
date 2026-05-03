# Test-Quality & Correctness Plan

**Scope:** everything after the April 2026 audit cleanup. Covers remaining test
issues (xfail / skip / flake / brittle), residual best-practice gaps,
specific bugs the team has flagged, and a full criticality ranking for
mutation-testing investment.

**Reading order:** Sections 1–3 are fact-finding (did we actually fix X? are
there still Y?). Section 4 is the criticality ranking. Section 5 is the
prioritized action plan with P0/P1/P2 phases.

---

## 1. Best-practice gap status

Status key: ✅ done · ⚠️ partial · ❌ gap.

| # | Gap                                            | Status | Evidence                                                                                                                                                                                                                                |
|---|------------------------------------------------|:------:|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 | Full mutation testing                          | ⚠️     | Only 5 critical modules. Several high-blast-radius modules (`slot_extraction_regex`, `query_templates.py`, `query_executor.py`, `chatbot/execution.py`, `chatbot/pipeline.py`) are NOT in the matrix. See Section 4 for the ranking.         |
| 2 | Branch coverage                                | ✅     | `test-quality.yml` runs `pytest --cov-branch`.                                                                                                                                                                                         |
| 3 | CI coverage gate                               | ✅     | `--cov-fail-under=85` in `test-quality.yml`. Artifact uploaded, summary posted to PR.                                                                                                                                                   |
| 4 | CI audit gate                                  | ✅     | `check_audit_baseline.py` runs before tests in `test-quality.yml`. Fails on any new finding above baseline.                                                                                                                             |
| 5 | Package-level patching over-reliance           | ⚠️     | Codemod ran (130 rewrites, 0 D7 findings) — but the back-compat re-exports in `chatbot/__init__.py` STILL EXIST to avoid breaking legacy tests. Latent footgun: a new dev can still write the wrong-target patch, the D7 check catches it but only after the fact. |
| 6 | Explicit test categorization (unit/integration/eval markers) | ❌ | No `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.eval`, or `@pytest.mark.slow` markers registered. Categorization is by directory only. No `pyproject.toml [tool.pytest.ini_options]` section at all. |
| 7 | Coverage measured in CI                        | ✅     | Same as #2/#3. Uploaded as artifact for debugging.                                                                                                                                                                                     |

**Bottom line:** 4 of 7 gaps closed. Residual: mutation coverage (partial
by design, but under-inclusive), `chatbot/__init__.py` re-exports
(cosmetic — the gate catches misuse), and test categorization (missing
entirely). Plan below addresses all three.

---

## 2. Remaining test-issue inventory

### 2.1 xfailed tests (6 total — all genuine feature gaps)

| # | File:Line                                       | Description                                                               | Disposition                                                                                                                     |
|---|-------------------------------------------------|---------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| 1 | `test_llm_slot_extractor.py:131`               | `hospital` regex keyword overrides LLM's correct `shelter` classification | **Fix in slot extractor.** The override logic should lose to the LLM when the LLM has HIGHER confidence (not just any match). P1. |
| 2 | `test_llm_slot_extractor.py:224`               | Same bug, different scenario                                              | Paired with #1 — will flip together when fixed.                                                                                 |
| 3 | `test_results_enhancements.py:65`              | Auto-execute for urgent+complete queries not implemented                  | **Design decision needed.** Trade-off: skipping confirmation saves a turn for urgent users, but the confirmation was added intentionally in R30 for safety. Recommend adding a feature flag and A/B eval. P2. |
| 4 | `test_results_enhancements.py:76`              | Paired with #3                                                            | Flips with #3.                                                                                                                  |
| 5 | `test_slot_extraction_regex.py:221`                   | Word-to-number conversion ("twenty-three" → 23) for voice-transcribed ages | **Ship when voice UI ships.** Currently a frontend stub; no user impact yet. P2, gate with voice rollout.                       |
| 6 | `test_slot_extraction_regex.py:968`                   | Prepositional family phrases ("for me and my kids", "have a baby")        | **Extend regex.** Small fix to `_FAMILY_PATTERNS` in `slot_extraction_regex.py`. P1.                                                    |

**Risk not currently mitigated:** `xfail_strict` is not set in pytest
config, so an xfailed test that starts passing (xpassed) does not fail
the build. If #1/#2 get fixed by a coincidental regex change, we lose
the "hey, this feature landed!" signal. **Fix: add
`xfail_strict = true` to `[tool.pytest.ini_options]`** (P1, one line).

### 2.2 Unconditionally skipped tests (3 total — feature-gap placeholders)

| # | File:Line                                  | Feature                                                         | Disposition                                                                                                               |
|---|--------------------------------------------|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| 1 | `test_frustration_and_crisis.py:176`       | "Frustrated restatement auto-executes" (escape-hatch from confirmation when frustrated + slots already present + prior results visible) | **Ties into xfail #3.** Same underlying question: when do we skip confirmation? Recommend designing one escape-hatch policy and shipping both. P2. |
| 2 | `test_frustration_and_crisis.py:208`       | Empathetic prefix on the escape-hatch above                     | Ships with #1.                                                                                                            |
| 3 | `test_frustration_and_crisis.py:335`       | `__crisis_geo_search__` sentinel (distinct from `__use_geolocation__`) to carry crisis-flag through the geolocation pipeline | **Design decision.** Current implementation is functionally correct (geo still resolves); the sentinel would enable crisis-specific post-resolve behavior (different follow-up prompts, priority routing). Not a bug — a product extension. P3 until product asks. |

### 2.3 Conditionally skipped tests (env-gated, correct)

| # | File:Line                                 | Condition                           | Assessment                                                      |
|---|-------------------------------------------|-------------------------------------|-----------------------------------------------------------------|
| 1 | `test_health_and_upload.py:350,368,377`   | Semantic router not loaded          | ✅ Correct — these test the "router present" path; without model, skip. |
| 2 | `test_llm_slot_extractor.py:434`          | `_api_key_looks_real()` returns False | ✅ Correct — live LLM tests. Placeholder keys skip cleanly.      |
| 3 | `test_narrative_and_eval_scenarios.py:16` | `requires_llm` marker               | ✅ Correct.                                                     |

### 2.4 xpassed tests

**None detected** at current state. Without `xfail_strict = true`, we
would not see them anyway. Implementing P1 fix gives us this signal
retroactively.

### 2.5 Brittle / flaky patterns still present

| Pattern                                                                        | Count | Files                                                                                                                              | Fix                                                                                                                   |
|--------------------------------------------------------------------------------|-------|-----------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| Tests that read `os.environ` without `monkeypatch` isolation (D5)             | 8     | `test_claude_client.py` (×2), `test_health_and_upload.py`, `test_main.py`, `test_classification_and_routing.py`, `test_format_pipeline_and_admin.py`, `test_http_routes_and_models.py`, `test_multi_turn_and_context.py` | Lift 3 of these into a conftest fixture (the audit README's follow-up #2). The other 5 deliberately test env-handling; keep but document. P1. |
| Tests relying on `send_multi(...)` return-value positional access              | ~30   | scattered                                                                                                                          | Audit for any accessing `r[0]["response"]` and asserting on a single word — those break on prose wording changes. P2. |
| Tests using `print("INFO: ...")` instead of assert                             | 0     | (all caught by D2, fixed in April cleanup)                                                                                         | Gated by D2 audit. No action.                                                                                         |
| `time.sleep()` in test body                                                    | 1     | `test_audit_log.py`                                                                                                                | Accepted baseline (D9=1). Consider replacing with `freeze_time` for determinism. P3.                                  |
| Real-time `elapsed/expired` comparisons without `freeze_time`                  | 1     | `test_claude_client.py::test_ping_cache_expires`                                                                                   | Accepted baseline (D8=1). Could refactor to use `freeze_time`. P3.                                                    |

### 2.6 "Known limitation" comments in test bodies (documentation gaps)

Locations where a test pins current behavior with "this is a known
limitation" rather than asserting expected behavior:

- `tests/unit/test_location_boundaries.py:230,242` — "first 'in X' match wins" for multiple location phrases
- `tests/unit/test_edge_cases.py:103` — unspecified
- `tests/unit/test_audit_regression.py:1491` — DV enrichment adds generic taxonomies

**Disposition:** these are legitimate — the tests document the boundary.
Track them in a single known limitations doc so product
can prioritize. P2.

---

## 3. Specific bugs from the team

### 3.1 `_fallback_results` is set but never consumed

**Confirmed.** `backend/app/services/chatbot/execution.py:575` stashes
fallback cards into `slots["_fallback_results"]`. No consumer exists —
`post_results.py` handlers read `_last_results` and `_filtered_results`
only.

**User impact:** the line in the team's note is correct — "is the first
one open?" after a fallback-augmented search searches only `_last_results`
(main cards). Fallback cards drop off the map post-search. Silent bug
for the user; silent data loss for admin observability.

**Fix options:**
- **(a) Merge** — append fallback cards to `_last_results` at search
  time (existing behavior actually does `services_list = services_list
  + fb_cards` on line:569, so they ARE in the cards the user sees). The
  stash is dead data. If nothing else reads it, just delete the stash.
- **(b) Wire** — have `_resolve_card_from_reference()` in `post_results.py`
  also consult `_fallback_results` if `_last_results` misses. Keeps
  fallbacks queryable post-search even when pagination has moved past them.
- **(c) Log** — persist the stash only for admin logging (not user
  queries). Rename to `_fallback_ids_logged`.

**Recommendation: (a) + audit logging.** The cards are already in
`_last_results` via the concat on line:569. The separate stash adds a
consumer-less field. Delete the stash, emit an audit event with the
fallback card IDs for admin visibility. P1.

### 3.2 Silent dedupe-to-empty on fallback

**Confirmed as described.** In `_run_population_fallback`, if every
candidate card was already in `existing_ids`, the function returns
`([], "")` without logging. From the admin console it looks like no
fallback ran at all.

**Fix:** emit an audit event when `fallback_attempted=True` and
`fallback_cards_after_dedup=0`. Two lines in
`execution.py::_run_population_fallback`. P1.

### 3.3 2× unrecognized service escalation — LLM bypass

**Confirmed.** The counter lives in `handlers/general.py:77-78`. It
only fires when the message hits the general handler — which requires
`service_type is None`. When `ANTHROPIC_API_KEY` is set, the LLM
extractor treats `asdfghjkl` as a potential `org_name` and routes
through the service pipeline, so the counter never increments.

**Tests work around this** by patching `_USE_LLM` False at both bind
sites (see `test_multi_turn_and_context.py:454-461`). That's correct
for the test; it masks the real-world gap.

**Fix options:**
- **(a) LLM prompt tightening** — instruct the LLM to return
  `service_type="unrecognized"` (new enum value) for clear nonsense,
  vs `other` for legitimate-but-uncategorized requests. Route
  `unrecognized` through the general handler.
- **(b) Post-LLM check** — in `pipeline.py`, after LLM classification,
  check if the LLM returned `service_type="other"` AND no location AND
  message is < 4 words of nonsense — downgrade to general handler.
- **(c) Accept the gap** — document that with the LLM, nonsense gets
  charitably interpreted. Remove the xfail, remove the test workaround,
  accept that the 3-tier escalation is a regex-only feature.

**Recommendation: (a).** Cleaner product semantics. "Unrecognized" is a
real category. Affects 11-scenario failure patterns in the eval suite.
P1.

### 3.4 `fallback_population` first-match-wins

**Confirmed.** `execution.py:395`:
```python
for label in labels:
    label_tx_lower = {...}
    if card_tx_lower & label_tx_lower:
        matched_label = label
        break  # ← iteration order wins
card["fallback_population"] = matched_label or labels[0]
```

The iteration order of `labels` is driven by the user's population
extraction order. A `LGBTQ Young Adult` card shown to a "trans
18-year-old" can label as `youth` or `lgbtq` depending on whether
`youth` or `lgbtq` was extracted first.

**Fix options:**
- **(a) Most-specific-first** — order `labels` by rarity (count of
  cards in the DB tagged with that population) before the loop. Rarest
  wins.
- **(b) All-match** — return a list of matching labels, let the UI
  render the most-distinctive one.
- **(c) Per-population priority map** — hardcode `lgbtq > youth > senior
  > veteran > ...` for tie-breaking.

**Recommendation: (a) with (c) as tie-break.** Data-driven is more
robust than hardcoded ordering; rarity is a reasonable proxy for
"distinguishing tag." Ship with unit tests pinning behavior on the
Ali Forney + multi-population scenario. P1.

---

## 4. Module criticality ranking

Criteria, in priority order: **safety** (could a bug cause user harm?)
→ **privacy** (PII exposure?) → **trust** (hallucination / wrong data?)
→ **dispatch** (scope of downstream effects) → **security**. Ties
broken by module size (bigger = more surface).

### Tier 1 — safety-critical (must have mutation testing)

| Rank | Module | LOC | Why |
|:---:|-----|---:|-----|
| 1 | `services/crisis_detector.py`             |  562 | **Safety-critical.** Miss a crisis → user doesn't get a hotline. Already covered. |
| 2 | `services/classifier.py`                  |  356 | Routes every message. Misroute = silent wrong behavior. Already covered. |
| 3 | `privacy/pii_redactor.py`                 |  376 | PII leak = privacy breach for vulnerable users. Already covered. |
| 4 | `services/chatbot/orchestrator.py`        |  462 | Main dispatch. Subtle routing bugs. Already covered. |
| 5 | `services/session_token.py`               |  ~80 | Security-adjacent — bad sign = identity confusion. Already covered. |

### Tier 2 — high blast-radius (should have mutation testing)

| Rank | Module | LOC | Why NOT currently covered |
|:---:|-----|---:|-----|
| 6 | `services/chatbot/pipeline.py`            |  292 | **Unified LLM gate.** Bug here affects ALL tier-3 classification. Controls when LLM fires vs regex. Add. |
| 7 | `services/chatbot/execution.py`           |  760 | **Runs every DB query, formats every service card.** Contains the fallback dedup + label-pick logic from §3. Add. |
| 8 | `services/slot_extraction_regex.py`              | 1788 | Extracts service_type, location, urgency, populations. Every downstream decision reads these. Largest module in the codebase. Add. |
| 9 | `rag/query_templates.py`                  | 1416 | SQL templates. Wrong query = wrong results. Latent risk because tests mostly assert on SQL string shape, not execution semantics. Add. |
| 10 | `services/chatbot/handlers/confirmation.py` |  660 | Confirmation flow — "yes/no" misinterpretation is high user-visibility. Add. |

### Tier 3 — moderate blast-radius (candidate for mutation testing)

| Rank | Module | LOC | Why |
|:---:|-----|---:|-----|
| 11 | `rag/query_executor.py`                   |  925 | PostGIS logic, borough expansion, result ordering |
| 12 | `services/chatbot/handlers/post_results.py` |  530 | Answers from stored cards; includes the filter-persistence logic |
| 13 | `services/semantic_router.py`             |  532 | Tier-2 classification; silent misroute |
| 14 | `services/chatbot/handlers/emotional.py`  |  316 | Crisis step-down + AVR pattern; tone-sensitive |
| 15 | `services/llm_slot_extractor.py`          |  783 | Tier-3 classification; contains the `_gender` vs `gender` API surface |

### Tier 4 — lower blast-radius (unit tests sufficient)

| Rank | Module | LOC | Why |
|:---:|-----|---:|-----|
| 16 | `services/audit_log.py`                   | 1174 | Logging + metrics. Bugs reduce observability, not user experience. |
| 17 | `services/responses.py`                   |  555 | Canned response strings. Regression = copy change, not correctness. |
| 18 | `services/phrase_lists.py`                |  595 | Data constants. Bugs caught by `test_phrase_audit.py`. |
| 19 | `services/chatbot/handlers/general.py`    |  146 | Unrecognized-service handler. Contains the counter from §3.3. |
| 20 | `services/chatbot/handlers/meta.py`       |  ~180 | Bot-capability questions. Static responses. |
| 21 | `services/semantic_routes.py`             |  417 | Route definitions (data). |
| 22 | `services/llm_classifier.py`              |  ~290 | Unified LLM classifier. Fallback path. |
| 23 | `services/bot_knowledge.py`               |  499 | Static fallback responses. |
| 24 | `services/rate_limiter.py`                |  ~200 | Availability. Bug = DoS risk, not correctness. |
| 25 | `services/session_store.py`               |  ~90 | In-memory dict. |
| 26 | `services/persistence.py`                 |  302 | Optional SQLite write-through; disable-on-failure. |
| 27 | `services/confirmation.py`                |  ~400 | Confirmation string formatting (distinct from the handler). |
| 28 | `services/chatbot/handlers/accessibility.py` | ~200 | Low-traffic accessibility handler. |
| 29 | `dependencies.py`                         |  395 | FastAPI DI bootstrap. |
| 30 | `llm/claude_client.py`                    |  387 | Claude SDK wrapper + `ping_llm`. |

### Recommendation: add Tier 2 (6-10) to the mutation matrix

These 5 modules collectively add ~20 minutes of weekly CI time and
cover the code paths where a silent bug has the widest downstream
impact. `execution.py` in particular has not been mutation-tested
despite containing the known bugs in §3.1, §3.2, §3.4.

**Initial thresholds** (can ratchet up):
- `pipeline.py`: 65%
- `execution.py`: 60% (contains some hard-to-isolate DB paths)
- `slot_extraction_regex.py`: 70%
- `query_templates.py`: 75% (pure logic)
- `handlers/confirmation.py`: 65%

---

## 5. Prioritized action plan

### P0 — Safety / correctness (ship this sprint)

| #  | Item | Effort | Owner hint | Rationale |
|----|------|:------:|------|------|
| P0.1 | **Fix `fallback_population` iteration order** (§3.4). Add rarity-based ordering + 3 unit tests. | ~S | backend | Currently a silent mislabeling for users with multi-population overlap (LGBTQ youth, disabled veteran, etc.). |
| P0.2 | **Emit audit event for silent dedup-to-empty** (§3.2). | XS | backend | Admin visibility. Current behavior hides a class of "fallback failed" from dashboards. |
| P0.3 | **Add `xfail_strict = true`** to `pyproject.toml`. | XS | any | Costs nothing. Gives us xpassed alerts when features ship. Prerequisite for P1.1 and P1.2. |

### P1 — Quality gates + pending fixes (next sprint)

| #  | Item | Effort | Rationale |
|----|------|:------:|------|
| P1.1 | **Fix hospital-keyword regex override** (§2.1 #1 + #2). Flip 2 xfails to passing. | S | Already has test coverage. Fix is probably a conditional in `slot_extraction_regex.py`'s override logic: "prefer regex ONLY when LLM confidence < 0.8 and regex keyword is unambiguous." |
| P1.2 | **Add prepositional family-phrase regex** (§2.1 #6). | XS | One-line fix to `_FAMILY_PATTERNS`. |
| P1.3 | **Decide and implement unrecognized-service LLM bypass** (§3.3, recommended option (a)). | M | Add `service_type="unrecognized"` to the LLM prompt enum + pipeline routing. Affects multi-intent, escalation, peer-navigator offer timing. |
| P1.4 | **Delete the dead `_fallback_results` stash** (§3.1). | XS | One line + audit event replacement. Zero user-facing change; removes a confusing dead field. |
| P1.5 | **Add pytest markers for test categorization**: register `unit`, `integration`, `eval`, `slow`, `requires_llm`, `requires_db` in `[tool.pytest.ini_options]`. Annotate existing tests. | M | Enables `pytest -m "not slow"`, `pytest -m "integration and not requires_db"`. Currently categorization is directory-only. |
| P1.6 | **Extend mutation-testing matrix** with Tier 2 modules (§4). | M | Adds `pipeline.py`, `execution.py`, `slot_extraction_regex.py`, `query_templates.py`, `handlers/confirmation.py` to weekly job. CI time impact: ~20 min added to parallel matrix. |
| P1.7 | **Remove back-compat re-exports** in `chatbot/__init__.py` (§1 gap #5). | S | Risk: breaks the 3-4 test files that import `from app.services.chatbot import detect_crisis, claude_reply, _build_confirmation_message`. Migrate those imports; re-exports go. Kills the D7 footgun at the source. |
| P1.8 | **Lift 3 D5 env-reading tests into a conftest fixture** (§2.5). | S | Audit README's explicit follow-up. Reduces D5 findings from 8 to 5 (all deliberate). |

### P2 — Feature gaps (backlog, product-gated)

| #  | Item | Effort | Rationale |
|----|------|:------:|------|
| P2.1 | **Auto-execute urgent queries + frustrated-restatement escape-hatch** (§2.1 #3-4 + §2.2 #1-2). Ship as one feature flag. Run an eval comparison run. | L | 2 xfails + 2 skips flip. Design decision on confirmation UX. |
| P2.2 | **Word-to-number conversion for voice ages** (§2.1 #5). | S | Gate with voice rollout. |
| P2.3 | **Crisis-specific geolocation sentinel** (§2.2 #3). | M | Product extension, not a bug. Waits on product ask. |
| P2.4 | **Document "known limitations" in a single doc**. Consolidate the 3-4 scattered test-body comments. | XS | Product visibility. |
| P2.5 | **Audit `send_multi()` return-value pattern** tests (§2.5 brittleness). Find any `r[n]["response"]` asserting on word choice — tighten or loosen as appropriate. | M | Flake prevention. |
| P2.6 | **Refactor `test_ping_cache_expires` and `test_audit_log`** to use `freeze_time` (§2.5 D8/D9). | S | Gets to D8=0, D9=0. |

### P3 — Nice-to-have

| #  | Item | Effort | Rationale |
|----|------|:------:|------|
| P3.1 | **Ratchet coverage gate up** 85% → 88% → 90% as under-covered modules (`main.py`, `admin.py`, `idempotency` paths) get more tests. | Continuous | Don't set it at 90 until verified stable. |
| P3.2 | **Add full-suite mutation testing on demand** (not CI) for Tier 3 modules. `make mutation-all` target. | S | Runs ~4-5 hours; use on major refactors. |
| P3.3 | **Human calibration of Opus judge** (20-30 scenarios scored by 2-3 humans). | L | From the eval pipeline's open-assumption list. Validates whether the judge scoring correlates with user perception. |
| P3.4 | **`audit_tests.py` D3 false-positive rate audit.** D3 has 19 findings, advisory only. Review each. | S | Currently advisory because "~50% false-positive rate." Real rate could be measured and the check either tightened or retired. |

---

## 6. Effort estimates summary

| Bucket | Items | Total effort |
|--------|:-----:|:------------:|
| P0 (ship this sprint)  | 3 | ~1 day |
| P1 (next sprint)       | 8 | ~1.5 weeks |
| P2 (backlog)           | 6 | ~3 weeks with product review |
| P3 (ongoing)           | 4 | continuous |

**If we ship only P0 + P1:** the suite goes from "deterministic and
comprehensive" (current state) to "deterministic, comprehensive, and
surfaces 4 known latent bugs in production." All four bugs in §3 are
addressed; 2 of 6 xfails flip to pass; test categorization is cleaned
up; mutation coverage expands from 5 → 10 modules.

**P0 unblocks:** nothing currently. These are independent and can be
done in any order.

**P1 dependencies:** P1.1 depends on P0.3 (`xfail_strict`) to catch the
flip. P1.7 should be done last (after migration), because the D7 gate
holds regardless of whether re-exports exist.
