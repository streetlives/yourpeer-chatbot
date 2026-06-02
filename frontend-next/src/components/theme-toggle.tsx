// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Theme toggle button. Switches between light and dark only.
 *
 * Icon reflects the resolved theme (what the user sees on screen):
 *   - light: sun icon
 *   - dark: moon icon
 *
 * Default is "system" (follows OS) with no stored preference. The first
 * tap persists the opposite of the current appearance; later taps flip
 * light ↔ dark. There is no UI path back to system-auto mode.
 *
 * Button is hydration-guarded: server render emits a placeholder
 * (same dimensions, different content) so the layout doesn't shift
 * when the real icon appears post-hydration. Critical because the
 * button sits in the header row and a width-change would push the
 * connection-status dot and subtitle.
 */

"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/hooks/use-theme";

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { resolved, cycle } = useTheme();

  // Guard against hydration mismatch: on first render, resolved is
  // always "light" (the useState default). The real resolved theme
  // only appears after the mount effect in useTheme runs. Rendering
  // the icon based on `resolved` without this guard would show a sun
  // briefly even for a user in dark mode — jarring during page load.
  //
  // The placeholder matches button dimensions exactly so nothing
  // shifts when the real icon swaps in.
  const [mounted, setMounted] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration-guard mount marker; intentional one-shot
  useEffect(() => setMounted(true), []);

  const { Icon, label } = mounted
    ? resolvedToIcon(resolved)
    : { Icon: PlaceholderIcon, label: "Loading theme" };

  return (
    <button
      type="button"
      onClick={cycle}
      aria-label={label}
      title={label}
      className={[
        // Base shape. 44x44 hit target (WCAG AAA for touch — critical
        // for mobile users). Visible icon size is smaller; hit area
        // is the full button.
        "inline-flex items-center justify-center",
        "w-11 h-11 rounded-full",
        // Visual — subtle in both themes, not a primary action
        "text-neutral-600 hover:text-neutral-900",
        "dark:text-neutral-400 dark:hover:text-neutral-100",
        "hover:bg-neutral-100 dark:hover:bg-neutral-800",
        // Focus ring for keyboard users — brand amber on light,
        // amber-300 (brighter) on dark so it's visible on the dark
        // background.
        "focus-visible:outline-none focus-visible:ring-2",
        "focus-visible:ring-amber-500 dark:focus-visible:ring-amber-300",
        "focus-visible:ring-offset-2 focus-visible:ring-offset-white",
        "dark:focus-visible:ring-offset-neutral-900",
        // Transition so the toggle doesn't feel abrupt
        "transition-colors",
        className,
      ].join(" ")}
    >
      <Icon className="w-5 h-5" aria-hidden="true" />
    </button>
  );
}

function resolvedToIcon(resolved: "light" | "dark") {
  if (resolved === "light") {
    return {
      Icon: Sun,
      label: "Theme: light (tap to switch to dark)",
    };
  }
  return {
    Icon: Moon,
    label: "Theme: dark (tap to switch to light)",
  };
}

/** Neutral placeholder used during SSR / pre-hydration — same
 *  dimensions as the real icons. Circle with no stroke avoids
 *  signaling any particular state. */
function PlaceholderIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="8" strokeOpacity="0.25" />
    </svg>
  );
}
