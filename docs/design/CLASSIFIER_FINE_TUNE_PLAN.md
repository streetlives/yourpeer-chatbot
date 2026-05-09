# Classifier fine-tune plan — owning the tone-classification foundation

**Status:** Design draft, May 2026. Targets engineering review and Streetlives staff input before commitment.

**Scope:** Fine-tune a YourPeer-specific emotion classifier on top of `sentence-transformers/all-MiniLM-L6-v2` (the chatbot's existing semantic-router backbone) to provide a maintainable, controllable foundation for tone classification beyond the regex/lexicon path.

**This document doesn't ship anything by itself.** It's the design + decision gates + cost estimate so the team can decide whether to commit to the work.

---

## Why this, and why now

Two parallel arguments.

**The foundation argument.** Regex/lexicon-based tone classification (`backend/app/services/classifier.py:228`) hits ~2% of classified turns. The R28 expansion (adding `undeserving`, `distrust`, `angry`, `alone`) closed some gaps but the substring matcher fundamentally can't generalize — every new way someone might express shame, fear, or distrust requires another lexicon entry. The chatbot is going to want richer tone signal long-term: not as a means of moving R42's specific eval ceiling (see below), but as infrastructure for things like situation-specific empathy variants, multi-emotion handling, and the eventual Pattern 1.5 categories (`lost`, `exhausted`, `hopeless`).

**The pretrained-classifier evaluation conclusion.** The May 2026 smoke test evaluated three pretrained candidates as alternatives to lexicon+chunked-MiniLM:

- **`kashyaparun/Mental-Health-Chatbot-using-RoBERTa-fine-tuned-on-GoEmotion`** — broken HuggingFace upload (declares wrong architecture, missing classifier head). Diagnosed and dropped.
- **`shhossain/all-MiniLM-L6-v2-sentiment-classifier`** — the architectural-footprint candidate. Loads against transformers v4 but breaks against transformers ≥ 5.0 (`all_tied_weights_keys` API change), uses `trust_remote_code` from a 2-year-stale upload. Patching would commit us to maintaining a compatibility shim against an unmaintained upstream. Dropped.
- **`j-hartmann/emotion-english-distilroberta-base`** — works. Properly maintained, vanilla architecture, no `trust_remote_code`. But: 329 MB, trained on Twitter-style content, not domain-aligned with service-finding context, and only addresses 4 of 48 R42 ceiling scenarios at conf ≥ 0.85.

The pretrained landscape isn't offering us a foundation — it's offering us individual models with individual maintenance risks. The architectural argument that motivated testing shhossain (own the classifier infrastructure, share the chatbot's MiniLM backbone, avoid `trust_remote_code` dependencies) is better served by training our own.

## What this is *not*

Setting expectations honestly upfront because the smoke-test results are clarifying:

- **This will not move the R42 tone+dignity ceiling materially.** The 48 scenarios scoring `response_tone=3` + `dignity_anti_stigma=3` are mostly bare service requests where there *is* no surface emotion for any classifier to detect. That ceiling is owned by response-copy refresh + Pattern 2 situational distress recognition, not by a better classifier. j-hartmann's 4-of-48 catch rate at conf ≥ 0.85 is approximately the upper bound for what *any* surface-emotion classifier can do here.
- **This will not replace the lexicon.** Lexicon is fast (microseconds), produces specific copy through the if-chain in `_pick_emotional_response`, and works well for explicit phrasings. The classifier is a backstop for cases the substring matcher misses.
- **This will not replace crisis detection.** Crisis runs first (regex pre-check + Sonnet stage 2 in `crisis_detector.py`). Tone classification is for sub-crisis distress.
- **This will not detect situational/implied distress.** "I have two kids and we need somewhere to stay tonight" is Pattern 2 territory (situational/transition-event bundles), not surface emotion. The classifier sees no surface emotion in that message.

The foundation argument is real but should not be oversold as a metric-mover. The reason to do this is controllability and maintainability over a 1–3 year horizon.

## What this *is*

A small, owned, vanilla-architecture emotion classifier:

- **Backbone:** `sentence-transformers/all-MiniLM-L6-v2` (already loaded for `semantic_router`; ~22M parameters; 80MB).
- **Head:** Standard linear → softmax classification head, single-label, 9 outputs matching our existing `_EMOTIONAL_RESPONSES` taxonomy (`shame`, `sad`, `rough_day`, `scared`, `grief`, `alone`, `undeserving`, `distrust`, `angry`).
- **Format:** Vanilla `RobertaForSequenceClassification`-style architecture. No `trust_remote_code`. No custom code on the HF Hub. Weights versioned in our infrastructure (S3 / artifact storage / checked into the eval-fixtures repo, depending on what fits).
- **Output:** Top-1 mapped category + confidence, threshold-gated, drop-in replacement for the proposed `TONE_ROUTES` semantic-router signal in Pattern 8.

The deployment footprint is essentially unchanged from today: we already load MiniLM. We're adding a classification head on the same encoder.

## Architecture

```
Input text
    │
    ▼
sentence-transformers/all-MiniLM-L6-v2 encoder
(384-dim sentence embedding, mean-pooled)
    │
    ▼
Linear(384 → 256) + ReLU + Dropout(0.2)
    │
    ▼
Linear(256 → 9) + softmax
    │
    ▼
{shame, sad, rough_day, scared, grief, alone, undeserving, distrust, angry}
+ confidence
```

Two design choices worth flagging:

- **Single-label, not multi-label.** A user can be both scared and alone, but the lexicon's existing `_pick_emotional_response` if-chain picks the most specific match anyway, so single-label matches the downstream consumer. Multi-label is a Phase 2 candidate if the labeling exercise shows the top-1 misses important second-emotion signals frequently.
- **Mean-pooled encoder, not per-token.** Same approach as `semantic_router`. Matches the "what tone does this whole utterance carry" granularity we actually want at the response-selection layer. Per-token attention would be needed for span-level emotion extraction, which we don't do.

## Dataset plan

Two-stage approach, with class-imbalance handling running through both stages.

### Stage 1 — `dair-ai/emotion` baseline

`dair-ai/emotion` is the public English emotion dataset (16,000 labeled Twitter-derived messages, 6 categories: sadness, joy, love, anger, fear, surprise). Maps to **3 of our 9 categories**:

| dair-ai label | Our category | Notes |
|---|---|---|
| sadness | sad | Direct mapping |
| anger | angry | Direct mapping |
| fear | scared | Direct mapping |
| joy | (drop) | Positive emotion outside our taxonomy |
| love | (drop) | Positive emotion outside our taxonomy |
| surprise | (drop) | Neutral/ambiguous; not a tone we route on |

Stage 1 baseline performance gate: **macro-F1 ≥ 0.65 on the dair-ai test set's 3 mapped categories**. j-hartmann reportedly hits ~0.66 macro-F1 on similar test setups; if our MiniLM-based head can't approach that, the backbone is too small for this task and we should reconsider before sinking more time into Stage 2.

### Stage 2 — YourPeer-domain augmentation

Six of our 9 categories have no `dair-ai/emotion` source: `shame`, `rough_day`, `alone`, `undeserving`, `distrust`, `grief`. These need YourPeer-domain training data.

**Target volume:** 100–200 labeled examples per category, 600–1,200 examples total. Estimates based on common practice for fine-tuning a 22M-param head on small task-specific data; below ~50/category the model can't learn the boundary.

**Source strategy** (in order of preference):

1. **Synthetic-then-staff-reviewed.** GPT-4-class model generates candidate phrasings for each category from a careful brief (with negative examples — what *isn't* this category); Streetlives staff with lived experience review and approve/reject/edit. This is the cleanest privacy posture and fastest path to volume. ~30–40 staff-hours.

2. **Existing eval-scenario opening messages.** The 184 R42 scenarios contain hand-written user inputs that are already labeled by category. ~30 of those map cleanly to our 9 tone categories (especially the `emotional_*` and `peer_*` scenarios). Free, but limited volume.

3. **Production transcripts.** Highest-quality signal but privacy-restricted and would need redaction + re-labeling. Probably not viable in this timeline; revisit for v2.

**Class imbalance handling.** dair-ai is heavily skewed toward joy/sadness; our 9-category mix will be skewed by data availability. Approach:

- Stage 1: weight the loss by inverse class frequency, or oversample minority classes. Standard.
- Stage 2: target balanced volumes per category in the augmentation set. Easier when generating synthetic data than mining real data.

**Labeling protocol.** Streetlives staff guidance — drafted from the per-category notes in `tools/tone_routes_draft.py` — will be the ground truth. Calibration step: label 10 examples together, measure agreement, iterate on the brief until 3+ raters hit ≥ 80% agreement on a small held-out set.

## Training plan

Stack: PyTorch + transformers + datasets (already in the dev environment). No external services.

- **Splits:** 80/10/10 train/val/test, stratified by category.
- **Optimizer:** AdamW, lr=2e-5 (standard for MiniLM fine-tunes).
- **Schedule:** Linear warmup over 10% of steps, then linear decay.
- **Loss:** Cross-entropy with class weights (inverse frequency).
- **Epochs:** 5–10 with early stopping on val loss.
- **Batch size:** 32 (fits comfortably in 8GB VRAM).
- **Dropout:** 0.2 on the head; encoder unfrozen.

**Compute requirements.** A single GPU. Free Colab tier (T4) is sufficient — fine-tune of a 22M-param head on ~17k examples is ~30 minutes. Local workstation with any modern GPU works. No A100 needed. No TPU. No external compute spend.

**Reproducibility.** Pinned random seeds for splits + initialization. Training command + dataset hash + git SHA recorded with each checkpoint. Standard practice for any model that ships.

## Evaluation plan

Three sequential gates. Each must pass before proceeding to the next.

### Gate 1 — Stage 1 baseline (after dair-ai training)

- **Metric:** macro-F1 on dair-ai test set, restricted to the 3 mapped categories.
- **Pass:** ≥ 0.65.
- **Stop condition:** < 0.65 → MiniLM-L6 backbone is too small for this task. Either upgrade to MiniLM-L12 (still small enough for our footprint argument) or stop the project and stay with j-hartmann + lexicon hybrid.

### Gate 2 — Stage 2 augmented (after YourPeer-domain training)

- **Metric A:** macro-F1 on a held-out 20% YourPeer-domain test set, all 9 categories.
- **Metric B:** Run through the existing `tier2_model_smoketest.py` harness on the 184 eval scenarios. Compare against j-hartmann at the conf ≥ 0.85 cutoff: should catch ≥ 80% of j-hartmann's high-confidence WIN set, with comparable false-positive rate on top-quartile non-emotional scenarios.
- **Pass:** Both A and B clear.
- **Stop condition:** Significant gap on Metric B → labeling quality issue. Re-do calibration step with Streetlives staff before more training cycles.

### Gate 3 — Pre-integration

- Smoke test's `_validate_loaded()` strict validator passes (id2label sanity, mapping-overlap, smoke probe ≥ 0.5 on a clear emotional input). Trivial for a properly-trained model; non-trivial for one that's been corrupted somewhere in the pipeline.
- Streetlives staff voice review: feed staff a sample of 50 production-style messages, compare classifier predictions to staff judgment, identify any categories that route inappropriately (esp. shame/undeserving — the categories most likely to misfire on factual queries).

## Production integration plan

Ordered set of changes when training succeeds:

1. **Version + ship the model artifact.** Checkpoint goes into the chatbot's model-loading path (alongside the existing all-MiniLM-L6-v2 sentence-transformer load). Single artifact, ~80MB; no incremental footprint over the existing MiniLM load since they share the encoder.
2. **Wire into `semantic_router.classify()`** as a third orthogonal output alongside service / population. Pattern 8's existing integration plan (see [`tools/README.md`](../../tools/README.md) "Integration TODOs") describes this; the fine-tuned classifier slots in where the proposed `TONE_ROUTES` similarity computation would have gone, with the classifier head replacing the cosine-similarity-vs-canonicals step.
3. **Update `_pick_emotional_response`** (`backend/app/services/responses.py:180`) with a third tier: lexicon hit wins (preserves backward compat) → classifier hit fires when lexicon misses → no tone copy when neither fires. Don't replace the lexicon — augment it.
4. **Per-category thresholds.** The existing `_PER_ROUTE_THRESHOLDS` pattern in `semantic_router.py` is the right shape. Default 0.65; per-category overrides tuned against eval data (`distrust` will likely need lower; `shame` may need higher to avoid over-firing on factual financial queries).
5. **Tests.** Per-category firing tests, threshold-guard tests, regression tests against the eval scenarios. The Stage 2 evaluation output gives the test cases worth pinning.

## Risks

**Class imbalance.** 6 of 9 categories rely entirely on YourPeer-only training data. If the augmentation set is uneven across categories, the model will be uneven across categories. Mitigation: balanced augmentation targets; per-category eval metrics, not just aggregate.

**Domain mismatch from dair-ai.** Twitter-style emotion expressions ("ugh today is the worst") differ from service-finding context ("I'm struggling and need a treatment program"). The Stage 2 augmentation is supposed to bridge this; the bridge may not be wide enough. Mitigation: track per-category performance differential between Stage 1 and Stage 2; if dair-ai categories (sad/scared/angry) regress on YourPeer test, that's the signal.

**Staff labeling time is the binding constraint.** Engineering work is ~1 week. Staff labeling is ~30–40 hours and depends on Streetlives staff availability. If staff time isn't allocated, project stalls in Stage 2.

**Synthetic data quality.** GPT-4-generated training examples can carry distributional artifacts (overly literary phrasing, unrealistic transitions). Mitigation: staff review every example, reject obvious tells. Recent literature (medrxiv 2026.01) on LLM-as-annotator caveats applies in inverse — we're using LLM-as-data-generator, with staff filtering on top.

**Bounded R42 impact, communicated upfront.** This is the foundation argument, not the metric argument. If stakeholders expect the fine-tune to lift `response_tone` on the 48-scenario ceiling, expectations need correcting before the work starts. The R42 ceiling is owned by response copy + Pattern 2.

**Maintenance burden going forward.** Once we own the classifier, we own re-training when our taxonomy evolves, when transformers updates the model loading API, when dair-ai changes their dataset, when staff-labeled data drifts. ~1–2 days/year of engineering for routine maintenance, more if a major version-skew breaks something. Worth budgeting explicitly.

## Cost estimate

| Item | Estimate |
|---|---|
| Engineering — Stage 1 baseline (dair-ai training + harness + gate 1) | 1–2 days |
| Engineering — Stage 2 augmentation (labeling pipeline, training, gate 2) | 2–3 days |
| Engineering — production integration | 1 day |
| Engineering — tests + R43 eval validation | 1 day |
| **Engineering subtotal** | **~1 week** |
| Streetlives staff — labeling + calibration | ~30–40 hours |
| Streetlives staff — voice review (gate 3) | ~5 hours |
| **Staff subtotal** | **~35–45 hours** |
| Compute (free Colab / local GPU) | $0 |
| External services / APIs | $0 |
| **Total external spend** | **$0** |

The binding constraint is staff time, not engineering. If staff time isn't allocated, this stalls regardless of how clean the engineering plan is.

## Decision gates

The doc commits to nothing until each gate is hit. Off-ramps at each:

- **Pre-Stage-1:** Is staff labeling time actually allocated for the next 2–4 weeks? If no, defer the project; don't start Stage 1 baseline because Stage 2 will block.
- **Post-Gate-1:** dair-ai macro-F1 ≥ 0.65? If no, stop and stay with j-hartmann + lexicon hybrid.
- **Post-Gate-2:** YourPeer test ≥ 80% of j-hartmann's high-conf catch rate? If no, return to labeling.
- **Post-Gate-3:** Staff voice review approves? If no, iterate on training before any integration commit.

At any gate, "stop and stay with j-hartmann + lexicon hybrid" is a perfectly reasonable outcome. The foundation argument is about having a maintained path forward, not about being committed to this specific path.

## Open questions for review

These need decisions before Stage 1 starts. Listed in priority order:

1. **Is the foundation argument enough to commit to this work?** R42 results showed bounded immediate impact. The case for doing this is "we'll need controllable classifier infrastructure 1–3 years out." Is that horizon real and prioritized, or is it speculative?

2. **Streetlives staff labeling availability — concrete dates.** The cleanest answer is "we have N hours allocated in [date range]." Without that, the project can't be scheduled.

3. **Single-label vs multi-label.** Default is single-label (matches downstream consumer in `_pick_emotional_response`). Multi-label is a Phase 2 candidate. Anyone strongly preferring multi-label should flag now.

4. **Synthetic-data source for Stage 2.** Default is GPT-4-class via Anthropic API for generation, Streetlives staff for review/approval. Alternatives: redacted production transcripts (slower, privacy-restricted), eval-scenario expansion (faster, smaller).

5. **Backbone if Gate 1 fails.** If MiniLM-L6 can't hit 0.65 macro-F1 on dair-ai, fallback is MiniLM-L12 (still small footprint). MPNet or larger would compromise the architectural argument. Worth making this fallback explicit before training starts.

## What's adjacent to this work but not in scope

- **Response-copy refresh.** The R42 leverage move. Editorial work, not engineering. Highest priority for actually moving the eval ceiling.
- **Pattern 2 expansion.** Situational distress recognition. Different signal (lexical context, not surface emotion).
- **Survivor-rhetoric audit** of `_EMOTIONAL_RESPONSES` against trauma-informed-homelessness research (Sarcina et al. 2025–26). Should land before the classifier ships, since the response copy the classifier routes to is the user-facing surface.
- **Crisis detection upgrades.** Separate path; uses different signals (regex + LLM stage 2).

## References

- `tools/README.md` (this repo) — Pattern 8 design + smoke test findings
- `docs/audits/PATTERN_LEVEL_OPPORTUNITIES.md` — original Pattern 8 specification
- `tier2_model_smoketest.py` — empirical evaluation of pretrained candidates that motivated this fine-tune path
- `R42_eval_analysis.md` — eval run that anchored the bounded-impact assessment
- dair-ai/emotion dataset — https://huggingface.co/datasets/dair-ai/emotion
- sentence-transformers/all-MiniLM-L6-v2 — the chatbot's existing backbone
- Demszky et al. 2020 (GoEmotions) — broader emotion-annotation literature
- Larsen et al. 2024 (Lifeline Australia) — hybrid lexicon + custom-category precedent

---

*Drafted May 2026. Status: design + cost estimate + decision gates complete; awaiting team review and Streetlives staff availability confirmation before Stage 1 starts.*
