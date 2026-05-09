// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface UseSpeechRecognitionReturn {
  isSupported: boolean;
  isListening: boolean;
  transcript: string;
  error: string | null;
  startListening: () => void;
  stopListening: () => void;
}

export function useSpeechRecognition(): UseSpeechRecognitionReturn {
  const [isSupported, setIsSupported] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);

  const recognitionRef = useRef<SpeechRecognition | null>(null);

  // Cumulative final text from this session. Each onresult that
  // delivers a final segment appends here; interim results are
  // temporary previews layered on top of this base. Resets at the
  // start of every new session.
  const finalTextRef = useRef("");

  // True between onstart firing and onend/onerror clearing it. Used by
  // startListening to refuse a second start on a recognition that's
  // already running. Without this, a rapid double-tap on the mic button
  // throws InvalidStateError (silently caught) AND the UX gets out of
  // sync — the user expected the second tap to cancel something that
  // had already begun.
  const activeRef = useRef(false);

  // Set by stopListening. Suppresses late onresult events that fire
  // while the engine is processing its tail-end audio buffer after
  // stop() has been requested. Without this guard, a "ghost" transcript
  // can appear in the input AFTER the user has already submitted —
  // setTranscript here triggers the parent's onTranscript prop, which
  // refills the (already-cleared) input with text from before send.
  const manualStopRef = useRef(false);

  useEffect(() => {
    const SR =
      window.SpeechRecognition || (window).webkitSpeechRecognition;
    if (!SR) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect -- window.SpeechRecognition is undefined during SSR; feature-detect then mark as supported
    setIsSupported(true);

    const recognition = new SR();

    // continuous=true: keep listening until the user manually stops by
    // tapping the mic button again. Previously this was false, which
    // told the engine to detect "end of speech" via a short (~1–2s)
    // silence and aggressively end recognition on natural pauses.
    // Users speaking slowly, in fragments ("um… I need… shelter…
    // somewhere in Brooklyn"), or thinking mid-sentence got cut off
    // after the first chunk and had to re-tap the mic to continue —
    // confusing because the mic icon visibly flipped back to its idle
    // state without an obvious cause.
    //
    // With continuous=true, the user owns the stop point. Browsers will
    // still auto-timeout on extended silence (Chrome ~60s, Safari
    // shorter), so battery drain stays bounded even if the user forgets
    // to tap stop.
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US";
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      activeRef.current = true;
      manualStopRef.current = false;
      finalTextRef.current = "";
      setIsListening(true);
      setError(null);
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      // Drop events that fire after manual stop. The engine can deliver
      // one or more late onresult events between stop() being called
      // and onend firing, as it flushes pending audio. Acting on those
      // would re-populate the input with ghost text after the user has
      // already submitted.
      if (manualStopRef.current) return;

      let interim = "";
      let newFinal = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          newFinal += t;
        } else {
          interim += t;
        }
      }

      if (newFinal) {
        const sep = finalTextRef.current ? " " : "";
        finalTextRef.current += sep + newFinal;
        setTranscript(finalTextRef.current);
      } else if (interim) {
        const sep = finalTextRef.current ? " " : "";
        setTranscript(finalTextRef.current + sep + interim);
      }
    };

    recognition.onerror = (event) => {
      activeRef.current = false;
      setIsListening(false);
      if (event.error === "not-allowed") {
        setError("Microphone access was denied. Please allow mic access and try again.");
      } else if (event.error !== "no-speech") {
        setError("Voice input didn't work. Try typing instead.");
      }
    };

    recognition.onend = () => {
      activeRef.current = false;
      setIsListening(false);
    };

    recognitionRef.current = recognition;

    return () => {
      try {
        recognition.stop();
      } catch {}
    };
  }, []);

  const startListening = useCallback(() => {
    if (!recognitionRef.current) return;
    // Refuse to start while already active. The button binds onClick
    // to (isListening ? stopListening : startListening), so React
    // state staleness can let a second tap arrive at startListening
    // even though recognition is already running. activeRef is the
    // synchronous truth.
    if (activeRef.current) return;
    finalTextRef.current = "";
    manualStopRef.current = false;
    setTranscript("");
    setError(null);
    try {
      recognitionRef.current.start();
    } catch {
      // recognition.start() throws InvalidStateError if the engine is
      // already starting (small window between start() and onstart
      // firing). Swallow: the user's intent was to be listening, and
      // they will be momentarily.
    }
  }, []);

  const stopListening = useCallback(() => {
    // Set BEFORE recognition.stop() so the late-event guard in
    // onresult is in place by the time the engine fires its final
    // results buffer.
    manualStopRef.current = true;
    setIsListening(false);
    try {
      recognitionRef.current?.stop();
    } catch {}
  }, []);

  return { isSupported, isListening, transcript, error, startListening, stopListening };
}
