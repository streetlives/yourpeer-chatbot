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

  setSessionId: (id: string | null) => void;
  addMessage: (msg: ChatMessage) => void;
  removeMessage: (id: string) => void;
  setLoading: (v: boolean) => void;
  setError: (msg: string | null) => void;
  markQuickRepliesUsed: () => void;
  resetChat: () => void;
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
    (set) => ({
      sessionId: null,
      messages: [makeWelcomeMessage()],
      lastActiveAt: Date.now(),
      isLoading: false,
      error: null,

      setSessionId: (id) => set({ sessionId: id }),

      addMessage: (msg) =>
        set((state) => ({
          messages: [...state.messages, msg],
          lastActiveAt: Date.now(),
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
        set({
          sessionId: null,
          messages: [makeWelcomeMessage()],
          lastActiveAt: Date.now(),
          isLoading: false,
          error: null,
        });
        // Also clear offline state — otherwise queued messages from
        // a prior session will flush against the (now-reset) session
        // and show up as bot responses with no corresponding user
        // messages in the chat. Fire-and-forget — failures here don't
        // block the reset.
        void clearQueue();
        void clearCachedResults();
      },
    }),
    {
      name: "yourpeer-chat",

      // Schema version — increment when the persisted shape changes.
      // The migrate function handles upgrading old data so users don't
      // lose their conversation or hit runtime errors after a deploy.
      version: 1,
      migrate: (persisted, version: number) => {
        if (version === 0) {
          // v0 → v1: no structural changes, just establishing the baseline.
          // Future migrations go here as additional `if` blocks:
          //   if (version < 2) { /* v1 → v2 migration */ }
        }
        return persisted;
      },

      // Only persist conversation state — not transient UI flags.
      // Transient messages (e.g. "Getting your location…") are stripped
      // so they don't survive page refreshes.
      partialize: (state) => ({
        sessionId: state.sessionId,
        messages: state.messages.filter((m) => !m.transient),
        lastActiveAt: state.lastActiveAt,
      }),

      onRehydrateStorage: () => (state) => {
        if (state) {
          // If the session has expired, reset to the welcome screen.
          const elapsed = Date.now() - (state.lastActiveAt || 0);
          if (elapsed > SESSION_TTL_MS) {
            // Defer the reset so it doesn't interfere with rehydration.
            setTimeout(() => useChatStore.getState().resetChat(), 0);
          } else {
            // Sync the message counter so new IDs don't collide.
            syncMsgCounter(state.messages);
          }
        }
      },
    },
  ),
);
