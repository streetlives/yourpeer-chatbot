// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useMemo, useState, useEffect } from "react";

/**
 * Allowed HTML tags and attributes for service descriptions.
 *
 * The Streetlives database contains service descriptions with light HTML
 * formatting — bullet lists, links, bold text. This allowlist defines
 * exactly which tags and attributes are safe to render. Everything else
 * is stripped (tags removed, text content preserved).
 */
const ALLOWED_TAGS = new Set([
  "a", "br", "p", "ul", "ol", "li",
  "b", "strong", "em", "i", "span",
]);

const ALLOWED_ATTRS: Record<string, Set<string>> = {
  a: new Set(["href"]),
};

/**
 * Decode HTML entities in-place using the browser's native parser.
 *
 * Why this exists: the Streetlives DB has descriptions where HTML
 * tags are stored already-entity-encoded (e.g. `&lt;/br&gt;` instead
 * of `</br>`). The fragment "Foo. &lt;/br&gt; Bar." would otherwise:
 *   1. Fail the `containsHtml` regex (no real `<` characters).
 *   2. Take the plain-text branch.
 *   3. Render via React, which decodes entities for display.
 *   4. User sees literal "</br>" between the two sentences.
 *
 * Decoding entities BEFORE the regex check means encoded tags get
 * treated as real tags and routed through the sanitizer, which
 * normalizes `</br>` to a real `<br />` line break.
 *
 * Implementation: use a `<textarea>`'s value getter — it's the
 * canonical "decode entities in a string" trick that doesn't fall
 * for HTML injection (textarea content is plain text, not HTML).
 * `String.prototype.replace` with a regex would only handle named
 * entities like &amp; and would miss numeric entities like &#60;.
 *
 * Safety: this DOES change the semantics of legitimately-encoded
 * text. A description with the literal characters "&lt;script&gt;"
 * (escaped because the author wanted those characters visible)
 * would now be treated as a real script tag and stripped by the
 * sanitizer. This is acceptable in our context because (a) the
 * Streetlives DB convention is "encoded tags are tags", not
 * "encoded tags are escaped text", and (b) the sanitizer's
 * allowlist makes the result safe regardless of input.
 */
function decodeEntities(text: string): string {
  if (typeof document === "undefined") return text; // SSR
  if (!text.includes("&")) return text; // fast path: no entities possible
  const ta = document.createElement("textarea");
  ta.innerHTML = text;
  return ta.value;
}

/**
 * Sanitize an HTML string by walking the parsed DOM tree and
 * stripping disallowed tags and attributes. Uses the browser's
 * DOMParser — no external dependencies.
 *
 * Disallowed tags are unwrapped (their text content is kept).
 * Disallowed attributes are removed. Links get target="_blank"
 * and rel="noopener noreferrer" for safety.
 */
function sanitizeHtml(dirty: string): string {
  // Pre-decode entities so &lt;/br&gt; etc. are treated as real tags.
  const decoded = decodeEntities(dirty);
  const doc = new DOMParser().parseFromString(decoded, "text/html");

  function walk(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeText(node.textContent || "");
    }

    if (node.nodeType !== Node.ELEMENT_NODE) return "";

    const el = node as Element;
    const tag = el.tagName.toLowerCase();

    // Recursively process children
    const children = Array.from(el.childNodes).map(walk).join("");

    if (!ALLOWED_TAGS.has(tag)) {
      // Strip the tag but keep its text content
      return children;
    }

    // Build sanitized attributes
    const allowedForTag = ALLOWED_ATTRS[tag];
    const attrs: string[] = [];

    if (allowedForTag) {
      for (const attrName of Array.from(el.attributes).map(a => a.name)) {
        if (allowedForTag.has(attrName)) {
          let value = el.getAttribute(attrName) || "";

          // For href: only allow http(s) and tel: protocols
          if (attrName === "href") {
            const lower = value.trim().toLowerCase();
            if (
              !lower.startsWith("http://") &&
              !lower.startsWith("https://") &&
              !lower.startsWith("tel:") &&
              !lower.startsWith("mailto:")
            ) {
              continue; // skip javascript:, data:, etc.
            }
          }

          // Escape characters that could break out of the attribute
          value = value
            .replace(/&/g, "&amp;")
            .replace(/"/g, "&quot;")
            .replace(/</g, "&lt;");

          attrs.push(`${attrName}="${value}"`);
        }
      }
    }

    // Links always open in new tab for safety
    if (tag === "a") {
      attrs.push('target="_blank"', 'rel="noopener noreferrer"');
    }

    const attrStr = attrs.length > 0 ? " " + attrs.join(" ") : "";

    // Self-closing tags
    if (tag === "br") return "<br />";

    return `<${tag}${attrStr}>${children}</${tag}>`;
  }

  return Array.from(doc.body.childNodes).map(walk).join("");
}

/** Escape text content to prevent injection through text nodes. */
function escapeText(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Detect whether a string contains HTML tags worth rendering.
 * Plain text (no tags) is returned as-is without dangerouslySetInnerHTML.
 *
 * The regex matches:
 *   • Real tag characters (`<a-z` / `</a-z`) — the typical case.
 *   • Entity-encoded tag characters (`&lt;a-z` / `&lt;/a-z`) — handles
 *     descriptions where the DB stored tags already entity-escaped,
 *     e.g. `&lt;/br&gt;`. Without this branch the entity-encoded form
 *     would fail the check, take the plain-text branch, and render
 *     as visible "</br>" text after React decoded the entities.
 *
 * Catching closing tags matters because the Streetlives DB occasionally
 * has malformed input like `</br>` (a closing tag for a void element)
 * — strings like that get routed through DOMParser, which the HTML
 * spec says treats `</br>` as equivalent to `<br>`. The sanitizer
 * then emits a real `<br />` line break.
 *
 * Bare `<` (e.g., math text "x < 5") still slips through and reaches
 * sanitizeHtml — DOMParser handles those correctly too: it treats them
 * as text since `<5` doesn't satisfy any tag-name pattern. So either
 * way the output is correct; the regex is purely a fast-path so plain
 * text doesn't pay for DOMParser instantiation.
 */
function containsHtml(text: string): boolean {
  return /<\/?[a-z][\s\S]*?>|&lt;\/?[a-z]/i.test(text);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SafeHtmlProps {
  html: string;
  className?: string;
}

/** Strip all HTML tags — used for SSR fallback where DOMParser isn't available.
 *
 * Decode entities first so &lt;br&gt; etc. are treated as tags during
 * the strip pass — same rationale as `sanitizeHtml`'s pre-decode.
 * On the server `decodeEntities` short-circuits to the input unchanged
 * (no `document` available), so this is effectively a regex-only strip
 * during SSR; the client takes over after hydration with the full
 * decode → sanitize pipeline. */
function stripTags(html: string): string {
  return decodeEntities(html).replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

/**
 * Render an HTML string with sanitization.
 *
 * If the string contains no HTML tags, it's rendered as plain text
 * (no dangerouslySetInnerHTML). If it contains HTML, it's sanitized
 * with an allowlist of safe tags and rendered.
 *
 * SSR: renders stripped plain text (DOMParser isn't available on the server).
 * Client: renders sanitized HTML after hydration.
 *
 * Usage:
 *   <SafeHtml html={service.description} className="text-xs text-neutral-500" />
 */
export function SafeHtml({ html, className }: SafeHtmlProps) {
  const [mounted, setMounted] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration-safe mount marker; runs once, fires no cascading render because the component immediately switches branches
  useEffect(() => setMounted(true), []);

  const hasHtml = useMemo(() => containsHtml(html), [html]);

  // SSR or plain text — render as text (no dangerouslySetInnerHTML)
  if (!mounted || !hasHtml) {
    return <div className={className}>{hasHtml ? stripTags(html) : html}</div>;
  }

  // Client with HTML — sanitize and render
  const clean = sanitizeHtml(html);
  return (
    <div
      className={className}
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
}
