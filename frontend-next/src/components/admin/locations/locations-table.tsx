// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useMemo, useState } from "react";
import { ExternalLink, AlertCircle, AlertTriangle } from "lucide-react";
import { Tooltip } from "@/components/admin/tooltip";
import {
  TablePagination,
  DEFAULT_PAGE_SIZE,
  type PageSize,
} from "@/components/admin/table-pagination";
import { TableSkeleton } from "@/components/admin/loading-skeleton";
import type {
  LocationsListResponse,
  LocationsListRow,
  LocationsListParams,
  LocationsSortKey,
  LocationsAgeBucket,
  BoroughLabel,
  LocationCategoriesResponse,
} from "@/lib/admin/locations-types";
import { isAdminApiError } from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/hooks/use-admin-fetch";
import {
  defaultSortDirForField,
  cycleSortDirForField,
} from "@/lib/admin/locations-sort-cycle";

/**
 * Section 2b — paginated, sortable, filterable triage table.
 *
 * Owns its own page/sort/filter state. Re-fetches the backend
 * /api/admin/locations/list endpoint on every parameter change.
 * This is correct (the backend's where-clause-builder needs to
 * see the current filter set) and cheap (the endpoint is cached
 * server-side and the result set is bounded to page_size).
 *
 * Default sort: last_validated_at ASC NULLS FIRST — surfaces
 * never-verified rows at the top of the queue. Matches the spec's
 * triage-first answer to question 2.
 *
 * `ageBucket` is a controlled prop (parent owns the value, passes
 * it down + a setter). This is what lets the page's freshness
 * histogram drive the table filter on bar click. Other filters
 * stay table-local since nothing outside drives them.
 *
 * The row-level "issue badges" (missing phone / address / hours,
 * recent flags) are derived from booleans returned by the backend.
 * No client-side calculation; all logic lives in
 * `aggregations.get_locations_list`.
 */
export function LocationsTable({
  ageBucket = "",
  onAgeBucketChange,
}: {
  ageBucket?: LocationsAgeBucket | "";
  onAgeBucketChange?: (next: LocationsAgeBucket | "") => void;
} = {}) {
  // ---------------- table state ----------------
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(DEFAULT_PAGE_SIZE);
  const [sortKey, setSortKey] = useState<LocationsSortKey>("last_validated_at");
  const [sortDir, setSortDir] = useState<
    "asc" | "desc" | "asc_nulls_first" | "desc_nulls_first"
  >("asc_nulls_first");

  // ---------------- filter state ----------------
  const [boroughFilter, setBoroughFilter] = useState<BoroughLabel[]>([]);
  // Internal age bucket state, defaulting to controlled value if provided.
  // The parent's value, if controlling, takes precedence on change.
  const [internalAgeBucket, setInternalAgeBucket] = useState<LocationsAgeBucket | "">("");
  const effectiveAgeBucket = onAgeBucketChange ? ageBucket : internalAgeBucket;
  const setEffectiveAgeBucket = onAgeBucketChange
    ? (next: LocationsAgeBucket | "") => onAgeBucketChange(next)
    : setInternalAgeBucket;
  const [hasIssuesOnly, setHasIssuesOnly] = useState(false);
  const [search, setSearch] = useState("");

  // Build query string from the param state. Memoized so the effect
  // only fires when something actually changes, not on every render.
  const queryString = useMemo(() => {
    const params: LocationsListParams = {
      page,
      page_size: pageSize,
      sort_key: sortKey,
      sort_dir: sortDir,
    };
    if (boroughFilter.length) params.borough = boroughFilter;
    if (effectiveAgeBucket) params.age_bucket = effectiveAgeBucket;
    if (hasIssuesOnly) params.has_issues = true;
    if (search.trim()) params.search = search.trim();

    const qs = new URLSearchParams();
    if (params.page !== undefined) qs.set("page", String(params.page));
    if (params.page_size !== undefined) qs.set("page_size", String(params.page_size));
    if (params.sort_key) qs.set("sort_key", params.sort_key);
    if (params.sort_dir) qs.set("sort_dir", params.sort_dir);
    if (params.borough) {
      // Repeated keys for FastAPI's Query(list)
      params.borough.forEach((b) => qs.append("borough", b));
    }
    if (params.age_bucket) qs.set("age_bucket", params.age_bucket);
    if (params.has_issues) qs.set("has_issues", "true");
    if (params.search) qs.set("search", params.search);
    return qs.toString();
  }, [page, pageSize, sortKey, sortDir, boroughFilter, effectiveAgeBucket, hasIssuesOnly, search]);

  // useAdminFetch handles the loading/error/data state and re-fetches
  // when queryString changes. Centralizes the cancelled-on-unmount
  // pattern and isAdminApiError handling — see L8 in v1_review.md.
  const url = `/api/admin/locations/list?${queryString}`;
  const { data, loading, error } = useAdminFetch<LocationsListResponse>(url);

  // Sort header click — toggles direction within the same key, or
  // switches to a fresh key with that key's "natural" default direction.
  //
  // The cycle is column-specific:
  //   • last_validated_at: asc_nulls_first ↔ desc. In both directions
  //     never-verified rows stay clustered at one end (the backend's
  //     `desc` maps to "DESC NULLS LAST"). This column has a meaningful
  //     "missing" category that admins want grouped, not interleaved.
  //   • All other columns: asc ↔ desc. No nulls in practice; nulls
  //     handling would be visual noise.
  //
  // Previous version cycled asc_nulls_first → desc → asc → desc → asc
  // and never returned to asc_nulls_first — the never-verified rows
  // could only be re-clustered by switching columns and switching back.
  function handleSortClick(field: LocationsSortKey) {
    if (sortKey === field) {
      setSortDir((d) => cycleSortDirForField(field, d));
    } else {
      setSortKey(field);
      setSortDir(defaultSortDirForField(field));
    }
    setPage(1);
  }

  // Reset to page 1 whenever any filter changes — otherwise an admin
  // who's on page 5 of an unfiltered view would suddenly see a stale
  // "page 5 of 1" empty state when they apply a filter.
  function setBoroughResetPage(next: BoroughLabel[]) {
    setBoroughFilter(next);
    setPage(1);
  }
  function setAgeBucketResetPage(next: LocationsAgeBucket | "") {
    setEffectiveAgeBucket(next);
    setPage(1);
  }
  function setHasIssuesResetPage(next: boolean) {
    setHasIssuesOnly(next);
    setPage(1);
  }
  function setSearchResetPage(next: string) {
    setSearch(next);
    setPage(1);
  }

  return (
    <div>
      <FilterBar
        boroughFilter={boroughFilter}
        setBoroughFilter={setBoroughResetPage}
        ageBucket={effectiveAgeBucket}
        setAgeBucket={setAgeBucketResetPage}
        hasIssuesOnly={hasIssuesOnly}
        setHasIssuesOnly={setHasIssuesResetPage}
        search={search}
        setSearch={setSearchResetPage}
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
          <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
          Failed to load locations: {error}
        </div>
      )}

      {loading && !data && (
        <TableSkeleton cols={9} rows={pageSize} />
      )}

      {data && (
        <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-neutral-50 dark:bg-neutral-800/50">
                <tr>
                  <SortHeader label="Location" field="name" current={sortKey} dir={sortDir} onClick={handleSortClick} />
                  <SortHeader label="Organization" field="organization" current={sortKey} dir={sortDir} onClick={handleSortClick} />
                  <SortHeader label="Borough" field="city" current={sortKey} dir={sortDir} onClick={handleSortClick} />
                  <SortHeader label="Services" field="service_count" current={sortKey} dir={sortDir} onClick={handleSortClick} className="text-right" />
                  {/*
                   * Last verified: whitespace-nowrap so the header
                   * "Last verified ▲" reads on one line. Auto-layout
                   * tables grow a non-wrapping column to fit its widest
                   * cell, pulling width from the more-flexible columns
                   * (Location / Organization / Borough) automatically —
                   * Categories doesn't need to be capped tighter to make
                   * this work. The body cell carries the same rule (see
                   * LocationRow) so timestamps like "7 years ago" stay
                   * on one line too.
                   *
                   * Categories stays at max-w-[200px] — the original
                   * cap. Earlier I tried tightening it to 170 to "make
                   * room" for Last verified, but in real data with
                   * `+N` overflow markers the resulting truncation was
                   * worse than the original wrap: strings like
                   * "Other service, Case W..." +7 lost more meaning to
                   * the ellipsis than they gained from the column
                   * shrink. With Last verified holding its own width
                   * via nowrap, Categories doesn't have to give any up.
                   */}
                  <th className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-left">Categories</th>
                  <SortHeader label="Last verified" field="last_validated_at" current={sortKey} dir={sortDir} onClick={handleSortClick} className="whitespace-nowrap" />
                  <th className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-center">Data</th>
                  <SortHeader label="Recent flags" field="recent_flags" current={sortKey} dir={sortDir} onClick={handleSortClick} className="text-right" />
                  <th className="px-4 py-3 text-xs font-semibold text-neutral-500 dark:text-neutral-400 text-right">Open</th>
                </tr>
              </thead>
              <tbody>
                {data.locations.length === 0 && (
                  <tr>
                    <td colSpan={9} className="px-4 py-10 text-center text-neutral-400 dark:text-neutral-500">
                      No locations match these filters.
                    </td>
                  </tr>
                )}
                {data.locations.map((loc) => (
                  <LocationRow key={loc.location_id} loc={loc} />
                ))}
              </tbody>
            </table>
          </div>

          {/* TablePagination sits INSIDE the card so its existing
              `border-t` visually attaches to the table (the previous
              `mt-4` wrapper produced a floating, disconnected control).
              The pagination's own `bg-neutral-50` matches the table
              header, so the card reads as a single unit:
              header → rows → footer. */}
          <TablePagination
            totalItems={data.total}
            page={data.page}
            pageSize={pageSize}
            onPageChange={setPage}
            onPageSizeChange={(ps) => {
              setPageSize(ps);
              setPage(1);
            }}
            ariaLabel="Locations table pagination"
          />
        </div>
      )}
    </div>
  );
}

// -----------------------------------------------------------------
// Filter bar
// -----------------------------------------------------------------

function FilterBar({
  boroughFilter,
  setBoroughFilter,
  ageBucket,
  setAgeBucket,
  hasIssuesOnly,
  setHasIssuesOnly,
  search,
  setSearch,
}: {
  boroughFilter: BoroughLabel[];
  setBoroughFilter: (next: BoroughLabel[]) => void;
  ageBucket: LocationsAgeBucket | "";
  setAgeBucket: (next: LocationsAgeBucket | "") => void;
  hasIssuesOnly: boolean;
  setHasIssuesOnly: (next: boolean) => void;
  search: string;
  setSearch: (next: string) => void;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-3">
      {/* Borough multi-select via toggle pills (no native multi-select —
       *  pills are easier to manipulate on a touchscreen and friendlier
       *  to keyboard nav than a floating <select multiple>). */}
      <div className="flex flex-wrap items-center gap-1">
        <span className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mr-1.5">Borough:</span>
        {(["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "Other"] as BoroughLabel[]).map((b) => {
          const active = boroughFilter.includes(b);
          return (
            <button
              key={b}
              type="button"
              onClick={() => {
                setBoroughFilter(active
                  ? boroughFilter.filter((x) => x !== b)
                  : [...boroughFilter, b]);
              }}
              className={`text-xs px-2.5 py-1 rounded-full border transition ${
                active
                  ? "bg-amber-300 border-amber-300 text-neutral-900"
                  : "border-neutral-200 text-neutral-600 hover:border-neutral-300 dark:border-neutral-700 dark:text-neutral-300"
              }`}
              aria-pressed={active}
            >
              {b}
            </button>
          );
        })}
      </div>

      {/* Age bucket — single-select dropdown */}
      <div className="flex items-center gap-1.5">
        <label className="text-sm font-medium text-neutral-500 dark:text-neutral-400" htmlFor="age-bucket">Age:</label>
        <select
          id="age-bucket"
          value={ageBucket}
          onChange={(e) => setAgeBucket(e.target.value as LocationsAgeBucket | "")}
          className="text-xs pl-2 pr-7 py-1 rounded border border-neutral-200 bg-white dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-200"
        >
          <option value="">All</option>
          <option value="lt30">&lt; 30 days</option>
          <option value="30to90">30–90 days</option>
          <option value="90to180">90–180 days</option>
          <option value="180to365">180 days – 1 year</option>
          <option value="gt365">&gt; 1 year</option>
          <option value="never">Never verified</option>
        </select>
      </div>

      {/* Has-issues toggle */}
      <label className="flex items-center gap-1.5 text-xs cursor-pointer">
        <input
          type="checkbox"
          checked={hasIssuesOnly}
          onChange={(e) => setHasIssuesOnly(e.target.checked)}
          className="rounded border-neutral-300 dark:border-neutral-600"
        />
        <span className="text-neutral-600 dark:text-neutral-300">Has issues only</span>
      </label>

      {/* Search */}
      <div className="flex-1 min-w-[200px]">
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name or organization…"
          className="w-full text-xs px-2.5 py-1.5 rounded border border-neutral-200 bg-white dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-200 placeholder:text-neutral-400 dark:placeholder:text-neutral-500"
        />
      </div>
    </div>
  );
}

// -----------------------------------------------------------------
// Header + row sub-components
// -----------------------------------------------------------------

function SortHeader({
  label, field, current, dir, onClick, className = "",
}: {
  label: string;
  field: LocationsSortKey;
  current: LocationsSortKey;
  dir: string;
  onClick: (f: LocationsSortKey) => void;
  className?: string;
}) {
  const active = current === field;
  const arrow = active ? (dir.startsWith("asc") ? " ▲" : " ▼") : "";
  return (
    // className applies to the inner button only — the button is
    // `w-full` and holds the visible padding + label, so its alignment
    // (text-left default, text-right when passed) is what's actually
    // rendered. Previously also applied to the th, which was either
    // redundant (button overrides) or accidentally double-applied.
    <th
      scope="col"
      className="px-0 py-0 border-b border-neutral-200 dark:border-neutral-800"
      aria-sort={active ? (dir.startsWith("asc") ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={() => onClick(field)}
        className={`w-full px-4 py-3 text-xs font-semibold cursor-pointer select-none transition-colors text-left hover:text-neutral-600 dark:hover:text-neutral-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-inset ${
          active ? "text-amber-600 dark:text-amber-400" : "text-neutral-500 dark:text-neutral-400"
        } ${className}`}
        aria-label={`Sort by ${label}`}
      >
        {label}{arrow}
      </button>
    </th>
  );
}

function LocationRow({ loc }: { loc: LocationsListRow }) {
  return (
    <tr className="border-t border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50/70 dark:hover:bg-neutral-800/30">
      <td className="px-4 py-3 text-sm text-neutral-900 dark:text-neutral-100 font-medium">
        {loc.location_name}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300">
        {loc.organization || "—"}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300">
        {loc.borough}
      </td>
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300 text-right tabular-nums">
        {loc.service_count}
      </td>
      {/* Categories cell — handles on-demand expand of the "+N"
       *  overflow marker. Logic lives in `CategoriesCell` below
       *  because of its local fetch + loading + error state; keeping
       *  it inline here would clutter LocationRow with hooks. */}
      <CategoriesCell loc={loc} />
      {/* whitespace-nowrap pairs with the same rule on the header so
          the column claims exactly the width "Last verified ▲" needs,
          and relative-time strings like "7 years ago" stay on one
          line. The browser's auto-table layout grows a non-wrapping
          column to fit its widest cell and pulls the room from
          flexible columns (Location / Organization / Borough), so we
          don't have to tighten Categories to make this work. */}
      <td className="px-4 py-3 text-sm text-neutral-600 dark:text-neutral-300 whitespace-nowrap">
        <LastVerifiedCell value={loc.last_validated_at} />
      </td>
      <td className="px-4 py-3 text-center">
        <DataBadges loc={loc} />
      </td>
      <td className="px-4 py-3 text-right text-sm">
        {loc.recent_flags > 0 ? (
          <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-400 font-medium">
            <AlertTriangle size={12} aria-hidden="true" />
            {loc.recent_flags}
          </span>
        ) : (
          <span className="text-neutral-400 dark:text-neutral-500">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-right">
        <a
          href={loc.yourpeer_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-amber-700 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300"
          aria-label={`Open ${loc.location_name} on YourPeer`}
        >
          YourPeer <ExternalLink size={12} aria-hidden="true" />
        </a>
      </td>
    </tr>
  );
}

/**
 * Categories cell with on-demand expansion of the "+N" overflow
 * marker.
 *
 * Default state (collapsed): renders the top-3 category names that
 * the /list endpoint returned, followed by a clickable "+N" if the
 * row has additional taxonomies beyond the cap. Clicking "+N" hits
 * the /api/admin/locations/{id}/categories endpoint and, on success,
 * replaces the entire cell content with the full taxonomy list.
 *
 * Why a local fetch instead of `useAdminFetch`: that hook runs on
 * mount and is designed for "open the page, load the data" patterns.
 * We want explicit user opt-in here — most rows never need the
 * expansion, and pre-fetching one per row would multiply the
 * page-load cost.
 *
 * State machine, briefly:
 *
 *     ┌──────────┐  click  ┌─────────┐ success  ┌──────────┐
 *     │collapsed │ ──────▶ │ loading │ ───────▶ │ expanded │
 *     │ (default)│         │         │          │  (final) │
 *     └──────────┘         └─────────┘          └──────────┘
 *           ▲                  │
 *           │                  │ error
 *           └──────────────────┘  (stay collapsed,
 *                                  show ⚠, allow retry)
 *
 * `expanded` is terminal — no collapse-back affordance, intentionally.
 * Once the full list is fetched and shown there's no reason to hide
 * it again, and a "Show less" link would clutter cells on a table
 * that's primarily for scanning. Paginating away or filtering the
 * table remounts the row and resets state.
 *
 * The cell still respects `max-w-[200px]` in both states so an
 * expanded row with many categories wraps to multiple lines rather
 * than pushing the rest of the table off-screen.
 */
function CategoriesCell({ loc }: { loc: LocationsListRow }) {
  // `null` when collapsed or after a failed fetch; populated once a
  // successful response arrives. Used as the single source of truth
  // for which state we're in — no separate `expanded` boolean needed.
  const [fullList, setFullList] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const collapsedText = loc.service_categories.join(", ") || "—";

  // No expand affordance needed if there's nothing beyond the top 3.
  // Render the same collapsed text without a button. The em-dash
  // case (no categories at all) is handled inside collapsedText so
  // we don't need a separate branch.
  if (loc.service_categories_more === 0) {
    return (
      <td className="px-4 py-3 text-xs text-neutral-500 dark:text-neutral-400 max-w-[200px]">
        {collapsedText}
      </td>
    );
  }

  // Once expanded, show the full taxonomy list and nothing else.
  // We don't fall back to the collapsed text on an empty response —
  // an empty list after a successful fetch is the catalog's actual
  // answer (e.g. the location was deleted between /list and the
  // click) and showing the now-stale top-3 would mislead.
  if (fullList !== null) {
    return (
      <td className="px-4 py-3 text-xs text-neutral-500 dark:text-neutral-400 max-w-[200px]">
        {fullList.length > 0 ? fullList.join(", ") : "—"}
      </td>
    );
  }

  const handleExpand = async () => {
    // Re-entry guard. With `disabled` on the button this shouldn't
    // be possible, but a defensive check keeps the click handler
    // honest if the disabled attribute is ever removed.
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const url = `/api/admin/locations/${encodeURIComponent(loc.location_id)}/categories`;
      const res = await fetch(url);
      const body: unknown = await res.json();
      if (isAdminApiError(body)) {
        setError(body.detail);
      } else {
        // Trust the response shape — the backend's contract is
        // pinned by `LocationCategoriesResponse`. A defensive
        // runtime check would be belt-and-braces; the same
        // contract assumption holds across every other admin
        // fetch in this file.
        const data = body as LocationCategoriesResponse;
        setFullList(data.categories);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <td className="px-4 py-3 text-xs text-neutral-500 dark:text-neutral-400 max-w-[200px]">
      {collapsedText}{" "}
      <button
        type="button"
        onClick={handleExpand}
        disabled={loading}
        // Resting style matches the previous static "+N" span so the
        // affordance is unchanged at a glance; hover/focus adds the
        // amber underline familiar from the YourPeer external link
        // elsewhere in the table, signalling "this is clickable."
        // disabled:cursor-wait covers the loading state.
        aria-label={
          loading
            ? `Loading ${loc.service_categories_more} more categories`
            : `Show ${loc.service_categories_more} more categories`
        }
        className="text-neutral-400 dark:text-neutral-500 hover:text-amber-700 dark:hover:text-amber-400 hover:underline underline-offset-2 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:cursor-wait disabled:hover:no-underline disabled:hover:text-neutral-400 dark:disabled:hover:text-neutral-500"
      >
        {loading ? "…" : `+${loc.service_categories_more}`}
      </button>
      {error && (
        // Error indicator stays small and unobtrusive — the +N button
        // remains clickable so the user can retry. We don't surface
        // the error inline because the typical failure mode (server
        // 500, network blip) is opaque to the staff member and the
        // retry covers most of it. aria-label carries the detail
        // unconditionally for screen readers; the Tooltip surfaces
        // the same detail to sighted users on hover.
        <Tooltip content={`Failed to load: ${error}. Click +${loc.service_categories_more} to retry.`}>
          <span
            className="ml-1 text-red-600 dark:text-red-400"
            aria-label={`Failed to load more categories: ${error}`}
          >
            ⚠
          </span>
        </Tooltip>
      )}
    </td>
  );
}

function LastVerifiedCell({ value }: { value: string | null }) {
  if (value === null) {
    return (
      <span className="inline-flex items-center gap-1 text-red-700 dark:text-red-400">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500 dark:bg-red-400 inline-block" aria-hidden="true" />
        Never
      </span>
    );
  }
  // Relative time + absolute on hover
  const date = new Date(value);
  const now = new Date();
  const days = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));
  const relative =
    days < 1 ? "today"
    : days === 1 ? "1 day ago"
    : days < 30 ? `${days} days ago`
    : days < 60 ? "1 month ago"
    : days < 365 ? `${Math.floor(days / 30)} months ago`
    : `${Math.floor(days / 365)} year${Math.floor(days / 365) > 1 ? "s" : ""} ago`;

  // Color cue: green <90d, amber 90-365d, red >365d
  const cls =
    days < 90 ? "text-green-700 dark:text-green-400"
    : days < 365 ? "text-amber-700 dark:text-amber-400"
    : "text-red-700 dark:text-red-400";

  return (
    <Tooltip content={date.toISOString().slice(0, 10)}>
      <span className={cls}>
        {relative}
      </span>
    </Tooltip>
  );
}

function DataBadges({ loc }: { loc: LocationsListRow }) {
  // Each badge represents a different *kind* of data, so the
  // present/missing tooltip phrasing isn't always uniform. Most
  // catalog fields read fine with "Has X" / "Missing X" — they
  // either exist or they don't. Reviews are different: they
  // accumulate over time as users leave feedback, so "Missing
  // reviews" mis-implies the data ought to be there. The optional
  // `tooltipHas` / `tooltipMissing` overrides let a badge spell
  // out its own wording when the default template would read
  // wrong; falling back to "Has X" / "Missing X" keeps the other
  // three concise.
  const items: {
    has: boolean;
    label: string;
    tooltipHas?: string;
    tooltipMissing?: string;
  }[] = [
    { has: loc.has_phone, label: "phone" },
    { 
      has: loc.has_address,
      label: "addr",
      tooltipHas: "Has address",
      tooltipMissing: "No address",
    },
    { 
      has: loc.has_hours,
      label: "hrs",
      tooltipHas: "Has hours",
      tooltipMissing: "No hours",
    },
    {
      has: loc.has_reviews,
      label: "revs",
      tooltipHas: "Has user feedback or reviews",
      tooltipMissing: "No user feedback or reviews yet",
    },
  ];
  // Each badge is wrapped in the shared `<Tooltip>` helper, which
  // wraps Radix Tooltip internally. Three reasons the native `title`
  // attribute didn't cut it here:
  //   1. Default browser delay (≈500-1000ms) is long enough that
  //      readers move on before the tooltip ever appears, especially
  //      on small ~24px-wide hit targets.
  //   2. The table sits inside `overflow-hidden` (the white card)
  //      AND `overflow-x-auto` (the horizontal scroll wrapper). A
  //      CSS-only tooltip rising above a badge would be clipped by
  //      either ancestor. The helper portals the content out of the
  //      DOM tree, escaping the clip.
  //   3. Native `title` isn't keyboard-accessible — focus alone
  //      doesn't surface it. Radix shows the tooltip on focus and
  //      dismisses on Escape; the helper inherits that for free.
  //
  // `aria-label` is set on each badge in addition to the tooltip
  // content. The Radix Trigger wires up the ARIA association between
  // trigger and the open content, but the explicit aria-label
  // guarantees screen readers announce the full meaning the moment
  // focus lands on the badge — without depending on the tooltip
  // having opened.
  return (
    <div className="inline-flex gap-1">
      {items.map((it) => {
        const tooltip = it.has
          ? (it.tooltipHas ?? `Has ${it.label}`)
          : (it.tooltipMissing ?? `Missing ${it.label}`);
        return (
          <Tooltip key={it.label} content={tooltip}>
            <span
              tabIndex={0}
              aria-label={tooltip}
              className={`text-xs px-1.5 py-0.5 rounded cursor-default focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 ${
                it.has
                  ? "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300"
                  : "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300"
              }`}
            >
              {it.label}
            </span>
          </Tooltip>
        );
      })}
    </div>
  );
}
