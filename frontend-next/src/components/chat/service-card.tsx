// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { MapPin, Phone, Mail, Clock, CheckCircle } from "lucide-react";
import type { ServiceResult } from "@/lib/chat/types";

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
  if (!dateStr) return null;

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

  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${
      isRecent ? "text-green-600" : "text-neutral-400"
    }`}>
      <CheckCircle size={12} aria-hidden="true" />
      Validated {label}
    </span>
  );
}

// Emoji map for co-located service categories
const ALSO_EMOJI: Record<string, string> = {
  "Shelter": "\u{1F6CF}\uFE0F",
  "Shower": "\u{1F6BF}",
  "Clothing Pantry": "\u{1F455}",
  "Clothing": "\u{1F455}",
  "Health": "\u{1F3E5}",
  "General Health": "\u{1F3E5}",
  "Mental Health": "\u{1F9E0}",
  "Laundry": "\u{1F9FC}",
  "Legal Services": "\u2696\uFE0F",
  "Benefits": "\u{1F4CB}",
  "Education": "\u{1F4DA}",
  "Employment": "\u{1F4BC}",
  "Food": "\u{1F37D}\uFE0F",
  "Food Pantry": "\u{1F37D}\uFE0F",
  "Soup Kitchen": "\u{1F372}",
  "Toiletries": "\u{1F9F4}",
  "Mail": "\u{1F4EC}",
  "Free Wifi": "\u{1F4F6}",
  "Haircut": "\u{1F487}",
  "Support Groups": "\u{1F91D}",
  "Drop-in Center": "\u{1F3E0}",
  "Crisis": "\u{1F198}",
  "Restrooms": "\u{1F6BB}",
  "Warming Center": "\u{1F525}",
};

export function ServiceCard({ service, isActive, index, total }: ServiceCardProps) {
  const name = service.service_name || "Service";
  const cardLabel =
    index !== undefined && total !== undefined
      ? `${name}, result ${index + 1} of ${total}`
      : name;

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

      {/* Organization + Validated */}
      <div className="flex flex-col gap-0.5 -mt-1">
        {service.organization && (
          <div className="text-xs text-neutral-500 font-medium">
            {service.organization}
          </div>
        )}
        <ValidatedBadge dateStr={service.last_validated_at} />
      </div>

      {/* Hours + status */}
      <div className="flex items-center gap-2 flex-wrap">
        <StatusBadge status={service.is_open} />
        {service.hours_today && (
          <span className="inline-flex items-center gap-1 text-xs text-neutral-500">
            <Clock size={14} className="text-neutral-400" aria-hidden="true" />
            <span>Hours: {service.hours_today}</span>
          </span>
        )}
      </div>

      {/* Address */}
      {service.address && (
        <div className="flex items-start gap-2 text-sm text-neutral-500 leading-snug">
          <MapPin size={14} className="text-neutral-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <span>Address: {service.address}</span>
        </div>
      )}

      {/* Phone */}
      {service.phone && (
        <div className="flex items-start gap-2 text-sm text-neutral-500">
          <Phone size={14} className="text-neutral-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <span>Phone: {service.phone}</span>
        </div>
      )}

      {/* Email */}
      {service.email && (
        <div className="flex items-start gap-2 text-sm text-neutral-500">
          <Mail size={14} className="text-neutral-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
          <span>Email: {service.email}</span>
        </div>
      )}

      {/* Description */}
      {service.description && (
        <div className="text-xs text-neutral-500 leading-relaxed line-clamp-3">
          {service.description}
        </div>
      )}

      {/* Fee badge */}
      {service.fees && (
        <span className="self-start inline-block text-xs font-semibold text-green-800 bg-green-100 px-2.5 py-0.5 rounded-lg">
          {service.fees}
        </span>
      )}

      {/* Referral badge */}
      {service.requires_membership && (
        <span className="self-start inline-block text-xs font-semibold text-amber-800 bg-amber-100 px-2.5 py-0.5 rounded-lg">
          Referral may be required
        </span>
      )}

      {/* Also available at this location */}
      {service.also_available && service.also_available.length > 0 && (
        <div className="pt-1 border-t border-neutral-100">
          <div className="text-[0.65rem] font-semibold uppercase tracking-wider text-neutral-400 mb-1.5">
            Also here
          </div>
          <div className="flex flex-wrap gap-1">
            {service.also_available.map((cat) => (
              <span
                key={cat}
                className="inline-block text-[0.68rem] font-medium px-2 py-0.5 rounded-md bg-neutral-50 border border-neutral-200 text-neutral-600"
              >
                {ALSO_EMOJI[cat] || "\u2022"} {cat}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Learn More */}
      {service.yourpeer_url && (
        <a
          href={service.yourpeer_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Learn more about ${name} on YourPeer`}
          className="block w-full py-2.5 rounded-lg bg-amber-300 text-center text-sm font-semibold text-neutral-900 transition-all hover:bg-amber-400 hover:shadow-md mt-auto"
        >
          Learn More on YourPeer
        </a>
      )}

      {/* Action buttons */}
      <div className="flex gap-1.5 pt-1" role="group" aria-label={`Actions for ${name}`}>
        {service.phone && (
          <a
            href={`tel:${service.phone.replace(/\D/g, "")}`}
            aria-label={`Call ${name}`}
            className="flex-1 py-2 rounded-lg border border-neutral-900 bg-neutral-900 text-center text-xs font-semibold text-white transition hover:bg-neutral-700"
          >
            Call
          </a>
        )}
        {service.address && (
          <a
            href={`https://maps.google.com/?q=${encodeURIComponent(service.address)}`}
            target="_blank"
            rel="noopener noreferrer"
            aria-label={`Get directions to ${name}`}
            className="flex-1 py-2 rounded-lg border border-neutral-200 bg-neutral-50 text-center text-xs font-semibold text-neutral-900 transition hover:bg-neutral-100 hover:border-neutral-300"
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
    </div>
  );
}
