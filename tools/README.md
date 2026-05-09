# `tools/` — Pattern 8 (semantic tone) draft artifacts

These are pre-implementation artifacts for **Pattern 8** from `docs/audits/PATTERN_LEVEL_OPPORTUNITIES.md`: extending the existing semantic router with tone classification alongside its existing service and population classification. None of this is wired into the production router yet. The dict, probe, and eval-comparison script are here so the design work is reviewable in code rather than scattered across drafting docs, and so the next person picking this up has a runnable starting point.

The plan in one sentence: the lexicon-based `_classify_tone` (`backend/app/services/classifier.py:228`) fires on only ~2% of classified turns; surface emotion the substring lexicon misses ("things have been rough," "I lost my job and don't know what to do") gets caught by adding tone as a third route category in `semantic_routes.py` alongside `SERVICE_ROUTES` and `POPULATION_ROUTES`, riding on the same `all-MiniLM-L6-v2` embedding pass.

## Files

| File | What it is | When to use |
|---|---|---|
| [`tone_routes_draft.py`](./tone_routes_draft.py) | The proposed `TONE_ROUTES` dict (9 categories × 9 utterances). Drop-in formatted for `backend/app/services/semantic_routes.py`. Contains the full design rationale in inline comments. | Ship target. Read its top docstring first — it captures all category-level decisions. |
| [`tone_routes_probe.py`](./tone_routes_probe.py) | Empirical verification probe. Embeds every canonical, computes within- vs between-category cosine similarity, flags outliers. Runs in ~30 seconds. | Run after any change to the canonicals, to catch outlier utterances pulled toward the wrong centroid. |
| [`tone_routes_eval_compare.py`](./tone_routes_eval_compare.py) | Comparison of lexicon vs proposed semantic on every first-turn message in `tests/eval/eval_llm_judge.py` (or all turns with `--all-turns`). Categorizes each comparison as WIN / CONFLICT / LEX_ONLY / AGREEMENT / DOUBLE_MISS. Supports `--chunk` for clause-level rather than full-message embedding. | Run before the human-labeling experiment to get a quick read on whether semantic adds coverage on inputs the team already cares about. |
| [`tier2_model_smoketest.py`](./tier2_model_smoketest.py) | Compares chunked-MiniLM baseline against j-hartmann (the one pretrained classifier we found viable) on the same eval message set. Includes strict load validation (id2label sanity, mapping-overlap, smoke-probe gating) so silent-failure modes don't slip through. | Run when evaluating whether to introduce a pretrained classifier dependency. The strict validator is the "did it actually load correctly" check the kashyaparun episode taught us to require — extension point for future model swaps. |

## Workflow

The right order to use these:

```
1. Edit tone_routes_draft.py
2. Run tone_routes_probe.py        ← catches structural outliers in the canonicals themselves
3. Run tone_routes_eval_compare.py ← measures real-world value-add against the existing eval suite
   (use --chunk for clause-level, the production-relevant configuration)
4. Optional: tier2_model_smoketest.py  ← only if a pretrained classifier is being considered
   alongside the lexicon+chunked-MiniLM baseline
5. Streetlives staff voice review  ← canonicals are the bot's "ear"; staff with lived experience validate
6. 100-message human-labeling experiment ← Pattern 8's specified empirical step
7. Wire into semantic_router.initialize() ← integration (see "Integration TODOs" below)
```

Steps 1–4 are local and free. Steps 5–6 are the gating decisions for engineering commit.

## Smoke test findings (May 2026)

The eval-comparison and tier-2 smoke tests have been run. The findings below should be read alongside `R42_eval_analysis.md` (the eval run that prompted this work). Two empirical results:

**Chunking adds 4–5 high-precision wins over full-message embedding.** `tone_routes_eval_compare.py` without `--chunk` produced 0 WINs, 5 AGREEMENTS, 323 DOUBLE_MISS — full-message embedding dilutes tone signal in multi-content messages. With `--chunk`, the same canonical set produced 5 real WINs (`staten_island_mental_health` rough_day@0.71, `wa_rough_sleeper_urgent` alone@0.68, `multi_emotional_accept_second_still_warm` rough_day@0.71, `multi_shame_food_bank_first_time` shame@0.67, plus one crisis-overlap), with two LEX_ONLY → AGREEMENT recoveries on the predicted dilution-fix targets. Chunking is the right production configuration; full-message embedding isn't.

**Pretrained emotion classifiers don't move the R42 ceiling materially.** The 48-scenario tone+dignity ceiling identified in R42 (`response_tone=3` + `dignity_anti_stigma=3`) is mostly *not* a classifier problem — most of those scenarios have no surface emotion for any classifier to detect. Pulling the numbers:

- **j-hartmann at conf>=0.85** catches **4 of 48** ceiling scenarios. At conf>=0.90, **2 of 48**. 32 of the 48 ceiling scenarios get exactly 0.0 confidence from j-hartmann — bare service requests like "need somewhere to wash my clothes" or "is there anywhere I can take a shower near Penn Station" register as no-emotion to the classifier (correctly), even though the eval judge wants more warmth.
- The classifier *correctly* identifies high-stakes situational distress (`peer_aging_out_foster` at sad@0.94, DV crisis at scared@0.99) — but those scenarios have other unaddressed gaps (multi-need recognition, population-pipeline). Adding a warmth prefix on top doesn't move them off the failure list.
- Most of j-hartmann's 25 high-confidence wins fire on already-empathetic top-quartile scenarios (`crisis_domestic_violence`, `crisis_suicidal`, `emotional_feeling_down`). Diminishing returns — adding empathetic prefix to an already-warm response doesn't lift the score.

The actionable read: **Pattern 8 should ship at the chunked-MiniLM baseline**. The R42 ceiling is owned by response-copy refresh (the three flat templates: `"Let's find something for you"`, `"I'll look for X — sound good?"`, `"I found N option(s) for you:"`) and Pattern 2 (situational/multi-need recognition), not by a better classifier.

**Foundation argument is being addressed by fine-tuning our own.** The architectural play — own the classifier infrastructure, share the chatbot's existing MiniLM backbone, no `trust_remote_code` dependencies — is captured in the design doc at [`docs/design/CLASSIFIER_FINE_TUNE_PLAN.md`](../docs/design/CLASSIFIER_FINE_TUNE_PLAN.md). Two pretrained candidates were evaluated and dropped:

- `kashyaparun/Mental-Health-Chatbot-using-RoBERTa-fine-tuned-on-GoEmotion` was the original GoEmotions-fine-tuned candidate. Turned out to be a broken HuggingFace upload — declares `architectures=['RobertaForMaskedLM']`, uses non-standard layer names so all encoder weights load as UNEXPECTED, missing classifier head entirely. Result: a randomly-initialized classifier emitting near-uniform `LABEL_0..27` outputs.
- `shhossain/all-MiniLM-L6-v2-sentiment-classifier` was the architectural-footprint candidate (same MiniLM backbone, ~22.7M params). Loaded successfully against transformers v4 but breaks against transformers ≥ 5.0 with `AttributeError: 'SententenceTransformerSentimentModel' object has no attribute 'all_tied_weights_keys'`. Model is ~2 years stale, depends on `trust_remote_code`, predates a v5 API requirement. Patching around it would mean owning a compatibility shim against an unmaintained upstream.

`pipeline()` returns successfully on the kashyaparun upload — there is no Python-level signal that the model is broken. The smoke test now includes strict load validation (`id2label` sanity check, mapping-overlap check, smoke-probe gate) that catches both kinds of silent failure in seconds. Any future pretrained-model swap should run through the smoke test's `_validate_loaded()` gate before anything depends on its output. The historical kashyaparun symptoms — `LABEL_0..N` placeholders, uniform-distribution noise on clear emotional probes (~1/n_classes per label) — are exactly what those checks look for.

## Key decisions already made

These were settled during drafting and are documented inline in the dict — surfaced here so they're not re-litigated without good reason.

**9 categories, mapped 1:1 to existing `_EMOTIONAL_RESPONSES` keys.** Pattern 8's example lists 8 (`shame`, `scared`, `lost`, `exhausted`, `hopeless`, `distrust`, `undeserving`, `alone`); the existing `_EMOTIONAL_RESPONSES` (`backend/app/services/responses.py:113`) has 9 (`scared`, `sad`, `rough_day`, `shame`, `grief`, `alone`, `undeserving`, `distrust`, `angry`). The conservative choice is to use the existing 9 so downstream response selection doesn't change. The doc's three net-new categories (`lost`, `exhausted`, `hopeless`) are deferred as Phase 1.5 — they require new entries in `_EMOTIONAL_RESPONSES` to drive different responses, which needs separate copy review.

**Per-route threshold override for `distrust` (0.55 vs 0.65 default).** Distrust is irreducibly heterogeneous in expression — questions ("what's the catch"), past-experience ("I've been lied to"), and stances ("I don't trust this") cluster by surface form rather than meaning under MiniLM. The fix is architectural (lower threshold + lexicon hybrid), not utterance editing. The codebase already supports per-route thresholds (see `"other": 0.78` in `semantic_router.py`).

**Probe metric measures canonical concentration, not production value.** The May 2026 lexicon-overlap analysis found Pearson r = −0.773 between a category's lexicon-miss-rate (its novel-coverage value) and its probe gap (n=9 categories). Categories whose canonicals are lexicon-redundant cluster more tightly because canonical phrasings concentrate in keyword space; categories whose canonicals are lexicon-novel inherently span more diverse surface forms.

This means **MARGINAL probe ratings on high-novel-coverage categories (`undeserving`, `distrust`, `angry`, `alone`) are working as designed, not bugs to fix.** Tightening these by adding lexicon-redundant utterances would improve the probe metric while *decreasing* real-world value. Each route in `tone_routes_draft.py` is tagged with its novel-coverage rating to make this trade-off legible.

**`sad ↔ rough_day` cross-similarity (~0.355) is acceptable.** Both categories route to compatible warmth-acknowledgment response copy. If real-world data shows the pair causing actual mistakes, the documented escape hatch is collapsing them into a single `distress` super-category — Lifeline Australia's online chat analysis (Larsen et al., 2024) uses Empath + a custom `Distress` category in exactly this shape, so there's precedent.

**No crisis-adjacent utterances in TONE_ROUTES.** Crisis detection runs first (regex pre-check + Sonnet stage 2). Tone is for sub-crisis distress only. The grief category specifically avoids "can't keep going" / "don't know how to keep going" framing for this reason.

**No bot-directed frustration in TONE_ROUTES.angry.** "you're not helping me" / "useless" stays in `_FRUSTRATION_PHRASES` and routes to the frustration handler. TONE_ROUTES.angry is anger at the situation/circumstances ("why does this keep happening to me") — distinct signal, distinct response.

## Research grounding

The 9-category set wasn't derived from a clinical literature review, but it aligns surprisingly well with existing work — captured here so the choice has a defensible footing.

**cPTSD symptom-cluster alignment.** PTSD prevalence in vulnerably housed populations is 21–53% (Carmichael & Goyer, 2021), with cPTSD characterized by *problems with affect regulation, negative beliefs about oneself, and difficulty in sustaining relationships*. The 9 categories map onto these clusters:

- Negative self-beliefs → `shame`, `undeserving`
- Affect regulation difficulty → `angry`, `scared`
- Difficulty sustaining trust / relationships → `distrust`, `alone`
- Acute distress states → `sad`, `rough_day`, `grief`

The R28 additions (`undeserving`, `distrust`, `angry`) drew from the same source body that informs trauma-informed care today (PMC studies, Harm Reduction Coalition, SAMHSA), so the alignment is non-coincidental.

**Lifeline Australia precedent for the hybrid pattern.** Larsen et al. (2024) on the Lifeline Australia online chat used the four validated Empath categories (Positive Emotion, Negative Emotion, Suffering, Optimism) plus two context-specific custom categories (Distress, Suicidality). Validates our pattern of "established lexicon + domain-specific custom categories."

**What we're not modeling.** Three frameworks worth knowing about that we deliberately don't replicate:

- *GoEmotions* (Demszky et al., 2020): 27 categories, Reddit-derived. Reddit users aren't the YourPeer chatbot's population; the fine-grained set has no notion of "shame about asking for help" as a discrete category. Fine-tuned BERT achieves only ~53% macro-F1 on the 27-label task — useful calibration for what's achievable on fine-grained emotion.
- *Ekman 6 / Plutchik 8*: too coarse. Our `shame` and `undeserving` would both collapse to "neutral" or "sadness."
- *Motivational Interviewing DARN-CAT*: about behavior-change motivation in counseling settings, not affect surface in a service-finding chatbot. Sometimes imported as a generic user-state classifier; that's a category error in our setting.

**Existing fine-tuned MiniLM variants — tested and dropped.** `shhossain/all-MiniLM-L6-v2-sentiment-classifier` (a 6-class emotion classifier trained on `dair-ai/emotion`, ~22.7M parameters) was the architectural-footprint candidate: same MiniLM backbone the chatbot already uses, no new model dependency. Eval was incomplete — the model loads against transformers v4 but breaks against transformers ≥ 5.0 (`all_tied_weights_keys` API change), uses `trust_remote_code` from a 2-year-stale upload. Patching would commit us to a compatibility shim against an unmaintained upstream. The architectural argument that motivated testing it is sound; the conclusion is to fine-tune our own classifier from `sentence-transformers/all-MiniLM-L6-v2` (the chatbot's existing backbone) on `dair-ai/emotion` plus YourPeer-domain augmentation. See [`docs/design/CLASSIFIER_FINE_TUNE_PLAN.md`](../docs/design/CLASSIFIER_FINE_TUNE_PLAN.md) for the design.

**LLM-as-annotator caveat for the labeling experiment.** Recent work (medrxiv 2026.01) on cancer peer-support posts flagged that LLM-based labeling can introduce systematic shifts in decision boundaries for subjective constructs like emotional intensity, with overconfidence error modes that aren't immediately visible. If the 100-message labeling experiment uses Opus or Sonnet as a labeler instead of human raters, report label-distribution shifts alongside accuracy.

## Adjacent ticket — survivor-rhetoric audit

A 2025–26 trauma-informed-homelessness study (Sarcina et al.) flagged a "survivor rhetoric" caveat — homelessness participants explicitly didn't want to be referred to as survivors and warned against language that normalizes trauma. **`_EMOTIONAL_RESPONSES` response copy should be audited against this finding.** Current copy doesn't use the word "survivor" but uses validating phrases like "It takes courage to say that" — worth confirming these phrasings against the research before tone classification ships.

This is a separate ticket from Pattern 8 but should land before or alongside it; flagged here so it doesn't get lost. The `dv_survivor` population tag is also in scope of this audit.

## Integration TODOs (Phase 2+)

When Pattern 8 ships, these are the changes to make. The dict and tools above are Phase 1 scaffolding; the engineering work is below.

1. **Move the dict.** `tone_routes_draft.py`'s `TONE_ROUTES` belongs in `backend/app/services/semantic_routes.py` between `POPULATION_ROUTES` and EOF. The header comment block can stay as-is; it documents the design.
2. **Extend `SemanticMatch`** (`backend/app/services/semantic_router.py:107`) with optional `tone: str | None` and `tone_confidence: float = 0.0` fields. The router currently returns one service + one population; tone is the third orthogonal output.
3. **Add a `TONE_THRESHOLDS` dict** mirroring the existing `_PER_ROUTE_THRESHOLDS` pattern. At minimum `{"distrust": 0.55}`. Default tone threshold: `0.65` (lower than `DEFAULT_SERVICE_THRESHOLD = 0.75`).
4. **Embed tone routes at startup.** `initialize()` already loops over `SERVICE_ROUTES` and `POPULATION_ROUTES`; add a third loop for `TONE_ROUTES` (prefix the keys with `tone_` like populations are prefixed with `pop_`, to keep namespaces clean).
5. **Update `classify()`** to compute tone similarity in parallel with service / population. The same embedding pass already runs; this is one extra dict comparison.
6. **Integrate into `_pick_emotional_response`** (`backend/app/services/responses.py:180`). Hybrid pattern: lexicon hit wins (preserves backward-compat copy and the if-chain's specific phrasings); semantic hit fires only when lexicon misses. Don't replace the lexicon — augment it.
7. **Add tests.** Per-route firing tests (one for each of the 9 categories), threshold-guard tests, regression tests against the existing eval scenarios. The `tone_routes_eval_compare.py` output gives you the test cases worth pinning.
8. **Tune thresholds against eval data** after Phase 1.5 (Pattern 8 § "Phase 4"). Measure firing rate and judge-tone-score correlation. The current `0.65` / `0.55` are starting points, not final.

## Phase 1.5 candidates (deferred)

Two extensions worth considering after Phase 1 ships:

- **Add `lost` / `exhausted` / `hopeless`** as new categories. These were in Pattern 8's example but require corresponding new entries in `_EMOTIONAL_RESPONSES` with appropriate response copy. Add after Streetlives staff confirms they're distinct enough from `rough_day` / `sad` / `scared` to warrant their own copy.
- **Collapse `sad` + `rough_day` into `distress`** (Lifeline Australia super-category) if the labeling experiment shows the cross-similarity is causing actual mistakes. Existing route entries would merge cleanly.

## What this isn't

- It isn't a replacement for `_classify_tone`. The lexicon is fast and produces specific copy through the if-chain; tone routing is a backstop for cases the substring matcher misses.
- It isn't a way to detect crisis. Crisis runs first (regex + Sonnet), in `crisis_detector.py`.
- It isn't a way to detect situational/implied emotion (asylum seeker, justice-impacted, "charging my phone in the cold"). That's Pattern 2's territory (transition-event bundles). This layer matches what the user *says* about how they feel, not what the situation *implies*.
- It isn't a way to detect bot-directed frustration. That stays in `_FRUSTRATION_PHRASES` and routes to the frustration handler.

## References

Inline citations above. Key sources:

- Demszky, D., et al. (2020). *GoEmotions: A Dataset of Fine-Grained Emotions.* ACL.
- Larsen, M.E., et al. (2024). *Changes in Mental State for Help-Seekers of Lifeline Australia's Online Chat Service: Lexical Analysis Approach.* JMIR.
- Carmichael, V., & Goyer, M.É. (2021). *Interventions to treat PTSD in vulnerably housed populations and trauma-informed care: A scoping review.* medRxiv.
- Mohammad, S.M., & Turney, P.D. (2010, 2012). *NRC Word-Emotion Association Lexicon.*
- Sarcina, et al. (2025–26). *Designing a trauma informed service to deliver trauma therapy with people experiencing homelessness: a qualitative study.* PMC.
- Thompson, L.K., et al. (2020). *Subgroups of suicidal texters engaging with Crisis Text Line.* PMC.
- Pattern 8 of `docs/audits/PATTERN_LEVEL_OPPORTUNITIES.md` (this repo) is the source plan.

---

*Drafted May 2026 alongside the R42 eval run. Status: design + tools complete; chunked-MiniLM eval-comparison done (5 high-precision WINs identified); pretrained-classifier evaluation done (kashyaparun confirmed broken upload, shhossain unmaintained against transformers v5, j-hartmann functional but bounded R42 impact); fine-tune-our-own design doc drafted at [`docs/design/CLASSIFIER_FINE_TUNE_PLAN.md`](../docs/design/CLASSIFIER_FINE_TUNE_PLAN.md). Streetlives staff review and engineering integration pending.*
