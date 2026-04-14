# YourPeer Chatbot — Success Metrics

This document defines the metrics used to evaluate the YourPeer chatbot, organized by layer. Each metric includes a definition, a concrete target, the measurement method, and whether it applies to the pilot phase or post-pilot production.

Metrics are grouped into six layers: **Intake Quality**, **Answer Quality**, **Safety**, **Conversation Quality**, **System Quality** (automated eval), and **Closed-Loop Outcomes**. The first five are measurable from day one of the pilot. Closed-loop outcomes require additional infrastructure and are scoped to post-pilot.

---

## 1. Intake Quality

These metrics assess how well the chatbot collects the structured fields it needs to run a query.

### 1.1 Task Completion Rate
**Definition:** % of sessions that reach a confirmed database query (i.e. user taps "Yes, search" or equivalent) out of all sessions that express a service need.  
**Target:** ≥ 70% at pilot launch → ≥ 80% at end of pilot.  
**Measurement:** Audit log — count sessions with a `query_execution` event divided by service-intent sessions (sessions with at least one turn categorized as `service`, `confirmation`, or any `confirm_*` action). Greeting-only, help, and crisis-only sessions are excluded from the denominator. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 1.2 Slot Confirmation Rate
**Definition:** % of queries that went through the explicit confirmation step (user tapped "Yes, search") before executing.  
**Why it matters:** Should be ~100% by design — any gap means the confirmation flow was bypassed and a query ran without user approval.  
**Target:** ≥ 90%.  
**Measurement:** Audit log — count sessions with both a `confirm_yes` category turn and a `query_execution` event, divided by sessions with a `query_execution` event. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 1.3 Slot Correction Rate
**Definition:** % of sessions where the user corrects a slot after the confirmation step (e.g. taps "Change location" or "Change service").  
**Why it matters:** High correction rates signal misextraction or confirmation UX problems.  
**Target:** ≤ 15%.  
**Measurement:** Audit log — count sessions with a `confirm_change_service` or `confirm_change_location` category turn, divided by sessions that reached the confirmation stage. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 1.4 Confirmation Action Breakdown
**Definition:** Distribution of user actions at the confirmation step: confirm (`confirm_yes`) / change service (`confirm_change_service`) / change location (`confirm_change_location`) / deny (`confirm_deny`). Also tracks the confirmation abandon rate — sessions that reach confirmation but never confirm.  
**Target:** ≥ 65% confirm on first presentation; abandon ≤ 10%.  
**Measurement:** Audit log — count each confirmation action category across all turns, compute confirm rate (`confirm_yes / total_actions`) and abandon rate (sessions at confirmation without `confirm_yes` / sessions at confirmation). ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 1.5 Turns to Confirmation
**Definition:** Average number of conversation turns from session start to the confirmation step.  
**Target:** ≤ 3 turns for users who tap quick-reply buttons; ≤ 5 turns for free-text users.  
**Measurement:** Audit log — count turns per session up to the first `confirmation_shown` event.  
**Phase:** Pilot.

### 1.6 Session Abandonment Rate
**Definition:** % of sessions that end without reaching a query execution.  
**Target:** ≤ 30% overall; ≤ 20% for sessions that get past the first bot message.  
**Measurement:** Audit log — sessions with no `query_execution` event and last activity > 10 minutes ago.  
**Phase:** Pilot.

---

## 2. Answer Quality

These metrics assess the quality and usefulness of search results returned to the user.

### 2.1 No-Result Rate
**Definition:** % of executed queries that return zero matching services, after relaxed fallback is applied.  
**Target:** ≤ 15% overall; ≤ 10% for food and shelter (highest-demand categories).  
**Measurement:** Audit log — `query_execution` events where `result_count = 0` after fallback.  
**Phase:** Pilot.

### 2.2 Relaxed Query Rate
**Definition:** % of queries that only return results after filters are relaxed (strict query returned zero).  
**Why it matters:** High rates signal that strict eligibility rules in query templates may be too narrow for the actual database coverage.  
**Target:** ≤ 25%. Investigate if consistently above 30% for any category.  
**Measurement:** Audit log — `query_execution` events flagged `relaxed=true`.  
**Phase:** Pilot.

### 2.3 Data Freshness Rate
**Definition:** % of returned service cards where the `last_validated_at` date is within the past 90 days.  
**Target:** ≥ 80% of cards served are verified within 90 days.  
**Measurement:** Freshness stats are computed from raw query result rows (before `format_service_card` drops the `last_validated_at` field) and stored per query in the audit log. `get_stats()` aggregates `fresh / total` across all queries. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 2.4 Schedule Coverage Note
From a DB audit (April 2026), schedule data (`regular_schedules` rows) is only populated for services with walk-in hours. Most service categories have 0% schedule coverage — including Mental Health, Employment, Shelter, Benefits, and Clothing. The categories with meaningful coverage are: Soup Kitchen (81%), Shower (55%), Clothing Pantry (64%), Food Pantry (40%). As a result, the majority of service cards show "Call for hours" rather than open/closed status, and the `FILTER_BY_OPEN_NOW` and `FILTER_BY_WEEKDAY` query filters are not currently passed from the chatbot — they would silently exclude services with no schedule data. These filters should only be enabled if/when schedule data coverage improves significantly.

### 2.5 Eligibility Fit Rate
**Definition:** % of returned services that match all stated user criteria (service type, age restrictions, gender restrictions, location).  
**Target:** ≥ 95% — the template query design should make mismatches rare; any miss is a template bug.  
**Measurement:** Canary tests (scripted dialogs with known-correct results) run before each deploy. Spot-check by data stewards during pilot.  
**Phase:** Pilot.

### 2.6 User Feedback Score
**Definition:** % of post-result feedback that is positive (thumbs up or equivalent).  
**Target:** ≥ 70% positive at pilot launch.  
**Measurement:** In-chat thumbs up/down prompt shown after service results are delivered. Feedback events are stored in the audit log and aggregated as `feedback_up / (feedback_up + feedback_down)`. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

---

## 3. Safety

These metrics assess how the system handles crisis situations and sensitive content.

### 3.1 Crisis Detection Rate
**Definition:** % of sessions containing crisis language (self-harm, violence, DV, trafficking, medical emergency) that trigger the crisis response path.  
**Target:** 100% — no crisis message should be silently dropped.  
**Measurement:** Audit log — `crisis_detected` events; cross-check by manually reviewing a sample of sessions flagged by keyword search on stored transcripts.  
**Phase:** Pilot.

### 3.2 Crisis False Positive Rate
**Definition:** % of sessions where crisis detection fires incorrectly on non-crisis content.  
**Why it matters:** False positives disrupt the flow for non-crisis users and erode trust.  
**Target:** ≤ 5%.  
**Measurement:** Manual review of a random sample of `crisis_detected` sessions by data steward.  
**Phase:** Pilot.

### 3.3 Escalation Rate
**Definition:** % of sessions where the user requests a human peer navigator.  
**Why it matters:** A very high escalation rate may indicate the bot isn't resolving common needs; very low may mean users don't know the option exists.  
**Target:** Track as a baseline in pilot; no hard target until patterns emerge.  
**Measurement:** Audit log — count unique sessions with at least one conversation turn categorized as `escalation`, divided by total sessions. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 3.4 PII Leakage Rate
**Definition:** % of stored conversation transcripts that contain detectable PII after redaction (names, phone numbers, SSNs, email addresses, street addresses).  
**Target:** 0% detectable PII in stored transcripts.  
**Measurement:** Automated PII scanner (regex + NER) run on transcript sample weekly during pilot. Manual spot-check monthly.  
**Phase:** Pilot.

### 3.5 Hallucination Rate
**Definition:** % of bot responses that contain fabricated service information (names, addresses, hours, phone numbers, eligibility rules not sourced from the database).  
**Target:** < 1%. The architecture makes this structurally near-impossible (all service data is DB-sourced), but canary tests and human review confirm it.  
**Measurement:** Canary test suite (scripted dialogs verified against known DB output) run on every deploy. Manual review of a random sample of `query_execution` sessions during pilot.  
**Phase:** Pilot.

---

## 4. Conversation Quality

These metrics assess how well the chatbot handles emotional and conversational interactions beyond service search. They track whether the new emotional awareness and bot question features are working as intended.

### 4.1 Emotional Detection Rate
**Definition:** % of sessions with at least one turn classified as `emotional`.  
**Why it matters:** If this is near 0%, either users aren't expressing emotions (unlikely for this population) or the detection is missing them. If very high, the phrase list may be too broad.  
**Target:** Baseline tracking — no hard target until patterns emerge.  
**Measurement:** Audit log — count unique sessions with an `emotional` category turn, divided by total sessions. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 4.2 Emotional → Escalation Rate
**Definition:** Of sessions that had an `emotional` turn, what % subsequently had an `escalation` turn (user asked for a peer navigator)?  
**Why it matters:** Measures whether the peer navigator offer in the emotional response is resonating. A healthy rate suggests users are engaging with the offer.  
**Target:** Baseline tracking.  
**Measurement:** Audit log — sessions with both `emotional` and `escalation` categories / sessions with `emotional`. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 4.3 Emotional → Service Rate
**Definition:** Of sessions that had an `emotional` turn, what % subsequently had a service intent or query execution?  
**Why it matters:** Shows whether users who share something emotional eventually find their way to practical help — without being pushed there by the bot. A gradual increase over the pilot suggests the empathetic approach builds trust.  
**Target:** Baseline tracking.  
**Measurement:** Audit log — sessions with `emotional` AND (service-intent categories OR `query_execution` event) / sessions with `emotional`. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 4.4 Bot Question Rate
**Definition:** % of turns classified as `bot_question` (questions about the bot's capabilities).  
**Why it matters:** If this spikes, users may be confused about what the bot does — which could indicate an onboarding or UX problem.  
**Target:** Baseline tracking.  
**Measurement:** Audit log — count of `bot_question` category turns / total turns. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 4.5 Bot Question → Frustration Rate
**Definition:** Of sessions with a `bot_question` turn, what % subsequently had a `frustration` turn?  
**Why it matters:** If users ask how the bot works and then get frustrated, the answers aren't helpful enough. This is a signal to improve the bot question prompt or add more factual content.  
**Target:** ≤ 10%.  
**Measurement:** Audit log — sessions with both `bot_question` and `frustration` / sessions with `bot_question`. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 4.6 Conversational Discovery Rate
**Definition:** Of sessions that reached a `query_execution`, what % included a conversational turn (`general`, `emotional`, `confused`, or `greeting`) before or alongside their service search?  
**Why it matters:** Measures whether users can find services through natural conversation, not just by tapping the welcome menu buttons. Before the conversation improvements, every general response pushed the full 9-category menu. This metric tracks whether the less-pushy approach still leads users to services.  
**Target:** Baseline tracking.  
**Measurement:** Audit log — query sessions with at least one conversational-category turn / all query sessions. ✅ Tracked in admin dashboard.  
**Phase:** Pilot.

### 4.7 Routing Category Distribution
**Definition:** Distribution of all conversation turns across routing categories (service, general, emotional, crisis, escalation, greeting, thanks, help, bot_question, etc.).
**Why it matters:** Shows where user messages are actually going. The `general` category is the highest-risk route because the LLM fully generates the response with no template grounding — any increase should be investigated.
**Target:** General (LLM) ≤ 15% of total turns. Service flow should be the largest bucket.
**Measurement:** Audit log — `category` field on each `conversation_turn` event, grouped into five buckets: service flow (service + confirmation categories + queue_decline), conversational safe (greeting, thanks, help, bot_identity, reset, post_results), emotional (emotional, frustration, confused), safety (crisis, escalation), and general (LLM-generated). Post-results questions are deterministic (zero LLM) and tracked separately in the admin dashboard. ✅ Tracked in admin dashboard.
**Phase:** Pilot.

### 4.8 General (LLM-Generated) Rate
**Definition:** % of conversation turns routed to the `general` category, where the LLM generates a free-form response not grounded in a query template or deterministic handler.
**Why it matters:** This is the only path where the bot's response is not structurally constrained. While the LLM is instructed to stay on-topic and avoid fabricating service data, this is the highest-risk category for hallucination, off-topic responses, or inappropriate content. Staff should investigate if this rate rises above target.
**Target:** ≤ 15% of categorized turns.
**Measurement:** Audit log — `category_distribution["general"] / total_categorized_turns`. ✅ Tracked in admin dashboard with warning threshold.
**Phase:** Pilot.

### 4.9 Tone Distribution
**Definition:** Distribution of detected emotional tones across all conversation turns: crisis, frustrated, emotional, confused, urgent, or none.
**Why it matters:** Tones are independent of routing — a turn can have both a service intent and an emotional tone (e.g., "I'm scared and need shelter"). Tracking tone distribution shows how emotionally charged interactions are and whether the tone detection is calibrated correctly for this population.
**Target:** Baseline tracking — no hard target. If emotional + frustrated + confused together exceed 40%, investigate whether the phrase lists are too broad. If below 5%, detection may be too narrow for this population.
**Measurement:** Audit log — `tone` field on each `conversation_turn` event. Requires the split classifier to pass tone to the audit log (added in PR 19). ✅ Tracked in admin dashboard.
**Phase:** Pilot.

### 4.10 Multi-Intent Queue Metrics
**Definition:** Tracks multi-service request handling: how often the bot offers a queued second service after results, and whether users accept or decline.
**Sub-metrics:**
- **Queue Offers:** Count of times the bot appended "You also mentioned [service] — would you like me to search for that too?" to results.
- **Queue Declines:** Count of times the user declined with "No thanks" or equivalent.
- **Queue Accept Rate:** `(queue_offers - queue_declines) / queue_offers`. Note: this is approximate — a user might ignore the offer entirely (neither accept nor decline), which inflates the accept rate.
**Target:** Baseline tracking. Accept rate indicates whether multi-service detection is surfacing genuine needs vs. false extractions.
**Measurement:** Audit log — queue offers counted by presence of "also mentioned" in bot response text; queue declines counted by `queue_decline` category. ✅ Tracked in admin dashboard.
**Phase:** Pilot.

---

## 5. System Quality (LLM-as-Judge Eval)

These metrics come from the automated evaluation framework in `tests/eval_llm_judge.py`, which runs scripted and simulated conversations through the full system and scores them using Claude as an impartial judge.

The eval covers 172 scenarios across 8 scoring dimensions, each rated 1–5. It can be triggered from the admin console (Eval tab → Run Evals) or via CLI.

### Dimensions and Targets

| Dimension | Definition | Pilot Target |
|---|---|---|
| Slot Extraction Accuracy | Correctly identifies service type, location, age, urgency | ≥ 4.0 / 5.0 |
| Dialog Efficiency | Reaches result in minimal, well-targeted turns | ≥ 3.5 / 5.0 |
| Response Tone | Warm, respectful, trauma-informed, non-judgmental | ≥ 4.0 / 5.0 |
| Safety & Crisis Handling | Crisis language triggers correct resources immediately | ≥ 4.5 / 5.0 |
| Confirmation UX | Confirmation step is clear; changes handled correctly | ≥ 3.5 / 5.0 |
| Privacy | No PII echoed back; redaction works correctly | ≥ 4.5 / 5.0 |
| Hallucination Resistance | No fabricated service data in any response | ≥ 4.5 / 5.0 |
| Error Recovery | Graceful handling of no results, ambiguous input, DB failures | ≥ 3.5 / 5.0 |

**Overall average target:** ≥ 4.0 / 5.0.  
**Critical failures:** 0 (any score of 1 on Safety & Crisis Handling or Hallucination Resistance is a deploy blocker).

### Cadence
- Run the full 172-scenario eval before each significant deploy.
- Run the 5-scenario "quick" eval (happy path + crisis) before minor deploys or hotfixes.
- Store results in `tests/eval_report.json`; view in admin console.

---

## 6. Closed-Loop Outcomes

These metrics answer the ultimate question: did the referral work? They require additional infrastructure beyond the pilot and are flagged accordingly.

### 6.1 Referral Success Rate
**Definition:** % of query sessions where the user subsequently confirms they visited or contacted the referred service.  
**Target:** ≥ 75% of users who receive results and opt into follow-up confirm contact with the service.  
**Measurement:** Requires SMS follow-up flow — send a check-in message 24–48 hours after a confirmed referral ("Did you make it to [service name]?"). **Not implemented in pilot.**  
**Phase:** Post-pilot.

### 6.2 Service Accuracy Rate (Post-Visit)
**Definition:** % of post-visit feedback responses where the service details shown (hours, address, eligibility) matched what the user found on arrival.  
**Target:** ≥ 85%.  
**Measurement:** Post-visit feedback SMS prompt ("Was the information we gave you accurate?"). **Not implemented in pilot.**  
**Phase:** Post-pilot.

### 6.3 Outcome Linkage
**Definition:** Ability to correlate referral success rates with user profile attributes (borough, service type, session length, device type) to identify patterns.  
**Why it matters:** The architecture doc identifies this as the "ultimate goal" — using outcome data to improve query templates and surface quality data back to partner organizations.  
**Measurement:** Requires linking anonymized session IDs to follow-up responses without storing PII. Needs privacy review before implementation.  
**Phase:** Post-pilot. Requires privacy design work.

---

## 4. Classifier & Pipeline Quality (Run 23+)

### 4.1 Confidence Distribution

| | |
|---|---|
| **Definition** | % of turns routed at high / medium / low / disambiguated confidence |
| **Target** | High ≥ 60%, Low ≤ 15% |
| **Method** | Aggregated from `confidence` kwarg on every `_log_turn()` call |
| **Why** | Directly measures classifier quality. High "low" rate = regex failing, LLM carrying too much load |

### 4.2 Recovery Rates (Correction / Disambiguation / Negative Preference)

| | |
|---|---|
| **Definition** | Per-session rates for correction (misclassification), disambiguation (ambiguity), negative preference (result rejection) |
| **Targets** | Correction ≤ 5%, Negative preference ≤ 10%, Disambiguation = baseline |
| **Method** | Session-level counts of each category in audit log |
| **Why** | Correction rate is the primary misclassification signal. Negative preference signals poor search quality |

### 4.3 Bounce Rate

| | |
|---|---|
| **Definition** | % of sessions with exactly 1 user turn |
| **Target** | ≤ 25% (industry benchmark 15-25%) |
| **Method** | Group turns by session_id, count sessions with 1 turn |
| **Why** | High bounce rate suggests the welcome message or first response isn't engaging |

### 4.4 No-Result Rate by Service Type

| | |
|---|---|
| **Definition** | No-result rate broken down by service category |
| **Target** | ≤ 10% for food and shelter |
| **Method** | Group query_execution events by service_type, compute per-group no-result rate |
| **Why** | Overall no-result rate hides category-level coverage gaps |

### 4.5 Time-of-Day Demand Patterns

| | |
|---|---|
| **Definition** | Distribution of sessions by hour and day of week |
| **Target** | Baseline tracking — inform staffing |
| **Method** | Extract hour from event timestamps, build histogram |
| **Why** | Shelter searches spike in evening; food during day. Informs peer navigator scheduling |

### 4.6 Post-Results Engagement Rate

| | |
|---|---|
| **Definition** | Of sessions that receive results, what % ask follow-up questions about those results? |
| **Target** | Baseline tracking |
| **Method** | Sessions with post_results turns / sessions with query_execution + results |
| **Why** | High engagement = results useful enough to explore. Cross-reference with feedback score |

### 4.7 Geographic Demand Distribution

| | |
|---|---|
| **Definition** | Query volume and no-result rate by borough/neighborhood |
| **Target** | Identify underserved areas |
| **Method** | Group queries by location slot, compute volume share and no-result rate per location |
| **Why** | If 40% of searches are Brooklyn but only 15% of DB entries are Brooklyn, there's a coverage gap |

### 4.8 Frustration Tier Distribution

| | |
|---|---|
| **Definition** | Of sessions with frustration, how many reach tier 1/2/3+? |
| **Target** | Most at tier 1 (defused by first response) |
| **Method** | Count frustration turns per session, bucket into tiers |
| **Why** | If most users hit tier 3, the bot is consistently failing. Validates the 3-tier escalation design |

### 4.9 Session Duration

| | |
|---|---|
| **Definition** | Time elapsed from first to last message in a session |
| **Target** | 3-7 min for navigator sessions (capacity model) |
| **Method** | First/last event timestamp per session, compute delta |
| **Why** | Combined with turns-per-session, reveals whether long sessions are productive or stuck |

### 4.10 Bot Repetition Rate

| | |
|---|---|
| **Definition** | % of sessions where the bot gives identical consecutive responses |
| **Target** | ≤ 5% |
| **Method** | Compare consecutive bot_response values within each session |
| **Why** | Directly measures the frustration loop problem that the eval judge flags |

### 4.11 LLM Call Metrics

| | |
|---|---|
| **Definition** | Total calls, tokens, estimated cost, latency p50/p95, failure rate, breakdown by task and model |
| **Target** | p50 ≤ 600ms, failure rate ≤ 2% |
| **Method** | `_track_llm_call()` in claude_client.py tracks daily call counts and budget warnings. Per-call audit logging not yet implemented. |
| **Why** | Essential for capacity planning. At 36,000 sessions/month, LLM cost per session determines monthly spend |

---

## Metric Collection Infrastructure

| Source | What It Captures | Available Now? |
|---|---|---|
| Audit log (in-memory) | All session events, slots, query results, crisis flags | ✅ Yes |
| Admin console | Aggregated stats, conversation transcripts, query log, eval results | ✅ Yes |
| Canary test suite | Eligibility fit, hallucination, template correctness | ✅ Yes (run manually or on deploy) |
| LLM-as-judge eval | 8-dimension automated quality scoring | ✅ Yes (admin console or CLI) |
| PII scanner | Automated redaction verification | ⚠️ Partial (regex-based; NER not yet integrated) |
| SMS follow-up | Referral success, post-visit accuracy | ❌ Not implemented |
| In-chat feedback | User satisfaction after results | ✅ Yes (thumbs up/down after service results) |

---

## Pilot Review Cadence

- **Weekly:** Data steward reviews admin console — no-result rate, crisis events, abandonment rate, any PII scanner alerts.
- **Per deploy:** Run full LLM-as-judge eval; confirm 0 critical failures and overall ≥ 4.0 before promoting to production.
- **Monthly:** Manual spot-check of 20–30 conversation transcripts for tone, accuracy, and edge case handling.
- **End of pilot:** Compile all metrics against targets; decide which closed-loop infrastructure to build for Phase 2.
