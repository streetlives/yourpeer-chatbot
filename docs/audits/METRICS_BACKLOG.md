# Admin Metrics Tab — Outstanding Work & Investigation Backlog

A working list of follow-ups identified during the R39-era review of the Metrics tab. Organized by what kind of work each item needs: UI-only changes, backend / data pipeline work, open questions about what the data means, and threshold values worth revisiting.

For context on what's already landed, see "Recently Landed" at the bottom.

---

## 1. Outstanding UI Work

### Small lift — no backend changes

**No-Result by Service: structured table.** The current row crams ten templates into a single comma-separated `subtitle` cell. `OrgNameQuery: 100% (1q)` looks alarming sitting next to `FoodQuery: 0% (62q)` despite being a single-query sample. Pull each template into its own row sorted by no-result rate, with columns for queries, % no-result, and (cross-referenced) any low-n flag. Reuses the existing `MetricRow` shape.

**Latency-by-task in the detail dialog.** When `Latency p50/p95` is in Watch state, the metric-detail dialog should expose the per-task breakdown (already in `llmMetrics.by_task`) so users can see *why* — e.g. crisis_detection's 1223ms is dragging the median up. Detail dialog already exists; this is data already in context.

**Tone breakdown empty-state.** When the tone-degenerate banner fires, the per-tone rows below it (`Tone: Urgent 2%`, `Tone: Frustrated 0%`, etc.) duplicate the banner's message in a less obvious way. Consider collapsing them into a single "Tone distribution" disclosure under the banner until classifier coverage improves.

**Routing distribution: collapse small buckets.** Section 6's routing buckets show `Service Flow 75% · Conversational 18% · Post-Results 5% · Emotional 0% · Safety 5% · Recovery 0%`. The four small buckets are individually noisy — fold them into an "Other" rollup row with a click-to-expand for the breakdown. Reduces the number of always-on tracking pills in an already busy section.

**Eligibility Fit Rate value text.** Now marked Post-pilot, but `value="By design (canary)"` still reads aspirationally — implies the canary suite exists. Either swap to `null` and rely on the no-data pill, or change to `"Pending canary suite"` to match reality.

### Medium lift — some backend work

**Data Freshness drill-down (per service category).** The headline `60% ⚠ Watch · 1085 stale cards` tells data stewards there's work but not where. A per-category breakdown surfaces priorities. Backend question: is `service_type` already joined to the freshness telemetry, or only collected at the query-result level? If joined, it's a UI-only change. If not, needs a query-template tweak to record service_type alongside freshness.

**Crisis Workload — show estimated cost contribution, not just call share.** The Section 3 callout currently says `83% of calls`. The cost story would be sharper as `~$X of $Y monthly LLM spend` since crisis is Sonnet-only and Sonnet is ~3× the price of Haiku. Needs the backend to expose per-task estimated cost (currently only top-line `estimated_cost` exists in `llm_metrics`).

**Long-session investigation hook.** Turn Distribution shows `11+ turns: 23` of 93 sessions — a quarter of all sessions are long. Add a "View long sessions" link from the Turn Distribution row that filters the Conversations tab to sessions with 11+ turns. UI work plus a query parameter on the Conversations endpoint.

### Larger lift — meaningful backend / infrastructure

**Status-aware top banner.** A header summarizing "1 metric off target, 2 in watch, X new since last visit" for quick scanning. Most useful once we can compare against a previous snapshot — needs persisted state (last-visit timestamp per user, or rolling history of metric values).

**7-day sparklines per row.** All current metrics are point-in-time. Sparklines change the analytical character of the dashboard — staff would see directionality. Needs backend rollup: nightly job that aggregates `audit_log` into daily metric snapshots, plus a new endpoint to return the trailing 7 days for a given metric key.

**Eval report metadata.** Already discussed in earlier session — surfacing weighted average, run number, run timestamp, and per-dimension score distributions from the `EvalReport` payload. All require eval reporter changes to emit the metadata, then UI changes in Section 8 to render it.

---

## 2. Surfacing R32+ Chatbot Improvements

The chatbot has had significant structural improvements through Runs 28–32 — semantic router activation, baseline warmth, shame normalization, population-specific routing, expanded crisis categories — but most are invisible in the Metrics tab because the audit log doesn't capture them yet. Each item below is dual-track: a UI row to add once instrumentation exists, plus the backend work required to capture the data.

**Crisis category breakdown.** Section 3 currently shows a single `Crisis Detection Count`. The chatbot now distinguishes seven categories — `suicide_self_harm`, `medical_emergency`, `domestic_violence`, `youth_runaway`, `assault_victim`, `safety_concern`, `trafficking` — each with population-specific resources. Per-category counts validate that the right resources are firing (e.g. youth runaway should hit Runaway Safeline + Covenant House, not DV hotlines). Backend: extend the `crisis_detected` audit event to include `crisis_category`. UI: a stacked breakdown row beneath Crisis Detection Count.

**Semantic router hit rate.** Section 6 currently shows confidence as `high: 644 · low: 5` — high = regex, low = LLM fallback. The semantic router is the Tier 2 layer activated in R31, sitting between regex and LLM. Live data likely folds Tier 2 hits into `high` since the routing is deterministic. Distinguishing them shows how often the router is actually resolving turns vs being bypassed (a genuine hit rate validates the model load + memory cost is paying off). Backend: emit a `medium` or `semantic_router` confidence value when Tier 2 resolves. UI: a third bucket in the existing confidence breakdown.

**Baseline warmth firing rate.** R32 added warmth prefixes to all confirmations and result responses — one of the single largest tone improvements in the eval era (Response Tone 3.51 → 3.72, +21 scenarios passing). We have no visibility into whether the prefix is actually reaching production users at the expected rate. Backend: log a `warmth_prefix_applied` flag on bot turns when the prefix was prepended. UI: a row in Section 4 showing "% of bot turns with warmth prefix applied" relative to eligible turns.

**Shame normalization firing rate.** R29 added a shame-detection normalizer that prepends "It takes real strength to reach out…" to confirmations when shame language co-occurs with service intent. Same observability gap as warmth — the eval shows it works, the dashboard is silent on whether it's firing in production. Backend: similar `shame_prefix_applied` flag. UI: paired row alongside warmth in Section 4.

**Population tag adoption.** R31+ introduced `foster_youth`, `pregnant`, `lgbtq`, and other population tags that drive resource selection. Tracking which tags are firing — and how often — surfaces whether the new routing is hitting real cases or sitting dormant. The eval shows them working in scripted scenarios; production usage is unknown. Backend: aggregator on the stats endpoint that counts sessions per tag (tags are already in the slot dict). UI: a tag distribution row in Section 5 (Intake & Confirmation Flow).

**PII warning firing rate.** R31 added user-facing warnings when the bot detects sensitive PII (SSN, phone). Currently invisible. Useful for understanding how often users share PII despite design intent — high rate may signal that the welcome flow needs to set clearer expectations about not sharing personal info. Backend: log `pii_warning_shown` events. UI: a row in Section 3 — natural fit alongside the Post-pilot PII Leakage Rate.

**Contradiction detection rate.** R28's `merge_slots` fix detects when a user explicitly changes their mind (the `multiturn_change_mind` scenario family) and overwrites preserved slots. The eval scenario `multiturn_change_mind` jumped 2.50 → 4.36 once this landed. Tracking the live firing rate is both a positive signal ("the fix is working") and a UX signal (high rate may mean the original confirmations were unclear). Backend: emit `slot_contradiction_resolved` events. UI: a row in Section 5 next to Slot Correction Rate.

**Spanish bilingual detection.** R31 added Spanish + service-intent detection that triggers a bilingual acknowledgment alongside the search. Currently invisible. Useful for accessibility tracking and for deciding whether to expand to other languages. Backend: log when the bilingual response triggered, plus whether the user continued in Spanish or English. UI: a row in Section 4 or in a future "Accessibility" section.

### Implementation strategy note

Most of these items share a common backend pattern: a flag added to existing audit events (`crisis_detected`, `bot_turn`, `slot_extraction_completed`), surfaced through the existing stats aggregator, and rendered with the existing `MetricRow` component. None individually requires significant new infrastructure. They could reasonably be batched into a single "R32+ telemetry" PR rather than picked off one at a time.

---

## 3. Surfacing Eval-Quality and Production-Outcome Signals

The Eval Quality Engineering Plan v2 (May 2026) lays out seven foundations for trusting the eval system. Several of those foundations produce signals that the production Metrics tab should surface but currently doesn't — the data is being measured (or about to be) on the eval side, and the people watching the dashboard are blind to it.

**Reading-level metric.** Eval Plan Foundation 7a — "the cheapest concrete add in this entire plan: ~50 lines of code, zero recurring cost, zero LLM dependency, deterministic results across runs." Compute Flesch-Kincaid Grade Level on every bot response. Surface as a row in Section 4 (or a new Accessibility section) with a 6th-grade target appropriate for the population (stricter than the Plain Language 8th-grade default for caseworker-facing tools). Critically, this is something the Opus judge cannot measure — Cultural Responsiveness can score 4 while the bot reads at college level. The two metrics belong on the same screen.

**Per-dimension variance σ on eval scores.** Eval Plan Foundation 1 will produce a `history.json` with per-scenario per-dimension mean and σ across the last N runs. Section 8 currently shows point-in-time dimension averages — once history exists, the subtitle should read `μ ± σ over last N runs`, and the value should color differently when the latest score is more than 1.5σ from the mean. Without σ, a 0.08 drop and a 0.30 drop look identical.

**Calibration metadata display.** Foundation 2 produces a human-vs-Opus correlation per dimension (ρ for each of the 11 dimensions). When this lands, every Section 8 row should show its calibration ρ alongside the score: a Tone score of 3.72 with ρ=0.7 vs. ρ=0.3 are different things to act on. Display the last calibration date too — calibration ages.

**Pinned model versions.** Foundation 4 records `summary["model_versions"]` in the eval report. Section 6 currently shows "Calls by Model" usage volume but not pinned versions. Add a row showing pinned versions of every LLM model in use (Haiku 4.5, Sonnet 4.6, judge Opus 4.6) with a "last changed" date. Answers the standing question when scores move: "did we change something or did Anthropic ship an update?"

**Production-vs-eval divergence.** Foundation 5 ships sampled production scoring. The Metrics tab should surface a row comparing production-scored mean against eval-scored mean for each dimension. Per the plan: "If production tone runs significantly worse than scenario tone, our scenarios are missing something." Threshold: `|prod − eval| > 0.5` is yellow; > 1.0 is red.

**Coverage-gap and lifecycle callout.** Foundation 6 emits a post-run health report listing retire / investigate / calibrating / stale scenario candidates. Surface the headline counts in the Metrics tab — "X scenarios calibrating, Y in investigate state, Z stale" — with a link to the latest health-report markdown file. Pulls Foundation 6's signals into the dashboard the team already opens daily.

**"Calibrating" scenario flag in Section 8.** Foundation 6 adds a "calibrating" status for scenarios with fewer than 3 runs of history (excluded from regression detection). Section 8 should show a small flag on those rows so a low score on a brand-new scenario isn't read as a regression.

**Outcome-vs-quality pairings.** Eval Plan Foundation 7's most consequential observation: "An LLM judge can score every conversation 4.9/5 while the bot remains useless because it reads at college level when the population reads at sixth grade, or because nobody clicks through to directions." Once Foundation 7a (reading level) and 7b (production satisfaction) ship, the Metrics tab should include an "Outcomes" panel that pairs:

- Eval Tone score ↔ User Feedback Score
- Cultural Responsiveness ↔ Reading Level
- Hallucination Resistance ↔ Referral Success Rate (when post-pilot)

These pairings flag the "fluent and irrelevant" failure mode — high judge scores that don't translate to outcomes. Without them, the dashboard can show all-green while the bot fails its mission.

---

## 4. LLM Observability Maturity Gaps

Patterns from the 2026 LLM observability landscape (Datadog, Confident AI, Braintrust, Langfuse, Helicone, Portkey) that the YourPeer dashboard doesn't yet implement. Most are post-pilot, but worth cataloging before the system grows past where retrofitting is cheap.

**Drill from metric to cohort.** Today the Metrics tab is read-only — clicking a row opens the metric-detail dialog with formula and rationale, not the underlying sessions. Standard pattern across observability platforms: every numeric metric links to a filtered view of the underlying data. Generalizes the long-session hook from §1: every `MetricRow` becomes clickable to a Conversations-tab cohort view. UI lift small; backend depends on the conversations endpoint accepting filter params (already in §5 backend dependencies).

**Anomaly / outlier surfacing.** Datadog's LLM Observability automatically surfaces "outliers across key dimensions ... analyzed over the past week." Once Foundation 1's history exists, the same data unlocks: a small "What's Unusual" rail at the top of the Metrics tab showing days, sessions, or queries that fell more than 2σ from baseline. Reuses the same daily-snapshot pipeline that powers sparklines.

**Threshold breach alerting.** The dashboard shows red/amber/green pills but has no notification channel. Confident AI describes the standard pattern: "thresholds on online evaluation scores and drift, with PagerDuty, Slack, and Teams." For YourPeer, a Slack notification when a deploy-blocker dimension crosses its threshold (Safety & Crisis < 4.5 or Hallucination Resistance < 4.5) catches problems faster than waiting for someone to open the dashboard. Out of scope for the pilot; natural next step once Foundation 1 history exists.

**Per-template performance trending.** We track aggregate eval dimension scores. We don't track per-query-template performance over time. From Confident AI's framing: "track how specific prompts and categories perform over time so degradation in one workflow is not hidden by aggregate stability." For YourPeer: which template (FoodQuery, HousingEligibilityQuery, etc.) is regressing? The aggregate `No-Result by Service` is the static version; the temporal version requires Foundation 1 history plus per-template tagging.

**Hierarchical topic clustering of production queries.** Datadog ships this as "Patterns — automated hierarchical topic clustering of your production traffic" specifically for surfacing what users are asking. Pairs with Foundation 5: a topic cluster that doesn't appear in any eval scenario is a coverage gap waiting to happen. Forward-looking item — only relevant once production transcript sampling is in place.

**Data freshness timestamp on the dashboard.** Currently no indication of how old the underlying data is. With `PILOT_DB_PATH` set, data persists across restarts; without it, an hour of downtime erases the dashboard. A small "Data updated X minutes ago · Y events captured · Z sessions today" header keeps users honest about what they're looking at.

**OpenTelemetry GenAI semantic conventions.** Standardization target for portable LLM observability — instrumenting against OTel conventions makes future migration to a third-party platform (Langfuse, Confident AI, Datadog) measured in weeks rather than months. Not worth implementing now, but worth knowing as the right convention if and when audit log instrumentation is rewritten.

**Sampling strategy for high-volume traffic.** Currently every conversation goes into the audit log. At pilot scale this is fine. The 2026 guidance: "For high-volume systems, sample 10–20% of requests for full traces while logging basic metrics for all traffic." Worth designing for before traffic grows past where storage and query cost become prohibitive. Decision point: at what `total_sessions/day` threshold does the dashboard's response time degrade if we move to sampled detail?

---

## 5. Backend / Data Pipeline Dependencies

These block multiple items above and are worth scoping together.

**`audit_log` → daily snapshot pipeline.** Powers any trend / sparkline / "since last visit" comparison. One scheduled job, one new table, one new endpoint. Probably a half-day of work but blocks several UI items.

**Per-task LLM cost estimation.** `llm_metrics.by_task` has calls + avg latency. Adding `input_tokens`, `output_tokens`, and `estimated_cost` per task would unblock the cost-contribution version of the Crisis Workload callout and similar sharpening elsewhere.

**Service-type joined to freshness telemetry.** Required for per-category Data Freshness drill-down. Trivial if already captured in `query_execution` event payloads; small backend tweak if not.

**Eval report metadata extension.** Add `run_number`, `run_timestamp`, `weighted_overall_average`, and `dimension_score_distributions` to the `EvalReport` JSON shape. Eval reporter (`eval_llm_judge.py`) emits these to the report file; UI reads them.

**Long-session filter on Conversations endpoint.** New query parameter `min_turns` (or similar) on the existing `/admin/api/conversations` route to support drilling from Metrics into specific session cohorts.

---

## 6. Investigation Backlog

These are open questions about what the live data is *saying* — not implementation tasks. Each should be looked at before acting.

**Why is the tone classifier almost never firing?** R39-era data: 16 of 649 classified turns have a tone detected (97.5% empty). The new banner now flags this in the UI, but the underlying question stays open. Possibilities worth checking:

- Are the tone phrase lists too narrow for actual user phrasing in the wild?
- Is the split classifier's tone-extraction step running on every turn, or only some?
- Is the data correctly captured — or is `tone_distribution` only logged when set, making the denominator look inflated?

**Why is `Avg Turns to Query` mean = 7.8 but median much lower?** The bimodal distribution is now visible (Turn Distribution shows the long tail), but the long sessions themselves haven't been examined. Are they:

- Power users using the bot productively across multiple needs (multi-intent working as designed)?
- Stuck users in frustration loops (would show up in Frustration Tier Distribution but currently shows only 2 frustrated sessions, T1 only)?
- Sessions where the user is exploring conversationally before searching?

Sample 5–10 sessions from the `11+ turns` bucket and read transcripts. The answer probably changes whether 7.8 is good news or bad.

**Why is Data Freshness at 60%?** The headline drops to ⚠ Watch but no diagnosis. Is it:

- Concentrated in specific service categories (most likely; some categories naturally have less frequent re-validation)?
- Concentrated in specific boroughs (Staten Island has historically thin coverage)?
- A single large data ingest from > 90 days ago dominating the count?

The per-category drill-down (UI item above) would answer #1 and #2 immediately. #3 needs a one-off SQL query against `services.last_validated_at`.

**Crisis detection running on 83% of all LLM calls — why?** This is the single largest cost / latency driver. Two questions:

- Is the regex pre-filter for crisis detection too narrow, forcing too many turns to the Sonnet-based fallback?
- Or is the architecture intentional (Sonnet on every turn for safety) and we just need to absorb the cost?

If the answer is #1, expanding the regex phrase list cuts Sonnet calls dramatically. If #2, the metric stops being a "warning" and just becomes infrastructure context. Probably worth sampling 20–30 crisis_detection calls and checking how many actually flagged crisis.

**`p50 LLM latency: 1074ms` vs. `target ≤ 600ms`.** The target was set when most calls were expected on Haiku. With 845/1020 calls now on Sonnet (per Calls by Model), exceeding the target is structural, not a bug — Sonnet's documented latency is 800-1500ms. Two paths:

- Update the target to reflect the actual model mix (e.g. `p50 ≤ 1000ms when crisis detection > 50% of calls`).
- Or treat this as an indicator that the crisis-detection volume itself needs to come down (see above).

The current Watch state correctly flags "something to look at" but the resolution is product-level, not engineering.

**`OrgNameQuery: 100% no-result (1q)`.** Single query, but worth a quick look: did the user actually search for an org by name and we returned nothing? Might be a slot-extraction issue (entity routed to the wrong template), might be a real coverage gap, might be one user with a typo. The structured no-result table will make these one-off events visible going forward.

**Why `User Feedback Score: 100% (n=7)`?** Genuinely positive signal or selection effect (the kind of users who give feedback at all are the satisfied ones)? Worth a quick read of the 7 sessions to see what was praised. Independent of whether to widen the prompt to capture more responses.

**Bot Repetition: 4 sessions (4%).** Below the ≤ 5% threshold so it's green, but the low-n threshold elsewhere would suggest n=4 is fragile. Read those 4 transcripts — repetition is the signature of a frustration loop the bot can't break. Even at low frequency, the impact per occurrence is high.

**Structural risk: high judge scores ≠ user outcomes.** Worth raising explicitly as a known limitation of the entire dashboard. The Eval Quality Plan v2 (Foundation 7) frames this directly: "An LLM judge can score every conversation 4.9/5 while the bot remains useless because it reads at college level when the population reads at sixth grade, or because nobody clicks through to directions, or because users abandon after the first follow-up question." Today the Metrics tab has User Feedback Score (n=7 in the live data) as the only outcome-adjacent signal, and that's a thumbs up/down on the bot's response, not on the actual service. Until Foundation 7a (reading level) and 7b (production satisfaction) land, every quality story the dashboard tells is conversation quality, not outcome quality. Acceptable for the pilot; worth not forgetting.

---

## 7. Decided Defaults to Revisit

Values set during the R39 review that were defensible but not deeply researched. Worth revisiting once we have more data.

| Setting | Current | Notes |
|---|---|---|
| `LOW_N_THRESHOLD` (in `metrics/page.tsx`) | 5 | Lets n=7 (User Feedback Score) display as a full-confidence pill. May want to raise to 10 once we have more rows where it applies. |
| Tone-degenerate banner trigger | coverage < 5% AND classified ≥ 50 | Picked to avoid false-positive at startup. The 5% threshold is judgment; the 50-turn floor is to avoid flapping. |
| Crisis Workload `warning` status | calls share > 50% | Currently triggers on the live data. May want to raise to 70% if we accept high crisis-detection volume as expected, or leave at 50% to keep visibility. |
| Confirmation UX target | ≥ 4.5 | Was raised from ≥ 3.5 in R39 prep based on actual scores (R32 = 4.83). Tighten further to ≥ 4.7 if scores stabilize. |
| Error Recovery target | ≥ 4.5 | Same as above — raised from ≥ 3.5. R32 = 4.76. |

---

## 8. Documentation Inconsistencies

Items where the docs and the dashboard now disagree, or where the docs themselves contradict each other.

**`docs/ops/METRICS.md` infrastructure table — PII scanner row.** Currently reads `PII scanner | Automated redaction verification | ⚠️ Partial (regex-based...)`. This conflates the input-side **redactor** (regex, runs on every message before storage, exists) and the audit-side **scanner** (would verify stored transcripts for missed PII, doesn't exist).

Cleanest fix is splitting the row:

```
| PII redactor (input-side) | Strips PII from messages before storage | ⚠️ Partial (regex-based; Microsoft Presidio NER identified as upgrade path) |
| PII scanner (audit-side)  | Detects PII leaks in stored transcripts | ❌ Not implemented (see §3.4) |
```

**Hallucination Rate — docs vs. structural reality.** `metric-definitions.ts` and the dashboard both treat Hallucination Rate's "on-target" status as a structural guarantee from the Safer Limited RAG architecture, with the LLM-as-Judge `hallucination_resistance` dimension as ongoing confirmation. METRICS.md §3.5 was updated in this batch to match. Worth a quick re-read for any other places that still describe canary tests as the primary measurement source.

**Aspirational language in METRICS.md.** Several sections still describe measurement as if the infrastructure exists — phrases like "the PII scanner runs weekly during pilot" or "canary tests run on every deploy". Now flagged in §2.5, §3.2, §3.4, §3.5, but worth a full pass to make sure no aspirational measurement language remains.

---

## Recently Landed (for context)

For anyone returning to this doc — what's already been shipped in the R39 prep window:

- Eval Targets section in Metrics tab now wires to live `evalResults` slice from the admin store. Previously hardcoded to `value=null` for every dimension.
- Headline rows added above eval dimensions: Overall Average, Passing Rate, Critical Failures.
- Confirmation UX and Error Recovery targets tightened from ≥ 3.5 to ≥ 4.5 (matching observed performance).
- Section 4 tone breakdown capped at top 6 with rollup row, matching Section 7's pattern.
- Sections 8 (Eval Targets) and 9 (Closed-Loop Outcomes) collapsed by default.
- Crisis False Positive Rate, PII Leakage Rate, and Eligibility Fit Rate moved to Post-pilot phase across UI, detail dialog, and METRICS.md — each with explicit "moves back to Pilot once X exists" criteria.
- METRICS.md infrastructure table flipped Canary suite to ❌ Not implemented.
- Pilot Review Cadence weekly bullet dropped the "PII scanner alerts" reference.
- Hallucination Rate measurement section in METRICS.md updated to reflect actual evidence sources (structural guarantee + LLM-as-Judge dimension), not the unbuilt canary suite.

R39-era investigation batch:

- `Avg Turns to Query`, `Avg Turns per Session`, and `Avg Session Duration` flipped to median-first display. Status now evaluated against the median; mean and p95 demoted to subtitle.
- `isLowN(n, threshold=5)` helper applied to User Feedback Score, Emotional → Escalation Rate, Emotional → Service Rate. Triggers `no-data` styling with an `n=X (low confidence)` pill when the denominator is below threshold.
- Crisis Detection Workload row added to Section 3, showing `crisis_detection` task volume, latency, and share of LLM calls.
- Tone-degenerate banner added to Section 4 — fires when tone classifier coverage is below 5% across at least 50 classified turns.

Conversations & transcript drawer batch:

- Conversations table gained filtering: search across session ID and slot keys/values, segmented outcome filter (All / Has results / No results / Crisis), minimum-turns numeric input, live result count with Clear affordance.
- Transcript drawer rewritten with a Slot Trace panel at the top (final slot map with per-key turn-set markers and overwrite history on hover), per-turn slot diffs (`+ added`, `~ overwritten`, `− removed`), `query_execution` and `feedback` events rendered inline as a unified timeline, and the drawer widened from 700px to 900px.
- `formatUptime` in System Health now drops to days for uptimes ≥ 24h (renders `9d 11h 36m` instead of `227h 16m`).

Admin code-review follow-up batch:

- `app/admin/page.tsx` (195-line stale duplicate of the metrics page) replaced with a server-side redirect to `/admin/overview`. The error boundary's "Back to overview" link updated to point at `/admin/overview` directly.
- `MODEL_VERSIONS` constant block introduced at the top of `model-data.ts` as the single in-frontend source of truth for model IDs and display names. `MODELS` table entries now read from it; `model-card.tsx`'s duplicate `labels` map removed in favour of `MODELS[model].name`. `TODO(eval-plan Foundation 4)` comment in `model-data.ts` points at the durable cross-language fix (backend `/api/health` exposing model versions at runtime).
- `cost-calculator.tsx` 10 individual `useState` hooks (8 sliders + 2 toggles) collapsed to one `useReducer` over a `CalculatorState` object. New "Reset" button next to the Inputs header restores defaults; disabled when no fields have been changed. `activeConfig` deliberately kept as separate `useState` so resetting sliders doesn't discard a deliberate config-comparison choice.

Threshold-consistency batch:

- New `lib/admin/eval-dimensions.ts` module owns the canonical list of LLM-judge dimensions, their labels (long for Metrics tab, short for Evals tab), targets, and blocker flags. The Metrics tab and the Evals tab now both consume from it — they previously disagreed on Confirmation UX and Error Recovery targets (3.5 vs 4.5).
- Four metrics renamed in the R39 prep batch (Median Turns to Query, Median Turns per Session, Median Session Duration, Crisis Detection Workload) added to `metric-definitions.ts`. Clicking those rows now opens a populated detail dialog instead of being a silent no-op.
- `Avg Turns to Result` thresholds in `overview/page.tsx` aligned with `metrics/page.tsx`: target ≤ 5, warn at 7. Previously showed `target ≤ 4` with thresholds at 4 / 6 / >6.
