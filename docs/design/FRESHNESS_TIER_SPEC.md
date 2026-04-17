# Freshness Tier Spec

**Status**: **Shipped** (April 17, 2026). Implemented in
`query_templates.py` as `_FRESHNESS_TIER_RANK` + `_FRESHNESS_DAYS`, wired
into `_BASE_ORDER_PARTS`. The 6 tests in `tests/unit/test_query_templates.py`
that previously xfailed against this spec now pass. `_FRESHNESS_DAYS` is
re-exported to `query_executor.py` as the single source of truth for the
freshness threshold.

**Original spec date**: April 17, 2026 (written same day, shipped same day)
**Related**: `docs/design/BUCKETED_DISTANCE_SORT_SPEC.md` (shipped earlier)

---

## Motivation

The bucketed-distance sort groups services into walking-time bands
(<500m, 500m–1km, 1km–2km, 2km+) so freshness can win within a band.
Currently, "freshness" means `last_validated_at DESC NULLS LAST` — a
continuous sort. That means a service verified yesterday barely edges
out one verified the day before, and a service verified 2 years ago
still beats an unverified one (NULLs last).

For this population, we care whether the data is **recent enough to
trust** (≤90 days) more than we care about exact recency within that
window. Within a walking band and a freshness tier, we don't need a
strict timestamp order — alphabetical or name-based is fine.

Tiered freshness with three buckets — fresh / stale / unverified —
makes the "recent enough to trust" signal explicit and sorts within
bands more meaningfully.

---

## Proposed shape

### Constants

```python
# In query_templates.py (new), matching query_executor.py:
_FRESHNESS_DAYS = 90

# New tiered CASE expression:
_FRESHNESS_TIER_RANK = """CASE
    WHEN l.last_validated_at >= CURRENT_DATE - INTERVAL '90 days' THEN 0
    WHEN l.last_validated_at IS NOT NULL THEN 1
    ELSE 2
END"""
```

The 90-day threshold should be shared between `query_templates.py` (SQL
sort) and `query_executor.py` (`_compute_freshness` stats). Currently
only the executor defines it. Pull it into a shared constant or have
one import from the other.

### ORDER BY composition

Before (continuous freshness only):
```
1. l.last_validated_at DESC NULLS LAST   — continuous freshness
2. s.name                                 — alphabetical tiebreaker
```

After (shipped, in `_BASE_ORDER_PARTS`):
```
1. _FRESHNESS_TIER_RANK                   — 3-tier bucket (fresh/stale/null)
2. l.last_validated_at DESC NULLS LAST    — continuous tiebreaker within a tier
3. s.name                                 — alphabetical tiebreaker
```

When proximity is active (bucketed distance spec), the full chain is:

```
1. Population boosts (LGBTQ / veteran / description) — if active
2. _DISTANCE_BAND_RANK                    — walking-time band
3. _FRESHNESS_TIER_RANK                   — 3-tier freshness bucket
4. l.last_validated_at DESC NULLS LAST    — continuous freshness tiebreaker
5. _DISTANCE_TIEBREAK                     — continuous distance tiebreaker
6. s.name                                 — alphabetical tiebreaker
```

### What NOT to do: open-now in SQL

Open-now sorting stays in Python (`_sort_open_first`). SQL's 2-bucket
CASE (open / not-open) is strictly worse than Python's 3-bucket
(open / closed / unknown). See the comment block at
`query_templates.py:430-458` for the history on why this was moved to
Python. Two of the original freshness-tier tests asserted a 4-element
`_BASE_ORDER_PARTS` with SQL open-now at index 0 — those tests were
deleted because they encoded a direction we've explicitly rejected.

---

## Implementation (shipped Apr 17, 2026)

1. ✅ Added `_FRESHNESS_DAYS = 90` and `_FRESHNESS_TIER_RANK` CASE
   expression to `query_templates.py`. The CASE uses `_FRESHNESS_DAYS`
   via f-string interpolation so the threshold propagates.
2. ✅ `query_executor.py` imports `_FRESHNESS_DAYS` from `query_templates`
   instead of defining its own — single source of truth.
3. ✅ `_BASE_ORDER_PARTS` extended from 2 to 3 elements: tier rank +
   continuous timestamp + name.
4. ✅ The ORDER BY builder's `*fresh_parts, name_part = _BASE_ORDER_PARTS`
   unpacking (written in the earlier bucketed-distance sprint as forward
   compat) handled the shape change automatically — no builder edits
   were needed.
5. ✅ Removed `@pytest.mark.xfail` decorators from the 5 tests; added
   `test_freshness_tier_before_continuous_timestamp` as an extra guard
   on the relative order within the freshness section (otherwise the
   tier has no effect). Updated `test_base_order_parts_current_shape`
   to assert the new 3-element shape.

## Rollout considerations

This changes sort order for every query in prod. Worth shadow-running
against the eval suite to measure impact before flipping on. The
current ship is unconditional — if any eval scenarios regress after
deploy, a feature flag can be added later by making `_BASE_ORDER_PARTS`
conditionally include `_FRESHNESS_TIER_RANK`.

The biggest observable change: services with `last_validated_at` in
tier 1 (stale, 90+ days old) now sort *below* all fresh services
regardless of timestamp. In the previous continuous sort, a 95-day-old
record was basically tied with a 90-day-old one; in the tiered sort,
the 89-day-old wins outright. This is the intended behavior but worth
measuring impact on the eval suite.

An eval run is the recommended next step to confirm no unintended
regressions in the 167 scenarios.
