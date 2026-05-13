// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import * as RadixTooltip from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";

/**
 * Thin wrapper around Radix Tooltip that hides the
 * Root/Trigger/Portal/Content boilerplate behind a one-line API.
 *
 * Why this exists: the admin section has ~20 small "hover for more
 * info" tooltips (truncated session ids, bar values, ISO timestamps
 * on relative times, explanation tooltips on chart panels). Each one
 * in raw Radix is 5+ JSX nodes (Root/Trigger asChild/Portal/Content/
 * Arrow) plus the styling block, which is both tedious to type and
 * easy to drift between call sites. This helper standardizes:
 *
 *   1. Styling — dark-bubble in light mode, light-bubble in dark
 *      mode, max-w-xs so explanation-style content wraps before
 *      reaching the viewport edge. Matches the original DataBadges
 *      Radix tooltip styling from the locations table.
 *
 *   2. Animation — Tailwind's `animate-in fade-in zoom-in-95`
 *      enter animations.
 *
 *   3. Conditional suppression — passing `null`, `undefined`, or
 *      an empty string for `content` bypasses the wrapper entirely
 *      and renders the children unwrapped. Lets callers write
 *      `content={maybeNull}` without guarding the whole JSX.
 *
 *   4. `asChild` on the Trigger — merges hover/focus/ref handlers
 *      onto the consumer's element rather than wrapping it in a
 *      `<span>`. The child must be a single ReactElement that can
 *      accept event handlers and a ref (any DOM element,
 *      forwardRef component, or anything Radix's Slot can hijack
 *      works).
 *
 * The Tooltip.Provider lives in `admin-shell.tsx` at the top of the
 * admin tree, so this component intentionally does NOT include its
 * own provider — nested providers would create conflicting
 * delayDuration values and double-render the tooltip layer.
 *
 * Accessibility note: Radix automatically wires `aria-describedby`
 * from the trigger to the open tooltip content. For non-focusable
 * triggers (table cells, chart bars), keyboard users won't reach
 * the tooltip — same trade-off as the native `title` attribute
 * has on those elements. For tooltip content that's genuinely
 * required to understand the page (e.g. a truncated session id),
 * pair this with `aria-label` on the trigger so screen-reader
 * users get the info regardless of focus.
 */
interface TooltipProps {
  /** The hoverable element. Use a single ReactElement child; Radix
   *  uses Slot to forward hover/focus handlers and a ref onto it.
   *  Wrapping a string or a fragment will throw — wrap the
   *  outermost DOM node instead. */
  children: ReactNode;
  /** Bubble content. String or any ReactNode (max-w-xs handles
   *  wrapping). Falsy values (null/undefined/empty string) skip
   *  the tooltip wrapper entirely. */
  content: ReactNode;
  /** Where the bubble sits relative to the trigger. Default top. */
  side?: "top" | "right" | "bottom" | "left";
  /** Pixel offset between trigger and bubble. Default 4. */
  sideOffset?: number;
  /** Optional override for alignment along the chosen side.
   *  Default center; "start" / "end" anchor to that edge of the
   *  trigger. Rarely needed — useful when a tooltip near the
   *  viewport edge would otherwise be cut off and `side` already
   *  has the best vertical placement. */
  align?: "start" | "center" | "end";
}

export function Tooltip({
  children,
  content,
  side = "top",
  sideOffset = 4,
  align,
}: TooltipProps) {
  // Bypass when there's nothing to show. Three falsy values are
  // expected from real callers: explicit null (no data), undefined
  // (optional field missing), and "" (templated string that came
  // out empty, e.g. session_id || ""). Numeric 0 would render
  // correctly as a tooltip but no caller passes a bare number, so
  // we treat all falsy-but-not-zero as suppression.
  if (content === null || content === undefined || content === "") {
    return <>{children}</>;
  }
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          side={side}
          sideOffset={sideOffset}
          align={align}
          className="z-50 select-none rounded-md bg-neutral-900 px-2.5 py-1.5 text-xs text-white shadow-lg animate-in fade-in zoom-in-95 dark:bg-neutral-100 dark:text-neutral-900 max-w-xs"
        >
          {content}
          <RadixTooltip.Arrow className="fill-neutral-900 dark:fill-neutral-100" />
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
