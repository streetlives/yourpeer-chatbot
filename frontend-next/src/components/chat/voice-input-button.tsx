// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useEffect, type MutableRefObject } from "react";
import { useSpeechRecognition } from "@/hooks/use-speech-recognition";
import { Mic, Square } from "lucide-react";

interface VoiceInputButtonProps {
  onTranscript: (transcript: string) => void;
  onError: (error: string) => void;
  onListeningChange: (listening: boolean) => void;
  stopRef?: MutableRefObject<(() => void) | null>;
  disabled: boolean;
}

export function VoiceInputButton({ onTranscript, onError, onListeningChange, stopRef, disabled }: VoiceInputButtonProps) {
  const {
    isSupported,
    isListening,
    transcript,
    error,
    startListening,
    stopListening,
  } = useSpeechRecognition();

  // Forward transcript to parent
  useEffect(() => {
    if (transcript) onTranscript(transcript);
  }, [transcript, onTranscript]);

  // Forward error to parent
  useEffect(() => {
    if (error) onError(error);
  }, [error, onError]);

  // Notify parent of listening state changes
  useEffect(() => {
    onListeningChange(isListening);
  }, [isListening, onListeningChange]);

  // Expose stopListening to parent via ref
  useEffect(() => {
    if (stopRef) stopRef.current = stopListening;
    return () => { if (stopRef) stopRef.current = null; };
  }, [stopRef, stopListening]);

  if (!isSupported) return null;

  return (
    <button
      type="button"
      onClick={isListening ? stopListening : startListening}
      disabled={disabled}
      aria-label={isListening ? "Stop listening" : "Voice input"}
      title={isListening ? "Tap to stop" : "Tap to speak"}
      className={`w-12 h-12 rounded-xl border flex items-center justify-center transition-all flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed ${
        isListening
          ? "bg-red-50 border-red-400 text-red-600 animate-pulse dark:bg-red-950/40 dark:border-red-600 dark:text-red-300"
          : "bg-white border-neutral-200 text-neutral-500 hover:bg-neutral-50 hover:border-neutral-300 hover:text-neutral-900 dark:bg-neutral-900 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:border-neutral-600 dark:hover:text-neutral-100"
      }`}
    >
      {isListening ? <Square size={18} strokeWidth={2.5} /> : <Mic size={20} />}
    </button>
  );
}
