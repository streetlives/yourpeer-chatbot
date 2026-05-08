# =============================================================================
# DRAFT — Pattern 8 Phase 1
#
# This file is the proposed new section to insert into
# `backend/app/services/semantic_routes.py` after `POPULATION_ROUTES`.
# It is NOT a standalone module; it is the diff target.
#
# Source: docs/audits/PATTERN_LEVEL_OPPORTUNITIES.md § Pattern 8
# Status: design draft, not yet wired up to the router
# =============================================================================


# ---------------------------------------------------------------------------
# TONE ROUTES
# ---------------------------------------------------------------------------
# Surface-emotion classification. Each key maps 1:1 to an existing key in
# `_EMOTIONAL_RESPONSES` (responses.py:113), so the addition of semantic
# matching does not require any change to downstream response selection.
#
# A lower confidence threshold than service routes (suggested 0.65 vs the
# default 0.75) is appropriate because:
#   - Tone false negatives matter more than false positives — missing a
#     shame signal silently degrades response warmth, while a borderline
#     false positive still surfaces a warm acknowledgment that's
#     reasonable in most contexts.
#   - Tone is orthogonal to service/population — a hit here doesn't
#     displace the service routing, it augments it.
#
# Per-category threshold override — `distrust`:
#   The empirical probe (May 2026) showed `distrust` with the lowest
#   within-category similarity (0.231) of any category — its own
#   utterances don't tightly cluster with each other. Inspection of
#   the dict reveals why: distrust is expressed in three distinct
#   rhetorical modes (questions to the bot, past-experience statements,
#   skepticism stances) and MiniLM groups by surface form. This is a
#   structural property of the category, not a fixable utterance issue.
#   Recommendation: distrust uses a per-route threshold of 0.55 (vs
#   0.65 default for tone). Trade-off: higher false-positive rate, but
#   the lexicon hybrid backstops it — when the lexicon hits "what's the
#   catch" the lexicon copy fires; when the lexicon misses, the
#   lower-threshold semantic match catches indirect distrust.
#
# IMPORTANT: probe metrics measure canonical concentration, NOT
# production value
#   The May 2026 probe-overlap analysis (Pearson r = −0.773 between
#   lexicon-miss-rate and probe gap, n=9 categories) showed that
#   categories whose canonicals are LEXICON-REDUNDANT cluster more
#   tightly (good probe metric) than categories whose canonicals are
#   LEXICON-NOVEL. This is intuitive — canonical phrasings concentrate
#   in keyword space; novel phrasings span surface forms.
#
#   The architectural purpose of TONE_ROUTES is to catch what the
#   lexicon MISSES. So MARGINAL probe ratings on high-novel-coverage
#   categories (especially `undeserving`, `distrust`, `angry`, `alone`)
#   are working as designed. Tightening these by adding more
#   lexicon-redundant utterances would IMPROVE the probe metric while
#   DECREASING real-world value. Don't.
#
#   The probe is useful for catching specific outliers (a single
#   utterance closer to the wrong centroid) — it caught two during
#   draft work and both were fixed. It is NOT useful as an aggregate
#   quality target. Real quality signal comes from the labeling
#   experiment (Pattern 8 § "Empirical verification") on actual
#   user messages — that's what tells you whether the layer catches
#   real-world tone the lexicon misses.
#
# Tone is the THIRD route category. It does NOT replace the
# lexicon-based `_classify_tone` in classifier.py; the integration is
# hybrid (lexicon hit wins for backward compatibility and specific
# copy; semantic hit fires when lexicon misses). See Pattern 8's
# "Migration strategy" section.
#
# What this layer is NOT for:
#   - Crisis language ("I want to die"). Crisis runs before tone and is
#     detected separately. These utterances are sub-crisis distress only.
#   - Situational/implied emotion (asylum seekers, justice-impacted,
#     "charging my phone in the cold"). That's Pattern 2's territory
#     (transition-event bundles). This layer matches what the user
#     SAYS about how they feel, not what the situation IMPLIES.
#   - Bot-directed frustration ("you're not helping," "useless").
#     That stays in `_FRUSTRATION_PHRASES` and routes to the frustration
#     handler, not the warm-acknowledgment handler.
#
# Design rationale — why these 9 categories:
#   The 9 keys were chosen to map 1:1 onto existing `_EMOTIONAL_RESPONSES`
#   keys (the migration-conservative choice from Pattern 8). They also
#   align with cPTSD symptom clusters as documented for the homeless
#   population (PTSD prevalence 21–53%, cPTSD characterized by problems
#   with affect regulation, negative beliefs about oneself, and
#   difficulty sustaining relationships):
#     - Negative self-beliefs           → shame, undeserving
#     - Affect regulation difficulty    → angry, scared
#     - Difficulty sustaining trust /
#       relationships                   → distrust, alone
#     - Acute distress states           → sad, rough_day, grief
#   This was not derived from cPTSD literature directly, but the
#   alignment is non-coincidental — the research-backed additions in
#   R28 (undeserving, distrust, angry) drew from the same source body.
#
# Sources:
#   - The existing _EMOTIONAL_PHRASES lexicon (120 fragments) expanded
#     into natural full sentences. Embedding works on sentences, not
#     keywords.
#   - Research-backed tone categories from R28 (PMC studies on shame /
#     undeserving, Harm Reduction Coalition on distrust as trauma
#     response, SAMHSA on anger at circumstances).
#   - Pattern 8 example utterances from PATTERN_LEVEL_OPPORTUNITIES.md.
#   - The R27 tone-gap audit (indirect shame phrases — "hard to ask").
#
# Maintenance:
#   These 81 utterances are the bot's "ear" for what each tone sounds
#   like. Streetlives staff with lived experience should validate and
#   extend before this layer ships. Pattern 8 § "Empirical verification"
#   describes the 100-message labeling experiment that should run
#   before engineering commits to wiring this up.
#
# Phase 1.5 candidates (not in scope for this draft):
#   `lost`, `exhausted`, `hopeless` — proposed in the Pattern 8 example
#   but require new entries in `_EMOTIONAL_RESPONSES` to actually drive
#   different responses. Add as a follow-up after staff review confirms
#   they're distinct enough from `rough_day` / `sad` / `scared` to
#   warrant their own response copy.
#
# Phase 1.5 escape hatch — `distress` as super-category:
#   If `sad` + `rough_day` confusion proves problematic on real-world
#   data (probe showed +0.355 cross-similarity, the highest pair),
#   collapsing them into a single `distress` category has precedent.
#   Lifeline Australia uses Empath + a context-specific `Distress`
#   category for crisis-chat lexical analysis (Larsen et al., 2024).
#   Defer the merge decision to the labeling experiment — the
#   `sad ↔ rough_day` confusion is acceptable as long as both route
#   to compatible warmth-acknowledgment copy (which they currently do).
#
# Adjacent ticket (out of scope here, flagging for tracking):
#   The trauma-informed-homelessness research (Sarcina et al., 2025–26)
#   surfaced a "survivor rhetoric" caveat — homelessness participants
#   cautioned providers against language that normalizes trauma. The
#   `_EMOTIONAL_RESPONSES` response copy should be audited against this
#   finding (not in TONE_ROUTES scope; flagging here so it doesn't get
#   lost). Current copy doesn't use "survivor" but does use
#   "It takes courage" / "you have nothing to be ashamed of"; worth
#   confirming these phrasings against the research.

TONE_ROUTES = {
    # Shame / stigma — #1 documented barrier to help-seeking in this
    # population. Surface vocabulary ranges from explicit ("ashamed")
    # to indirect ("hard to ask," "first time," "burden").
    #
    # Novel-coverage value: MEDIUM (3/9 utterances are lexicon-missed).
    #
    # Note: "burden" framing is a known boundary case — it sits closer
    # to undeserving's "I'm not worth it" theme than to shame's
    # "I'm embarrassed to be here" theme under MiniLM embedding. This
    # category intentionally avoids "burden" anchors in favor of
    # explicit-shame and admission-difficulty phrasings. Burden-themed
    # utterances stay in the lexicon (`_EMOTIONAL_PHRASES`) and route
    # through the existing if-chain in `_pick_emotional_response`,
    # which sends them to the shame response copy.
    "shame": [
        "I hate asking for help like this",
        "I'm really embarrassed to be doing this",
        "I never thought I'd be in this position",
        "this is humiliating to admit",
        "I had to swallow my pride to come here",
        "this is the first time I've ever asked for help",
        "I can't believe I'm in this situation",
        "I'm ashamed I have to ask for this",
        "it's hard for me to admit I need help",
    ],

    # Sad / down. Includes paraphrases of "feeling sad" / "not okay"
    # that the lexicon catches in fragments but may miss when wrapped
    # in longer sentences.
    #
    # Novel-coverage value: HIGH (4/9 utterances are lexicon-missed).
    "sad": [
        "I'm feeling really down lately",
        "I've been so sad these past few weeks",
        "I'm just not doing well",
        "I feel depressed and I can't shake it",
        "things feel really heavy right now",
        "I'm not okay",
        "I've been pretty depressed since this started",
        "feeling really low these days",
        "I just feel sad most of the time",
    ],

    # Rough day / general hardship without a more specific emotion.
    # Catches "things have been rough" / "I just need things to work
    # out for once" — Pattern 8's named example phrasings. Note: the
    # third Pattern 8 example, "I lost my job and don't know what to
    # do," was a probe outlier (predicted grief due to "lost X"
    # embedding pull) and is moved to Phase 2 integration tests rather
    # than retained as a canonical anchor here.
    #
    # Novel-coverage value: HIGH (4/9 utterances are lexicon-missed).
    "rough_day": [
        "I'm having a really rough day",
        "things have been so hard lately",
        "everything is falling apart on me",
        "I just can't catch a break",
        "I've been having a really tough time",
        "things keep getting worse and worse",
        "this whole week has been a disaster",
        "my life is falling apart right now",
        "I just need things to work out for once",
    ],

    # Scared / afraid / fear about the future. Distinct from crisis —
    # these don't express intent to self-harm, they express anxiety
    # about what's coming next.
    #
    # Novel-coverage value: LOW (2/9 utterances are lexicon-missed).
    # The lexicon already catches most explicit fear vocabulary; this
    # category mostly serves as a stable centroid anchor for the few
    # narrative phrasings the lexicon misses.
    "scared": [
        "I'm really scared right now",
        "I'm afraid of what's going to happen",
        "I'm terrified about what comes next",
        "I don't know what's going to happen to me",
        "I'm so scared and I don't know what to do",
        "I'm frightened about my situation",
        "I'm scared about losing my place to live",
        "I'm afraid I won't make it through this",
        "I'm worried sick about everything",
    ],

    # Grief / loss. Major life-disruption trigger noted in P2 audit.
    # Phrasings here intentionally avoid crisis-adjacent "can't keep
    # going" — those route through crisis detection first.
    #
    # Novel-coverage value: LOW (2/9 utterances are lexicon-missed).
    # The lexicon catches most "lost / died / grieving / mourning"
    # vocabulary. This category mostly serves as a stable anchor.
    "grief": [
        "my partner passed away recently",
        "I lost someone close to me",
        "I've been grieving",
        "my mother died last month",
        "someone I loved just died",
        "I'm in mourning",
        "I lost my husband and I'm struggling to cope",
        "I'm grieving and I don't have anyone to talk to",
        "I just lost a family member",
    ],

    # Isolation / loneliness. Distinct from grief (which is about
    # loss of a specific person) — alone is about lacking support
    # network in general.
    #
    # Novel-coverage value: HIGH (4/9 utterances are lexicon-missed).
    "alone": [
        "I have no one to turn to",
        "I'm completely alone in this",
        "I don't have any family or friends to help",
        "there's nobody I can call",
        "I'm all by myself trying to figure this out",
        "nobody understands what I'm going through",
        "I have no one in my life right now",
        "I feel completely isolated",
        "I don't have anybody to lean on",
    ],

    # Undeserving — research-backed (R28 / PMC: 41% of homeless people
    # report feeling undeserving of help). Specifically: comparison-
    # based self-deflection ("others need it more"), not generic
    # shame.
    #
    # Novel-coverage value: HIGHEST (7/9 utterances are lexicon-missed).
    # This is the most lexicon-novel category in the dict — its
    # MARGINAL probe rating (+0.099) is the cost of catching what
    # the lexicon misses. Do not attempt to "tighten" by adding
    # lexicon-redundant utterances.
    "undeserving": [
        "other people need this more than I do",
        "I don't really deserve help",
        "I'm not worth all this trouble",
        "there are people who have it way worse than me",
        "someone else probably needs this more",
        "I feel like I'm taking from people who really need it",
        "I shouldn't be using these services",
        "other people are way more deserving than me",
        "I don't think I deserve to be here",
    ],

    # Distrust / suspicion — named trauma response (Harm Reduction
    # Coalition); top barrier to care-seeking (PMC, SAMHSA). Often
    # phrased as a question to the bot.
    #
    # Novel-coverage value: HIGH (5/9 utterances are lexicon-missed).
    # Note that some "lexicon-missed" cases are substring-mismatch
    # artifacts: lexicon has "is this safe" but utterance is
    # "is this actually safe to use" (with intervening "actually");
    # semantic catches these where substring fails.
    "distrust": [
        "how do I know this is legit",
        "what's the catch",
        "this seems too good to be true",
        "I don't really trust this",
        "I've been lied to before",
        "is this actually safe to use",
        "why is this free, what's the deal",
        "I've been burned by services before",
        "I'm not sure I can trust this",
    ],

    # Anger at situation / circumstances. Distinct from bot-directed
    # frustration ("you're not helping me") which routes to the
    # frustration handler. Anger here is at life / circumstances /
    # systems that have failed the user.
    #
    # Novel-coverage value: HIGH (5/9 utterances are lexicon-missed).
    "angry": [
        "I'm so angry about everything that's happened",
        "I'm furious that I'm in this situation",
        "I'm fed up with how unfair life has been",
        "why does this keep happening to me",
        "it's not fair that I'm dealing with this",
        "I'm sick of having to fight for everything",
        "this whole situation makes me so angry",
        "I'm exhausted from being angry all the time",
        "I shouldn't have to be doing this and it pisses me off",
    ],
}
