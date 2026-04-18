# Bucketed Distance Sort — Design Spec

**Date**: April 16, 2026
**Status**: Proposed
**Priority**: P1 — improves result quality, not blocking

---

## Problem

The current ORDER BY uses continuous distance as the second-highest sort key
(after population boosts). This means a service 800m away ALWAYS outranks one
801m away, even if the closer one hasn't been verified in a year and the farther
one was verified yesterday.

For this population, both distance and data freshness matter:
- **Distance** because someone needing shelter tonight can't travel far
- **Freshness** because an unverified listing might have wrong hours, be closed
  permanently, or have changed eligibility rules

The current sort treats these as strictly hierarchical (distance always wins).
A better approach: group results into distance bands and sort by freshness
within each band.

---

## Solution

Replace the continuous `ST_Distance(...)` ORDER BY expression with a bucketed
CASE expression. Within each band, freshness sorting takes over.

### Distance bands

| Band | Range | Walking time | Label |
|------|-------|-------------|-------|
| 0 | < 500m | ~5 minutes | Right here |
| 1 | 500m – 1 km | ~10 minutes | Nearby |
| 2 | 1 km – 2 km | ~15–20 minutes | Walkable |
| 3 | 2 km+ | 20+ minutes | Further away |

These bands are NYC-specific. In NYC, a 500m difference is negligible — both
services are "right there." A 2km difference matters — that's a 20-minute walk
or a subway ride. Within a single band, the user should see the most trustworthy
(recently verified) results first.

### SQL expression

```sql
-- Replace:
ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography)

-- With:
CASE
    WHEN ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography) < 500 THEN 0
    WHEN ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography) < 1000 THEN 1
    WHEN ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography) < 2000 THEN 2
    ELSE 3
END
```

### Resulting ORDER BY

```sql
ORDER BY
    -- 1. Population boosts (if active)
    lgbtq_boost_rank,
    -- 2. Distance BAND (not continuous distance)
    CASE WHEN ST_Distance(...) < 500 THEN 0
         WHEN ST_Distance(...) < 1000 THEN 1
         WHEN ST_Distance(...) < 2000 THEN 2
         ELSE 3 END,
    -- 3. Freshness (within each band, verified first)
    l.last_validated_at DESC NULLS LAST,
    -- 4. Continuous distance (within same band + freshness, prefer closer)
    ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography),
    -- 5. Name tiebreaker
    s.name
```

Note: continuous distance is kept as a **4th** tiebreaker. This means within
the same distance band AND same freshness tier, the physically closer service
still wins. This prevents arbitrary ordering when two services are both in
band 1 and both verified last week.

### Python post-sort (unchanged)

`_sort_open_first()` still runs after SQL results, re-sorting by open status.
The final sort priority becomes:

```
1. Open status (Python)      — open > closed > unknown
2. Population boosts (SQL)   — LGBTQ/veteran services float up
3. Distance band (SQL)       — <500m > 500m-1km > 1km-2km > 2km+
4. Freshness (SQL)           — recently verified > unverified
5. Continuous distance (SQL) — closer > farther (within same band+freshness)
6. Name (SQL)                — alphabetical tiebreaker
```

---

## Implementation

### Code change

In `query_templates.py`, replace `_DISTANCE_RANK` with `_DISTANCE_BAND_RANK`
and add continuous distance as a tiebreaker:

```python
# Bucketed distance: groups results into walking-time bands so that
# freshness can sort within each band. Without bucketing, a service
# 1 meter closer always beats a service verified yesterday.
_DISTANCE_BAND_RANK = (
    "CASE"
    " WHEN ST_Distance(l.position::geography,"
    " ST_MakePoint(:lon, :lat)::geography) < 500 THEN 0"
    " WHEN ST_Distance(l.position::geography,"
    " ST_MakePoint(:lon, :lat)::geography) < 1000 THEN 1"
    " WHEN ST_Distance(l.position::geography,"
    " ST_MakePoint(:lon, :lat)::geography) < 2000 THEN 2"
    " ELSE 3"
    " END"
)

# Continuous distance as tiebreaker within same band + freshness.
_DISTANCE_TIEBREAK = (
    "ST_Distance(l.position::geography, ST_MakePoint(:lon, :lat)::geography)"
)
```

In the ORDER BY builder (`_BASE_ORDER_PARTS` and surrounding logic in `backend/app/rag/query_templates.py`):

```python
# 2. Distance (when proximity search is active)
if _has_distance:
    order_parts.append(_DISTANCE_BAND_RANK)

# 3. Base tiebreakers: freshness, then name
order_parts.extend(_BASE_ORDER_PARTS)

# 4. Continuous distance tiebreaker (after freshness)
if _has_distance:
    order_parts.append(_DISTANCE_TIEBREAK)
```

### What about non-proximity queries?

When the user specifies a borough/neighborhood but not GPS coordinates, there
is no distance calculation — the `_has_distance` flag is False. In that case,
the ORDER BY is just freshness → name, which is correct. No change needed.

---

## Effect on the reported screenshots

The user's query returned results from Brooklyn with geolocation active.
Current sort: SCO (unverified, closest) → FSNNY (unverified, 2nd closest) →
Safe Horizon (verified 2 weeks, 3rd closest).

With bucketed sort, if all three are in the same distance band (e.g., all
within 1km), the order becomes: Safe Horizon (verified 2 weeks, band 1) →
SCO (unverified, band 1) → FSNNY (unverified, band 1). Verified results
surface first within each band.

If SCO is in band 0 (<500m) and Safe Horizon is in band 1 (500m-1km), SCO
still appears first — closer band wins. This is correct: a shelter 3 blocks
away is more useful tonight than one 8 blocks away, even if verified.

---

## Edge cases

### All results in the same band
Freshness fully determines the order. This is the desired behavior — when
everything is equally walkable, trust the most recently verified.

### No geolocation
No distance bands, no change. Freshness → name ordering applies.

### Band boundaries
A service at 499m and one at 501m will be in different bands. This is
acceptable — the bands are wide enough that the boundary cases are
negligible in practice. The continuous distance tiebreaker ensures that
within the same band, closer still beats farther.

---

## Estimated effort

- SQL expression change: 30 minutes
- ORDER BY builder update: 15 minutes
- Tests: 1 hour
- Total: ~2 hours

---

## Acceptance criteria

1. Two services in the same distance band: verified sorts before unverified
2. Service in band 0 sorts before service in band 1, regardless of freshness
3. Within same band and freshness, closer service sorts first (tiebreaker)
4. Non-proximity queries unaffected (no distance → freshness only)
5. Population boosts still outrank distance bands
6. Python open-now sort still runs after SQL (open > closed > unknown)
7. All existing tests pass
