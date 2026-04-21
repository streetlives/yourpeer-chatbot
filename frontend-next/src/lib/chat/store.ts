// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ChatMessage, QuickReply } from "./types";
import { clearQueue } from "./send-queue";
import { clearCachedResults } from "./offline-cache";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatStore {
  sessionId: string | null;
  messages: ChatMessage[];
  lastActiveAt: number;
  isLoading: boolean;
  error: string | null;
  /**
   * Snapshot of the last bot message with service cards, saved
   * immediately before resetChat wipes the conversation (TTL expiry
   * or user-triggered reset). When present, the welcome UI offers a
   * "See your earlier results" link. Cleared after the user taps
   * the link (or starts typing something new that explicitly
   * supersedes the old context).
   */
  lastResultsBeforeReset: ChatMessage | null;

  setSessionId: (id: string | null) => void;
  addMessage: (msg: ChatMessage) => void;
  /**
   * Shallow-merge patch into an existing message by id. Silently
   * no-ops if the id isn't found (e.g. the message was removed by the
   * user cancelling). Used to transition message.status through its
   * lifecycle: pending → sending → sent / failed / cancelled.
   */
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void;
  removeMessage: (id: string) => void;
  setLoading: (v: boolean) => void;
  setError: (msg: string | null) => void;
  markQuickRepliesUsed: () => void;
  resetChat: () => void;
  /**
   * Bring the lastResultsBeforeReset snapshot back into the chat
   * stream as a fresh bot message with a clarifying prefix. Clears
   * the snapshot so the link disappears once used. No-op when the
   * snapshot is empty.
   */
  restoreEarlierResults: () => void;
  /**
   * Discard the lastResultsBeforeReset snapshot without restoring it.
   * Called when the user takes an action that makes the old results
   * clearly irrelevant (e.g. searches for something else).
   */
  dismissEarlierResults: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Backend session TTL is 30 minutes — expire localStorage to match. */
const SESSION_TTL_MS = 30 * 60 * 1000;

const WELCOME_MESSAGE =
  "Hi, welcome to YourPeer. I can help you find services like food, shelter, showers, and more in your area. Your conversation is private — I don't save your name or personal details. You can stop or start over anytime.\n\nWhat are you looking for today?";

const INITIAL_QUICK_REPLIES: QuickReply[] = [
  { label: "🍽️ Food", value: "I need food" },
  { label: "🏠 Shelter", value: "I need shelter" },
  { label: "🚿 Showers", value: "I need a shower" },
  { label: "👕 Clothing", value: "I need clothing" },
  { label: "🏥 Health Care", value: "I need health care" },
  { label: "💼 Jobs", value: "I need help finding a job" },
  { label: "⚖️ Legal Help", value: "I need legal help" },
  { label: "🧠 Mental Health", value: "I need mental health support" },
  { label: "📋 Other", value: "I need other services" },
];

// ---------------------------------------------------------------------------
// Message IDs — monotonic counter that survives rehydration
// ---------------------------------------------------------------------------

let msgCounter = 0;

export function nextMsgId(): string {
  return `msg-${++msgCounter}-${Date.now()}`;
}

/**
 * After rehydrating from localStorage, bump the counter past any existing
 * message IDs so new messages don't collide.
 */
function syncMsgCounter(messages: ChatMessage[]): void {
  for (const m of messages) {
    const match = m.id.match(/^msg-(\d+)-/);
    if (match) {
      const n = parseInt(match[1], 10);
      if (n > msgCounter) msgCounter = n;
    }
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeWelcomeMessage(): ChatMessage {
  return {
    id: nextMsgId(),
    role: "bot",
    text: WELCOME_MESSAGE,
    quick_replies: INITIAL_QUICK_REPLIES,
  };
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useChatStore = create<ChatStore>()(
  persist(
    (set, get) => ({
      sessionId: null,
      messages: [makeWelcomeMessage()],
      lastActiveAt: Date.now(),
      isLoading: false,
      error: null,
      lastResultsBeforeReset: null,

      setSessionId: (id) => set({ sessionId: id }),

      addMessage: (msg) =>
        set((state) => ({
          messages: [...state.messages, msg],
          lastActiveAt: Date.now(),
        })),

      updateMessage: (id, patch) =>
        set((state) => ({
          messages: state.messages.map((m) => {
            if (m.id !== id) return m;
            // Terminal-state guard: once a message is "cancelled"
            // (the user explicitly pulled it back), do NOT let any
            // subsequent patch change its status. This closes a
            // narrow race window in the queue flush loop where a
            // cancellation could happen between a cancel-check and
            // a status write. The caller can still patch non-status
            // fields, but we drop any status change in the patch.
            if (m.status === "cancelled" && "status" in patch) {
              const { status: _dropped, ...safePatch } = patch;
              void _dropped;
              return { ...m, ...safePatch };
            }
            return { ...m, ...patch };
          }),
        })),

      removeMessage: (id) =>
        set((state) => ({
          messages: state.messages.filter((m) => m.id !== id),
        })),

      setLoading: (v) => set(v ? { isLoading: true, error: null } : { isLoading: false }),

      setError: (msg) => set({ error: msg }),

      markQuickRepliesUsed: () =>
        set((state) => ({
          messages: state.messages.map((m) =>
            m.quick_replies ? { ...m, quick_replies: undefined } : m,
          ),
        })),

      resetChat: () => {
        // Before wiping, snapshot the most recent bot message that
        // showed service results. If the user was mid-search when the
        // session expired, this is the context they likely still care
        // about — losing it silently is a common UX failure mode.
        const prevState = get();
        const lastResults = [...prevState.messages]
          .reverse()
          .find((m) => m.role === "bot" && m.services && m.services.length > 0) || null;

        set({
          sessionId: null,
          messages: [makeWelcomeMessage()],
          lastActiveAt: Date.now(),
          isLoading: false,
          error: null,
          lastResultsBeforeReset: lastResults,
        });
        // Also clear offline state — otherwise queued messages from
        // a prior session will flush against the (now-reset) session
        // and show up as bot responses with no corresponding user
        // messages in the chat. Fire-and-forget — failures here don't
        // block the reset.
        void clearQueue();
        void clearCachedResults();
      },

      restoreEarlierResults: () => {
        const snapshot = get().lastResultsBeforeReset;
        if (!snapshot) return;
        // Inject a brief orienting prefix so the restored message
        // doesn't read like the bot is responding to nothing.
        const prefix: ChatMessage = {
          id: nextMsgId(),
          role: "bot",
          text: "Here are the services you were looking at before:",
        };
        // New ID on the restored results so it doesn't collide with
        // anything else in the current chat stream.
        const restored: ChatMessage = {
          ...snapshot,
          id: nextMsgId(),
          // Don't re-show quick replies — they were contextual to the
          // previous conversation state.
          quick_replies: undefined,
          // Don't show feedback affordance again for the same results.
          showFeedback: false,
        };
        set((state) => ({
          messages: [...state.messages, prefix, restored],
          lastResultsBeforeReset: null,
          lastActiveAt: Date.now(),
        }));
      },

      dismissEarlierResults: () => set({ lastResultsBeforeReset: null }),
    }),
    {
      name: "yourpeer-chat",

      // Schema version — increment when the persisted shape changes.
      // The migrate function handles upgrading old data so users don't
      // lose their conversation or hit runtime errors after a deploy.
      // v2: added lastResultsBeforeReset (null-default is safe on legacy reads).
      version: 2,
      migrate: (persisted, version: number) => {
        const p = persisted as Record<string, unknown> | null | undefined;
        if (!p) return p;
        if (version < 1) {
          // v0 → v1: no structural changes, just establishing the baseline.
        }
        if (version < 2) {
          // v1 → v2: introduce lastResultsBeforeReset. Missing field
          // defaults to null — legacy users lose no data, they just
          // don't get a "your earlier results" link on first rehydrate
          // after upgrade. Acceptable: the feature is only meaningful
          // after a reset anyway.
          p.lastResultsBeforeReset = null;
        }
        return p;
      },

      // Only persist conversation state — not transient UI flags.
      // Transient messages (e.g. "Getting your location…") are stripped
      // so they don't survive page refreshes.
      partialize: (state) => ({
        sessionId: state.sessionId,
        messages: state.messages.filter((m) => !m.transient),
        lastActiveAt: state.lastActiveAt,
        lastResultsBeforeReset: state.lastResultsBeforeReset,
      }),

      onRehydrateStorage: () => (state) => {
        if (state) {
          // If the session has expired, reset to the welcome screen.
          const elapsed = Date.now() - (state.lastActiveAt || 0);
          if (elapsed > SESSION_TTL_MS) {
            // Defer the reset so it doesn't interfere with rehydration.
            // queueMicrotask runs after the current tick's synchronous
            // work (so rehydrate can finish) but before any I/O —
            // faster and more predictable than setTimeout(0).
            //
            // Guard against double-fire: onRehydrateStorage can be
            // invoked twice in a row (Next.js fast refresh, Suspense
            // retries). Re-check staleness inside the microtask —
            // if an earlier reset already ran, `lastActiveAt` is now
            // fresh and we bail out.
            queueMicrotask(() => {
              const current = useChatStore.getState();
              if (Date.now() - (current.lastActiveAt || 0) > SESSION_TTL_MS) {
                current.resetChat();
              }
            });
          } else {
            // Sync the message counter so new IDs don't collide.
            syncMsgCounter(state.messages);
          }
        }
      },
    },
  ),
);
