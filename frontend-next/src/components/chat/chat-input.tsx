// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { VoiceInputButton } from "./voice-input-button";
import { Send } from "lucide-react";

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled: boolean;
}

const MAX_MESSAGE_LENGTH = 1000;

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState("");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const stopListeningRef = useRef<(() => void) | null>(null);
  // Snapshot of any text typed before the mic button was pressed,
  // so voice transcript replaces only the voice portion, not the typed prefix.
  const preVoiceTextRef = useRef("");
  // Tracks whether the user is mid-IME-composition (CJK, Vietnamese
  // Telex/VNI, some Spanish accent input). When composing, Enter
  // commits the composition; submitting the form on that Enter would
  // either send incomplete text or skip the user's commit. Stored as
  // a ref so composition state changes don't trigger re-renders —
  // this is purely a transient flag the form submit consults.
  // See onCompositionStart/End on the input below.
  const composingRef = useRef(false);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // Defense-in-depth: the onKeyDown handler on the input also
    // intercepts Enter during composition and preventDefaults the
    // form submit. This re-check guards programmatic submits or
    // IME edge cases where the keydown handler doesn't fire.
    if (composingRef.current) return;
    // Stop voice input if active
    stopListeningRef.current?.();
    const text = value.trim();
    if (!text) return;
    setValue("");
    setVoiceError(null);
    onSend(text);
  }

  // Refocus input after send completes
  useEffect(() => {
    if (!disabled) inputRef.current?.focus();
  }, [disabled]);

  // Replace (not append) the input value with the full transcript.
  // The speech recognition hook already accumulates text internally —
  // each `transcript` update is the complete voice text so far.
  // We prepend any text the user typed before pressing mic.
  const handleTranscript = useCallback((transcript: string) => {
    setVoiceError(null);
    const pre = preVoiceTextRef.current;
    const sep = pre ? " " : "";
    setValue((pre + sep + transcript).slice(0, MAX_MESSAGE_LENGTH));
  }, []);

  const handleVoiceError = useCallback((error: string) => {
    setVoiceError(error);
  }, []);

  const handleListeningChange = useCallback((listening: boolean) => {
    if (listening) {
      setVoiceError(null);
      // Capture whatever the user typed before pressing the mic button
      preVoiceTextRef.current = inputRef.current?.value.trim() || "";
    }
  }, []);

  return (
    // No inline safe-area-inset-bottom here — the outer chat-container
    // already adds env(safe-area-inset-bottom, 0px) to its bottom
    // padding, which pushes ChatInput (the last child of that
    // container) above the home indicator on PWA mode for notched
    // iPhones. Adding the inset again here would compose two times,
    // producing extra empty space below the input.
    //
    // Button order in the row: input → send → mic. Send sits directly
    // adjacent to the input it's submitting, which is the affordance
    // people expect (the action that confirms what's typed should be
    // visually attached to it). The mic is the alternative-input mode
    // and lives at the trailing edge — it's a less-frequent action and
    // doesn't compete for the "primary affordance" position next to
    // the field.
    <div className="flex flex-col gap-1.5">
      <form onSubmit={handleSubmit} className="flex gap-2" aria-label="Chat input">
        <label htmlFor="chat-message-input" className="sr-only">
          Message
        </label>
        <input
          ref={inputRef}
          id="chat-message-input"
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          onKeyDown={(e) => {
            // IME composition guard: when the user presses Enter to
            // commit a CJK/Vietnamese/Spanish-IME composition, some
            // browsers and IME combinations let the keypress leak
            // through and submit the form. Suppress that.
            //
            // Two signals checked for redundancy:
            //   - composingRef (set by onCompositionStart/End above):
            //     covers any browser that fires composition events
            //     correctly.
            //   - e.nativeEvent.isComposing: standard since 2014, set
            //     by all modern browsers during composition. Covers
            //     timing edge cases where compositionend fires after
            //     this keydown (uncommon but observed).
            //
            // Either signal triggers the gate. The user's Enter still
            // commits the IME composition (browser default); we just
            // don't escalate it to a form submit.
            if (
              e.key === "Enter" &&
              (composingRef.current || e.nativeEvent.isComposing)
            ) {
              e.preventDefault();
            }
          }}
          maxLength={MAX_MESSAGE_LENGTH}
          placeholder="What do you need help with?"
          autoComplete="off"
          disabled={disabled}
          className="flex-1 px-4 py-3 border border-neutral-200 rounded-xl bg-white text-neutral-900 text-base outline-none transition-all focus:border-neutral-300 focus:ring-2 focus:ring-amber-300/30 placeholder:text-neutral-400 disabled:opacity-50 dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-100 dark:focus:border-neutral-600 dark:placeholder:text-neutral-500"
        />

        <button
          type="submit"
          disabled={disabled || !value.trim()}
          aria-label="Send message"
          className="w-12 h-12 border-none rounded-xl bg-neutral-900 text-white flex items-center justify-center transition-transform hover:scale-[1.04] active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed disabled:transform-none dark:bg-neutral-100 dark:text-neutral-900"
        >
          <Send size={18} strokeWidth={2.5} />
        </button>

        <VoiceInputButton
          onTranscript={handleTranscript}
          onError={handleVoiceError}
          onListeningChange={handleListeningChange}
          stopRef={stopListeningRef}
          disabled={disabled}
        />
      </form>

      {voiceError && (
        <p role="alert" className="text-xs text-red-600 px-1 dark:text-red-400">
          {voiceError}
        </p>
      )}
    </div>
  );
}
