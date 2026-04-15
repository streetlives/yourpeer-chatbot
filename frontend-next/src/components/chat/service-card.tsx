// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import { MapPin, Phone, Mail, Clock, CheckCircle, AlertTriangle, ChevronDown } from "lucide-react";
import type { ServiceResult } from "@/lib/chat/types";
import { LocationFeedbackRow } from "./location-feedback-row";
import { CallConfirmDialog } from "./call-confirm-dialog";

interface ServiceCardProps {
  service: ServiceResult;
  isActive?: boolean;
  index?: number;
  total?: number;
}

function StatusBadge({ status }: { status?: string }) {
  if (status === "open") {
    return (
      <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-green-100 text-green-800">
        Open now
      </span>
    );
  }
  if (status === "closed") {
    return (
      <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-red-50 text-red-700">
        Closed
      </span>
    );
  }
  return (
    <span className="inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg bg-neutral-100 text-neutral-500">
      Call for hours
    </span>
  );
}

function ValidatedBadge({ dateStr }: { dateStr?: string }) {
  if (!dateStr) {
    return (
      <span className="inline-flex items-center gap-1 text-[0.65rem] font-medium text-amber-600">
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
      <span className="inline-flex items-center gap-1 text-[0.65rem] font-medium text-amber-600">
        <AlertTriangle size={11} aria-hidden="true" />
        Unverified — call ahead
      </span>
    );
  }

  return (
    <span className={`inline-flex items-center gap-1 text-[0.65rem] font-medium ${
      isRecent ? "text-green-600" : "text-neutral-400"
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

export function ServiceCard({ service, isActive, index, total }: ServiceCardProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [alsoExpanded, setAlsoExpanded] = useState(false);

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
      className="flex-shrink-0 w-[280px] snap-start bg-white border border-neutral-200 rounded-2xl p-4 flex flex-col gap-2.5 transition-all hover:border-neutral-300 hover:shadow-md"
    >
      {/* Name */}
      <div className="text-[0.95rem] font-semibold tracking-tight text-neutral-900 leading-snug">
        {name}
      </div>

      {/* Organization + Verified */}
      <div className="flex flex-col gap-0.5 -mt-1">
        {service.organization && (
          <div className="text-xs text-neutral-500 font-medium">
            {service.organization}
          </div>
        )}
        <ValidatedBadge dateStr={service.last_validated_at} />
      </div>

      {/* Hours + status */}
      <div className="flex items-center gap-2">
        <StatusBadge status={service.is_open} />
        {service.hours_today && (
          <span className="inline-flex items-center gap-1 text-xs text-neutral-500 whitespace-nowrap">
            <Clock size={14} className="text-neutral-400 flex-shrink-0" aria-hidden="true" />
            <span>{service.hours_today}</span>
          </span>
        )}
      </div>

      {/* Review highlight — visible by default (builds trust) */}
      {service.review_highlight && (
        <div className="text-xs text-neutral-500 leading-relaxed bg-neutral-50 border border-neutral-100 rounded-lg px-3 py-2 italic">
          <span aria-hidden="true">💬 </span>
          {service.review_highlight.length > 120
            ? service.review_highlight.slice(0, 117) + "…"
            : service.review_highlight}
        </div>
      )}

      {/* Badges row — referral, eligibility, fees side by side */}
      {(service.fees || service.requires_membership || service.eligibility_summary) && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {service.requires_membership && (
            <span className="inline-block text-xs font-semibold text-amber-800 bg-amber-100 px-2 py-0.5 rounded-lg">
              Ref. may be required
            </span>
          )}
          {service.eligibility_summary && (
            <span className="inline-block text-xs font-semibold text-blue-800 bg-blue-50 px-2 py-0.5 rounded-lg">
              {service.eligibility_summary}
            </span>
          )}
          {service.fees && (
            <span className="inline-block text-xs font-semibold text-green-800 bg-green-100 px-2 py-0.5 rounded-lg">
              {service.fees}
            </span>
          )}
        </div>
      )}

      {/* ── Collapsible Details ── */}
      <DetailsSection service={service} hasDetails={hasDetails} detailsOpen={detailsOpen} setDetailsOpen={setDetailsOpen} />

      {/* Also available at this location — limited to 3 with expand */}
      {alsoItems.length > 0 && (
        <div className="pt-1 border-t border-neutral-100">
          <div className="text-[0.65rem] font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
            Also here
          </div>
          <div className="flex flex-wrap gap-1">
            {alsoVisible.map((cat) => (
              <span
                key={cat}
                className="inline-block text-[0.68rem] font-medium px-2 py-0.5 rounded-md bg-neutral-50 border border-neutral-200 text-neutral-600"
              >
                {ALSO_EMOJI[cat] || "\u2022"} {cat}
              </span>
            ))}
            {!alsoExpanded && alsoHiddenCount > 1 && (
              <button
                type="button"
                onClick={() => setAlsoExpanded(true)}
                className="inline-block text-[0.68rem] font-medium px-2 py-0.5 rounded-md bg-neutral-50 border border-neutral-200 text-blue-600 hover:bg-blue-50 hover:border-blue-200 transition-colors"
              >
                +{alsoHiddenCount} more
              </button>
            )}
          </div>
        </div>
      )}

      {/* Action buttons — mt-auto pins buttons + footer to card bottom */}
      <ActionButtons service={service} name={name} />

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
    <div className="border-t border-neutral-100 pt-1">
      <button
        type="button"
        onClick={() => setDetailsOpen(!detailsOpen)}
        aria-expanded={detailsOpen}
        className="flex items-center justify-between w-full py-1.5 text-xs font-semibold uppercase tracking-wider text-neutral-400 hover:text-neutral-600 transition-colors"
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
            <div className="flex items-start gap-2 text-xs text-neutral-500 leading-snug">
              <MapPin size={14} className="text-neutral-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
              <span>{service.address}</span>
            </div>
          )}
          {service.phone && (
            <div className="flex items-start gap-2 text-xs text-neutral-500">
              <Phone size={14} className="text-neutral-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
              <span>{service.phone}</span>
            </div>
          )}
          {service.email && (
            <div className="flex items-start gap-2 text-sm text-neutral-500">
              <Mail size={14} className="text-neutral-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
              <span>{service.email}</span>
            </div>
          )}
          {service.accessibility && (
            <div className="flex items-start gap-2 text-sm text-neutral-500 leading-snug">
              <span className="mt-0.5 flex-shrink-0 text-sm" aria-hidden="true">♿</span>
              <span>{service.accessibility}</span>
            </div>
          )}
          {service.description && (
            <div className="text-xs text-neutral-500 leading-relaxed">
              {service.description}
            </div>
          )}
          {service.required_documents && service.required_documents.length > 0 && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 leading-snug">
              <span className="mt-0.5 flex-shrink-0" aria-hidden="true">📄</span>
              <span>Bring: {service.required_documents.join(", ")}</span>
            </div>
          )}
          {service.languages && service.languages.length > 0 &&
            !(service.languages.length === 1 && service.languages[0] === "English") && (
            <div className="flex items-start gap-2 text-xs text-neutral-500 leading-snug">
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

  return (
    <>
      <div className="flex gap-1.5 pt-1 mt-auto" role="group" aria-label={`Actions for ${name}`}>
        {service.phone && (
          <button
            type="button"
            onClick={() => setShowCallConfirm(true)}
            aria-label={`Call ${name}`}
            className="flex-1 py-2 rounded-lg border border-neutral-900 bg-neutral-900 text-center text-xs font-semibold text-white transition hover:bg-neutral-700"
          >
            Call
          </button>
        )}
        {service.address && (
          <a
            href={`https://maps.google.com/?q=${encodeURIComponent(service.address)}`}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Get directions to ${name}`}
            className="flex-1 py-2 rounded-lg border border-amber-300 bg-amber-300 text-center text-xs font-semibold text-neutral-900 transition hover:bg-amber-400 hover:border-amber-400"
          >
            Directions
          </a>
        )}
        {service.website && (
          <a
            href={service.website}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Visit ${name} website`}
            className="flex-1 py-2 rounded-lg border border-neutral-200 bg-neutral-50 text-center text-xs font-semibold text-neutral-900 transition hover:bg-neutral-100 hover:border-neutral-300"
          >
            Website
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
}

export function LocationCard({ services, isActive, index, total }: LocationCardProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);

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
      className="flex-shrink-0 w-[280px] snap-start bg-white border border-neutral-200 rounded-2xl p-4 flex flex-col gap-2.5 transition-all hover:border-neutral-300 hover:shadow-md"
    >
      {/* Organization header */}
      <div className="flex flex-col gap-0.5">
        <div className="text-[0.95rem] font-semibold tracking-tight text-neutral-900 leading-snug">
          {orgName}
        </div>
        <ValidatedBadge dateStr={bestVerified ?? undefined} />
      </div>

      {/* Shared hours — shown once when identical */}
      {allSameHours && (
        <div className="flex items-center gap-2">
          <StatusBadge status={primary.is_open} />
          {primary.hours_today && (
            <span className="inline-flex items-center gap-1 text-xs text-neutral-500 whitespace-nowrap">
              <Clock size={14} className="text-neutral-400 flex-shrink-0" aria-hidden="true" />
              <span>{primary.hours_today}</span>
            </span>
          )}
        </div>
      )}

      {/* Shared badges — shown once when identical */}
      {allSameBadges && (primary.fees || primary.requires_membership || primary.eligibility_summary) && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {primary.requires_membership && (
            <span className="inline-block text-xs font-semibold text-amber-800 bg-amber-100 px-2 py-0.5 rounded-lg">
              Ref. may be required
            </span>
          )}
          {primary.eligibility_summary && (
            <span className="inline-block text-xs font-semibold text-blue-800 bg-blue-50 px-2 py-0.5 rounded-lg">
              {primary.eligibility_summary}
            </span>
          )}
          {primary.fees && (
            <span className="inline-block text-xs font-semibold text-green-800 bg-green-100 px-2 py-0.5 rounded-lg">
              {primary.fees}
            </span>
          )}
        </div>
      )}

      {/* Service list */}
      <div className="pt-1 border-t border-neutral-100">
        <div className="text-[0.65rem] font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
          Services
        </div>
        <div className="flex flex-col gap-2">
          {services.map((svc, i) => {
            const hasPerServiceInfo = !allSameHours || !allSameBadges;
            return (
              <div
                key={svc.service_id || i}
                className={`flex flex-col gap-1.5 ${i > 0 && hasPerServiceInfo ? "pt-2 border-t border-neutral-100" : ""}`}
              >
                {/* Service name */}
                <div className="text-xs font-semibold text-neutral-700">
                  {ALSO_EMOJI[svc.service_name || ""] ? `${ALSO_EMOJI[svc.service_name || ""]} ` : ""}{svc.service_name || "Service"}
                </div>

                {/* Per-service hours — only when they differ */}
                {!allSameHours && (
                  <div className="flex items-center gap-2">
                    <StatusBadge status={svc.is_open} />
                    {svc.hours_today && (
                      <span className="inline-flex items-center gap-1 text-xs text-neutral-500 whitespace-nowrap">
                        <Clock size={12} className="text-neutral-400 flex-shrink-0" aria-hidden="true" />
                        <span>{svc.hours_today}</span>
                      </span>
                    )}
                  </div>
                )}

                {/* Per-service badges — only when they differ */}
                {!allSameBadges && (svc.fees || svc.requires_membership || svc.eligibility_summary) && (
                  <div className="flex items-center gap-1 flex-wrap">
                    {svc.requires_membership && (
                      <span className="inline-block text-[0.65rem] font-semibold text-amber-800 bg-amber-100 px-1.5 py-0.5 rounded-md">
                        Ref. may be required
                      </span>
                    )}
                    {svc.eligibility_summary && (
                      <span className="inline-block text-[0.65rem] font-semibold text-blue-800 bg-blue-50 px-1.5 py-0.5 rounded-md">
                        {svc.eligibility_summary}
                      </span>
                    )}
                    {svc.fees && (
                      <span className="inline-block text-[0.65rem] font-semibold text-green-800 bg-green-100 px-1.5 py-0.5 rounded-md">
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

      {/* Review highlight */}
      {review && (
        <div className="text-xs text-neutral-500 leading-relaxed bg-neutral-50 border border-neutral-100 rounded-lg px-3 py-2 italic">
          <span aria-hidden="true">💬 </span>
          {review.length > 120 ? review.slice(0, 117) + "…" : review}
        </div>
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
