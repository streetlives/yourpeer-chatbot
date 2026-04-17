# Drift Detection

Automated checks that catch divergences between the chatbot's query logic and the YourPeer production app / Streetlives database before they become user-facing bugs.

**Why this exists:** The chatbot bypasses the YourPeer REST API and queries the database directly. This means our query logic — taxonomy lists, eligibility filtering, sort order — can silently diverge from what users see on yourpeer.nyc. This system was created after the Covenant House bug (April 2026), where a 19-year-old searching for shelter couldn't see Covenant House because our code added the "Youth" taxonomy only for users under 18, while Covenant House serves ages 16–24. That rule was invented by the chatbot and never validated against YourPeer or DYCD policy.

---

## Quick Start

```bash
# Run all checks locally
DATABASE_URL=postgresql://user:pass@host:5432/streetlives \
  python scripts/drift_check.py

# Run a single check
python scripts/drift_check.py taxonomy_drift

# CI mode — exit code 1 on any failure
python scripts/drift_check.py --ci

# JSON output for programmatic consumption
python scripts/drift_check.py --json
```

## Requirements

| Check | Needs DB? | Needs network? | Dependencies |
|-------|-----------|----------------|--------------|
| `taxonomy_drift` | Yes | No | `sqlalchemy`, `psycopg2-binary` |
| `source_parity` | No | Yes (GitHub raw) | None (stdlib `urllib`) |
| `result_parity` | Yes | Yes (Streetlives API) | `sqlalchemy`, `psycopg2-binary` |
| `business_rules` | Yes | No | `sqlalchemy`, `psycopg2-binary` |

Install dependencies:

```bash
pip install sqlalchemy psycopg2-binary
```

Set the `DATABASE_URL` environment variable for checks that need DB access:

```bash
export DATABASE_URL=postgresql://user:password@host:5432/streetlives
```

Use the **non-PROD** endpoint for testing and the **PROD** endpoint for validation, per the Streetlives API docs.

---

## The Four Checks

### 1. Taxonomy Drift (`taxonomy_drift`)

**What it does:** Compares the chatbot's hardcoded `taxonomy_names` lists (in `query_templates.py`) against the actual taxonomy tree in the Streetlives database.

**What it catches:**

- **Stale references** — our template lists a taxonomy name that was renamed or deleted in the DB. Our SQL `LOWER(t.name) = ANY(:taxonomy_names)` silently matches nothing for that entry, and results quietly shrink.
- **Missing children** — a new child taxonomy was added under a parent we track (e.g., a new food sub-type under "Food"). YourPeer auto-includes it via parent taxonomy ID expansion; our chatbot doesn't know about it until the template is updated.

**How it works:**

1. Queries the DB: `SELECT t.id, t.name, t.parent_name FROM taxonomies t`
2. For each chatbot template, checks that every listed taxonomy name exists in the DB
3. For each parent taxonomy in our list, checks whether the DB has child taxonomies we don't list

**Example output:**

```
✅ All template taxonomy names exist in DB
⚠️ [MEDIUM] food: DB has children of 'food' not in template: {'community garden'}.
   YourPeer includes them automatically via parent taxonomy expansion; our chatbot does not.
```

**When it fails:** Update the template's `taxonomy_names` list in `query_templates.py` and add the new taxonomy. Also update `VALID_DB_TAXONOMY_NAMES` in `test_query_templates.py`.

---

### 2. Source Parity (`source_parity`)

**What it does:** Fetches YourPeer's source code from GitHub and parses key constants to detect when YourPeer's category structure or taxonomy mapping changes.

**What it catches:**

- **New categories** — YourPeer adds a service category we don't have
- **New sub-filters** — YourPeer adds a shelter sub-filter (e.g., "Veterans"), a food sub-type, or a health sub-category we don't map
- **Taxonomy mapping changes** — a category starts pointing to a different parent taxonomy

**How it works:**

1. Fetches `https://raw.githubusercontent.com/streetlives/yourpeer.nyc/main/src/components/common.ts`
2. Parses `CATEGORY_TO_TAXONOMY_NAME_MAP` to extract category → taxonomy mappings
3. Parses `SHELTER_PARAM_*_VALUE`, `FOOD_PARAM_*_VALUE`, etc. to detect sub-filters
4. Compares against our known mapping

**No DB access required.** Only needs HTTPS access to `raw.githubusercontent.com`.

**Example output:**

```
Found 7 YourPeer categories
Found 3 YourPeer shelter sub-filters: ['FAMILY', 'SINGLE', 'YOUTH']
✅ No new YourPeer categories or sub-filters detected
```

**When it fails:** Review the YourPeer change, decide whether it's relevant to the chatbot, and update our mapping or templates accordingly. Document the decision in `QUERY_PARITY_AUDIT.md`.

---

### 3. Result Parity (`result_parity`)

**What it does:** Runs the same search through both our SQL pipeline and the Streetlives REST API, then compares which location IDs are returned.

**What it catches:** Any divergence in results, regardless of cause — taxonomy differences, filtering differences, eligibility logic differences, hidden/visible status differences.

**How it works:**

1. Fetches taxonomy IDs from the Streetlives `/taxonomy` endpoint
2. For each canonical category (food, shelter, clothing, personal care):
   - Queries our DB with the chatbot's SQL template
   - Queries the Streetlives API with the equivalent taxonomy ID
   - Compares the resulting location ID sets
3. Reports locations that appear in one system but not the other

**This is the most expensive check.** It hits both the database and the production API. Disabled by default in the CI workflow — enable for periodic deep audits.

**Example output:**

```
✅ food: 185 locations match exactly
⚠️ shelter: 3 locations in API but not chatbot, 12 in chatbot but not API
```

**When it fails:** The extra locations in our chatbot results are usually from our broader taxonomy lists (we include child taxonomies the API handles via parent expansion). Locations only in the API suggest a taxonomy we're missing. Investigate both directions.

---

### 4. Business Rule Validation (`business_rules`)

**What it does:** Verifies specific assumptions the chatbot makes about the database structure — the assumptions that, if wrong, cause bugs like the Covenant House issue.

**Five sub-checks:**

| Sub-check | What it verifies | Why it matters |
|-----------|-----------------|---------------|
| Youth-not-Shelter | Are locations tagged "Youth" also tagged "Shelter"? | If not, they're only visible because we always include "youth" in our taxonomy list. Without that fix, they'd disappear. |
| Age eligibility ranges | What age ranges exist in shelter eligibility data? | Our code doesn't enforce age thresholds anymore, but this data reveals what the DB actually contains — useful for validating that the API's eligibility filtering is correct. |
| DV taxonomy tags | Are Safe Horizon locations tagged "Crisis"? | Our DV shelter enrichment adds "crisis" and "drop-in center" to the taxonomy list. If Safe Horizon isn't actually tagged with either, the enrichment has no effect. |
| Key location tags | How are Covenant House and Ali Forney tagged? | If they're tagged "Youth" but not "Shelter", they're only visible because of our always-include-youth fix. This validates that the fix is still necessary. |
| Description pattern effectiveness | Do our 85 regex patterns match any services? | A pattern like `dental|dentist` is useless if no health-tagged service has "dental" in its description. This catches dead patterns. |

**Example output:**

```
⚠️ 3 locations tagged 'Youth' but NOT 'Shelter': ['Covenant House', 'Ali Forney Center', ...]
✅ Safe Horizon - Streetwork Project: ['Crisis', 'Drop-in Center', 'Youth']
✅ 'dental care filter': 12 matching locations
⚠️ 'detox filter': 0 matches — pattern may be ineffective
```

**When it fails:** Each sub-check failure has a different resolution. Youth-not-Shelter failures confirm our always-include-youth fix is still needed. DV tag failures mean we should verify whether Safe Horizon's taxonomy changed. Empty pattern failures mean a description filter is dead code — either the pattern is wrong or no services have that description text.

---

## CI / GitHub Actions

The workflow file (`scripts/drift-check.yml`) runs three checks weekly:

```yaml
on:
  schedule:
    - cron: '0 9 * * 1'  # Monday 9am UTC
  workflow_dispatch:       # Manual trigger
```

**Checks run in CI:**

| Check | Runs in CI? | Reason |
|-------|-------------|--------|
| `taxonomy_drift` | ✅ Yes | Fast, catches the most common drift |
| `source_parity` | ✅ Yes | Fast, no DB needed, catches YourPeer changes |
| `business_rules` | ✅ Yes | Validates our key assumptions |
| `result_parity` | ❌ Commented out | Expensive, hits production API — enable for quarterly audits |

**Setup:**

1. Copy `scripts/drift-check.yml` to `.github/workflows/drift-check.yml`
2. Add `DATABASE_URL` as a GitHub Actions secret (Settings → Secrets → Actions)
3. Optionally add `SLACK_WEBHOOK_URL` for failure notifications

**Slack notifications:** On any check failure, the workflow posts to Slack via webhook. Remove the Slack step if you don't use Slack.

---

## Interpreting Results

### Status codes

| Status | Meaning | CI exit code |
|--------|---------|--------------|
| `PASS` | All sub-checks passed | 0 |
| `FAIL` | One or more issues detected | 1 (with `--ci`) |
| `SKIP` | Check couldn't run (no DB, no network) | 0 |
| `ERROR` | Check crashed unexpectedly | 1 (with `--ci`) |

### Severity levels

| Severity | Meaning | Action |
|----------|---------|--------|
| `HIGH` | Results are wrong — users see incorrect data | Fix immediately |
| `MEDIUM` | Results may be incomplete — some services invisible | Fix before next release |
| `LOW` | Minor inconsistency — no user impact yet | Track and fix when convenient |

### JSON output

Use `--json` for programmatic consumption:

```bash
python scripts/drift_check.py --json 2>/dev/null | jq '.taxonomy_drift.status'
```

```json
{
  "taxonomy_drift": {
    "status": "PASS",
    "issues": []
  },
  "source_parity": {
    "status": "FAIL",
    "issues": [
      {
        "type": "NEW_SHELTER_PARAM",
        "severity": "HIGH",
        "detail": "YourPeer has new shelter sub-filters: {'VETERANS'}"
      }
    ]
  }
}
```

---

## Adding New Checks

To add a new drift check:

1. Write a function in `scripts/drift_check.py` that returns `{"status": "PASS"|"FAIL", "issues": [...]}`
2. Add it to the `ALL_CHECKS` dict at the bottom of the file
3. Add it to the GitHub Actions workflow if it should run in CI

Each check should be independent — it should work even if other checks fail or are skipped.

---

## Related Documents

- [QUERY_PARITY_AUDIT.md](../docs/QUERY_PARITY_AUDIT.md) — full comparison of chatbot vs YourPeer query logic, with every divergence cataloged
- [FEATURES.md](../docs/FEATURES.md) — chatbot feature reference including taxonomy narrowing and description filtering
- [test_narrowing.py](../tests/test_narrowing.py) — 166 unit tests for the narrowing system
