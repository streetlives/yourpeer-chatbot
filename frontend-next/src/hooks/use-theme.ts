// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

/**
 * React hook for theme: returns the user's stored choice, the
 * currently-resolved theme, and setters.
 *
 * SSR-safe initial render: the hook returns "system" + "light" on
 * first render (regardless of actual stored value) so the server
 * render and client hydration agree. The `useEffect` below then
 * reads the real values and triggers a re-render with the correct
 * state. This matches what the FOUC-prevention inline script does
 * at the DOM level — the html tag gets the right class before React
 * hydrates, so users never see the wrong theme. The hook state then
 * catches up on first effect tick.
 *
 * Listening for OS preference changes lets users who are in "system"
 * mode see the app flip when they toggle system-wide dark mode
 * mid-session. This matters because many users toggle based on
 * ambient light (e.g., iOS auto-dark at sunset).
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ThemeChoice,
  ResolvedTheme,
  readStoredChoice,
  writeStoredChoice,
  systemPrefersDark,
  resolveTheme,
  applyResolvedTheme,
  toggleExplicitChoice,
} from "@/lib/theme";

export function useTheme(): {
  /** The user's explicit preference: "system" | "light" | "dark". */
  choice: ThemeChoice;
  /** The actual theme currently applied: "light" | "dark". */
  resolved: ResolvedTheme;
  /** Set an explicit theme choice. */
  setChoice: (choice: ThemeChoice) => void;
  /** Toggle light ↔ dark; from system, picks opposite of resolved. */
  cycle: () => void;
} {
  // SSR-safe defaults on first render. The real state is loaded in
  // the mount-effect below. See file-level comment.
  const [choice, setChoiceState] = useState<ThemeChoice>("system");
  const [resolved, setResolved] = useState<ResolvedTheme>("light");

  // On mount, read actual stored choice and compute resolved.
  // Also subscribe to OS preference changes (important for the
  // "system" branch — when the user toggles iOS/macOS dark mode
  // while the app is open, we want the UI to follow).
  useEffect(() => {
    const storedChoice = readStoredChoice();
    const resolvedTheme = resolveTheme(storedChoice);
    // Mount-time sync from external state (localStorage + matchMedia)
    // into React state. These setState calls are idempotent and run
    // exactly once — same pattern as the Zustand hydration effect in
    // chat-container.tsx, and the lint rule's legitimate-case exception.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- mount-time external-source sync; idempotent
    setChoiceState(storedChoice);
    setResolved(resolvedTheme);
    // The inline FOUC script already applied the resolved class to
    // <html>, so this re-apply is usually a no-op. We re-apply anyway
    // to handle the edge case where the stored choice changed between
    // the inline script running and React hydrating (e.g., a second
    // tab wrote to localStorage).
    //
    // The .theme-transitions-off class is added AND removed by the
    // inline FOUC script itself (via requestAnimationFrame), so we
    // don't need to touch it here — see app/layout.tsx. By the time
    // this effect runs the class is already gone and any class change
    // we make below (e.g., reacting to OS preference) animates
    // smoothly as intended.
    applyResolvedTheme(resolvedTheme);

    // Watch for OS-level changes. Only relevant when choice is "system";
    // for explicit "light"/"dark" the override wins regardless of OS.
    // We attach the listener unconditionally so it's correct if the
    // user switches back to "system" later — and we re-check `choice`
    // at fire time via a closure over the latest stored value.
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    let mediaQuery: MediaQueryList;
    try {
      mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    } catch {
      return;
    }

    const handler = () => {
      // Re-read the choice rather than capture from closure — the
      // user might have changed their choice since mount.
      const currentChoice = readStoredChoice();
      if (currentChoice !== "system") return; // explicit override wins
      const newResolved: ResolvedTheme = systemPrefersDark() ? "dark" : "light";
      setResolved(newResolved);
      applyResolvedTheme(newResolved);
    };

    // addEventListener is the modern API; older Safari supports only
    // addListener. Use feature detection for portability.
    if (typeof mediaQuery.addEventListener === "function") {
      mediaQuery.addEventListener("change", handler);
      return () => mediaQuery.removeEventListener("change", handler);
    } else if (typeof (mediaQuery as unknown as { addListener?: (fn: () => void) => void }).addListener === "function") {
      const legacy = mediaQuery as unknown as {
        addListener: (fn: () => void) => void;
        removeListener: (fn: () => void) => void;
      };
      legacy.addListener(handler);
      return () => legacy.removeListener(handler);
    }
  }, []);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    writeStoredChoice(next);
    const newResolved = resolveTheme(next);
    setResolved(newResolved);
    applyResolvedTheme(newResolved);
  }, []);

  const cycle = useCallback(() => {
    // Read the latest choice at click time rather than relying on the
    // closed-over `choice` state — avoids a stale-closure bug if the
    // user clicks rapidly during a render cycle.
    setChoiceState((prev) => {
      const currentResolved = resolveTheme(prev);
      const next = toggleExplicitChoice(prev, currentResolved);
      writeStoredChoice(next);
      const newResolved = resolveTheme(next);
      setResolved(newResolved);
      applyResolvedTheme(newResolved);
      return next;
    });
  }, []);

  return { choice, resolved, setChoice, cycle };
}
