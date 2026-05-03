// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * Theme: light / dark / system.
 *
 * Three-way selection: "system" follows the OS preference (via
 * prefers-color-scheme); "light"/"dark" are explicit overrides that
 * persist to localStorage. Default is "system" — respecting the user's
 * OS choice is both more accessible (for people on system-wide dark
 * mode to reduce eye strain / migraines / photosensitivity) and avoids
 * a visible jarring flash when those users first load the app.
 *
 * No React in this module — kept framework-free so the same resolution
 * logic runs in:
 *   - the inline FOUC-prevention script (blocks first paint)
 *   - the React hook (post-hydration, reacts to changes)
 *   - the theme toggle (user action)
 *
 * Storage key is versioned so a future v2 (e.g., adding an
 * "auto-by-time-of-day" option) can ignore or migrate v1 entries
 * cleanly. Value is a plain string, not JSON — minimal surface for
 * localStorage corruption or parsing errors on the critical path.
 *
 * Why not next-themes? The library is great, but the full dependency
 * adds ~4kb and ships a number of features we don't need (SSR theme
 * forcing, data-attribute selectors, custom color schemes). The
 * maintained yourpeer philosophy is hand-written minimal code (see
 * the top-of-file comment in public/sw.js). Reimplementing the ~30
 * lines we care about is cheaper than owning the dependency upgrade
 * path and the abstraction surface.
 */

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

/** localStorage key. Versioned so we can migrate on format changes. */
export const THEME_STORAGE_KEY = "yourpeer:theme:v1";

/** HTML class used by Tailwind's darkMode: ["class"] to activate the
 *  dark variants. Must match tailwind.config.ts. */
export const DARK_CLASS = "dark";

/** HTML class added by the inline FOUC-prevention script and removed
 *  by the useTheme mount-effect. While present, the global color
 *  transitions in globals.css are suppressed — preventing the
 *  unwanted fade-in on initial paint. The class name must match the
 *  selector in globals.css; duplicated as a constant so both sides
 *  reference the same source-of-truth. */
export const TRANSITIONS_OFF_CLASS = "theme-transitions-off";

/**
 * Read the stored theme choice from localStorage. Returns "system"
 * when nothing is stored or the stored value is invalid — "system"
 * is always a safe default because it defers to the OS.
 *
 * Wrapped in try/catch because:
 *   - localStorage throws in some browsing contexts (strict privacy
 *     mode, cross-origin iframes, some corporate deployments that
 *     lock down storage APIs).
 *   - SSR has no window/localStorage — guard for Node builds.
 */
export function readStoredChoice(): ThemeChoice {
  if (typeof window === "undefined") return "system";
  try {
    const v = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
    return "system";
  } catch {
    return "system";
  }
}

/**
 * Persist a theme choice to localStorage. Writing "system" removes
 * the entry — storing an explicit "system" and storing nothing are
 * semantically identical, and keeping the store empty when the user
 * is on the default avoids surprising them with a stored preference
 * they never set (e.g., if we ever ship a privacy-compliant "wipe
 * all local state" button).
 *
 * Errors are swallowed because theme persistence is cosmetic — a
 * failed write just means the user's choice won't survive a reload,
 * which is a lesser harm than a crash or a blocked UI action.
 */
export function writeStoredChoice(choice: ThemeChoice): void {
  if (typeof window === "undefined") return;
  try {
    if (choice === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, choice);
    }
  } catch {
    // ignore — see docstring
  }
}

/**
 * Read the OS-level color-scheme preference. Returns "dark" when the
 * user has system-wide dark mode on, otherwise "light". Defaults to
 * "light" in environments without matchMedia (old browsers, SSR).
 */
export function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/**
 * Resolve a theme choice to an actual light/dark value. "system"
 * delegates to the OS preference; "light"/"dark" pass through as-is.
 */
export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice === "system") return systemPrefersDark() ? "dark" : "light";
  return choice;
}

/**
 * Apply the resolved theme to the document. Idempotent — safe to
 * call repeatedly. Called from:
 *   - the FOUC-prevention inline script (one-time, pre-hydration)
 *   - the useTheme hook (whenever choice or OS preference changes)
 *
 * Uses classList.toggle with the force parameter so we don't accidentally
 * bounce the class when the current state already matches the target.
 * Unnecessary DOM writes trigger style recalc in some browsers.
 */
export function applyResolvedTheme(resolved: ResolvedTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle(DARK_CLASS, resolved === "dark");
}

/**
 * Cycle the theme choice forward. Order: system → light → dark → system.
 *
 * Forward-only cycling is simpler for the toggle button (one action,
 * predictable next state) than a dropdown with three explicit options.
 * The button's visual state always reveals the current choice, so
 * users who want to skip a step can just tap again.
 */
export function nextChoice(current: ThemeChoice): ThemeChoice {
  if (current === "system") return "light";
  if (current === "light") return "dark";
  return "system";
}
