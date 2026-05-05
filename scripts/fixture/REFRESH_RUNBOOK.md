# Foundation 8: Fixture Refresh Runbook

**Last refresh:** May 5, 2026 — produced 277 rows, all required pins ✅, all cohort thresholds met.
**Audience:** Streetlives data team or whoever has DB read access for fixture refresh.

## Why this exists

The eval fixture at `tests/eval/fixtures/services.json` mocks production's `query_services` so eval runs are hermetic. Earlier versions of the fixture (218 rows, 5-per-bucket extraction) were severely under-covered for population-specific cohorts:

| Cohort | Pre-refresh (218 rows) | After May 5 refresh (277 rows) |
|---|---|---|
| Families-tagged shelter rows | 0 across all boroughs | 1 (Manhattan — DHS AFIC) |
| LGBTQ Young Adult rows | 0 | 1 (Manhattan — Ali Forney Center) |
| Youth-tagged rows | 0 | 1 (Manhattan — Covenant House Young Adult Services) |
| Drop-in Center rows | 1 (Manhattan only) | 5 (Manhattan, Brooklyn, Queens, Staten Island) |
| Substance Use Treatment | 3 (M, Q, SI) | 7 (Manhattan ×2, Brooklyn ×2, Queens, SI) |

Before refresh, **15 of 19 named providers** from the Cornell sample-queries doc were missing. After refresh, all 6 required pins are present plus 8 of the 13 nice-to-have pins.

This runbook describes the May 5 refresh process so it can be repeated quarterly or whenever upstream data changes meaningfully.

## What's in `scripts/fixture/`

- **`04_extract_fixture_hybrid.sql`** — canonical extraction. Three-strategy UNION: bucket (5 per service_type × borough), pinned (named providers from Cornell + R41), cohort (≥1 row per service_type × borough × population_cohort). Successor to `_q3_clean.sql`.
- **`04_extract_fixture_hybrid_cast.sql`** — DBeaver-immune variant. Identical except uses `CAST(expr AS type)` instead of `expr::type`. Use this if DBeaver settings can't be changed (see "DBeaver gotchas" below).
- **`05_diagnostic_cohort_coverage.sql`** — run BEFORE the extract to see what's available in the source DB. Surfaces upstream-data gaps before they show up as eval failures.
- **`normalize_dbeaver_export.py`** — converts the raw DBeaver JSON export (which uses Postgres array literal strings like `'{a,"b c"}'`) into proper JSON arrays. Required after every DBeaver export.
- **`verify_refresh.py`** — post-refresh sanity check. Validates cohort coverage and required pinned providers. Exits non-zero if anything's missing. Run this before committing the new fixture.

## Step-by-step refresh

### Step 1 — set DBeaver up correctly (one-time)

DBeaver has two settings that wreck this SQL out of the box. Fix them once:

**a. Disable variable substitution** (else `::jsonb` and `::geometry` get parsed as named-parameter bindings).

- Window → Preferences → Editors → SQL Editor → SQL Processing
- Uncheck **"Enable SQL parameters"**
- Uncheck **"Anonymous SQL parameters"**
- Click Apply and Close

**b. Disable blank-line statement delimiter** (else CTE chains separated by blank lines get split into multiple statements).

- Same screen.
- **"Blank line is statement delimiter"** dropdown → change from "Always" to **Never**

If you can't or won't change these, run `04_extract_fixture_hybrid_cast.sql` instead — it sidesteps both issues.

### Step 2 — run the diagnostic

Open `05_diagnostic_cohort_coverage.sql` in DBeaver, connect to the Streetlives production read-replica, execute.

Expected output:
- **Cohort coverage matrix:** ~25 rows shaped `(cohort_name, service_type, borough, count, status)`.
- Cells with `status='GAP'` are upstream-data gaps. Flag to the data team if a critical cohort is missing — for example, if `families` shows 0 across all boroughs, the Families cohort still won't appear in the fixture even after refresh.

The May 5 diagnostic showed the source DB has thin coverage for families, youth, and lgbtq_young_adult (Manhattan-only). That's a known production data limitation, not a script bug.

### Step 3 — run the extract

Open `04_extract_fixture_hybrid.sql` (or the `_cast.sql` variant) in DBeaver, execute.

Expected: ~270-450 rows depending on what production has. The May 5 run produced **277 rows**.

### Step 4 — export to JSON

Right-click on the result grid → **Export Data** → JSON.

Save as `services_raw.json` (do NOT save directly as `services.json` — there's a normalization step).

### Step 5 — normalize the export

```bash
python3 scripts/fixture/normalize_dbeaver_export.py \
    services_raw.json \
    tests/eval/fixtures/services.json
```

Expected output:
```
Loaded 277 rows from services_raw.json

Converted Postgres-array strings → JSON arrays:
  service_taxonomies      237 rows converted
  also_available          181 rows converted
  languages_spoken        189 rows converted

Wrote tests/eval/fixtures/services.json (277 rows)
```

The script does two things:
- Unwraps DBeaver's "JSON-keyed-by-SQL-text" wrapping. DBeaver sometimes wraps the result set in `{<huge_sql_text>: [...]}` instead of returning a bare array. The script handles both.
- Converts Postgres array literals (`'{Employment,"Mental Health"}'`) to proper JSON arrays (`["Employment", "Mental Health"]`).

### Step 6 — verify

```bash
python3 scripts/fixture/verify_refresh.py
```

Expected: `✅ FIXTURE VERIFICATION PASSED (277 rows)` with exit code 0.

If verification fails:

- **Required pin missing:** the data team probably hasn't ingested that provider yet. Check the diagnostic from step 2. If it's there in production but missing from your fixture, the pin pattern in the SQL may need updating (May 5 surfaced this with Safe Horizon Streetwork — pattern was matching `location_name` only, fixed to also match `organization_name`).
- **Cohort below threshold:** check the diagnostic. If production has 0 rows for that cohort, this is upstream — file with the data team and don't ship the fixture until resolved (or downgrade the threshold in the verifier with a clear comment explaining why).

### Step 7 — run unit tests

```bash
python3 -m pytest tests/unit/ -q
```

Expected: ~3870 passing, zero regressions. The dispatcher tests are baseline-relative so they survive fixture refresh, but if you've changed test assertions hardcoded to old row counts elsewhere, they'll fail here.

## Required pinned providers

The verifier hard-fails if any of these are missing. They're the providers that eval scenarios reference by name and the bot is expected to surface for the relevant queries:

| Pin | Provider | Required for |
|---|---|---|
| covenant_house | Covenant House | Family/youth shelter scenarios |
| ali_forney | Ali Forney Center | LGBTQ youth shelter (cluster 3) |
| make_the_road | Make the Road NY | Immigration scenarios |
| safe_horizon_streetwork | Safe Horizon Streetwork Project | DV/youth drop-in |
| dhs_family_intake | DHS family intake (PATH or AFIC) | Emergency family shelter |
| msbi_addiction | Mt Sinai Beth Israel Addiction Institute | Detox scenarios |

**About PATH vs AFIC:** Streetlives production data may have either Adult Family Intake Center (AFIC) or Prevention Assistance and Temporary Housing (PATH) — both are DHS family intake centers and operationally equivalent for eval purposes. The pin pattern accepts either. The May 5 refresh found AFIC; PATH may be ingested in a future data update.

## Cohort thresholds (calibrated May 5)

The verifier uses per-cohort thresholds based on what production data realistically supports:

| Cohort | Min boroughs |
|---|---|
| drop_in_center | ≥3 of 5 |
| substance_use_treatment | ≥3 of 5 |
| families | ≥1 of 5 |
| youth | ≥1 of 5 |
| lgbtq_young_adult | ≥1 of 5 |

The lower thresholds for families/youth/lgbtq_young_adult reflect that those populations have thin coverage in the source DB. Production has Ali Forney Center (LGBTQ) in Manhattan but no equivalent provider currently ingested for the other boroughs. Same for Families (only DHS AFIC is tagged) and Youth (only Covenant House Young Adult Services is tagged). Better data ingestion would change this — adjust thresholds upward when it does.

## DBeaver gotchas (lessons from May 5)

These tripped us up the first time. Documented here so they don't trip the next person.

### "Syntax error near 'combined'" at a position that doesn't match the file

DBeaver was misparsing `::jsonb` and `::geometry` casts as named-parameter bindings (`:` followed by `:jsonb`). The query got mangled, the parser failed, and DBeaver reported a position from a partially-mangled internal buffer. The error message pointed at `combined` because that was the next clean SQL token to resync on.

**Fix:** disable variable substitution (Step 1a above), or use `04_extract_fixture_hybrid_cast.sql`.

### `service_taxonomies` came out as the string `'{Employment,"Mental Health"}'`

Postgres `text[]` columns serialize as their text representation in DBeaver's JSON export, not as JSON arrays. The cohort filter scanned characters of the string instead of array elements.

**Fix:** run `normalize_dbeaver_export.py` (Step 5 above). Always.

### Result set keyed by the entire SQL query text

DBeaver names result sets after the SQL when there's no explicit alias. We got `{<huge_sql_string>: [...rows...]}` instead of `[...rows...]`.

**Fix:** also handled by `normalize_dbeaver_export.py`.

### Pin patterns matched location/service name only, missed organization name

May 5 first run had Safe Horizon Streetwork Project as the **organization_name** but the pin pattern only checked `location_name LIKE '%streetwork%'`. The location was `Uptown/Harlem DYCD Youth Drop-in Center` — no "streetwork" in it. Pin missed despite the data being there.

**Fix:** the pin pattern in `04_extract_fixture_hybrid.sql` now checks all three of organization_name, location_name, and service_name. Same lesson applied to DHS pin (now matches PATH or AFIC).

## Predicted R42 impact

R41 had 15 critical failures. With cluster 1 + this fixture refresh shipped:

| Scenario | R41 status | Refresh impact |
|---|---|---|
| `peer_young_mom_multiple_needs` | failing 3.91 | ✅ Now returns Adult Families Intake + Young Adult Services |
| `peer_lgbtq_youth_shelter_soho` | passing 4.27, CF | ✅ Ali Forney now in fixture |
| `multi_lgbtq_youth_ali_forney` | passing 4.00, CF | ✅ Ali Forney now in fixture |
| `multi_family_with_children_path` | passing 4.36, CF | ✅ DHS family intake row now available |
| `peer_detox_manhattan` (R40 holdover) | failing 3.64 | ✅ Mt Sinai Beth Israel + Realization + Project Renewal now in fixture |
| `peer_free_id_manhattan` (R40 holdover) | failing 3.09 | ✅ IDNYC now in fixture (5 rows) |

Combined with cluster 1 (eligibility filter), expect R42 to resolve **6-8 of the 15 R41 critical failures** at the data layer. Remaining failures (location precision, service-detail mismatch, urgency-resource handling) are clusters 2/4/5 — separate work.

## Known limitations

1. **Pin patterns are name-pattern matches.** "Cabrini Immigrant" hits `Cabrini Immigrant Services` today; if that org renames, the pin breaks silently. Review the pattern list quarterly against the Cornell sample-queries doc.
2. **Cohort coverage is upstream-data-bounded.** If Streetlives' DB doesn't have a Families-tagged shelter in the Bronx, the fixture won't either. The diagnostic exposes this. Fix is data ingestion, not script.
3. **Larger fixture, slightly slower mock bootstrap.** 277 rows vs. 218: ~2-3ms additional bootstrap per eval test. Negligible compared to the coverage gain.
4. **Service-detail filter still permissive.** Cluster 5 (sub-category mismatch) needs both data work AND a stricter mock filter. The data side is partially addressed by this refresh; the mock side remains.
5. **DYCD Youth Drop-in providers don't exist as a separate org.** They're operated by Safe Horizon Streetwork at locations named "DYCD Youth Drop-in Center". The Safe Horizon pin already covers this case, but the verifier's `dycd_youth_drop_in` pin shows 0 — that's expected and not a fixture failure.
6. **doobneek isn't in source DB.** Cornell sample queries reference it (a peer-built financial-advice tool) but it's not a Streetlives-listed organization. Pin shows 0; this is correct.

## Refresh checklist

```
[ ] DBeaver settings: Enable SQL parameters OFF, Blank line delimiter set to Never
[ ] Run 05_diagnostic_cohort_coverage.sql, eyeball gaps, flag any new ones
[ ] Run 04_extract_fixture_hybrid.sql (or _cast.sql if settings can't change)
[ ] Export → JSON, save as services_raw.json
[ ] python3 scripts/fixture/normalize_dbeaver_export.py services_raw.json tests/eval/fixtures/services.json
[ ] python3 scripts/fixture/verify_refresh.py  → expect exit 0
[ ] python3 -m pytest tests/unit/ -q  → expect 3870 passing
[ ] git diff tests/eval/fixtures/services.json → eyeball for sanity
[ ] Commit, PR
```
