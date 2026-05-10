// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { AlertCircle, AlertTriangle, Info, CheckCircle2 } from "lucide-react";
import type {
  IntegrityCalloutsResponse,
  IntegrityCallout,
} from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/lib/admin/use-admin-fetch";

/**
 * Section 6 — data integrity callouts.
 *
 * Renders the list of firing callouts (count > 0). When nothing
 * fires, surfaces a positive ✓ state instead of an empty list —
 * "no issues" is the wanted outcome and worth surfacing positively.
 *
 * Severity color cues match the rest of the page: amber for
 * "warning" (active data bug), neutral blue-ish for "info"
 * (cosmetic / handled-at-render-time issue).
 */
export function DataIntegrityCallouts() {
  const { data, loading, error } = useAdminFetch<IntegrityCalloutsResponse>(
    "/api/admin/locations/integrity-callouts",
  );


  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load integrity callouts: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[120px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  // Positive empty-state — admins want to see this.
  if (data.all_clear) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 size={16} aria-hidden="true" />
          <span>
            No integrity issues detected — orphan checks, phone formats, and HTML
            encoding all clean.
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {data.callouts.map((callout) => (
        <CalloutCard key={callout.id} callout={callout} />
      ))}
    </div>
  );
}

function CalloutCard({ callout }: { callout: IntegrityCallout }) {
  const isWarning = callout.severity === "warning";
  // Same color scale as the rest of the admin page so the visual
  // language stays consistent. Warning → amber; info → neutral blue-ish.
  const containerClass = isWarning
    ? "border-amber-200 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-900/50"
    : "border-blue-200 bg-blue-50 dark:bg-blue-950/20 dark:border-blue-900/50";
  const iconClass = isWarning
    ? "text-amber-600 dark:text-amber-400"
    : "text-blue-600 dark:text-blue-400";
  const countClass = isWarning
    ? "text-amber-900 dark:text-amber-200"
    : "text-blue-900 dark:text-blue-200";
  const Icon = isWarning ? AlertTriangle : Info;

  return (
    <div className={`rounded-lg border p-4 ${containerClass}`}>
      <div className="flex items-start gap-3">
        <Icon size={18} className={`flex-shrink-0 mt-0.5 ${iconClass}`} aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-3 mb-1">
            <h3 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
              {callout.title}
            </h3>
            <span className={`text-base font-bold tabular-nums ${countClass}`}>
              {callout.count.toLocaleString()}
            </span>
          </div>
          <p className="text-xs text-neutral-700 dark:text-neutral-300 leading-relaxed">
            {callout.action_hint}
          </p>
        </div>
      </div>
    </div>
  );
}
