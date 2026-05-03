// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Offline staleness banner.
 *
 * Shown when the user is offline. Uses amber (not brand yellow) so it
 * reads as a warning state distinct from the normal UI chrome.
 *
 * Note on wording: the previous version of this component displayed a
 * specific cache age ("Last updated 5 minutes ago"). That was
 * misleading — the chat messages shown on the page come from the
 * Zustand persist layer (localStorage), not the IDB cache the timer
 * was reading. The two can disagree substantially. We now just tell
 * the user their view may be outdated without claiming to know when
 * it last updated.
 */

"use client";

interface OfflineBannerProps {
  /** Whether the user has previously-returned results visible. When
   *  true, the banner emphasizes "these may be outdated"; when false,
   *  it focuses on the queued-send messaging. */
  hasCachedResults: boolean;
  /** Queue depth — when > 0, tell the user their message is pending. */
  queueDepth: number;
}

export function OfflineBanner({ hasCachedResults, queueDepth }: OfflineBannerProps) {
  const hasQueued = queueDepth > 0;

  // Amber color chosen specifically to distinguish from the brand
  // yellow (#FFD54F). See design decision in PR.
  //
  // Dark-mode adjustments: the amber-50 bg becomes invisible against
  // the dark page background, so in dark mode we use an amber-950
  // tint (still warm but visible). amber-900 text on amber-50 has
  // good contrast in light mode; we keep the amber hue in dark but
  // shift to amber-200 for the text so it reads on a near-black page.
  const palette = {
    bg: "bg-amber-50 dark:bg-amber-950/40",
    border: "border-amber-400 dark:border-amber-600",
    text: "text-amber-900 dark:text-amber-200",
    icon: "text-amber-600 dark:text-amber-400",
  };

  let primaryText: string;
  let secondaryText: string;

  if (hasCachedResults) {
    primaryText = "You're offline — results shown may be outdated.";
    secondaryText = hasQueued
      ? `${queueDepth} ${queueDepth === 1 ? "message" : "messages"} will send when you're back online.`
      : "Messages you send will be delivered when you're back online.";
  } else {
    primaryText = "You're offline.";
    secondaryText = hasQueued
      ? `${queueDepth} ${queueDepth === 1 ? "message" : "messages"} will send when you're back online.`
      : "Messages you send will be delivered when you're back online.";
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
