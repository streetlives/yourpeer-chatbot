# Upstream Issue Draft — streetlives/streetlives-api

**Purpose**: Draft text for filing an issue against
https://github.com/streetlives/streetlives-api to propose adding
coordinate-derived borough validation to the Streetlives schema.

**Context**: During work on a pilot chat interface over the Streetlives
API, we discovered that `physical_addresses` has no `borough` column and
that `pa.city` has enough data-quality inconsistencies to produce
noticeable user-facing bugs. This doc contains the issue body ready to
paste into GitHub, plus notes on how to file it.

**Status**: Ready to submit. See "How to file" at the bottom.

---

## Title

Data quality: `physical_addresses.city` contains borough mismatches; proposal to add coordinate-derived borough

## Labels (if write access)

`enhancement`, `data-quality` (create if missing)

## Body

### Summary

We've been building a chat interface on top of the Streetlives API and
discovered a data-quality pattern worth flagging: some rows in
`physical_addresses` have `city` values that don't agree with the
physical location of `l.position`. For example, a service with
`pa.city = 'New York'` (which implies Manhattan) whose coordinates are
actually in the Bronx. End users see these as "Manhattan" results that
are clearly not in Manhattan.

We mitigated this on our side by vendoring the NYC DCP Borough Boundaries
polygon file and deriving a `geographic_borough` from `l.position` using
shapely. But the right fix feels like it belongs upstream in
`streetlives-api` so every downstream consumer benefits — including
YourPeer and GoGetta, which both rely on the same city-based filtering.

### Symptoms

Two downstream patterns we observed:

1. **Wrong-borough results**: a service with `pa.city = 'New York'` shows
   up in a Manhattan search, but its actual address and `l.position`
   place it in the Bronx. Confusing for users who expect location
   filtering to work.

2. **Casing / typo variance on `pa.city`**: we've seen `'BRONX'`,
   `'Bronx'`, `'The Bronx'`, and (less commonly) regional names like
   `'Astoria'` or `'Far Rockaway'` as city values for what are
   all legally Bronx/Queens addresses. Borough-level queries work by
   building a list of known city variants per borough (`city = ANY([...])`),
   which is brittle.

### Root cause

`physical_addresses` has no `borough` column (confirmed via
`information_schema.columns`), so callers can only filter by `pa.city` or
by spatial containment against `l.position`. `pa.city` is human-entered
and not validated against the coordinates, so mismatches accumulate
whenever:

- An SSTT volunteer typed the wrong city
- An org HQ'd in one borough has a service location in another, and the
  data was inherited from the parent record
- Address geocoding at data-entry time picked the wrong candidate (NYC
  has many duplicate street names across boroughs)

### Suggested fix (two options, not mutually exclusive)

**Option A — Derived column / view on read**

Add a `borough` column or materialized view on `physical_addresses`
populated from PostGIS containment against the five NYC borough
polygons (NYC Open Data dataset `gthc-hcne`, water-clipped). Something
like:

```sql
ALTER TABLE physical_addresses
ADD COLUMN borough TEXT GENERATED ALWAYS AS (
    (SELECT b.name
     FROM nyc_boroughs b
     JOIN locations l ON l.id = physical_addresses.location_id
     WHERE ST_Within(l.position::geometry, b.geometry)
     LIMIT 1)
) STORED;
```

(The exact shape depends on your preferred pattern — generated column,
trigger, view, or a join table. Happy to draft any of them.)

This would give every consumer a trustworthy borough value derived from
coordinates, not from self-report. Callers could then use
`WHERE pa.borough = 'Manhattan'` cleanly, instead of the `city_list`
workaround that YourPeer and we both use today.

**Option B — Validate at write time via NYC GeoSearch**

In the SSTT write path (wherever new / edited addresses land), call
NYC Planning Labs' free reverse-geocode API
(`https://geosearch.planninglabs.nyc/v1/reverse?point.lat=...&point.lon=...`)
after an address is geocoded and reject or flag the record when the
returned borough disagrees with the entered city. This catches the
error at source rather than fixing it on read.

GeoSearch is the official NYC DCP geocoder, free, no API key, and used
by most NYC government web apps.

### What we built as mitigation

Since we need this working before a fix could land upstream, we added
client-side validation on our end: vendored the borough polygons, load
them into a shapely STRtree at startup, and check every returned service
against the polygon for its coordinates. Mismatches are logged with the
service ID so our Community Information Specialists can triage. We're
not filtering mismatched results from users yet — just annotating and
collecting frequency data.

Full write-up:
[docs/BOUNDARY_AUDIT.md](LINK-TO-OUR-REPO/docs/BOUNDARY_AUDIT.md)

### How we'd like to help

Happy to open a PR against `streetlives-api` with either Option A
(`ALTER TABLE` migration + any index/trigger needed) or Option B
(GeoSearch call in the SSTT write path). Let us know which approach
fits your roadmap better and we'll draft it. If there's an approach
you'd prefer we haven't mentioned, we're open — we don't have strong
preferences beyond wanting the fix to land *somewhere* in the stack of
record.

### Supporting evidence

We can share (privately if preferred, to avoid exposing specific
service IDs in a public issue):

- Query output from `information_schema.columns` confirming
  `physical_addresses` has no `borough` column
- Sample of service records where the geographic borough (derived from
  `l.position`) disagrees with the `pa.city` value
- Our own mismatch rates from a week of production logs

---

## How to file

1. **Sanity-check the framing**: we're proposing, not complaining. The
   maintainers are running this on volunteer/nonprofit time; make the
   ask easy to accept or easy to defer. If the tone above feels off,
   adjust before submitting.

2. **Substitute the placeholders**:
   - `LINK-TO-OUR-REPO/docs/BOUNDARY_AUDIT.md` — point to wherever this
     repo gets published / shared (or attach the PDF if keeping it private)

3. **Attach evidence carefully**: don't include specific `service_id`
   values in the public issue body. Either redact before posting or
   offer to share privately (the closing section already does this).

4. **Submit at**: https://github.com/streetlives/streetlives-api/issues/new

5. **Watch for**: the repo has a small active maintainer set (adambard1
   and doobneek1 per the issue list on yourpeer.nyc). Expect async
   response over days, not hours. If no reply in ~2 weeks, a gentle
   nudge comment is fine.

6. **Ready to PR**: if the team accepts Option A, the migration is
   roughly:
   - Create `nyc_boroughs` table/table-or-view with the polygons
   - Add the generated borough column on `physical_addresses` (or a
     trigger-maintained column if they prefer)
   - Backfill for existing rows
   - Add a tests/integration fixture covering the Yankee-Stadium-with-
     `pa.city='New York'` case so the regression is guarded

   If they accept Option B, the PR is even smaller — a helper around
   the GeoSearch reverse-geocode API and a validation hook in whichever
   SSTT write path creates or edits physical_addresses rows.

## Related reading for the reviewer

- NYC Planning Labs GeoSearch: https://geosearch.planninglabs.nyc/
- NYC Open Data Borough Boundaries (gthc-hcne):
  https://data.cityofnewyork.us/City-Government/Borough-Boundaries/gthc-hcne
- Our BOUNDARY_AUDIT.md has the full context, the
  coordinate-swap / proximity-leak / neighborhood-coverage gaps we
  found, and the suggested NTA-based neighborhood expansion for future
  work.
