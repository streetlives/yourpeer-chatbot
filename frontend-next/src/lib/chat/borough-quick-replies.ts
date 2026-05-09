// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import type { QuickReply } from "./types";

/**
 * Borough quick-reply chips offered to the user when their location can't be
 * resolved from browser geolocation (permission denied, timeout, or device
 * doesn't support it). Picking one of these falls back to a borough-level
 * search; tapping "🌆 All NYC" runs a citywide search across all 5 boroughs.
 *
 * Why this is here, not inline:
 *   The frontend builds borough chips directly when geolocation fails — the
 *   user never reaches the backend's confirmation handler in that case, so
 *   the backend's quick-reply construction (in `confirmation.py`,
 *   `accessibility.py`, etc.) doesn't fire. This list mirrors the backend's
 *   borough-prompt list so both code paths offer the same options to the
 *   user. If the backend list changes (e.g. a new "Outer NYC" option lands),
 *   update both.
 *
 *   Lifted out of `use-chat.ts` after May 2026's audit — there were two
 *   inlined copies of an identical 5-button list, and neither had been
 *   updated when the backend added the "🌆 All NYC" option. This module is
 *   the single source of truth for the frontend; one place to edit when the
 *   list changes.
 *
 * Why "All NYC" comes last:
 *   Same ordering as the backend (`accessibility.py:138` and friends).
 *   Specific-borough chips first so users who know their borough tap them
 *   directly; the citywide option is the deliberate broader fallback for
 *   users who genuinely want results from anywhere in the city.
 *
 * The button value `"All NYC"` flows through normal slot extraction on the
 * backend (`slot_extraction_regex._CITYWIDE_PHRASES`) and resolves to
 * `CITYWIDE_SENTINEL`, which the query layer interprets as a citywide
 * search across all 5 boroughs.
 */
export const BOROUGH_QUICK_REPLIES: ReadonlyArray<QuickReply> = [
  { label: "Manhattan", value: "Manhattan" },
  { label: "Brooklyn", value: "Brooklyn" },
  { label: "Queens", value: "Queens" },
  { label: "Bronx", value: "Bronx" },
  { label: "Staten Island", value: "Staten Island" },
  { label: "🌆 All NYC", value: "All NYC" },
];
