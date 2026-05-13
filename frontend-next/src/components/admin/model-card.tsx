// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { Fragment } from "react";
import { MODELS } from "./model-data";

/**
 * Open the Sources & methodology details element and scroll the
 * targeted source row into view.
 *
 * DOM-based rather than state-lifted because the citation buttons
 * live inside ModelCard and the <details> element lives inside
 * ModelAnalysis — at sibling positions in the tree with no shared
 * parent that would naturally own the open/scroll state. A context
 * would work but adds plumbing for one interaction. The IDs the
 * function relies on are owned by ModelAnalysis:
 *
 *   • `#sources-methodology` on the <details> element itself, so
 *     we can flip `open` programmatically. The DOM <details> API
 *     is the cleanest way to expand the section — no React state
 *     to round-trip through, the change is purely visual.
 *
 *   • `#source-{id}` on each source row inside the details, so we
 *     can scroll the right one into view rather than just the
 *     section header.
 *
 * The rAF before scrollIntoView lets the details element's open
 * transition start, so the target row's measured position
 * accounts for the now-visible content above it. Without the
 * rAF, scrollIntoView would compute against the still-collapsed
 * layout and land the row at the wrong y-position.
 */
function openSourceAndScroll(id: string) {
  if (typeof document === "undefined") return;
  const details = document.getElementById("sources-methodology");
  if (details instanceof HTMLDetailsElement && !details.open) {
    details.open = true;
  }
  const target = document.getElementById(`source-${id}`);
  if (target) {
    requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: "smooth", block: "center" });

      // Trigger the brief amber wash. Two subtleties here:
      //
      //   1. Strip the class first and force a reflow before
      //      re-adding. CSS animations don't replay when the same
      //      class is set on an element that already has it — a
      //      second click on the same citation would scroll but
      //      not re-flash without this dance. Reading offsetWidth
      //      pushes the browser to commit the style change before
      //      the next add, which re-arms the animation.
      //
      //   2. Clean up via animationend (one-shot) rather than
      //      setTimeout. The .source-flash class itself is a no-op
      //      after the animation finishes, but leaving it on
      //      forever would prevent step 1's restart logic from
      //      working — the next click would have to remove a
      //      stale class before adding it. animationend keeps the
      //      DOM tidy and means there's exactly one place the
      //      class lives.
      //
      // For users with `prefers-reduced-motion: reduce`, the CSS
      // animation rule is wrapped in a media query that excludes
      // them, so the class becomes inert — animationend will never
      // fire and the class stays on the element. That's fine: it's
      // visually invisible (no animation defined for them) and a
      // future click still re-adds it without observable effect.
      target.classList.remove("source-flash");
      void target.offsetWidth;
      target.classList.add("source-flash");
      target.addEventListener(
        "animationend",
        () => target.classList.remove("source-flash"),
        { once: true },
      );
    });
  }
}

/**
 * Render a strength or weakness line, splitting `[N]` citation
 * markers into clickable buttons that jump to the corresponding
 * source row. Plain text (everything outside the brackets) passes
 * through as-is. Wraps the parts in one outer span so the
 * surrounding flex layout sees a single child rather than the
 * mix of buttons + text fragments inside.
 *
 * The regex `(\[\d+\])` captures `[N]` patterns and, by including
 * the capture group in `split`, keeps the matched tokens in the
 * resulting array — so `"foo [1] bar [2] baz".split(/(\[\d+\])/g)`
 * yields `["foo ", "[1]", " bar ", "[2]", " baz"]`. The map then
 * decides per-part: if the part matches the citation shape, render
 * a button; otherwise render the text.
 */
function CitationText({ text }: { text: string }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <span>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/);
        if (!match) {
          // Plain text segment. Using Fragment to avoid an extra
          // span wrapper per text run — keeps the flow inline and
          // the DOM lighter than a per-segment span would.
          return <Fragment key={i}>{part}</Fragment>;
        }
        const id = match[1];
        return (
          <button
            key={i}
            type="button"
            onClick={() => openSourceAndScroll(id)}
            // Inline citation styling — matches the amber link
            // treatment used for the source URLs themselves in
            // model-analysis.tsx, so the visual language is
            // "amber = clickable reference" throughout. The
            // hover/focus underline gives a clear "this is a link"
            // affordance without a permanent underline that would
            // compete with the body text. font-medium keeps the
            // bracket pair from disappearing into the surrounding
            // text weight.
            className="text-amber-600 dark:text-amber-400 hover:underline focus-visible:underline focus-visible:outline-none font-medium cursor-pointer"
            aria-label={`Source ${id} — jump to citations`}
          >
            {part}
          </button>
        );
      })}
    </span>
  );
}

export function ModelBadge({ model }: { model: "haiku" | "sonnet" | "opus" }) {
  // Inverted recipe for dark mode: the light-bg + saturated-text
  // pairing reads as a sticker in light mode (which is what these
  // badges want to feel like — a visible model tag). In dark mode
  // the same pairing renders as a bright white-ish pill on a dark
  // surface and pulls more attention than the badge deserves; the
  // /30-alpha 900-shade fill + 300-shade text recipe (same as
  // event-feed / conversation-table / metric-row pills in this
  // section) reads as a tinted chip instead, which is the
  // dark-mode aesthetic the rest of the admin uses.
  const colors = {
    haiku: "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-300",
    sonnet: "bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300",
    opus: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300",
  };
  const labels = {
    haiku: "Haiku 4.5",
    sonnet: "Sonnet 4.6",
    opus: "Opus 4.6",
  };
  return (
    <span
      className={`inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg ${colors[model]}`}
    >
      {labels[model]}
    </span>
  );
}

export function ModelCard({ modelKey }: { modelKey: "haiku" | "sonnet" | "opus" }) {
  const m = MODELS[modelKey];
  const accents: Record<string, string> = {
    haiku: "border-t-green-400",
    sonnet: "border-t-violet-400",
    opus: "border-t-amber-400",
  };
  const textColors: Record<string, string> = {
    haiku: "text-green-700",
    // Sonnet's 700-shade violet renders too dark against the
    // neutral-900 card surface in dark mode — the model name
    // becomes hard to read. Bumping to violet-400 (rgb(167 139
    // 250)) restores legibility while keeping the violet identity.
    // Haiku and Opus are not affected: green-700 and amber-700 both
    // hold up at this size against the dark card background.
    sonnet: "text-violet-700 dark:text-violet-400",
    opus: "text-amber-700",
  };
  const accent = accents[modelKey];
  const textColor = textColors[modelKey];

  return (
    <div className={`bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 border-t-[3px] ${accent}`}>
      <div className="flex items-baseline justify-between mb-2.5">
        <span className={`text-base font-bold ${textColor}`}>
          {m.name}
        </span>
        <span className="text-sm font-mono text-neutral-400 dark:text-neutral-500">
          ${m.input}/${m.output}/MTok
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-sm text-neutral-500 dark:text-neutral-400 mb-3">
        <span>Speed: {m.speed}</span>
        <span>Context: {m.context}</span>
        {/* Latency is the only meta field that currently carries a
         *  source citation (`Est. ~0.4s TTFT [3]`). Wrapping it in
         *  CitationText turns the [3] into the same clickable
         *  reference the strengths/weaknesses use. Speed / Context /
         *  ID are static and unannotated, so they stay as plain
         *  spans. */}
        <span>Latency: <CitationText text={m.latency} /></span>
        <span>
          ID: <code className="text-xs bg-neutral-100 dark:bg-neutral-800 px-1 rounded">
            {/* Strip the date suffix (8 trailing digits prefixed with "-")
                if present. Truncating to 3 segments via .split("-").slice(0,3)
                used to drop the model's minor version too — `claude-haiku-4-5-20251001`
                became `claude-haiku-4`, hiding which generation was actually
                running. The regex preserves the version while dropping only
                the build-date tail. Models without a date suffix
                (`claude-sonnet-4-6`) pass through unchanged. */}
            {m.id.replace(/-\d{8}$/, "")}
          </code>
        </span>
      </div>

      <div className="mb-2">
        <div className="text-xs font-semibold text-green-600 dark:text-green-400 mb-1">
          Strengths
        </div>
        {m.strengths.slice(0, 4).map((s, i) => (
          <div key={i} className="text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed flex gap-1.5">
            <span className="text-green-500 dark:text-green-400 flex-shrink-0">+</span>
            <CitationText text={s} />
          </div>
        ))}
      </div>
      <div>
        <div className="text-xs font-semibold text-red-500 dark:text-red-400 mb-1">
          Weaknesses
        </div>
        {m.weaknesses.slice(0, 3).map((w, i) => (
          <div key={i} className="text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed flex gap-1.5">
            <span className="text-red-400 dark:text-red-300 flex-shrink-0">&minus;</span>
            <CitationText text={w} />
          </div>
        ))}
      </div>
    </div>
  );
}
