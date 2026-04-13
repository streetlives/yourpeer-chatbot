// Copyright (c) 2024 Streetlives, Inc.
// Use of this source code is governed by an MIT-style license.

"use client";

import { useState } from "react";
import { ThumbsUp, ThumbsDown, ChevronDown, ChevronUp } from "lucide-react";
import { useChatStore } from "@/lib/chat/store";
import { sendLocationFeedback } from "@/lib/chat/api";

interface LocationFeedbackRowProps {
  serviceId: string;
  locationName: string;
  /** Optional YourPeer URL — renders "Learn more →" on the left of the trigger row. */
  learnMoreUrl?: string;
}

type Dimension = "safety" | "friendliness" | "cleanliness" | "queer_friendly";

const DIMENSIONS: { key: Dimension; label: string }[] = [
  { key: "safety", label: "Safe?" },
  { key: "friendliness", label: "Friendly?" },
  { key: "cleanliness", label: "Clean?" },
  { key: "queer_friendly", label: "LGBTQ+ friendly?" },
];

export function LocationFeedbackRow({ serviceId, locationName, learnMoreUrl }: LocationFeedbackRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [ratings, setRatings] = useState<Record<Dimension, boolean | null>>({
    safety: null,
    friendliness: null,
    cleanliness: null,
    queer_friendly: null,
  });
  const [submitted, setSubmitted] = useState(false);

  function rate(dim: Dimension, value: boolean) {
    if (submitted) return;
    setRatings((prev) => ({ ...prev, [dim]: value }));
  }

  function submit() {
    if (submitted) return;
    const sessionId = useChatStore.getState().sessionId;
    if (!sessionId) return;

    const hasRating = Object.values(ratings).some((v) => v !== null);
    if (!hasRating) return;

    setSubmitted(true);
    sendLocationFeedback({
      session_id: sessionId,
      location_id: serviceId,
      location_name: locationName,
      ...(ratings.safety !== null && { safety: ratings.safety }),
      ...(ratings.friendliness !== null && { friendliness: ratings.friendliness }),
      ...(ratings.cleanliness !== null && { cleanliness: ratings.cleanliness }),
      ...(ratings.queer_friendly !== null && { queer_friendly: ratings.queer_friendly }),
    });
  }

  if (submitted) {
    return (
      <div className="border-t border-neutral-100 pt-1.5">
        <div className="flex items-center justify-between">
          {learnMoreUrl && (
            <a
              href={learnMoreUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[0.68rem] font-medium text-amber-700 hover:text-amber-900 hover:underline transition-colors"
            >
              Learn more →
            </a>
          )}
          <span className="text-[0.68rem] text-neutral-400">
            Thanks for your feedback!
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="border-t border-neutral-100 pt-1.5">
      {/* Trigger row: Learn More (left) + Rate (right) */}
      <div className="flex items-center justify-between">
        {learnMoreUrl ? (
          <a
            href={learnMoreUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[0.68rem] font-medium text-amber-700 hover:text-amber-900 hover:underline transition-colors"
          >
            Learn more →
          </a>
        ) : (
          <span />
        )}
        {serviceId && (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-[0.68rem] text-neutral-400 hover:text-neutral-600 transition-colors"
          >
            <span>Rate this location</span>
            {expanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
          </button>
        )}
      </div>

      {/* Expanded rating dimensions */}
      {expanded && (
        <div className="flex flex-col gap-1.5 pt-1.5 animate-in fade-in">
          {DIMENSIONS.map(({ key, label }) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-[0.68rem] text-neutral-500">{label}</span>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => rate(key, true)}
                  aria-label={`${label} yes`}
                  className={`w-6 h-6 rounded border flex items-center justify-center transition-all ${
                    ratings[key] === true
                      ? "bg-green-100 border-green-300 text-green-700"
                      : "border-neutral-200 bg-white text-neutral-400 hover:border-neutral-300"
                  }`}
                >
                  <ThumbsUp size={11} />
                </button>
                <button
                  type="button"
                  onClick={() => rate(key, false)}
                  aria-label={`${label} no`}
                  className={`w-6 h-6 rounded border flex items-center justify-center transition-all ${
                    ratings[key] === false
                      ? "bg-red-50 border-red-200 text-red-600"
                      : "border-neutral-200 bg-white text-neutral-400 hover:border-neutral-300"
                  }`}
                >
                  <ThumbsDown size={11} />
                </button>
              </div>
            </div>
          ))}
          <button
            type="button"
            onClick={submit}
            disabled={!Object.values(ratings).some((v) => v !== null)}
            className="w-full py-1.5 rounded-lg bg-neutral-900 text-white text-[0.68rem] font-medium transition hover:bg-neutral-700 disabled:opacity-30 disabled:cursor-not-allowed mt-0.5"
          >
            Submit feedback
          </button>
        </div>
      )}
    </div>
  );
}
