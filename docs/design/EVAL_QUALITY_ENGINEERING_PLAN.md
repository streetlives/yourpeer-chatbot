# YourPeer — Evaluation Quality Engineering Plan (v2)

<!-- drift:ignore-file -->
<!-- This is a plan document — it intentionally references files,
     scripts, and tests that don't exist yet. Suppressing file-ref
     and prose-line-num warnings file-wide because the doc's
     purpose is to describe work not yet done. -->

**Written:** May 4, 2026 (rev. 2)
**Supersedes:** `EVAL_QUALITY_ENGINEERING_PLAN.md` (April 21, 2026, R34-era)
**Anchor run:** R38 (May 3, 2026) — 175 scenarios, 173 passing (98.9%), 8 critical failures
**Concurrent work:** Phase 2 of `PRE_LLM_REDACTION_SCOPE.md` (flag-ON eval running)
**Rev. 2 changes:** Added Foundation 7 (real-world outcome measurement) after reviewing the Nava PBC case study on AI for caseworkers. Adjusted sequencing to interleave Foundation 7 work alongside Foundation 5.

---

> **A note on Foundation numbering.** This plan defines its own Foundations 1–7, all about **eval quality** — cross-run history, judge calibration, triangulation, model-version pinning, production-to-eval bridge, automated lifecycle, and outcome measurement. No Foundation 8 exists in this plan.
>
> Other documents in the repo (notably `docs/ops/EVAL_RESULTS_R28-R41.md`, `scripts/fixture/REFRESH_RUNBOOK.md`, `tests/eval/eval_llm_judge.py` comments, and `docs/design/PRE_LLM_REDACTION_SCOPE.md`) reference "Foundation 7" and "Foundation 8" in a parallel **fixture-engineering** workstream — must-include partner pinning and dispatcher eligibility filters respectively. Those numbers are not part of this plan and the workstreams should not be conflated. References that conflate them (e.g., `PRE_LLM_REDACTION_SCOPE.md` line 533 calling eligibility-filter gaps "Foundation 6 in the eval-quality plan" — Foundation 6 here is the automated lifecycle health report, not anything bot-specific) are documentation drift, not a real cross-reference.
>
> When a future PR cleans this up, the right shape is to rename the fixture series to non-conflicting labels (e.g. "Fixture Pin", "Fixture Filter Dispatch") rather than renumber this plan. The plan's F1–F7 are referenced by external eval architecture docs and run write-ups; those references are correct and should remain stable.

---

## Why this plan exists, and why v1 wasn't enough

The v1 plan (April 21) named five threats to eval trust: judge noise, scenario drift, coverage gaps, slow feedback loop, and scenarios that confirm rather than test. That framing was correct and remains the right diagnosis. What v1 got wrong was treating the threats as a **scenario-level engineering problem** — six bug-fix patches, twelve scenarios to retire, eighteen new scenarios to author — when the actual problem is **infrastructural and methodological**.

Concretely:

- v1's Workstream A (six bug fixes) was largely closed by the unified-extractor migration, not by the targeted patches v1 proposed. `peer_diabetic_insulin` (3.09 → 4.45 in R38) had been failing for 11 runs; the fix that worked was an architectural bet, not a 20-LOC patch. **v1 misread structural failures as point bugs.**
- v1's Workstream B (scenario hygiene) was almost entirely unimplemented eight months later. Twelve retire-candidate scenarios are still in the suite. Eighteen coverage-gap scenarios still don't exist. **v1's scenario hygiene approach didn't survive contact with engineering reality** — manual scenario curation is something nobody schedules.
- v1's Workstream C (judge-noise mitigation) shipped a half-implementation: `non_deterministic_scenarios` is in the eval report, but it counts scenarios that *used the LLM simulator*, not scenarios whose scores actually swing across runs. **The proxy got built; the thing didn't.**
- v1's Workstream D (fast unit tests) is the workstream that actually landed — `test_service_change_merge.py`, `test_multi_intent_queue.py`, `test_frustration_and_crisis.py` exist, total ~1,300 lines. **The "build infrastructure" parts of v1 worked. The "do hygiene" parts didn't.**
- v1's Workstream E (process scaffolding) had the energy of a New Year's resolution. Quarterly reviews, lifecycle metadata, fragility tags — none implemented. **v1 underweighted process automation in favor of process suggestion.**

There is also a class of issues v1 did not see at all: no machine-readable cross-run history, no model-version pinning beyond the judge, no eval-framework regression test, no calibration-period for new scenarios, no production-to-eval feedback loop, no triangulation across judge protocols. These are the issues this plan opens with.

There is one further category v1 didn't see — and that this plan's first revision also missed until Nava PBC's case study on AI assistive chatbots for caseworkers surfaced it. **Everything in v1 measured conversation quality. Almost nothing measured whether users actually got help.** Conversational tone, slot extraction, dignity, hallucination resistance — all useful dimensions, all judged by an LLM looking at a transcript. None of them measure: did the user follow through to the service? Did they understand the response? Did the bot's reading level match the population's literacy level? Did anyone's life materially improve because they used this tool? An LLM judge can score every conversation 4.9/5 while the bot remains useless because it reads at college level when the population reads at sixth grade, or because nobody clicks through to directions, or because users abandon after the first follow-up question. Foundation 7 in this revision addresses that gap.

The shape of v2 is therefore different: **fewer workstreams, each addressing a structural gap; automation over hygiene; triangulation over single-judge trust; outcomes over transcripts.**

---

## What we have now (the inventory v1 did without)

Before proposing changes, the current state of the eval system:

**Scenario set.** 184 scenarios as of May 6, 2026: 175 from R38, plus 7 redaction-targeting added in Phase 2 of `PRE_LLM_REDACTION_SCOPE.md`, plus 2 cluster-targeting added in PR #87 (`multiturn_substance_disclosure_then_food_no_carryover` for the cross-turn substance-use addendum gate, `multi_cross_borough_three_services_queue_depth` for the queue-depth transparency fix). Largest categories: `multi_intent` (35), `natural_language` (28), `happy_path` (19), `edge_case` (17), `crisis` (14), `privacy` (10). Smallest: `staten_island` (2), `schedule` (2), `referral` (1).

**Judge.** Claude Opus 4.6 (`claude-opus-4-6`), temperature 0, single call per conversation, returns 11-dimension JSON with critical_failures list. Pointwise scoring only — no pairwise, no multi-sample, no self-consistency check.

**Simulator.** Claude Sonnet (`claude-sonnet-4-20250514`), temperature 0, fires only when the bot's response can't be matched to a scripted turn or quick-reply button. The current code emits a warning every time the LLM fallback fires, which is the correct instinct — non-determinism logged.

**Chatbot models** (`backend/app/llm/claude_client.py`): conversational/slot/classification on `claude-haiku-4-5-20251001`, crisis detection on `claude-sonnet-4-6`. **None of these versions are recorded in the eval report.** Only the judge model is.

**Report shape.** `generate_report()` returns timestamp, summary, critical_failures, per-scenario detail, and (post-PR) `redact_before_llm` flag state. The summary includes `judge_model`, `semantic_router_available`, and `baseline: "R38"` (default — `R28` is also selectable via `--baseline R28`). Both baselines are hardcoded constants in `eval_llm_judge.py::BASELINES`. **No history file. No raw report archive in the repo.** Run-over-run comparison is done by reading `EVAL_RESULTS.md` — a hand-written narrative — and eyeballing.

**Comparison infrastructure.** One script: `scripts/compare_eval_reports.py` (709 lines). Built for the unified-extractor migration's Phase 2. Hardcoded to `legacy` vs `unified` semantics, the four-scenario "set-equality watch list," and `multi_cross_borough` as a special-case acceptance check. **Not reusable as-is for the redaction Phase 2 comparison** — would need either generalization or a parallel script.

**Unit test layer.** 76 unit tests in `tests/unit/`, including the v1-D-workstream files. PWA/offline has unit tests (`test_idempotency.py`, `test_browser_geolocation.py`) but **zero eval scenarios**. R29 emotional categories (`distrust`, `undeserving`, `anger_at_situation`) have responses defined in `responses.py` but **zero eval scenarios** — gap is now ~10 runs old.

**Pytest config** (`pyproject.toml`). `xfail_strict = true`, custom markers registered (`slow`, `requires_llm`, `requires_db`, `live`), `pythonpath = ["backend"]`. The audit-driven hygiene the v1 doc proposed actually shipped here.

**Audit baseline.** `check_audit_baseline.py` runs in CI before tests. Audit findings are gated.

The high-level takeaway from this inventory: **the unit-test and CI layer is healthy, the eval framework itself is intact, and what's missing is the layer between them — the cross-run analysis, calibration, and process layer.** That's the gap v2 fills.

---

## Foundation 1 — Cross-run history (the prerequisite for everything else)

Without this, every other piece of the plan is impossible. Most of v1's incoherence about variance, regression, and hygiene traces back to "we don't know what each scenario's actual distribution looks like."

**Concretely:** archive every eval report JSON under `eval_results/` and add a `scripts/eval_history.py` that aggregates per-scenario time series from those archives.

**Specifically:**

1. **Run archival.** Every `--output` invocation that produces a report also writes a timestamped, immutable copy to `eval_results/runs/<YYYY-MM-DD>_<run-tag>.json`. Run tag is either `R<n>` if the user passes one, or the git short-SHA. (Right now there is no archive at all — the user passes `--output some.json` and that's the only copy.)

2. **History index.** A JSON file `eval_results/history.json` keyed by `scenario_id`, with each entry holding the full time series of dimension scores across runs. Built and updated by `scripts/eval_history.py rebuild`.

3. **Per-scenario empirical variance.** From the time series, compute mean and standard deviation per scenario per dimension across the last N runs (default N=10). This is what v1's Workstream C *intended* to do but couldn't because no data was stored.

4. **Variance-aware classification.** A scenario's drop from mean μ to score s on a single run is "noise" if `(μ - s) < 1.5 × σ`, "regression" if `> 1.5 × σ`. The `1.5σ` threshold is loose — chosen to be lenient on the noise call so genuine signal isn't suppressed. (Tightening to 1σ later is reasonable once we have more data.)

5. **Replaces the hardcoded `BASELINES` dict in eval_llm_judge.py.** `R28_BASELINE` and `R38_BASELINE` are hardcoded snapshots from specific runs, used for delta computations. They should come from history.json, not be frozen as constants. The R28 → R38 anchor shift was done manually in May 2026 — adding a new constant alongside the old one rather than retiring the old one. With history.json, "the baseline" becomes a CLI flag pointing to a named row in the time series rather than a hardcoded dict, and the next anchor shift is a one-line update.

**Cost:** ~1 engineering day for the archival + history.json + variance computation. ~1 day for backfill (parsing `EVAL_RESULTS.md` to seed the time series for R28-R38, since we have no JSONs for those). Backfill will be lossy — `EVAL_RESULTS.md` has summary numbers, not per-scenario per-dimension scores. We get summary-level history immediately and per-scenario history only from new runs forward.

**Prerequisite for:** Foundations 3 (triangulation requires baseline distributions), 4 (model-drift detection requires history), and most of v1's unimplemented Workstream B (automated retire detection requires "this scenario has scored ≥4.8 for 3+ consecutive runs" — a query that the current architecture cannot answer).

---

## Foundation 2 — Human calibration of the Opus judge

The single highest-leverage item in v1. v1 marked it P3 ("research-gated"). It is, in fact, the linchpin: every other intervention in v1 and v2 reduces noise *relative to the judge*. None of them ask whether the judge is actually measuring what we think.

**The research is unambiguous** that this matters. G-Eval research (cited in 2026 reviews) shows CoT prompting raises Pearson correlation with humans from 0.51 to 0.66 — a meaningful gap that's only measurable if you have human labels. Recent 2025-2026 work ("Rating Roulette," "Judging the Judges," "When Judgment Becomes Noise") finds that **even strong models with carefully designed rubrics can have unexplained variance exceeding 90%** in their judgments — meaning the rubric's weight in the actual decision is much smaller than the rubric's authors believe. The only way to know whether YourPeer's Opus judge is one of these or not is to compare to humans.

**The methodology** is bounded:

1. **Sample size: 30-50 scenarios.** Per recent industry guidance ("Label Your Data" 2026: 30-50 minimum, 100-200 production-ready). Stratify to cover the full score range — including the 32 scenarios scoring ≥4.8 every run (ensure judge agrees they're "easy"), the 8 critical-failure scenarios from R38 (ensure judge agrees they're failing), and the borderline 30-40 scoring 3.8-4.2 (where humans most often disagree with each other and where judge calibration matters most).

2. **Annotators: 2-3 people, with population context.** Streetlives team members familiar with the target population, ideally including someone with lived experience. Inter-annotator agreement (Cohen's κ or Krippendorff's α) is itself a signal — if humans disagree among themselves, asking whether the judge agrees is incoherent.

3. **What gets measured.** Per-dimension Spearman correlation between human and Opus scores, plus dimension-level agreement (Scott's π, Cohen's κ binarized at the 4.0 threshold). Goal: identify which dimensions Opus measures well (high human correlation) vs. poorly (low correlation, even if the dimension reads sensible on the rubric page).

4. **What we expect to find.** The two strict dimensions (Tone 3.94, Dignity 3.94 in R38) are the most likely to be miscalibrated. The R28 rubric calibration explicitly chose to keep them strict on grounded reasoning ("Buber's I-It framework," PMC research on transactional encounters) — but that's a normative choice, not an empirical finding. We should know whether Opus's strict 3 on a "functional but flat" response actually correlates with how the target population perceives that response. If it does, the rubric is doing what it should. If it doesn't, every Tone/Dignity-driven product decision since R28 has been noise.

**Cost:** 1 week of Streetlives team time (40 person-scenarios: 30 scenarios × ~10 minutes per dimension scored). One week of engineering time to build the annotation interface (could be a simple gradio or notebook UI; nothing fancy) and the correlation analysis. **This is bounded work, not a research project.**

**Prerequisite for:** evaluating whether *any* of the eval's scoring claims are trustworthy. Until this lands, every "regression," every "improvement," every "STOP gate" floor in `PRE_LLM_REDACTION_SCOPE.md` is anchored to a judge whose alignment is asserted but unmeasured.

The reason v1 marked this P3 was probably because it requires non-engineering time. That's also the reason it's the most important thing in this plan.

---

## Foundation 3 — Triangulate the judge

Single-judge architectures have known failure modes. The 2025-2026 research is consistent on this: pointwise scoring is **less stable** than pairwise comparison; single judges have **self-recognition bias** (favoring outputs from their own model family); judges can have **>90% unexplained variance** even on well-designed rubrics; and `temperature=0` can actually **hurt human alignment** ("Rating Roulette," 2025) because it locks the judge into a single sample of its own distribution.

YourPeer's eval is single-judge, pointwise, temperature=0 — every architecturally fragile choice the literature flags. We made these choices for reproducibility, and reproducibility *is* a real value. But reproducibility of an unaligned signal is reproducibility of the wrong thing.

Triangulation strategies to consider, **applied selectively, not to the whole suite**:

### 3a. Multi-sample on borderline scenarios

Scenarios scoring 3.8-4.2 (within 0.2 of the threshold) get judged 3 times per run with `temperature=0.3` rather than once at temperature=0, and the median is recorded. The variance from this sampling (vs. variance across runs from Foundation 1's history) tells us how much of "this scenario keeps swinging" is judge non-determinism vs. real signal. ~15-25 scenarios eligible per run; 3x sampling adds ~$3-5 per eval. Cost is tolerable; statistical signal is huge.

The temperature non-zero is the counterintuitive part. v1 didn't propose this; the 2025 research is what changed. The intuition: temperature=0 returns the *modal* output, which has whatever idiosyncratic biases the model has. Multi-sampling at moderate temperature averages over those biases, producing a more human-correlated mean.

### 3b. Pairwise spot-check for subjective dimensions

For Tone and Dignity specifically — the two dimensions where pointwise scoring is least defensible because subjective absolute scoring requires a "stable internal reference" the judge may not have — add a periodic pairwise check.

Mechanics: take the previous run's response on the same scenario, ask Opus "which response handles this conversation with more dignity, A or B?" Run on ~20 scenarios per run. Cost is small. Result is a sanity signal: did Tone go up because the bot got warmer, or because the judge felt different that day? Pairwise comparison answers the first; pointwise alone can't distinguish.

This is where v1's hardcoded `compare_eval_reports.py` infrastructure could grow legs — it already compares two reports; extending it to surface pairwise judge questions is a small step beyond the current cross-tabulation.

### 3c. Self-consistency check (cheap sanity test)

Once per eval run, pick one scenario at random, run the judge twice, and emit a warning if the two scores disagree by more than 1 point on any dimension. **This is the v1 plan's "non_deterministic_scenarios" field reborn correctly** — a real measurement of judge variance, not a count of which scenarios used the LLM simulator. Cost: one extra judge call per run (~$0.10).

### 3d. Frozen golden set (framework regression test)

Five-to-ten "golden" conversations with expected dimension scores. These are not scored by the live judge in normal eval runs — they're scored only when the judge prompt, rubric, or model version changes. If a rubric edit moves the golden set's scores by more than a small tolerance, the change is potentially recalibrating, not refining. The team should know that before the change ships.

This is the missing safety net for prompt iteration. The R28 baseline shift documents what happens without it: a judge model swap (Sonnet → Opus) plus three new dimensions plus weighted scoring landed simultaneously, and post-hoc nobody could disentangle judge strictness from the new dimensions from the weight changes. A golden set lets you change one thing at a time.

**Cost across 3a-3d:** ~1 engineering week. Recurring cost increase ~$5-10 per full eval run (multi-sample on borderline + self-consistency).

---

## Foundation 4 — Pin and record all model versions

Anthropic does not publish public changelogs for incremental model improvements. The 2026 research literature treats this as a known production-ML problem: "Service LLMs evolve without public changelogs, complicating reproducible evaluation" (Wiese 2026, longitudinal study tracking model drift). The R28 baseline shift is the YourPeer instance of this — and we lucked into that one being intentional. The next one might not be.

**Right now**, the eval report records `judge_model` (Opus 4.6). It does not record:

- The chatbot's conversational/slot/classification model (Haiku 4.5)
- The chatbot's crisis-detection model (Sonnet 4.6)
- The simulator model (Sonnet 4-20250514)
- The semantic-router sentence-transformer version (`all-MiniLM-L6-v2`)
- The PII redactor's regex pattern hash (proxy for "did the redaction logic change between runs?")

If Anthropic ships an updated Haiku, R39 numbers will move and we'll have no way to attribute the move correctly. If we change `claude_client.py` to use a different Haiku version, same problem.

**Concretely:**

1. **Record every model version in the report.** Read `claude_client.py` constants at report-generation time, snapshot them into `summary["model_versions"]`. Same for the simulator and judge.

2. **Pin them in the deploy environment.** Pinning means: the model strings live in `claude_client.py` and the eval runner; they're not read from a flag or environment. If a model needs to change, it changes via a PR that explicitly re-baselines the eval. This is the **discipline** part — the technology is trivial.

3. **Drift detector.** A `scripts/check_model_drift.py` that compares model versions in the most recent run against the previous run and warns if any have moved without a corresponding entry in `EVAL_RESULTS.md`. Optional — useful if model versions are sometimes updated by tooling rather than humans.

**Cost:** Half a day. The benefit is preventing the "we have no idea why the eval moved" failure mode.

---

## Foundation 5 — Bridge eval to production

The eval scenario set is hand-authored, drawn from Cornell sample queries, lived-experience peer queries, design docs, and bug regressions. These sources are good but partial. Real users say things scenario authors don't think to write. Without a path from production transcripts back into the scenario suite, we slowly diverge from reality.

**Concretely:**

1. **Sampled production transcripts.** Once per week or per N production conversations, sample a small set (e.g., 10) and run them through the *judge alone* (not the simulator, since the conversation actually happened). This produces per-dimension scores on real user interactions. Compare to scenario distribution: does the production score distribution look like the scenario score distribution? If production tone runs significantly worse than scenario tone, our scenarios are missing something.

2. **Anonymization is required.** This requires production transcripts to be PII-redacted and stored at all (decision pending in `Streetlives Feedback Pilot Engineering Design Document.docx`). The PII-redaction prerequisite specifically gates on **Phase 3** of `PRE_LLM_REDACTION_SCOPE.md` (the production env-var flip that turns `REDACT_BEFORE_LLM=true` on for live traffic), not Phase 1 (which only landed plumbing behind a flag-defaults-OFF). Phase 3 is currently blocked on full-R41 confirmation; once it ships, the post-flip transcripts route through the redactor before reaching Anthropic's API. **This Foundation is gated on a product/policy decision, not an engineering one.**

3. **Failure-mode harvesting.** When production conversations get a low judge score, surface them as candidate new scenarios. The "production-to-eval pipeline" is what every modern LLM observability platform now ships (Confident AI, Arize, etc.). It doesn't have to be sophisticated — even "judge low-scored production conversations and have a human decide if they should become scenarios" is a giant step over the current "scenario authors imagine what users say."

**Cost:** Engineering: ~1 week to build a sampled-production pipeline. Policy/product: dependent on the Streetlives team's data retention decisions, which are probably more conservative than any pipeline I'd design. **This may be the slowest-to-land Foundation, but it's the one that makes the eval suite continuously informed by reality rather than slowly drifting from it.**

---

## Foundation 6 — Automate scenario lifecycle (replacing v1's E.1 quarterly review)

v1's Workstream E proposed a quarterly scenario health review. That's the kind of process suggestion that doesn't survive engineer turnover. The fix is to move it from "calendared meeting" to "automated post-run health report."

**Concretely:** every full eval run, in addition to the existing report, emit a `eval_results/health/<run-tag>.md` with three sections:

1. **Retire candidates.** Scenarios scoring ≥4.8 average for 3+ consecutive runs across all dimensions. (Pulls from Foundation 1's history.)
2. **Investigate candidates.** Scenarios scoring <4.0 for 3+ consecutive runs.
3. **Calibrating scenarios.** New scenarios with fewer than 3 runs of history — flagged "calibrating" and excluded from regression detection. (This addresses a v1 gap directly: v1 had no concept of "this scenario is too new to have a baseline.")
4. **Stale scenarios.** Scenarios with no run-time changes in 6+ months that *also* haven't moved more than σ=0.1 across all runs in that window — strong candidates for retirement, since they're providing neither variance signal nor coverage of recently-changed code.

The format is markdown so it can be checked into the repo as a paper trail without being process-heavy. PRs that change the eval suite are expected to reference the most recent health report and explain departures.

**Cost:** ~2 days, mostly building on Foundation 1's history infrastructure. No new dependencies.

---

## Foundation 7 — Measure real-world outcomes, not just conversation quality

This Foundation was added in rev. 2 after reading Nava PBC's case study, *Evaluating an AI assistive chatbot for caseworkers* (NavaPBC, 2025). The Nava team — working with Cornell and Georgetown academic partners — measured things our framework doesn't even attempt: accuracy against ground truth (40% improvement in caseworker answer accuracy), Net Promoter Score from real users (NPS = 11), automated reading-level analysis (10th-12th grade vs. an 8th-grade target), follow-through and adoption patterns across organizational contexts, and qualitative interviews using the Implementation Science framework. Their methodology is structured around six dimensions they call the "6 A's": **Accuracy, Acceptability, Appropriateness, Administrative burden, Accessibility, Implementation insights.**

Three of those dimensions live entirely outside the conversation transcript and therefore outside what an LLM judge can measure:

- **Accessibility (reading level)** is computational and deterministic — a Flesch-Kincaid score on every bot response.
- **Acceptability (NPS, satisfaction)** is reported by real users via a feedback channel.
- **Implementation insights (when it works, when it doesn't)** comes from observing patterns across organizational contexts, not from individual conversations.

Foundations 1-6 sharpen our LLM-judge instrument. Foundation 7 adds **three orthogonal instruments** that measure things the LLM judge fundamentally cannot.

### 7a. Reading-level analysis (the immediate concrete win)

Reading level is the cheapest concrete add in this entire plan: ~50 lines of code, zero recurring cost, zero LLM dependency, deterministic results across runs.

**Concretely:**

1. Compute Flesch-Kincaid Grade Level on every bot response in every eval scenario, using the `textstat` library (or equivalent — algorithm is well-defined enough that any implementation matches).
2. Add a per-scenario `reading_level_grade` field to the report, plus an aggregate `mean_reading_level` to the summary.
3. Set a soft target (8th grade — the Plain Language guideline for government communications, the level Nava measured against, and a reasonable proxy for the population YourPeer serves) and surface scenarios with bot responses above 10th grade in the post-run health report (Foundation 6).
4. Add the same to the per-conversation production sampling pipeline (Foundation 5). If production reading level exceeds eval reading level by more than σ = 1 grade, our scenarios are missing something about how the bot actually phrases responses to real users.

**Why this is high-value despite being cheap:** the LLM judge is unable to measure this. Asking Opus "is this written at 8th grade level?" returns a calibration-free guess. Asking `textstat` returns a number. Cultural Responsiveness (currently ~3.96 — the third-lowest dimension) is the closest LLM-judge proxy for accessibility, but it's a subjective dimension scored by Opus against a rubric that doesn't include reading level. A bot that scores 4 on Cultural Responsiveness can simultaneously read at college level — those don't contradict.

**Targets for the population:** the Nava case study's 8th-grade benchmark is appropriate for caseworkers; for YourPeer's population (people experiencing homelessness, often with limited literacy, often non-native English speakers, sometimes in crisis) a stricter target — 6th grade — is defensible. Worth deciding with the Streetlives team rather than imposing.

**Cost:** Half a day. The recurring cost is zero — `textstat` runs in microseconds per response.

### 7b. Production satisfaction signal (the medium-term path)

YourPeer's `Streetlives_Feedback_Pilot_Engineering_Design_Document.docx` already specifies a feedback feature: thumbs up/down per conversation, optional text reasons, surfaced to providers. **The pilot's eval workstream and the pilot's feedback feature are currently disconnected.** They should not be.

**Concretely:**

1. When the feedback feature ships, every feedback signal carries a `conversation_id` (anonymized).
2. The eval runner gains a "feedback view" — when reviewing eval scenarios, it shows whether *similar* production conversations got positive or negative feedback.
3. Aggregate satisfaction (`% positive`) is included in the post-run health report (Foundation 6) and in the per-scenario records (Foundation 1's history). The eval suite gradually develops a feedback-prediction signal: "scenarios that score well by Opus judging vs. scenarios that get positive user feedback in production." Where these diverge, the eval is measuring the wrong thing.

**The Nava case study reported NPS = 11 from caseworkers**, which is modest but positive in a population where these tools were new and the use case is nuanced. The equivalent metric in YourPeer's population — people in crisis, often distrustful of institutions — would likely skew differently and shouldn't be benchmarked against Nava's number. The point isn't the absolute score; it's having a number at all, and having it move when changes ship.

**Cost:** Engineering work is bounded (~3-5 days once feedback exists in production). The blocker is the feedback feature shipping. **This Foundation accelerates if the feedback feature is prioritized for the pilot.**

### 7c. The Cornell-partnership conversation (the slow burn)

The Nava case study's academic partners are listed: Allison Koenecke (Cornell, algorithmic fairness) and Jennah Gosciak (Cornell, computational social science). YourPeer's project files mention Cornell directly via `Cornell_AI_chatbot_Copy_of_Sample_queries.docx`. **These are likely the same researchers, or at minimum the same Cornell research community.**

If Cornell is already a Streetlives partner, the methodology bar Nava demonstrated — Implementation Science framework, mixed-methods qualitative analysis, RCT-grade outcome measurement — is reachable without a major new investment. It's a scoping conversation about an existing relationship, not a hire.

**Concretely:**

1. **Audit the existing Cornell relationship.** What's it scoped to do today? Is rigorous outcome evaluation in scope, or only sample-query authoring?
2. **Identify what would be required to extend it.** A pilot study like Nava's (61 caseworkers, 14 weeks, mixed methods) is bounded work. A smaller version — 20 outreach workers, 4 weeks, focused on reading-level effectiveness and follow-through — would be a defensible first step.
3. **Decide who owns the question.** This isn't an engineering decision; it's a research/program decision for the Streetlives team. The plan's role is to identify the gap and the path; the team's role is to walk the path.

**There's an asymmetry worth naming.** Nava's chatbot serves caseworkers — intermediaries who catch errors before they reach vulnerable people. YourPeer talks directly to the person in crisis. That makes Nava's "I don't know" failure mode a soft fall (the caseworker checks elsewhere) and YourPeer's equivalent a hard one (the person doesn't get help). The stakes of an outcome-measurement gap are higher for YourPeer than for Nava. This is a reason to invest *more* in outcome measurement than Nava did, not less.

**Cost:** Streetlives team time (initial scoping conversation, ~2 hours). Subsequent cost depends entirely on what the partnership scoping reveals. Could be free; could be a multi-month research project. The first conversation is the unblocking step.

### How Foundation 7 relates to Foundations 1-6

Foundations 1-6 measure conversation quality with progressively better instruments — history, calibration, triangulation, model-version pinning, production sampling, automated lifecycle. Foundation 7 measures whether the conversations matter. Both layers are necessary; neither is sufficient.

A bot that scores 4.9/5 by Opus, has σ = 0.1 cross-run variance, has 0.85 human-judge correlation on every dimension, and has every model version pinned — and that nobody follows through with — is a high-quality bot that fails the mission. Foundation 7 is the layer that catches that failure.

---

## Coverage gaps that persist (and how to close them durably)

These are real gaps where v1's prescription was "go author scenarios" and the prescription failed. Each remains open. The plan here is **not** to write a list of new scenarios — it's to make scenario authoring a structural requirement of feature shipping, which is what would actually have prevented these gaps in the first place.

### The persistent zero-coverage gaps

| Area | Code state | Eval state | Age |
|---|---|---|---|
| R29 emotional categories (distrust, undeserving, anger_at_situation) | Live in `responses.py` | 0 scenarios | ~10 runs / 5+ months |
| PWA / offline / idempotency | Unit tests in `tests/unit/` | 0 eval scenarios | Multiple sprints |
| Phase 1 redaction (server-side) | Shipped in flag-OFF default | 0 scenarios pre-Phase 2 (now 7) | Just closed |
| Spanish bilingual handling | Active in code, R32 fix landed | 1 scenario (`wa_non_english_speaker`) | Stable but fragile |
| Misspelling tolerance | Design doc exists | Unclear; needs audit | TBD |
| Admin / data-validation flows | Has unit + integration tests | 0 eval scenarios | Always |

**The structural fix: a "ship-with-eval-coverage" gate on design docs and feature PRs.** Specifically:

- Every design doc in `docs/design/` carries an "Eval coverage" section listing the scenarios that will exercise this behavior.
- The reviewer for any PR that adds or modifies LLM-touching behavior is responsible for confirming the eval coverage section was implemented before merge.
- If the answer is "this feature isn't user-facing enough to need eval coverage," that's a defensible answer — but it's stated, not silent.

This is process discipline. It's the part of v1 that didn't land because it wasn't enforceable. The way to make it enforceable is to put it in the PR template and design-doc template, not in a quarterly meeting.

### The currently-acute gap

The R29 emotional categories are the most embarrassing item in this list and the cheapest to fix. Three scenarios, ~30 minutes of authoring time, plus paired classifier unit tests in `tests/unit/test_emotional_responses.py` (which doesn't exist). **This should land in the same week as Foundation 1**, regardless of where the larger plan goes — it's a five-month-old gap on safety-adjacent code (population-specific emotional responses for a vulnerable group).

---

## What the v1 plan tried to patch, and what we should do instead

| v1 prescription | Why it didn't work | v2 replacement |
|---|---|---|
| **A.1-A.4** (point-fix six bugs) | Fixes were architectural, not point | Don't list bug fixes in the eval plan; the eval surfaces them, the engineering team owns them |
| **B.1** (manually retire 12 scenarios) | Manual hygiene didn't survive scheduling | Automated retire-candidate detection (Foundation 6) |
| **B.2** (update 4 miscalibrated scenarios) | Identified the right scenarios but no mechanism to find the next 4 | "Investigate" flag in the health report (Foundation 6); automated, continuous |
| **B.3** (manually author 18 coverage-gap scenarios) | List didn't get authored; new gaps appeared since | Process gate on design-doc and PR templates (above) |
| **C.1** (multi-run averaging on borderline) | Right idea, no infrastructure | Foundation 3a, post-Foundation-1 |
| **C.2** (variance tracking) | Half-implemented as a proxy | Foundation 1 + 3c — real measurement |
| **C.3** (human calibration) | Marked P3, never started | **Foundation 2 — promoted to a foundational priority** |
| **D workstream** (fast unit tests) | Mostly worked | Continue; not part of v2's scope |
| **E.1** (quarterly review) | Process suggestion; didn't survive | Foundation 6 — automated health report |
| **E.2-E.5** (lifecycle metadata, fragility tags, CI gates, KNOWN_LIMITATIONS) | None landed | Per-item: E.2 superseded by Foundation 6's history. E.3 superseded by Foundation 1's empirical variance. E.4 partially landed (CI gates exist for unit tests). E.5 still useful but low-priority. |
| *(v1 had no analog — entirely missed)* | v1 measured conversation quality only; outcome measurement wasn't on its radar | **Foundation 7 — outcome measurement: reading level, satisfaction, follow-through, partnership-driven research** |

---

## Sequencing

Unlike v1's six-week calendar (which was aspirational), this plan sequences by **dependency**, not by week. The team works through the foundations in order; the calendar is whatever calendar the team has.

### Phase A — History infrastructure (1 week, blocking everything else)

- Foundation 1: archive runs, build history.json, compute empirical variance, replace the hardcoded `R28_BASELINE` and `R38_BASELINE` constants with history-derived references.
- Backfill what's possible from `EVAL_RESULTS.md` (summary-level only).
- Add R29 emotional category scenarios (the acute coverage gap) — small, parallel, doesn't depend on anything.
- **Foundation 7a (reading-level analysis)** — slot in here. ~50 LOC, deterministic, no dependencies, parallelizable with the history work. Surfaces a metric Opus cannot measure starting from the very next eval run. This is the cheapest concrete win in the plan.

### Phase B — Calibration (1-2 weeks, dependent on Streetlives team availability)

- Foundation 2: human calibration of 30-50 scenarios.
- This is the highest-leverage workstream. It can run in parallel with later phases once started, but it should *start* as early as possible because it's gated on people, not engineering time.

### Phase C — Triangulation (2-3 weeks, depends on history existing)

- Foundation 3a: multi-sample on borderline scenarios.
- Foundation 3b: pairwise spot-checks for Tone/Dignity.
- Foundation 3c: self-consistency check.
- Foundation 3d: frozen golden set.
- Use calibration findings (Phase B) to prioritize which dimensions need triangulation most.

### Phase D — Drift and discipline (1 week)

- Foundation 4: model-version recording and pinning.
- Foundation 6: automated health report (depends on Phase A).
- Process gate on design-doc and PR templates for eval coverage.

### Phase E — Production bridge (gated on policy)

- Foundation 5: production transcript sampling.
- **Foundation 7b (production satisfaction signal)** — pairs with this phase. Both are gated on the feedback feature shipping; both consume the same anonymized conversation_id pipeline. Building them together avoids reimplementing the sampling logic twice.
- Engineering work is bounded but the policy gate is real.

### Phase F — Outcome research partnership (gated on Streetlives team / Cornell)

- **Foundation 7c (Cornell-partnership conversation)** — the slowest-burning item in the plan. The unblocking step is a 2-hour scoping conversation. Subsequent timeline depends entirely on what that conversation reveals. Worth starting in parallel with Phase A so it has time to mature; not blocking any other phase.

### Always-on

- Coverage gap closure as features ship (the process gate).
- Continued maintenance of the v1-D-workstream unit tests.

---

## Risks and prerequisites

**Foundation 2 needs Streetlives team time.** The single biggest risk to this plan is that human calibration is the linchpin and human calibration requires non-engineering hours. If the Streetlives team can't allocate ~1 person-week, the rest of the plan still helps but its trustworthiness ceiling is fixed.

**Foundation 5 needs a policy decision.** Production transcript sampling is gated on the Phase 1 redaction work being deployed (which is in flight) and on the Streetlives team's data retention posture. If the policy decision goes "no production transcripts ever stored," Foundation 5 is unimplementable — and the eval suite is structurally limited to imagined scenarios. That's a real limitation worth naming.

**Multi-judge / multi-sample increases cost.** Foundations 3a and 3c add ~$5-10 per full eval run. The full plan's recurring cost: $20-30 per eval (vs. current $15-25). Tolerable but worth budgeting.

**Calibration findings could invalidate prior conclusions.** If Foundation 2 finds that Opus's Tone scoring has weak human correlation, every Tone-driven product decision since R28 needs re-examination. This is uncomfortable but it's the point of doing the calibration. The alternative is staying confidently miscalibrated.

**Backfill is lossy.** Foundation 1 can backfill summary-level history from `EVAL_RESULTS.md` but per-scenario per-dimension history starts from this PR forward. Variance estimates will be weak for the first 3-5 runs after this lands, then stabilize. The plan's variance-aware classifications (1.5σ for noise vs. signal) are weak signals during this period — explicitly so.

**Foundation 7 has a long tail.** 7a (reading-level) lands in days. 7b (production satisfaction) is gated on the feedback feature. 7c (Cornell partnership) could be free or could become a multi-month research engagement. The plan's most consequential outcome — knowing whether the bot actually helps people — is also its slowest. Don't let the long tail of 7c block the immediate value of 7a; they're different timescales of the same Foundation, and 7a should ship in Phase A regardless of where 7c lands.

---

## What "trust the eval" means after this plan

v1 framed this end state as "a green eval means the bot works; a red eval means something specific is wrong." That framing was right but underspecified. v2's version:

1. **Every score has a measured uncertainty.** We can say "this scenario averages 4.27 with σ=0.18 across 8 runs" rather than "it scored 4.18 last time and 4.45 the time before, who knows."
2. **Every dimension has a measured human correlation.** When the report says Response Tone went from 3.94 to 3.86, we know whether that's a 0.08 drop in something Opus measures with ρ=0.7 vs. ρ=0.3 — which determines whether to act.
3. **Every model version is recorded.** When R39 lands and Tone has moved, the first thing we check is "did any model version change?" and we have a definitive answer.
4. **Every framework change has a regression test.** When a rubric is edited, we know whether the change is a refinement (golden set holds) or a recalibration (golden set moves) — and we know it before the change ships, not after.
5. **Coverage is process-driven, not list-driven.** Features ship with eval coverage because the PR template requires it, not because someone remembered to write the scenarios.
6. **The eval feeds from production.** Real user transcripts shape the scenario set, so the suite is a living model of real interactions, not a frozen snapshot of what scenario authors imagined.
7. **Outcomes are measured, not assumed.** Reading level is a number, not an LLM-judge guess. Production satisfaction is a measured signal, not a hypothesis. We know whether the bot helps people, not just whether the bot scores well by Opus.

These are the conditions under which the next "regression alarm" can be acted on with confidence rather than investigated with anxiety. They're also the conditions under which Phase 3 of `PRE_LLM_REDACTION_SCOPE.md` (the production env-var flip) carries less risk: the comparison floors in that doc were chosen against the R38 baseline, but their statistical interpretation depends on knowing how much R38 itself varies — which is exactly what Foundation 1 measures.

---

## Appendix A: Concrete first commits

If the team adopts this plan, here's what the first PR looks like:

1. `eval_results/runs/` directory created. `.gitkeep` plus `README.md` explaining the archive format. (Note: this PR's durability work has already created this directory structure — the eval-quality-plan first PR should build on it, not recreate it.)
2. `tests/eval/eval_llm_judge.py` modified: when `--output PATH` is passed, also write a copy to `eval_results/runs/<timestamp>_<run-tag>.json`. Run-tag from `--run-tag` flag (new) or `git rev-parse --short HEAD`. (Partially shipped in the durability PR — what's left is the `--run-tag` flag and the git-SHA fallback.)
3. `scripts/eval_history.py` created: builds and updates `eval_results/history.json` from archived runs. Computes per-scenario per-dimension mean and σ over the last N runs.
4. **Foundation 7a in this PR**: `compute_reading_level()` helper added to the runner. Per-scenario `reading_level_grade` field in the report. Aggregate `mean_reading_level` in the summary. ~50 LOC, deterministic, no API cost.
5. Three new scenarios in `tests/eval/eval_llm_judge.py` for `emotional_distrust`, `emotional_undeserving`, `emotional_anger_at_situation`. Closes the most acute coverage gap.
6. Pytest test for `scripts/eval_history.py` so the history infrastructure itself doesn't silently regress. Pytest test for `compute_reading_level()` — Flesch-Kincaid is well-defined enough that a few hand-computed expected values can pin the implementation.
7. Design-doc template (`docs/design/_TEMPLATE.md`) and PR template (`.github/pull_request_template.md`) updated with "Eval coverage" sections.

That's a self-contained ~3 days of work that unblocks every later Foundation and ships the cheapest concrete outcome-measurement win in the same PR.

---

## Appendix B: Research backing for the methodology choices

For the Streetlives team and reviewers, the key research points underlying the methodology choices:

- **Pointwise scoring is less stable than pairwise** for subjective dimensions (Wolfe 2024, multiple 2025 papers). Justifies Foundation 3b's pairwise spot-checks for Tone/Dignity specifically.
- **Pairwise is more vulnerable to distractor features** (Tripathi et al., 2025: 35% pairwise flip vs. 9% pointwise on the same content). Justifies *not* replacing pointwise with pairwise wholesale — triangulation, not substitution.
- **`temperature=0` can hurt human alignment** ("Rating Roulette," 2025) because it returns the modal output. Justifies Foundation 3a's moderate temperature (0.3) for multi-sampling on borderline scenarios.
- **Judges have >90% unexplained variance even on well-designed rubrics** ("When Judgment Becomes Noise," 2025). Justifies Foundation 2's calibration as not optional.
- **Service LLMs evolve without public changelogs** (Wiese 2026 longitudinal study identified divergent stability trajectories across model families). Justifies Foundation 4's model-version recording.
- **30-50 examples is the minimum viable for human calibration; 100-200 is production-ready** (Label Your Data 2026 industry guidance). Justifies Foundation 2's bounded scope.
- **G-Eval CoT prompting raised Pearson ρ from 0.51 to 0.66** on summarization. Justifies that judge prompt engineering still has headroom — but only worth pursuing once calibration is in place to measure improvement.

For Foundation 7 specifically:

- **Nava PBC, "Evaluating an AI assistive chatbot for caseworkers" (2025).** A 14-week pilot with 61 caseworkers across 18 organizations, plus an RCT with 125 participants, plus mixed-methods qualitative analysis using the Implementation Science framework. Measured accuracy improvement (40%), NPS (11), reading level (10th-12th grade vs. 8th-grade target), adoption patterns by organizational context. The plan's Foundation 7 directly mirrors three of Nava's measurement axes. Cornell collaborators: Allison Koenecke, Jennah Gosciak.
- **Plain Language Action and Information Network (PLAIN) guidelines.** US federal communications target: 8th-grade reading level. Justifies Foundation 7a's default target. For populations with lower average literacy, 6th grade is more appropriate.
- **Flesch-Kincaid Grade Level (Kincaid et al., 1975).** Well-defined formula on syllables-per-word and words-per-sentence. Multiple Python implementations (`textstat`, `py-readability-metrics`) match each other to within rounding. Justifies the determinism claim — the same response always produces the same reading level, no LLM dependency.
- **Implementation Science framework (Damschroder et al., CFIR, 2009/2022 update).** Standard methodology for studying how interventions land in organizational contexts. Foundation 7c's reference framework if the Cornell partnership extends to outcome research — not because it's the only methodology, but because Nava used it and academic partners likely already work in it.

---

## Appendix C: Where the Nava case study comes apart, and where it doesn't

For honest framing: Nava's case study is useful as a methodology template but has limits worth naming for the Streetlives team.

**It applies cleanly:** the 6 A's structure is reusable. Reading level as an automated computational metric is reusable. Mixed-methods qualitative analysis with academic partners is reusable. Implementation Science framing is reusable.

**The user-population asymmetry matters.** Nava's chatbot serves caseworkers — professionals with stable workplaces, training, peer networks, and backup resources when the AI is wrong. YourPeer talks directly to people in crisis, often with limited literacy, often distrustful of institutions, often without follow-up capacity. **A failure that's recoverable for a Nava user is not recoverable for a YourPeer user.** This makes the stakes of outcome measurement higher, not lower — but it also makes the satisfaction signal harder to interpret. NPS = 11 from caseworkers tells a different story than NPS = 11 from people experiencing homelessness.

**The accuracy benchmark doesn't transfer directly.** Nava measured "did the caseworker give the right answer?" against a known set of policy questions. YourPeer doesn't have a clean analog — there's no single right answer to "I need a bed tonight in Soho," and the verified-source-of-truth (the Streetlives database) is itself imperfect. Foundation 7 should adapt this dimension as "did the user reach a result?" and "did they follow through?" — proxies, not the direct accuracy measure Nava had access to.

**The 14-week, 61-user pilot is a higher bar than YourPeer's pilot work has reached so far.** Replicating Nava's methodology fully would be a real research investment. Foundation 7c's recommendation (start with a scoping conversation about the existing Cornell relationship) is bounded in a way that reflects this — the goal is to scale to Nava's bar over time, not to start there.
