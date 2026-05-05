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

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
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
          maxLength={MAX_MESSAGE_LENGTH}
          placeholder="What do you need help with?"
          autoComplete="off"
          disabled={disabled}
          className="flex-1 px-4 py-3 border border-neutral-200 rounded-xl bg-white text-neutral-900 text-[0.94rem] outline-none transition-all focus:border-neutral-300 focus:ring-2 focus:ring-amber-300/30 placeholder:text-neutral-400 disabled:opacity-50 dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-100 dark:focus:border-neutral-600 dark:placeholder:text-neutral-500"
        />

        <VoiceInputButton
          onTranscript={handleTranscript}
          onError={handleVoiceError}
          onListeningChange={handleListeningChange}
          stopRef={stopListeningRef}
          disabled={disabled}
        />

        <button
          type="submit"
          disabled={disabled || !value.trim()}
          aria-label="Send message"
          className="w-12 h-12 border-none rounded-xl bg-neutral-900 text-white flex items-center justify-center transition-transform hover:scale-[1.04] active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed disabled:transform-none dark:bg-neutral-100 dark:text-neutral-900"
        >
          <Send size={18} strokeWidth={2.5} />
        </button>
      </form>

      {voiceError && (
        <p role="alert" className="text-xs text-red-600 px-1 dark:text-red-400">
          {voiceError}
        </p>
      )}
    </div>
  );
}
