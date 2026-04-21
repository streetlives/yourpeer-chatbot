// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Offline staleness banner.
 *
 * Shown when the user is offline AND cached results exist. Uses amber
 * (not brand yellow) so it reads as a warning state distinct from the
 * normal UI chrome. See offline-cache.ts for the cache contract.
 *
 * When no cached results exist (and the user is just-plain offline),
 * this is replaced by OfflineNoticeBanner below — a gentler message
 * that explains queued-send behavior.
 */

"use client";

import { formatCacheAge } from "@/lib/chat/offline-cache";

interface OfflineBannerProps {
  /** Age of cached results in ms. When non-null, show staleness UX. */
  cacheAge: number | null;
  /** Queue depth — when > 0, tell the user their message is pending. */
  queueDepth: number;
}

/**
 * Primary offline banner. Handles both cases:
 *   - Cached results exist → amber "these may be outdated" warning
 *   - No cache → gentler "you're offline, messages will send when
 *     you're back" note
 */
export function OfflineBanner({ cacheAge, queueDepth }: OfflineBannerProps) {
  // Choose wording based on what the user has available
  const hasCache = cacheAge !== null;
  const hasQueued = queueDepth > 0;

  // Amber color chosen specifically to distinguish from the brand
  // yellow (#FFD54F). See design decision in PR.
  const palette = {
    bg: "bg-amber-50",
    border: "border-amber-400",
    text: "text-amber-900",
    icon: "text-amber-600",
  };

  let primaryText: string;
  let secondaryText: string | null = null;

  if (hasCache) {
    primaryText = "You're offline — these results may be outdated.";
    secondaryText = `Last updated ${formatCacheAge(cacheAge)}.`;
    if (hasQueued) {
      secondaryText += ` ${queueDepth} ${queueDepth === 1 ? "message" : "messages"} waiting to send.`;
    }
  } else {
    primaryText = "You're offline.";
    if (hasQueued) {
      secondaryText = `${queueDepth} ${queueDepth === 1 ? "message" : "messages"} will send when you're back online.`;
    } else {
      secondaryText = "Messages you send will be delivered when you're back online.";
    }
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={`mx-1 mb-2 px-3 py-2 rounded-lg border-l-4 ${palette.bg} ${palette.border} ${palette.text} text-sm`}
    >
      <div className="flex items-start gap-2">
        {/* Simple cloud-off icon — inline SVG so no new asset */}
        <svg
          className={`w-5 h-5 mt-0.5 shrink-0 ${palette.icon}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 3l18 18M18.364 5.636a9 9 0 00-12.728 0M7.05 7.05a5 5 0 015.7-.95M12 15a3 3 0 110-6" />
        </svg>
        <div className="flex-1">
          <div className="font-medium">{primaryText}</div>
          {secondaryText && (
            <div className="text-xs mt-0.5 opacity-90">{secondaryText}</div>
          )}
        </div>
      </div>
    </div>
  );
}
