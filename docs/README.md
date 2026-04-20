# YourPeer Chatbot — Documentation

Start here. This file is the map of the `docs/` directory.

## If you're new to this codebase

Read these in order. They're all at the top level of `docs/`:

1. **[ONBOARDING.md](ONBOARDING.md)** — the primary onboarding guide. Architecture, message lifecycle, 3-tier routing system, database schema, and first-week checklist.
2. **[SETUP.md](SETUP.md)** — get the backend and frontend running locally.
3. **[architecture.md](architecture.md)** — one-page high-level architecture overview.
4. **[FEATURES.md](FEATURES.md)** — full feature reference by area (conversation, crisis, search, cards, privacy, accessibility, staff tools). Living document; update when shipping a feature.
5. **[CHATBOT_BEHAVIOR.md](CHATBOT_BEHAVIOR.md)** — routing pipeline, message categories, emotional handling, crisis step-down, guardrails, how to extend. Living document.
6. **[TESTING.md](TESTING.md)** — how the test suite is organized, how to run it, where to add new tests.

For deployment details, see **[DEPLOY.md](DEPLOY.md)**. For Claude Code / AI-assisted development context, see **[CLAUDE.md](CLAUDE.md)**.

## Directory layout

```
docs/
  README.md                       ← you are here
  ONBOARDING.md, SETUP.md, DEPLOY.md, architecture.md, CLAUDE.md
  FEATURES.md, CHATBOT_BEHAVIOR.md, TESTING.md

  design/      how specific features are designed and why
  audits/      point-in-time analyses and reports
  ops/         living operational data (eval runs, metrics)
```

## `design/` — feature design docs

Reference material for people working on a specific feature. Each file describes the design, the tradeoffs considered, and how to extend.

| File | What it covers |
|------|----------------|
| [CRISIS_DETECTION.md](design/CRISIS_DETECTION.md) | Two-stage crisis pipeline, 8 categories, fail-open policy, phrase lists |
| [PII_REDACTION.md](design/PII_REDACTION.md) | Seven detection categories, patterns, known gaps, future work |
| [SEMANTIC_ROUTING_DESIGN.md](design/SEMANTIC_ROUTING_DESIGN.md) | Tier 2 semantic router — model choice, hybrid multi-intent, route definitions, scaling |
| [SPANISH_LANGUAGE_DESIGN.md](design/SPANISH_LANGUAGE_DESIGN.md) | Spanish language support design (deferred from pilot) |
| [POPULATION_FALLBACK_SPEC.md](design/POPULATION_FALLBACK_SPEC.md) | Citywide fallback for rare populations (LGBTQ YA, youth, senior, veteran) |
| [BUCKETED_DISTANCE_SORT_SPEC.md](design/BUCKETED_DISTANCE_SORT_SPEC.md) | Four-band distance sort — trading precision for perceived fairness |
| [FRESHNESS_TIER_SPEC.md](design/FRESHNESS_TIER_SPEC.md) | Three-tier freshness ranking: recent → stale → unknown |

## `audits/` — point-in-time analyses

These captured findings at a specific date. Most have been acted on; they're retained so future work has context for why things are the way they are.

| File | What it covers |
|------|----------------|
| [BOUNDARY_AUDIT.md](audits/BOUNDARY_AUDIT.md) | NYC borough polygon validation — why `pa.city` isn't trustworthy and how we mitigate |
| [QUERY_PARITY_AUDIT.md](audits/QUERY_PARITY_AUDIT.md) | Line-by-line comparison of chatbot vs YourPeer query logic |
| [REGEX_AUDIT.md](audits/REGEX_AUDIT.md) | Regex collision analysis — which keywords were retired and why |
| [PHRASE_LIST_AUDIT.md](audits/PHRASE_LIST_AUDIT.md) | Phrase lists cross-referenced against C-SSRS, ISEAR, clinical literature |
| [HARDCODED_MESSAGES_REVIEW.md](audits/HARDCODED_MESSAGES_REVIEW.md) | Every user-facing hardcoded message with trigger conditions and source |
| [MULTI_INTENT_PLAN.md](audits/MULTI_INTENT_PLAN.md) | Shipped — architecture plan for multi-service intent (all 5 PRs done) |
| [FEATURE_PARITY_TASKS.md](audits/FEATURE_PARITY_TASKS.md) | Shipped — 16 YourPeer parity gaps and their implementation decisions |

## `ops/` — living operational data

These grow over time and are updated on every eval run or metric recompute.

| File | What it covers |
|------|----------------|
| [EVAL_RESULTS.md](ops/EVAL_RESULTS.md) | Per-run eval scoring — all runs 14 onwards, critical failures, fixes |
| [METRICS.md](ops/METRICS.md) | 35+ success metrics across 7 layers with definitions, targets, phasing |

## Related docs elsewhere in the repo

- **[`../README.md`](../README.md)** — top-level project README (what the product is, quick start)
- **[`../scripts/DB_AUDIT.md`](../scripts/DB_AUDIT.md)** — database audit script purpose and usage
- **[`../scripts/CHECK_DOCS.md`](../scripts/CHECK_DOCS.md)** — the drift checker (what it validates and why)
- **[`../scripts/DRIFT_DETECTION.md`](../scripts/DRIFT_DETECTION.md)** — detecting upstream changes in `yourpeer.nyc`

## Maintaining these docs

When you ship a feature, update `FEATURES.md` and (if behavior-relevant) `CHATBOT_BEHAVIOR.md`. For bigger features, add a design doc to `design/`. For point-in-time analyses, put them in `audits/` with a date stamp.

The drift checker (`scripts/check_docs.py`) validates that hardcoded test counts, model IDs, env vars, file paths, and inter-doc links still match reality. Run it before merging — or let CI run it for you.
