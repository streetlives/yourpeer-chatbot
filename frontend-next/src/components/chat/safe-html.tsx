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
 * Sanitize an HTML string by walking the parsed DOM tree and
 * stripping disallowed tags and attributes. Uses the browser's
 * DOMParser — no external dependencies.
 *
 * Disallowed tags are unwrapped (their text content is kept).
 * Disallowed attributes are removed. Links get target="_blank"
 * and rel="noopener noreferrer" for safety.
 */
function sanitizeHtml(dirty: string): string {
  const doc = new DOMParser().parseFromString(dirty, "text/html");

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
 */
function containsHtml(text: string): boolean {
  return /<[a-z][\s\S]*?>/i.test(text);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface SafeHtmlProps {
  html: string;
  className?: string;
}

/** Strip all HTML tags — used for SSR fallback where DOMParser isn't available. */
function stripTags(html: string): string {
  return html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
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
