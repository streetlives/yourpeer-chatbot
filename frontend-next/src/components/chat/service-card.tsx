// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState, useEffect } from "react";
import { MapPin, Phone, Mail, Clock, CheckCircle, AlertTriangle, ChevronDown, ExternalLink } from "lucide-react";
import type { ServiceResult } from "@/lib/chat/types";
import { LocationFeedbackRow } from "./location-feedback-row";
import { CallConfirmDialog } from "./call-confirm-dialog";
import { ReviewDetailDialog } from "./review-detail-dialog";
import { SafeHtml } from "./safe-html";
import { formatPhone } from "@/lib/chat/format-phone";

/**
 * Extract a display-friendly domain from a website URL. Used by the
 * Website button to show users where they're being navigated *before*
 * they tap, addressing the Aurora-session feedback ("This is the
 * separate website. So this is run by street work and not by us
 * anymore?") — the domain answers the provenance question that the
 * external-link icon alone doesn't.
 *
 * Strips a leading "www." since users don't think of that as part of
 * the brand identity. Returns null on parse failure (scheme-less
 * URLs, malformed input, empty string) so callers can degrade
 * gracefully — the button still renders, just without the subtitle.
 *
 * Note: scheme-less URLs in `service.website` (e.g. "alifoneny.org"
 * with no http:// prefix) will fail to parse here AND would also be
 * broken hyperlinks in the browser — `<a href="alifoneny.org">`
 * resolves to a relative path. Showing an icon-only fallback when
 * the URL doesn't parse correctly matches that broken-link reality;
 * pretending we know the domain via prefix-prepending would mislead.
 * Backend should normalize source data to always include the scheme.
 */
function extractDomain(url: string): string | null {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

// Review preview thresholds.
//
// On mobile, vertical space is precious — every line on the service
// card matters because users have to scroll past N cards to evaluate
// options, and reviews that take 3+ lines compound into a real cost.
// The "Read more" dialog is the right surface for the full text.
//
// Both mobile and desktop card widths wrap reviews at ~35 chars/line.
// The difference isn't horizontal — it's visual context: on mobile
// the card is one of many that fill the chat region; on desktop the
// card sits in a roomier carousel with more whitespace around it.
// So mobile gets the tight threshold, desktop gets the original.
//
// Mobile: 60 chars ≈ 2 lines at 280px card width (px-3 padding, text-xs,
// ~36 chars/line). Keeping below the line-wrap threshold means line-clamp-2
// is a safety net only. "Read more" is rendered outside the clamped span
// so it's always visible regardless of whether clamp fires.
const REVIEW_TRUNCATE_AT_MOBILE = 60;
const REVIEW_TRUNCATE_TO_MOBILE = 57;
const REVIEW_TRUNCATE_AT_DESKTOP = 120;
const REVIEW_TRUNCATE_TO_DESKTOP = 117;

/**
 * Hook returning the right truncation threshold for the current viewport.
 *
 * SSR-safe by design: the initial value is the desktop threshold (which
 * is what the SSR-rendered HTML will use), then a layout effect after
 * mount reads the real viewport and re-renders if it's mobile. The
 * intermediate "show desktop preview, then re-render to mobile preview"
 * step happens in a single tick, before the user sees anything, so
 * there's no visible flash. This pattern avoids the hydration mismatch
 * that would result from reading window.innerWidth directly in render.
 *
 * Threshold matches Tailwind's `sm:` breakpoint (640px) for consistency
 * with the rest of the responsive layout — same boundary used by the
 * header layout, chat region min-height, and quick-reply padding fixes.
 *
 * Doesn't subscribe to resize events. The viewport doesn't change
 * frequently in practice (orientation change or window resize), and
 * the cost of subscribing — adding a listener per service card on
 * the page — outweighs the rare benefit. If a user resizes mid-session
 * across the breakpoint, their reviews stay at the previous size until
 * the next message arrives and re-renders the card. Acceptable.
 */
export function useReviewTruncate(): { at: number; to: number } {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- matchMedia is undefined during SSR; one-time bridge from SSR default (desktop) to real client viewport
    setIsMobile(window.matchMedia("(max-width: 639px)").matches);
  }, []);
  return isMobile
    ? { at: REVIEW_TRUNCATE_AT_MOBILE, to: REVIEW_TRUNCATE_TO_MOBILE }
    : { at: REVIEW_TRUNCATE_AT_DESKTOP, to: REVIEW_TRUNCATE_TO_DESKTOP };
}

interface ServiceCardProps {
  service: ServiceResult;
  isActive?: boolean;
  index?: number;
  total?: number;
  reviewTruncate: { at: number; to: number };
}

function StatusBadge({ status, allDay }: { status?: string; allDay?: boolean }) {
  // All-day case: collapse "Open now" + "12:00 AM – 11:59 PM" into a
  // single "Open 24 hours" pill. The redundant hours line is hidden by
  // the caller. Backend signals all-day by returning the literal
  // string "Open 24 hours" as `hours_today`; see
  // _compute_schedule_status in backend/app/rag/query_templates.py.
  if (allDay) {
    return (
      <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-green-100 text-green-800 dark:bg-green-300 dark:text-green-950">
        Open 24 hours
      </span>
    );
  }
  if (status === "open") {
    return (
      <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-green-100 text-green-800 dark:bg-green-300 dark:text-green-950">
        Open now
      </span>
    );
  }
  if (status === "closed") {
    return (
      <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-red-50 text-red-700 dark:bg-red-300 dark:text-red-950">
        Closed
      </span>
    );
  }
  return (
    <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-neutral-100 text-neutral-500 dark:bg-neutral-300 dark:text-neutral-900">
      Call for hours
    </span>
  );
}

// Sentinel for the all-day case. Must match the literal string the
// backend emits in _compute_schedule_status. If the backend's wording
// ever changes, update this constant — TypeScript won't catch the
// drift since both ends are plain strings. The `verify:contract`
// sentinel covers the related is_open enum but not this string.
const HOURS_ALL_DAY_SENTINEL = "Open 24 hours";

function ValidatedBadge({ dateStr }: { dateStr?: string }) {
  if (!dateStr) {
    return (
      <span className="inline-flex items-center gap-1 text-[0.65rem] font-medium text-amber-600 dark:text-amber-400">
        <AlertTriangle size={11} aria-hidden="true" />
        Unverified — call to confirm
      </span>
    );
  }

  const validated = new Date(dateStr);
  if (isNaN(validated.getTime())) return null;

  const now = new Date();
  const diffMs = now.getTime() - validated.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  let label: string;
  if (diffDays === 0) label = "today";
  else if (diffDays === 1) label = "yesterday";
  else if (diffDays < 7) label = `${diffDays} days ago`;
  else if (diffDays < 30) {
    const weeks = Math.floor(diffDays / 7);
    label = weeks === 1 ? "1 week ago" : `${weeks} weeks ago`;
  } else if (diffDays < 365) {
    const months = Math.floor(diffDays / 30);
    label = months === 1 ? "1 month ago" : `${months} months ago`;
  } else {
    label = "over a year ago";
  }

  const isRecent = diffDays <= 90;
  const isStale = diffDays > 180;

  if (isStale) {
    return (
      <span className="inline-flex items-center gap-1 text-[0.65rem] font-medium text-amber-600 dark:text-amber-400">
        <AlertTriangle size={11} aria-hidden="true" />
        Unverified — call ahead
      </span>
    );
  }

  return (
    <span className={`inline-flex items-center gap-1 text-[0.65rem] font-medium ${
      isRecent ? "text-green-600 dark:text-green-400" : "text-neutral-400 dark:text-neutral-500"
    }`}>
      <CheckCircle size={11} aria-hidden="true" />
      Verified {label}
    </span>
  );
}

// Emoji map for co-located service categories
const ALSO_EMOJI: Record<string, string> = {
  // Shelter & Housing
  "Shelter": "\u{1F6CF}\uFE0F",
  "Drop-in Center": "\u{1F3E0}",
  "Warming Center": "\u{1F525}",
  "Crisis": "\u{1F198}",
  // Food
  "Food": "\u{1F37D}\uFE0F",
  "Food Pantry": "\u{1F37D}\uFE0F",
  "Soup Kitchen": "\u{1F372}",
  "Farmers Market": "\u{1F966}",
  "Food Benefits (SNAP)": "\u{1F4CB}",
  "Hot Meals": "\u{1F372}",
  // Clothing
  "Clothing": "\u{1F455}",
  "Interview Clothing": "\u{1F454}",
  // Personal Care
  "Shower": "\u{1F6BF}",
  "Laundry": "\u{1F9FC}",
  "Toiletries": "\u{1F9F4}",
  "Haircut": "\u{1F487}",
  "Restrooms": "\u{1F6BB}",
  // Health
  "Health": "\u{1F3E5}",
  "Harm Reduction": "\u{1F48A}",
  "Syringe Exchange": "\u{1F489}",
  "Overdose Prevention": "\u{1F489}",
  "Substance Use Help": "\u{1F49A}",
  // Mental Health
  "Mental Health": "\u{1F9E0}",
  // Legal
  "Legal Services": "\u2696\uFE0F",
  "Immigration Services": "\u{1F30D}",
  // Employment & Education
  "Employment": "\u{1F4BC}",
  "Education": "\u{1F4DA}",
  // Benefits & Support
  "Benefits": "\u{1F4CB}",
  "Case Management": "\u{1F4C2}",
  "Referral": "\u{1F517}",
  "Support Groups": "\u{1F91D}",
  "Mail": "\u{1F4EC}",
  "Free Wifi": "\u{1F4F6}",
  "Financial Help": "\u{1F4B0}",
  "Intake": "\u{1F4DD}",
  // Family & Youth
  "Baby Supplies": "\u{1F476}",
  "Senior Center": "\u{1F9D3}",
};

const ALSO_HERE_VISIBLE = 3;

export function ServiceCard({ service, isActive, index, total, reviewTruncate }: ServiceCardProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [alsoExpanded, setAlsoExpanded] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);

  const name = service.service_name || "Service";
  const cardLabel =
    index !== undefined && total !== undefined
      ? `${name}, result ${index + 1} of ${total}`
      : name;

  // Determine if there are any detail fields to show
  const hasDetails = !!(
    service.address ||
    service.phone ||
    service.email ||
    service.accessibility ||
    service.description ||
    (service.required_documents && service.required_documents.length > 0) ||
    (service.languages && service.languages.length > 0 &&
      !(service.languages.length === 1 && service.languages[0] === "English"))
  );

  const alsoItems = service.also_available || [];
  const alsoHiddenCount = alsoItems.length - ALSO_HERE_VISIBLE;
  // When only 1 item would be hidden, show it inline instead of "+1 more"
  // — the button takes the same space as just showing the name.
  const alsoVisible = alsoExpanded || alsoHiddenCount <= 1
    ? alsoItems
    : alsoItems.slice(0, ALSO_HERE_VISIBLE);

  return (
    <div
      role="listitem"
      aria-label={cardLabel}
      aria-current={isActive ? "true" : undefined}
      className="flex-shrink-0 w-[280px] max-w-[calc(100vw-5rem)] snap-start bg-white border border-neutral-200 rounded-2xl p-4 flex flex-col gap-2.5 transition-all hover:border-neutral-300 hover:shadow-md dark:bg-neutral-800 dark:border-neutral-700 dark:hover:border-neutral-600"
    >
      {/* Name */}
      <div className="text-[0.95rem] font-semibold tracking-tight text-neutral-900 dark:text-neutral-100 leading-snug break-words">
        {name}
      </div>

      {/* Organization + Verified */}
      <div className="flex flex-col gap-0.5 -mt-1">
        {service.organization && (
          <div className="text-xs text-neutral-500 dark:text-neutral-400 font-medium">
            {service.organization}
          </div>
        )}
        <ValidatedBadge dateStr={service.last_validated_at} />
      </div>

      {/* Hours + status. When the location is open all day, the
       * StatusBadge renders "Open 24 hours" and the separate hours
       * line is suppressed — showing "Open 24 hours" alongside
       * "12:00 AM – 11:59 PM" reads as redundant and confusing
       * (the literal endpoints suggest "narrowly NOT 24 hours").
       * The all-day sentinel is set by the backend; see
       * HOURS_ALL_DAY_SENTINEL above. */}
      <div className="flex items-center gap-2">
        <StatusBadge
          status={service.is_open}
          allDay={service.hours_today === HOURS_ALL_DAY_SENTINEL}
        />
        {service.hours_today && service.hours_today !== HOURS_ALL_DAY_SENTINEL && (
          <span className="inline-flex items-center gap-1 text-xs text-neutral-500 dark:text-neutral-400 whitespace-nowrap">
            <Clock size={14} className="text-neutral-400 dark:text-neutral-500 flex-shrink-0" aria-hidden="true" />
            <span>{service.hours_today}</span>
          </span>
        )}
      </div>

      {/* Review highlight — visible by default (builds trust). When the
          full text exceeds the inline preview length, render as a button
          that opens the detail dialog so users can read the rest.

          Two-layer truncation:
            1. Character-count truncate (REVIEW_TRUNCATE_AT_MOBILE/DESKTOP)
               cuts the preview text at a natural prose length.
            2. CSS line-clamp-2 on mobile is a defensive cap: if the
               truncated text still wraps to more than 2 lines (long
               unbreakable words, very narrow viewports, etc.), clamp
               kicks in and shows ellipsis at line 2. sm:line-clamp-none
               restores normal text flow on desktop where vertical
               space is plentiful. */}
      {service.review_highlight && (() => {
        const { at, to } = reviewTruncate;
        const truncated = service.review_highlight.length > at;
        const preview = truncated
          ? service.review_highlight.slice(0, to) + "…"
          : service.review_highlight;
        const baseCls = "text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed bg-neutral-50 border border-neutral-100 rounded-lg px-3 py-2 italic dark:bg-neutral-700/60 dark:border-neutral-700";
        if (!truncated) {
          return (
            <div className={baseCls}>
              <span aria-hidden="true">💬 </span>
              {preview}
            </div>
          );
        }
        return (
          <button
            type="button"
            onClick={() => setReviewOpen(true)}
            aria-label={`Read full review for ${name}`}
            className={`${baseCls} line-clamp-2 sm:line-clamp-none text-left w-full transition hover:bg-neutral-100 hover:border-neutral-200 cursor-pointer dark:hover:bg-neutral-800 dark:hover:border-neutral-700`}
          >
            <span aria-hidden="true">💬 </span>
            {preview}
            <span className="ml-1 not-italic font-medium text-amber-700 dark:text-amber-400">
              Read more
            </span>
          </button>
        );
      })()}

      {reviewOpen && service.review_highlight && (
        <ReviewDetailDialog
          review={service.review_highlight}
          locationName={service.organization || name}
          onClose={() => setReviewOpen(false)}
        />
      )}

      {/* Badges row — referral, eligibility, fees side by side */}
      {(service.fees || service.requires_membership || service.eligibility_summary) && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {service.requires_membership && (
            <span className="inline-block text-xs font-semibold text-amber-800 bg-amber-100 px-2 py-0.5 rounded-lg dark:text-amber-950 dark:bg-amber-300">
              Ref. may be required
            </span>
          )}
          {service.eligibility_summary && (
            <span className="inline-block text-xs font-semibold text-blue-800 bg-blue-50 px-2 py-0.5 rounded-lg dark:text-blue-950 dark:bg-blue-300">
              {service.eligibility_summary}
            </span>
          )}
          {service.fees && (
            <span className="inline-block text-xs font-semibold text-green-800 bg-green-100 px-2 py-0.5 rounded-lg dark:text-green-950 dark:bg-green-300">
              {service.fees}
            </span>
          )}
        </div>
      )}

      {/* ── Collapsible Details ── */}
      <DetailsSection service={service} hasDetails={hasDetails} detailsOpen={detailsOpen} setDetailsOpen={setDetailsOpen} />

      {/* Also available at this location — limited to 3 with expand */}
      {alsoItems.length > 0 && (
        <div className="pt-1 border-t border-neutral-100 dark:border-neutral-800">
          <div className="text-[0.65rem] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500 mb-1.5">
            Also here
          </div>
          <div className="flex flex-wrap gap-1">
            {alsoVisible.map((cat) => (
              <span
                key={cat}
                className="inline-block text-[0.68rem] font-medium px-2 py-0.5 rounded-md bg-neutral-50 border border-neutral-200 text-neutral-600 dark:bg-neutral-700 dark:border-neutral-600 dark:text-neutral-200"
              >
                {ALSO_EMOJI[cat] || "\u2022"} {cat}
              </span>
            ))}
            {!alsoExpanded && alsoHiddenCount > 1 && (
              <button
                type="button"
                onClick={() => setAlsoExpanded(true)}
                className="inline-block text-[0.68rem] font-medium px-2 py-0.5 rounded-md bg-neutral-50 border border-neutral-200 text-blue-600 hover:bg-blue-50 hover:border-blue-200 transition-colors dark:bg-neutral-700 dark:border-neutral-600 dark:text-blue-300 dark:hover:bg-blue-950/40 dark:hover:border-blue-900"
              >
                +{alsoHiddenCount} more
              </button>
            )}
          </div>
        </div>
      )}

      {/* Action buttons — mt-auto pins buttons + footer to card bottom */}
      <ActionButtons service={service} name={service.organization || name} />

      {/* Footer: Learn More + Rate — shared row */}
      {(service.yourpeer_url || service.service_id) && (
        <LocationFeedbackRow
          serviceId={service.service_id || ""}
          locationName={name}
          learnMoreUrl={service.yourpeer_url}
        />
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// SHARED SUB-COMPONENTS (used by both ServiceCard and LocationCard)
// ---------------------------------------------------------------------------

function DetailsSection({ service, hasDetails, detailsOpen, setDetailsOpen }: {
  service: ServiceResult;
  hasDetails: boolean;
  detailsOpen: boolean;
  setDetailsOpen: (v: boolean) => void;
}) {
  if (!hasDetails) return null;
  return (
    <div className="border-t border-neutral-100 pt-1 dark:border-neutral-800">
      <button
        type="button"
        onClick={() => setDetailsOpen(!detailsOpen)}
        aria-expanded={detailsOpen}
        className="flex items-center justify-between w-full py-1.5 text-xs font-semibold uppercase tracking-wider text-blue-600 underline underline-offset-2 hover:text-blue-800 transition-colors dark:text-blue-400 dark:hover:text-blue-300"
      >
        <span>Details</span>
        <ChevronDown
          size={14}
          className={`transition-transform duration-200 ${detailsOpen ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {detailsOpen && (
        <div className="flex flex-col gap-2 pb-1 animate-in fade-in slide-in-from-top-1 duration-150">
          {service.address && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 dark:text-neutral-400 leading-snug">
              <MapPin size={14} className="text-neutral-400 dark:text-neutral-500 mt-0.5 flex-shrink-0" aria-hidden="true" />
              <span>{service.address}</span>
            </div>
          )}
          {service.phone && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 dark:text-neutral-400">
              <Phone size={14} className="text-neutral-400 dark:text-neutral-500 mt-0.5 flex-shrink-0" aria-hidden="true" />
              <span>{formatPhone(service.phone)}</span>
            </div>
          )}
          {service.email && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 dark:text-neutral-400">
              <Mail size={14} className="text-neutral-400 dark:text-neutral-500 mt-0.5 flex-shrink-0" aria-hidden="true" />
              <a
                href={`mailto:${service.email}`}
                className="underline underline-offset-2 hover:text-neutral-700 dark:hover:text-neutral-200 break-all"
              >
                {service.email}
              </a>
            </div>
          )}
          {service.accessibility && (
            <div className="flex items-start gap-2 text-sm text-neutral-500 dark:text-neutral-400 leading-snug">
              <span className="mt-0.5 flex-shrink-0 text-sm" aria-hidden="true">♿</span>
              <span>{service.accessibility}</span>
            </div>
          )}
          {service.description && (
            <SafeHtml
              html={service.description}
              className="text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:list-decimal [&_ol]:pl-4 [&_li]:mb-0.5 [&_a]:text-blue-600 dark:[&_a]:text-blue-400 [&_a]:underline [&_p]:mb-1 last:[&_p]:mb-0"
            />
          )}
          {service.required_documents && service.required_documents.length > 0 && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 dark:text-neutral-400 leading-snug">
              <span className="mt-0.5 flex-shrink-0" aria-hidden="true">📄</span>
              <span>Bring: {service.required_documents.join(", ")}</span>
            </div>
          )}
          {service.languages && service.languages.length > 0 &&
            !(service.languages.length === 1 && service.languages[0] === "English") && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 dark:text-neutral-400 leading-snug">
              <span className="mt-0.5 flex-shrink-0" aria-hidden="true">🗣️</span>
              <span>{service.languages.join(", ")}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActionButtons({ service, name }: { service: ServiceResult; name: string }) {
  const [showCallConfirm, setShowCallConfirm] = useState(false);

  // Pre-compute the domain once per render. Used both in the
  // visible subtitle and the aria-label, so we only want to parse
  // the URL once. Null when extraction fails — the button degrades
  // to the single-line icon-only form in that case.
  const websiteDomain = service.website ? extractDomain(service.website) : null;

  return (
    <>
      <div className="flex gap-1.5 pt-1 mt-auto" role="group" aria-label={`Actions for ${name}`}>
        {/* All three action buttons get `flex-1 min-w-0`. flex-1 alone
         * gives `flex: 1 1 0%` but flex items default to `min-width:
         * auto` (= min-content), so an item with long unbreakable
         * content can refuse to shrink below that — pushing the row
         * out of equal thirds and starving the other buttons.
         *
         * Specifically: when service.website is something like
         * `agapehome.churchtrac.com`, the domain string has no break
         * opportunities, so its min-content width is the full string.
         * Without min-w-0, the website button keeps that width and
         * the inner `truncate` span never has a constrained parent to
         * ellipsize against.
         *
         * `min-w-0` on each item overrides the default and lets flex
         * actually distribute width. The inner truncate then engages
         * inside the website button's 1/3 share.
         */}
        {service.phone && (
          <button
            type="button"
            onClick={() => setShowCallConfirm(true)}
            aria-label={`Call ${name}`}
            className="flex-1 min-w-0 py-2 rounded-lg border border-neutral-900 bg-neutral-900 text-center text-xs font-semibold text-white transition hover:bg-neutral-700 dark:border-neutral-400 dark:bg-neutral-400 dark:text-neutral-900 dark:hover:bg-neutral-300 dark:hover:border-neutral-300"
          >
            Call
          </button>
        )}
        {service.address && (
          <a
            href={`https://maps.google.com/?q=${encodeURIComponent(service.address)}`}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Get directions to ${name} (opens in new tab)`}
            className="flex-1 min-w-0 py-2 rounded-lg border border-amber-300 bg-amber-300 text-center text-xs font-semibold text-neutral-900 transition hover:bg-amber-400 hover:border-amber-400 dark:border-[rgba(255,213,79,0.75)] dark:bg-[rgba(255,213,79,0.75)] dark:hover:bg-[rgba(255,213,79,0.95)] dark:hover:border-[rgba(255,213,79,0.95)]"
          >
            <span className="block leading-tight">
              Directions
            </span>
            {websiteDomain && (
              <span
                className="block text-[0.65rem] font-normal text-neutral-700 leading-tight mt-0.5 truncate px-1"
                title="Google Maps"
              >
                Google Maps
              </span>
            )}
          </a>
        )}
        {service.website && (
          <a
            href={service.website}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={
              websiteDomain
                ? `Visit ${name} website at ${websiteDomain} (opens in new tab)`
                : `Visit ${name} website (opens in new tab)`
            }
            className="flex-1 min-w-0 py-2 rounded-lg border border-neutral-200 bg-neutral-50 text-center text-xs font-semibold text-neutral-900 transition hover:bg-neutral-100 hover:border-neutral-300 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-neutral-800 dark:hover:border-neutral-600"
          >
            <span className="block leading-tight">
              Website
              <ExternalLink size={11} aria-hidden="true" className="inline ml-1 -mt-0.5" />
            </span>
            {websiteDomain && (
              <span
                className="block text-[0.65rem] font-normal text-neutral-500 dark:text-neutral-400 leading-tight mt-0.5 truncate px-1"
                title={websiteDomain}
              >
                {websiteDomain}
              </span>
            )}
          </a>
        )}
      </div>

      {showCallConfirm && service.phone && (
        <CallConfirmDialog
          phone={service.phone}
          name={name}
          onConfirm={() => setShowCallConfirm(false)}
          onCancel={() => setShowCallConfirm(false)}
        />
      )}
    </>
  );
}


// ---------------------------------------------------------------------------
// LOCATION CARD — groups multiple services at the same physical location
// ---------------------------------------------------------------------------

interface LocationCardProps {
  services: ServiceResult[];
  isActive?: boolean;
  index?: number;
  total?: number;
  reviewTruncate: { at: number; to: number };
}

export function LocationCard({ services, isActive, index, total, reviewTruncate }: LocationCardProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);

  const primary = services[0];
  const orgName = primary.organization || "Location";
  const cardLabel =
    index !== undefined && total !== undefined
      ? `${orgName} (${services.length} services), result ${index + 1} of ${total}`
      : orgName;

  const bestVerified = services.reduce((best, svc) => {
    if (!svc.last_validated_at) return best;
    if (!best) return svc.last_validated_at;
    return svc.last_validated_at > best ? svc.last_validated_at : best;
  }, null as string | null);

  const review = services.find((s) => s.review_highlight)?.review_highlight;

  const hasDetails = !!(
    primary.address ||
    primary.phone ||
    primary.email ||
    primary.accessibility ||
    (primary.languages && primary.languages.length > 0 &&
      !(primary.languages.length === 1 && primary.languages[0] === "English"))
  );

  // Detect shared fields — if all services have the same value, show once
  const allSameHours =
    services.every((s) => s.hours_today === primary.hours_today && s.is_open === primary.is_open);
  const allSameBadges =
    services.every(
      (s) =>
        s.requires_membership === primary.requires_membership &&
        s.eligibility_summary === primary.eligibility_summary &&
        s.fees === primary.fees,
    );

  return (
    <div
      role="listitem"
      aria-label={cardLabel}
      aria-current={isActive ? "true" : undefined}
      className="flex-shrink-0 w-[280px] max-w-[calc(100vw-5rem)] snap-start bg-white border border-neutral-200 rounded-2xl p-4 flex flex-col gap-2.5 transition-all hover:border-neutral-300 hover:shadow-md dark:bg-neutral-800 dark:border-neutral-700 dark:hover:border-neutral-600"
    >
      {/* Organization header */}
      <div className="flex flex-col gap-0.5">
        <div className="text-[0.95rem] font-semibold tracking-tight text-neutral-900 dark:text-neutral-100 leading-snug break-words">
          {orgName}
        </div>
        <ValidatedBadge dateStr={bestVerified ?? undefined} />
      </div>

      {/* Shared hours — shown once when identical. All-day handling
       * mirrors ServiceCard: pill says "Open 24 hours" and the
       * literal range is suppressed. See HOURS_ALL_DAY_SENTINEL. */}
      {allSameHours && (
        <div className="flex items-center gap-2">
          <StatusBadge
            status={primary.is_open}
            allDay={primary.hours_today === HOURS_ALL_DAY_SENTINEL}
          />
          {primary.hours_today && primary.hours_today !== HOURS_ALL_DAY_SENTINEL && (
            <span className="inline-flex items-center gap-1 text-xs text-neutral-500 dark:text-neutral-400 whitespace-nowrap">
              <Clock size={14} className="text-neutral-400 dark:text-neutral-500 flex-shrink-0" aria-hidden="true" />
              <span>{primary.hours_today}</span>
            </span>
          )}
        </div>
      )}

      {/* Shared badges — shown once when identical */}
      {allSameBadges && (primary.fees || primary.requires_membership || primary.eligibility_summary) && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {primary.requires_membership && (
            <span className="inline-block text-xs font-semibold text-amber-800 bg-amber-100 px-2 py-0.5 rounded-lg dark:text-amber-950 dark:bg-amber-300">
              Ref. may be required
            </span>
          )}
          {primary.eligibility_summary && (
            <span className="inline-block text-xs font-semibold text-blue-800 bg-blue-50 px-2 py-0.5 rounded-lg dark:text-blue-950 dark:bg-blue-300">
              {primary.eligibility_summary}
            </span>
          )}
          {primary.fees && (
            <span className="inline-block text-xs font-semibold text-green-800 bg-green-100 px-2 py-0.5 rounded-lg dark:text-green-950 dark:bg-green-300">
              {primary.fees}
            </span>
          )}
        </div>
      )}

      {/* Service list */}
      <div className="pt-1 border-t border-neutral-100 dark:border-neutral-800">
        <div className="text-[0.65rem] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-500 mb-1.5">
          Services
        </div>
        <div className="flex flex-col gap-2">
          {services.map((svc, i) => {
            const hasPerServiceInfo = !allSameHours || !allSameBadges;
            return (
              <div
                key={svc.service_id || i}
                className={`flex flex-col gap-1.5 ${i > 0 && hasPerServiceInfo ? "pt-2 border-t border-neutral-100 dark:border-neutral-800" : ""}`}
              >
                {/* Service name */}
                <div className="text-xs font-semibold text-neutral-700 dark:text-neutral-200">
                  {ALSO_EMOJI[svc.service_name || ""] ? `${ALSO_EMOJI[svc.service_name || ""]} ` : ""}{svc.service_name || "Service"}
                </div>

                {/* Per-service hours — only when they differ.
                 * All-day handling identical to ServiceCard; see
                 * HOURS_ALL_DAY_SENTINEL. */}
                {!allSameHours && (
                  <div className="flex items-center gap-2">
                    <StatusBadge
                      status={svc.is_open}
                      allDay={svc.hours_today === HOURS_ALL_DAY_SENTINEL}
                    />
                    {svc.hours_today && svc.hours_today !== HOURS_ALL_DAY_SENTINEL && (
                      <span className="inline-flex items-center gap-1 text-xs text-neutral-500 dark:text-neutral-400 whitespace-nowrap">
                        <Clock size={12} className="text-neutral-400 dark:text-neutral-500 flex-shrink-0" aria-hidden="true" />
                        <span>{svc.hours_today}</span>
                      </span>
                    )}
                  </div>
                )}

                {/* Per-service badges — only when they differ */}
                {!allSameBadges && (svc.fees || svc.requires_membership || svc.eligibility_summary) && (
                  <div className="flex items-center gap-1 flex-wrap">
                    {svc.requires_membership && (
                      <span className="inline-block text-[0.65rem] font-semibold text-amber-800 bg-amber-100 px-1.5 py-0.5 rounded-md dark:text-amber-950 dark:bg-amber-300">
                        Ref. may be required
                      </span>
                    )}
                    {svc.eligibility_summary && (
                      <span className="inline-block text-[0.65rem] font-semibold text-blue-800 bg-blue-50 px-1.5 py-0.5 rounded-md dark:text-blue-950 dark:bg-blue-300">
                        {svc.eligibility_summary}
                      </span>
                    )}
                    {svc.fees && (
                      <span className="inline-block text-[0.65rem] font-semibold text-green-800 bg-green-100 px-1.5 py-0.5 rounded-md dark:text-green-950 dark:bg-green-300">
                        {svc.fees}
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Review highlight — clickable to open the full text when
          truncated. See ServiceCard above for the same pattern,
          including the rationale for the two-layer truncate
          (character count + line-clamp). */}
      {review && (() => {
        const { at, to } = reviewTruncate;
        const truncated = review.length > at;
        const preview = truncated
          ? review.slice(0, to) + "…"
          : review;
        const baseCls = "text-xs text-neutral-500 dark:text-neutral-400 leading-relaxed bg-neutral-50 border border-neutral-100 rounded-lg px-3 py-2 italic dark:bg-neutral-700/60 dark:border-neutral-700";
        if (!truncated) {
          return (
            <div className={baseCls}>
              <span aria-hidden="true">💬 </span>
              {preview}
            </div>
          );
        }
        return (
          <button
            type="button"
            onClick={() => setReviewOpen(true)}
            aria-label={`Read full review for ${orgName}`}
            className={`${baseCls} line-clamp-2 sm:line-clamp-none text-left w-full transition hover:bg-neutral-100 hover:border-neutral-200 cursor-pointer dark:hover:bg-neutral-800 dark:hover:border-neutral-700`}
          >
            <span aria-hidden="true">💬 </span>
            {preview}
            <span className="ml-1 not-italic font-medium text-amber-700 dark:text-amber-400">
              Read more
            </span>
          </button>
        );
      })()}

      {reviewOpen && review && (
        <ReviewDetailDialog
          review={review}
          locationName={orgName}
          onClose={() => setReviewOpen(false)}
        />
      )}

      {/* Shared details */}
      <DetailsSection service={primary} hasDetails={hasDetails} detailsOpen={detailsOpen} setDetailsOpen={setDetailsOpen} />

      {/* Shared action buttons */}
      <ActionButtons service={primary} name={orgName} />

      {/* Footer */}
      {(primary.yourpeer_url || primary.service_id) && (
        <LocationFeedbackRow
          serviceId={primary.service_id || ""}
          locationName={orgName}
          learnMoreUrl={primary.yourpeer_url}
        />
      )}
    </div>
  );
}