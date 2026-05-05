// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useEffect, useRef } from "react";

/**
 * Safety button for users in unsafe situations (DV, surveillance,
 * controlling family members) who need to leave YourPeer instantly if
 * someone walks in or looks over their shoulder.
 *
 * Design choices, with sources in /docs (or session research notes):
 *
 *   - LABEL "Leave site": Oomph's user testing (oomphinc.com, 2023)
 *     found "Quick Exit" was misunderstood by 5/5 participants;
 *     "Escape" can be triggering for survivors. "Leave site" is calm,
 *     unambiguous, and doesn't reference flight or violence.
 *
 *   - PLACEMENT fixed top-right: matches the reading-flow position
 *     for an "exit" action; out of the way of the chat input on
 *     mobile (which sits at the bottom). The NSW Design System
 *     recommends bottom-fixed, but on this app the chat input
 *     occupies that zone, so top-right is the safer compromise.
 *
 *   - COLOR red-600: universal stop/exit semantic, distinct from the
 *     brand amber, AAA contrast against both light and dark
 *     backgrounds.
 *
 *   - DOUBLE-ESC keyboard shortcut: NSW Design System pattern. A
 *     single Esc would conflict with the existing dialog-close
 *     behavior in CallConfirmDialog and ReviewDetailDialog.
 *
 *   - EXIT BEHAVIOR (in order):
 *       1. Cloak: hide <html> so no sensitive content is visible
 *          during the redirect (NSW Design System, Oomph).
 *       2. Wipe chat localStorage: a returning user (potentially the
 *          abuser) opening YourPeer should not see the prior chat.
 *          This is YourPeer-specific — the canonical pattern doesn't
 *          touch storage because most DV sites have nothing
 *          persisted client-side.
 *       3. Open weather.com in a new tab (Columbia Health, Goldberg
 *          Law) so the visible foreground tab is innocuous.
 *       4. window.location.replace to google.com on the current tab
 *          so the back button can't return to YourPeer.
 *
 *   - WHAT THIS DOES NOT DO: It cannot clear browser history (browser
 *     security forbids it from JS). It cannot help against remote
 *     device monitoring. Per Tech Safety / NNEDV guidance, this
 *     mitigates "someone walks into the room", not comprehensive
 *     surveillance. We deliberately do NOT show a tooltip making
 *     stronger claims.
 */

const NEW_TAB_URL = "https://www.weather.com/";
const REPLACE_URL = "https://www.google.com/";
const DOUBLE_ESC_WINDOW_MS = 1000;

function performExit() {
  // Step 1: cloak — hide the page contents instantly so nothing
  // sensitive is visible while the redirect is in flight. Setting
  // display:none on the root rather than the body so even fixed-
  // position elements (banners, this very button) disappear.
  try {
    document.documentElement.style.display = "none";
  } catch {
    // If this throws somehow (e.g. CSP, sandboxed iframe), continue
    // anyway — a delayed cloak is better than no exit.
  }

  // Step 2: clear chat data. The store key matches store.ts's
  // persist `name`. We also clear any send-queue / cache keys with
  // the yourpeer prefix to be thorough.
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem("yourpeer-chat");
      // Sweep any ancillary keys (queue, cache, pending responses).
      const toRemove: string[] = [];
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith("yourpeer")) toRemove.push(k);
      }
      toRemove.forEach((k) => localStorage.removeItem(k));
    }
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.clear();
    }
  } catch {
    // localStorage can throw in private mode on some browsers;
    // continue — exit is still the priority.
  }

  // Step 3: open the new tab to a neutral site. Has to happen in
  // direct response to user input or the popup blocker stops it.
  // We use a noopener target to prevent the new tab from holding a
  // reference to the YourPeer window object.
  try {
    window.open(NEW_TAB_URL, "_blank", "noopener,noreferrer");
  } catch {
    // Pop-up blocked or otherwise failed — the replace below still
    // covers the foreground tab, which is what an observer sees.
  }

  // Step 4: replace the current tab. `replace` (vs assign) means
  // the browser back button does not return to YourPeer — the
  // history entry is overwritten.
  window.location.replace(REPLACE_URL);
}

export function QuickExit() {
  // Double-press-Esc handler. We track the last Esc timestamp; a
  // second Esc within DOUBLE_ESC_WINDOW_MS triggers exit.
  const lastEscRef = useRef(0);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      // Don't fire from inside <input>/<textarea> — Esc is commonly
      // used there to clear input or close IME suggestions, and
      // accidentally exiting from a typo would be jarring.
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || t?.isContentEditable) {
        return;
      }
      const now = Date.now();
      if (now - lastEscRef.current < DOUBLE_ESC_WINDOW_MS) {
        lastEscRef.current = 0;
        performExit();
        return;
      }
      lastEscRef.current = now;
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <>
      {/* Screen-reader-only context that explains the keyboard
          shortcut. Visible users learn it from the visible button
          tooltip; SR users get it on first encounter. */}
      <div className="sr-only" role="note">
        To leave this site quickly, press the Escape key twice or
        activate the Leave site button.
      </div>

      <button
        type="button"
        onClick={performExit}
        aria-label="Leave site immediately and replace this page"
        title="Leave site (or press Esc twice)"
        className={[
          // Inline in chat header next to ThemeToggle. Previously
          // fixed top-right of viewport; moved here for visual
          // adjacency with the theme toggle. Trade-off: the button
          // is briefly absent during chat hydration (which renders
          // <Loading…> until Zustand persist completes). Accepted
          // because hydration is sub-second on a healthy session
          // and the Esc-Esc keyboard shortcut still works during
          // hydration via the document-level keydown listener.
          "inline-flex items-center",
          // Visual: red, dense, unmissable but not screaming.
          "px-3 py-2 rounded-lg",
          "bg-red-600 text-white text-sm font-semibold",
          "shadow-md ring-1 ring-red-700/40",
          "transition hover:bg-red-700 active:scale-[0.97]",
          // Accessible focus ring — visible on both backgrounds.
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-neutral-950",
          // Dark mode: same red, slightly muted ring against the
          // dark surface.
          "dark:bg-red-600 dark:hover:bg-red-500 dark:ring-red-900/60",
        ].join(" ")}
      >
        <span aria-hidden="true">✕ </span>
        Leave site
      </button>
    </>
  );
}
