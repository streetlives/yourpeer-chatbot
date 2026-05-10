// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useMemo } from "react";
import type { AuditEvent } from "@/lib/chat/types";
import { formatTimeOfDayWithSeconds } from "@/lib/admin/format-time";
import {
  FEEDBACK_CRITERIA,
  FEEDBACK_CRITERION_LABELS,
  type FeedbackCriterion,
} from "@/lib/admin/locations-types";

interface TranscriptDrawerProps {
  sessionId: string;
  events: AuditEvent[] | null;
  loading: boolean;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Slot diff helpers
// Given the slots-before and slots-after a turn, compute:
//   added:       keys present only in `after`
//   overwritten: keys in both, with different non-null values
//   removed:     keys non-null in `before`, null/missing in `after`
// ---------------------------------------------------------------------------

type SlotMap = Record<string, string | null | undefined>;

interface SlotDiff {
  added: Array<{ key: string; value: string }>;
  overwritten: Array<{ key: string; from: string; to: string }>;
  removed: Array<{ key: string; from: string }>;
}

function nonNull(s: SlotMap | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!s) return out;
  for (const [k, v] of Object.entries(s)) {
    if (v != null && v !== "") out[k] = String(v);
  }
  return out;
}

function diffSlots(before: SlotMap | undefined, after: SlotMap | undefined): SlotDiff {
  const b = nonNull(before);
  const a = nonNull(after);
  const added: SlotDiff["added"] = [];
  const overwritten: SlotDiff["overwritten"] = [];
  const removed: SlotDiff["removed"] = [];

  for (const [k, v] of Object.entries(a)) {
    if (!(k in b)) {
      added.push({ key: k, value: v });
    } else if (b[k] !== v) {
      overwritten.push({ key: k, from: b[k], to: v });
    }
  }
  for (const [k, v] of Object.entries(b)) {
    if (!(k in a)) {
      removed.push({ key: k, from: v });
    }
  }
  return { added, overwritten, removed };
}

// ---------------------------------------------------------------------------
// TranscriptDrawer
// ---------------------------------------------------------------------------

export function TranscriptDrawer({
  sessionId,
  events,
  loading,
  onClose,
}: TranscriptDrawerProps) {
  // Sort events chronologically. Interleave query_execution / crisis events
  // alongside conversation_turn events so the drawer reads as a single
  // unified timeline.
  const ordered = useMemo(() => {
    if (!events) return [] as AuditEvent[];
    return [...events].sort((a, b) =>
      a.timestamp.localeCompare(b.timestamp),
    );
  }, [events]);

  // Slot evolution: walk turns in order, accumulating non-null slot values.
  // Each turn records the slots before it ran and the slots reported by it.
  const slotHistory = useMemo(() => {
    let prev: Record<string, string> = {};
    const turns: Array<{
      turnIndex: number;
      timestamp: string;
      before: Record<string, string>;
      after: Record<string, string>;
      diff: SlotDiff;
    }> = [];
    let i = 0;
    for (const e of ordered) {
      if (e.type !== "conversation_turn") continue;
      const after = nonNull(e.slots);
      // Carry forward keys that the turn didn't explicitly clear — slot
      // events in the audit log only contain the slots that were touched
      // on that turn, so we union into prev rather than replace.
      const merged: Record<string, string> = { ...prev, ...after };
      const diff = diffSlots(prev, merged);
      turns.push({
        turnIndex: i,
        timestamp: e.timestamp,
        before: prev,
        after: merged,
        diff,
      });
      prev = merged;
      i += 1;
    }
    return { turns, finalSlots: prev };
  }, [ordered]);

  // Index turn-level diffs by timestamp so the per-turn footer can read them
  // back without re-walking.
  const diffByTimestamp = useMemo(() => {
    const m = new Map<string, SlotDiff>();
    for (const t of slotHistory.turns) m.set(t.timestamp, t.diff);
    return m;
  }, [slotHistory]);

  const turnCount = slotHistory.turns.length;
  const queryCount = ordered.filter((e) => e.type === "query_execution").length;
  const crisisCount = ordered.filter((e) => e.type === "crisis_detected").length;

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50 z-50 animate-in fade-in" />
        <Dialog.Content className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-white border border-neutral-200 rounded-2xl max-w-[900px] w-[92%] max-h-[85vh] overflow-y-auto p-7 z-50 animate-in fade-in slide-in-from-bottom-2">
          <div className="flex justify-between items-start mb-4">
            <div>
              <Dialog.Title className="text-base font-semibold">
                Session {sessionId.slice(0, 12)}…
              </Dialog.Title>
              {!loading && ordered.length > 0 && (
                <div className="text-xs text-neutral-400 mt-0.5">
                  {turnCount} turn{turnCount !== 1 ? "s" : ""}
                  {queryCount > 0 && ` · ${queryCount} quer${queryCount !== 1 ? "ies" : "y"}`}
                  {crisisCount > 0 && ` · ${crisisCount} crisis event${crisisCount !== 1 ? "s" : ""}`}
                </div>
              )}
            </div>
            <Dialog.Close asChild>
              <button
                aria-label="Close transcript"
                className="w-8 h-8 rounded-lg border border-neutral-200 bg-neutral-50 text-neutral-400 flex items-center justify-center transition hover:border-red-300 hover:text-red-500"
              >
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {loading && <p className="text-neutral-400 text-sm">Loading…</p>}

          {!loading && ordered.length === 0 && (
            <p className="text-neutral-400 text-sm">No events found for this session.</p>
          )}

          {/* Slot evolution panel — final state, with each value annotated by
              the turn it was first set or last overwritten. */}
          {!loading && Object.keys(slotHistory.finalSlots).length > 0 && (
            <SlotEvolutionPanel turns={slotHistory.turns} finalSlots={slotHistory.finalSlots} />
          )}

          {/* Unified timeline */}
          <div className="space-y-3">
            {ordered.map((e, i) =>
              renderEvent(e, i, diffByTimestamp.get(e.timestamp)),
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

// ---------------------------------------------------------------------------
// Event renderers
// ---------------------------------------------------------------------------

function renderEvent(e: AuditEvent, i: number, diff: SlotDiff | undefined) {
  switch (e.type) {
    case "conversation_turn":
      return <TurnEvent key={i} e={e} diff={diff} />;
    case "query_execution":
      return <QueryEvent key={i} e={e} />;
    case "crisis_detected":
      return <CrisisEvent key={i} e={e} />;
    case "session_reset":
      return (
        <div
          key={i}
          className="text-xs text-neutral-400 italic px-3.5 py-1.5 border-l-[3px] border-neutral-200"
        >
          Session reset · {formatTimeOfDayWithSeconds(e.timestamp)}
        </div>
      );
    case "feedback":
      return (
        <div
          key={i}
          className="bg-blue-50/60 border-l-[3px] border-blue-300 px-3.5 py-2 rounded-r-lg text-sm"
        >
          <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-blue-600 mb-0.5">
            Feedback {e.rating ? `· ${e.rating}` : ""}
          </div>
          {e.comment && <div className="text-neutral-600">{e.comment}</div>}
        </div>
      );
    case "location_feedback":
      return <LocationFeedbackEvent key={i} e={e} />;
    default:
      return null;
  }
}

/**
 * Location feedback event — shows the location name, per-criterion
 * chips, and any free-text comment.
 *
 * Originally fell through the generic feedback case, which only knew
 * about `rating` and `comment`. The AuditEvent type was extended on
 * day 1 of the locations admin build with `location_id`, `location_name`,
 * and `ratings` (per-criterion booleans), but no renderer was updated
 * to use them — so clicking a row in the new section 5c stream opened
 * the drawer to a generic-feedback event without the location attribution.
 * This dedicated case closes that gap.
 *
 * Chip rendering mirrors `<CriterionBadge>` from feedback-comments-stream:
 * negative ratings get a red pill with "Not …" prefix, positive get an
 * emerald pill, unrated criteria are omitted entirely (a hollow chip
 * row would be visual noise). Sorted criterion order matches the
 * FEEDBACK_CRITERIA constant so chips render in a stable order.
 */
function LocationFeedbackEvent({ e }: { e: AuditEvent }) {
  const ratings = e.ratings ?? {};
  // FEEDBACK_CRITERIA encodes the canonical order. Build chip data
  // by walking that array so the chip order matches the feedback
  // aggregates and the comments stream — admins see the same four
  // criteria in the same order everywhere.
  const negativeCrits = FEEDBACK_CRITERIA.filter(
    (c) => ratings[c] === false,
  );
  const positiveCrits = FEEDBACK_CRITERIA.filter(
    (c) => ratings[c] === true,
  );
  const hasCriteria = negativeCrits.length + positiveCrits.length > 0;
  const locationLabel = e.location_name || (e.location_id ? `#${e.location_id.slice(0, 8)}` : null);

  return (
    <div className="bg-blue-50/60 border-l-[3px] border-blue-300 px-3.5 py-2 rounded-r-lg text-sm">
      <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-blue-600 mb-0.5">
        Location feedback{e.rating ? ` · ${e.rating}` : ""}
      </div>
      {locationLabel && (
        <div className="text-xs text-neutral-700 dark:text-neutral-300 mb-1">
          {locationLabel}
        </div>
      )}
      {hasCriteria && (
        <div className="flex flex-wrap gap-1 mb-1">
          {negativeCrits.map((c) => (
            <CriterionChip key={`n-${c}`} criterion={c} negative />
          ))}
          {positiveCrits.map((c) => (
            <CriterionChip key={`p-${c}`} criterion={c} negative={false} />
          ))}
        </div>
      )}
      {e.comment && <div className="text-neutral-600">{e.comment}</div>}
    </div>
  );
}

/**
 * Criterion chip — same visual language as `<CriterionBadge>` in
 * feedback-comments-stream. Duplicated here rather than imported
 * because the chat-side transcript-drawer shouldn't depend on the
 * admin/locations component tree; the styling is small enough to
 * carry locally. If it ever grows, lift to a shared module under
 * `lib/admin/`.
 */
function CriterionChip({
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
  const prefix = negative ? "Not " : "";
  return (
    <span className={`text-[0.7rem] px-1.5 py-0.5 rounded ${cls}`}>
      {prefix}{label.toLowerCase()}
    </span>
  );
}

function TurnEvent({ e, diff }: { e: AuditEvent; diff: SlotDiff | undefined }) {
  const meta: React.ReactNode[] = [];
  if (e.services_count) meta.push(`${e.services_count} service(s) delivered`);
  if (e.quick_replies?.length) meta.push(`buttons: ${e.quick_replies.join(", ")}`);

  return (
    <div className="animate-in fade-in slide-in-from-bottom-1">
      {/* User turn */}
      {e.user_message && (
        <div className="bg-amber-50/60 border-l-[3px] border-amber-400 px-3.5 py-2.5 rounded-r-lg mb-2">
          <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-amber-600 mb-1">
            User · {formatTimeOfDayWithSeconds(e.timestamp)}
          </div>
          <div className="text-sm whitespace-pre-wrap leading-relaxed">{e.user_message}</div>
        </div>
      )}
      {/* Bot turn */}
      <div className="bg-neutral-50 border-l-[3px] border-neutral-300 px-3.5 py-2.5 rounded-r-lg">
        <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-neutral-400 mb-1">
          Bot
        </div>
        <div className="text-sm whitespace-pre-wrap leading-relaxed">{e.bot_response}</div>

        {/* Per-turn slot diff — preferred over flat slots dump */}
        {diff && (diff.added.length > 0 || diff.overwritten.length > 0 || diff.removed.length > 0) && (
          <div className="text-xs font-mono text-neutral-500 mt-2 space-y-0.5">
            {diff.added.map((s) => (
              <div key={`add-${s.key}`}>
                <span className="text-green-600">+</span> {s.key}={s.value}
              </div>
            ))}
            {diff.overwritten.map((s) => (
              <div key={`over-${s.key}`}>
                <span className="text-amber-600">~</span> {s.key}={" "}
                <span className="line-through text-neutral-400">{s.from}</span>{" "}
                → {s.to}
              </div>
            ))}
            {diff.removed.map((s) => (
              <div key={`rem-${s.key}`}>
                <span className="text-red-500">−</span> {s.key}={" "}
                <span className="line-through text-neutral-400">{s.from}</span>
              </div>
            ))}
          </div>
        )}

        {meta.length > 0 && (
          <div className="text-xs text-neutral-400 mt-1.5 font-mono">
            {meta.map((m, idx) => (
              <span key={idx}>
                {idx > 0 && " · "}
                {m}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function QueryEvent({ e }: { e: AuditEvent }) {
  const resultColor =
    (e.result_count ?? 0) > 0 ? "text-emerald-600" : "text-neutral-400";
  return (
    <div className="bg-emerald-50/40 border-l-[3px] border-emerald-400 px-3.5 py-2 rounded-r-lg">
      <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-emerald-600 mb-1">
        Query · {formatTimeOfDayWithSeconds(e.timestamp)}
      </div>
      <div className="text-sm font-mono">
        <span className="font-semibold">{e.template_name}</span>
        {" → "}
        <span className={resultColor}>
          {e.result_count ?? 0} result{(e.result_count ?? 0) !== 1 ? "s" : ""}
        </span>
        {e.relaxed && (
          <span className="ml-2 text-amber-600 text-xs">(relaxed)</span>
        )}
        {e.execution_ms != null && (
          <span className="ml-2 text-neutral-400 text-xs">{e.execution_ms}ms</span>
        )}
      </div>
    </div>
  );
}

function CrisisEvent({ e }: { e: AuditEvent }) {
  return (
    <div className="bg-red-50 border-l-[3px] border-red-500 px-3.5 py-2.5 rounded-r-lg">
      <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-red-600 mb-1">
        ⚠ Crisis Detected · {formatTimeOfDayWithSeconds(e.timestamp)}
      </div>
      <div className="text-sm">
        {e.crisis_category && (
          <span className="font-semibold">{e.crisis_category}: </span>
        )}
        {e.user_message}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Slot evolution panel — top-of-drawer summary showing the final slot map
// with the turn each value was set on. Hover surfaces the full history
// for keys that were overwritten.
// ---------------------------------------------------------------------------

interface SlotEvolutionPanelProps {
  turns: Array<{
    turnIndex: number;
    diff: SlotDiff;
  }>;
  finalSlots: Record<string, string>;
}

function SlotEvolutionPanel({ turns, finalSlots }: SlotEvolutionPanelProps) {
  // For each final-slot key, find every turn that touched it (added or
  // overwrote). Last entry is the value as of the end of the conversation.
  const keyHistory: Record<
    string,
    Array<{ turnIndex: number; value: string }>
  > = {};

  for (const t of turns) {
    for (const a of t.diff.added) {
      if (!keyHistory[a.key]) keyHistory[a.key] = [];
      keyHistory[a.key].push({ turnIndex: t.turnIndex, value: a.value });
    }
    for (const o of t.diff.overwritten) {
      if (!keyHistory[o.key]) keyHistory[o.key] = [];
      keyHistory[o.key].push({ turnIndex: t.turnIndex, value: o.to });
    }
  }

  return (
    <div className="bg-white border border-neutral-200 rounded-lg p-3 mb-4">
      <div className="text-[0.7rem] font-semibold uppercase tracking-wider text-neutral-500 mb-2">
        Slot Trace
      </div>
      <div className="flex flex-wrap gap-1.5">
        {Object.entries(finalSlots).map(([key, value]) => {
          const history = keyHistory[key] ?? [];
          const overwritten = history.length > 1;
          const setOnTurn = history[history.length - 1]?.turnIndex;
          const tooltip = overwritten
            ? history
                .map((h) => `T${h.turnIndex + 1}: ${h.value}`)
                .join("\n")
            : undefined;
          return (
            <span
              key={key}
              title={tooltip}
              className={`inline-flex items-baseline gap-1 px-2 py-1 rounded-md text-xs font-mono border ${
                overwritten
                  ? "bg-amber-50 border-amber-200 text-amber-800"
                  : "bg-neutral-50 border-neutral-200 text-neutral-700"
              }`}
            >
              <span className="font-semibold">{key}</span>
              <span>=</span>
              <span>{value}</span>
              {setOnTurn != null && (
                <span className="text-neutral-400 text-[0.65rem] ml-0.5">
                  T{setOnTurn + 1}
                  {overwritten ? "*" : ""}
                </span>
              )}
            </span>
          );
        })}
      </div>
      {Object.values(keyHistory).some((h) => h.length > 1) && (
        <div className="text-[0.65rem] text-neutral-400 mt-2">
          * overwritten — hover for history
        </div>
      )}
    </div>
  );
}
