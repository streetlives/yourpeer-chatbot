# Database Audit Script

`scripts/db_audit.py` is a standalone diagnostic tool that validates the YourPeer chatbot's query logic against the actual Streetlives database. It can be run against any environment (staging or production) and exits with a non-zero code if anything requires attention.

---

## Why This Exists

The chatbot's query templates are hardcoded — they filter on specific taxonomy names, rely on a borough column, and make assumptions about eligibility data structure. If the database content diverges from those assumptions, services silently disappear from search results with no error.

We discovered this the hard way during staging: the ClothingQuery was matching only 18 "Clothing" services while 84 "Clothing Pantry" services went entirely unmatched, causing zero results for clothing searches in Queens. The FoodQuery was missing 732 "Food Pantry" and 180 "Soup Kitchen" services — a 12x gap in coverage — because the template only matched the literal string "Food".

This script exists so those kinds of gaps are caught proactively, not by a user reporting zero results.

---

## What It Checks

| Section | What It Validates |
|---|---|
| `taxonomy` | Every taxonomy name in the DB is matched by at least one query template. Flags names not seen in staging. |
| `borough` | Service counts by borough and category. Flags borough + category combos with fewer than 5 services (high no-result risk). Validates that the chatbot's nearby-borough suggestions still reflect actual service availability. |
| `hidden` | How many services are excluded by `FILTER_NOT_HIDDEN`. Flags any taxonomy where >10% of services are hidden. |
| `schedule` | Schedule data coverage by taxonomy. Flags if coverage has improved enough to justify re-enabling the open-now filter (currently dormant because most categories have 0% schedule data). |
| `phone` | Phone number coverage by taxonomy. Flags categories where the Call button will be absent for >20% of services. |
| `eligibility` | Eligibility rule types and how many services each applies to. Flags any new parameter types not handled by the application. |
| `freshness` | Distribution of `last_validated_at` across locations. Flags if fewer than 80% of locations were validated within the last 90 days (our METRICS.md target). |
| `description` | Description field coverage by taxonomy. Informational — high absence is expected and already handled gracefully by the card UI. |
| `names` | Checks for null, empty, or generic service names. Flags long names that may affect card layout. |
| `membership` | Distribution of membership eligibility values. Verifies the "Referral may be required" badge will appear on the right number of services. |

---

## Usage

### Prerequisites

```bash
pip install sqlalchemy psycopg2-binary
```

### Run all sections

```bash
DATABASE_URL=postgresql://user:pass@host/dbname python scripts/db_audit.py
```

### Run a single section

```bash
DATABASE_URL=postgresql://... python scripts/db_audit.py --section taxonomy
DATABASE_URL=postgresql://... python scripts/db_audit.py --section borough
DATABASE_URL=postgresql://... python scripts/db_audit.py --section schedule
```

### Save a report

```bash
DATABASE_URL=postgresql://... python scripts/db_audit.py --output audit_report.md
```

Output is written to both stdout and the file.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed — no action items |
| `1` | One or more action items found — review output before deploying |

This means the script can be used as a pre-deploy gate in CI:

```bash
DATABASE_URL=$PROD_DATABASE_URL python scripts/db_audit.py || exit 1
```

---

## When to Run It

### Before every production deploy

Run `--section taxonomy` at minimum. New taxonomy names in the DB are the highest-risk gap — a new name like `"Food Closet"` under food would silently go unmatched until someone notices zero results. This takes under 10 seconds.

### When connecting to a new database

Run all sections. The current application logic was validated against staging data (April 2026). Production may have a different service distribution, taxonomy vocabulary, eligibility structure, or freshness profile. In particular:

- `--section taxonomy` — may reveal new names requiring template updates
- `--section borough` — validates the no-result borough suggestions in `backend/app/services/confirmation.py`
- `--section schedule` — determines whether the open-now filter can be safely enabled
- `--section freshness` — establishes the production freshness baseline against our ≥80% target

### After a data import or partner update

Run `--section taxonomy` and `--section borough`. Bulk data imports often introduce new taxonomy names or shift the geographic distribution of services.

### Monthly during the pilot

Run all sections as part of the weekly data steward review cadence. Paste the output into the pilot review doc. Any action items from `taxonomy` or `borough` should be addressed before the next weekly review.

### When no-result rates spike in the admin console

If the Metrics tab shows a sudden increase in the no-result rate, run `--section taxonomy` and `--section hidden` first. The most common causes are a new taxonomy name going unmatched, or a batch of services being hidden from search.

---

## Interpreting Results

### Taxonomy section

```
✓  Food Pantry                                   732 services
✓  Soup Kitchen                                  180 services
✗  Food Closet                                    24 services  [NEW]
```

A `✗` means the taxonomy name isn't matched by any template. A `[NEW]` tag means it wasn't present in the staging DB audit. Any `✗` with ≥5 services is flagged as an action item.

**Fix:** Add the name (lowercased) to the appropriate template's `taxonomy_names` list in `backend/app/rag/query_templates.py`, and add the display name to the corresponding `taxonomy_aliases` list. Then add it to `VALID_DB_TAXONOMY_NAMES` and `EXPECTED_TAXONOMY_NAMES` in `tests/unit/test_query_templates.py`.

### Borough section

A table like:

```
taxonomy        borough         services
─────────────   ─────────────   ────────
Shower          Manhattan       14
Shower          Bronx           5
Shower          Queens          4
Shower          Staten Island   2
Shower          Brooklyn        2
```

If the production distribution differs significantly from staging — for example, if Brooklyn now has 20 shower services — update `_NEARBY_BOROUGHS_BY_SERVICE` in `backend/app/services/phrase_lists.py` to reflect the new ordering.

### Schedule section

If schedule coverage has improved above 60% for a high-volume category, the script will flag it as a candidate for re-enabling `FILTER_BY_OPEN_NOW`. Before enabling it, verify the improvement is real (not a data entry artifact) and test with the canary suite.

### Freshness section

The ≥80% target is defined in `METRICS.md` section 2.3. If production freshness is below this threshold, it's a data quality issue for the partner organization rather than a code problem — but it should be flagged to data stewards.

---

## Keeping the Script Current

The script contains two hardcoded reference sets that need updating after each audit:

**`KNOWN_TAXONOMY_NAMES`** — the set of all taxonomy names seen in previous audits. When a new name is confirmed as valid and added to a template, add it here so it stops being flagged as `[NEW]` in future runs.

**`TEMPLATE_TAXONOMY_NAMES`** — mirrors the `taxonomy_names` lists in `query_templates.py`. Keep these in sync whenever a template is updated. If they drift, the script's matching logic will produce false positives or miss gaps.

Both sets live at the top of `scripts/db_audit.py`, clearly labeled.
