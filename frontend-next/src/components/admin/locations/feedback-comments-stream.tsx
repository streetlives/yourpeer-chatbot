// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import { AlertCircle, MessageSquare, ExternalLink } from "lucide-react";
import type {
  FeedbackCommentsResponse,
  FeedbackComment,
  FeedbackCriterion,
} from "@/lib/admin/locations-types";
import { useAdminFetch } from "@/lib/admin/use-admin-fetch";
import {
  FEEDBACK_CRITERION_LABELS,
} from "@/lib/admin/locations-types";
import type { AuditEvent } from "@/lib/chat/types";
import { fetchConversationDetail } from "@/lib/chat/api";
import { TranscriptDrawer } from "@/components/admin/transcript-drawer";

/**
 * Section 5c — recent feedback comments stream.
 *
 * Reverse-chronological list of the last 50 (configurable) feedback
 * events with non-empty comments. Each row has:
 *   - Location name (truncated if very long)
 *   - Criterion badges (negative ones colored red, positive green)
 *   - Comment text (full, no truncation — short comments fit easily;
 *     long ones get their own paragraph)
 *   - Relative timestamp
 *   - "View transcript" affordance that opens TranscriptDrawer
 *     scoped to the originating session
 *
 * Why open TranscriptDrawer for context: a feedback comment alone is
 * often just a sentiment ("staff was rude"); the surrounding chat
 * shows what the user was looking for and what services they ended
 * up seeing. That's the difference between "she didn't like THIS
 * pantry" and "she didn't like ANY of the pantries we showed her" —
 * matters for ops triage.
 *
 * The TranscriptDrawer is reused as-is (same component
 * conversation-table.tsx uses) so admins navigate the transcript
 * with the same UX they already know. Single fetch via
 * fetchConversationDetail; the drawer renders the full event list.
 */
export function FeedbackCommentsStream() {
  const { data, loading, error } = useAdminFetch<FeedbackCommentsResponse>(
    "/api/admin/locations/feedback-comments",
  );

  // Drawer state — same pattern as conversation-table.tsx.
  // selectedSessionId drives drawer visibility; transcript holds the
  // pre-fetched events while loading is in flight.
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<AuditEvent[] | null>(null);
  const [transcriptLoading, setTranscriptLoading] = useState(false);


  async function openTranscript(sessionId: string) {
    setSelectedSessionId(sessionId);
    setTranscriptLoading(true);
    try {
      const events = await fetchConversationDetail(sessionId);
      setTranscript(events);
    } catch {
      // Mirrors the conversation-table pattern: render the drawer
      // with empty events on failure rather than failing silently.
      // The drawer's own loading state handles the empty case.
      setTranscript([]);
    } finally {
      setTranscriptLoading(false);
    }
  }

  function closeTranscript() {
    setSelectedSessionId(null);
    setTranscript(null);
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/30 dark:border-red-900 dark:text-red-300">
        <AlertCircle className="inline w-4 h-4 mr-1.5 -mt-0.5" />
        Couldn&apos;t load comments: {error}
      </div>
    );
  }

  if (loading || !data) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="h-[280px] animate-pulse bg-neutral-100 rounded dark:bg-neutral-800" />
      </div>
    );
  }

  if (data.comments.length === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-4 dark:bg-neutral-900 dark:border-neutral-800">
        <div className="flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-400">
          <MessageSquare size={14} aria-hidden="true" />
          <span>No comments yet — populates as users add written feedback to their ratings.</span>
        </div>
      </div>
    );
  }

  const truncated = data.total_with_comments > data.comments.length;

  return (
    <>
      <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden dark:bg-neutral-900 dark:border-neutral-800">
        <div className="px-4 py-3 border-b border-neutral-200 dark:border-neutral-800">
          <div className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
            Recent comments
          </div>
          <div className="text-xs text-neutral-500 dark:text-neutral-400 mt-0.5">
            {truncated ? (
              <>
                Most recent <span className="font-medium">{data.comments.length}</span> of{" "}
                <span className="font-medium">{data.total_with_comments}</span> events with comments.
              </>
            ) : (
              <>
                <span className="font-medium">{data.total_with_comments}</span>{" "}
                event{data.total_with_comments === 1 ? "" : "s"} with comments.
              </>
            )}{" "}
            Click any row to view the originating session&apos;s transcript.
          </div>
        </div>

        <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
          {data.comments.map((c, idx) => (
            <CommentRow
              key={`${c.session_id}-${c.timestamp}-${idx}`}
              comment={c}
              onOpenTranscript={openTranscript}
            />
          ))}
        </ul>
      </div>

      {selectedSessionId && (
        <TranscriptDrawer
          sessionId={selectedSessionId}
          events={transcript}
          loading={transcriptLoading}
          onClose={closeTranscript}
        />
      )}
    </>
  );
}

function CommentRow({
  comment,
  onOpenTranscript,
}: {
  comment: FeedbackComment;
  onOpenTranscript: (sessionId: string) => void;
}) {
  const hasAnyCriteria =
    comment.negative_criteria.length + comment.positive_criteria.length > 0;

  return (
    <li>
      <button
        type="button"
        onClick={() => onOpenTranscript(comment.session_id)}
        className="w-full text-left px-4 py-3 hover:bg-neutral-50 dark:hover:bg-neutral-800/40 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 focus-visible:ring-inset"
        aria-label={`View transcript for ${comment.location_name || "feedback"}`}
      >
        {/* Header row: location name + timestamp + drill-down hint */}
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          <div className="min-w-0 flex-1">
            <span className="text-sm font-medium text-neutral-900 dark:text-neutral-100 truncate">
              {comment.location_name || (
                <span className="text-neutral-400 italic font-normal">(no name captured)</span>
              )}
            </span>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-[0.7rem] text-neutral-400 dark:text-neutral-500 tabular-nums whitespace-nowrap">
              {formatRelativeTime(comment.timestamp)}
            </span>
            <ExternalLink
              size={12}
              aria-hidden="true"
              className="text-neutral-300 dark:text-neutral-600"
            />
          </div>
        </div>

        {/* Criterion badges. Hidden when no criteria were rated —
         *  some users leave just a comment without rating any
         *  criterion, and a hollow badge row would be visual noise. */}
        {hasAnyCriteria && (
          <div className="flex flex-wrap items-center gap-1 mb-1.5">
            {comment.negative_criteria.map((crit) => (
              <CriterionBadge key={`neg-${crit}`} criterion={crit} negative />
            ))}
            {comment.positive_criteria.map((crit) => (
              <CriterionBadge key={`pos-${crit}`} criterion={crit} negative={false} />
            ))}
          </div>
        )}

        {/* Comment text. Full content rendered (no truncation). Long
         *  comments wrap naturally; short ones don't take extra space.
         *  whitespace-pre-wrap preserves any newlines the user typed
         *  — multi-paragraph comments stay multi-paragraph. */}
        <p className="text-sm text-neutral-700 dark:text-neutral-300 whitespace-pre-wrap break-words">
          {comment.comment}
        </p>
      </button>
    </li>
  );
}

function CriterionBadge({
  criterion,
  negative,
}: {
  criterion: FeedbackCriterion;
  negative: boolean;
}) {
  const label = FEEDBACK_CRITERION_LABELS[criterion];
  const cls = negative
    ? "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-300"
    : "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300";
  // Use the appropriate prefix to convey the rating direction. "Not"
  // works for safety / friendliness / cleanliness / queer-friendly
  // (e.g. "Not safe", "Not clean") — the criteria are framed as
  // positive properties, so the negation reads naturally.
  const prefix = negative ? "Not " : "";
  return (
    <span className={`text-[0.7rem] px-1.5 py-0.5 rounded ${cls}`}>
      {prefix}{label.toLowerCase()}
    </span>
  );
}

/**
 * Lightweight relative-time formatter — same shape as the one in
 * feedback-aggregates.tsx. Inline rather than lifted to a shared
 * util because the format ("today" / "X days ago" / "X months ago")
 * is admin-table-specific; the rest of the app uses
 * Intl.RelativeTimeFormat which doesn't fit our preferred form.
 */
function formatRelativeTime(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (isNaN(date.getTime())) return "—";
  const now = new Date();
  const days = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60 * 24));
  if (days < 1) {
    const hours = Math.floor((now.getTime() - date.getTime()) / (1000 * 60 * 60));
    if (hours < 1) {
      const mins = Math.floor((now.getTime() - date.getTime()) / (1000 * 60));
      return mins < 1 ? "just now" : `${mins}m ago`;
    }
    return `${hours}h ago`;
  }
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  if (days < 60) return "1 month ago";
  if (days < 365) return `${Math.floor(days / 30)} months ago`;
  const years = Math.floor(days / 365);
  return `${years} year${years > 1 ? "s" : ""} ago`;
}
