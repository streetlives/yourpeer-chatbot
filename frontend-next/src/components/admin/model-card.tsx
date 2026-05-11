// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import { MODELS } from "./model-data";

export function ModelBadge({ model }: { model: "haiku" | "sonnet" | "opus" }) {
  const colors = {
    haiku: "bg-green-50 text-green-700",
    sonnet: "bg-violet-50 text-violet-700",
    opus: "bg-amber-50 text-amber-700",
  };
  const labels = {
    haiku: "Haiku 4.5",
    sonnet: "Sonnet 4.6",
    opus: "Opus 4.6",
  };
  return (
    <span
      className={`inline-block text-xs font-semibold px-2.5 py-0.5 rounded-lg ${colors[model]}`}
    >
      {labels[model]}
    </span>
  );
}

export function ModelCard({ modelKey }: { modelKey: "haiku" | "sonnet" | "opus" }) {
  const m = MODELS[modelKey];
  const accents: Record<string, string> = {
    haiku: "border-t-green-400",
    sonnet: "border-t-violet-400",
    opus: "border-t-amber-400",
  };
  const textColors: Record<string, string> = {
    haiku: "text-green-700",
    sonnet: "text-violet-700",
    opus: "text-amber-700",
  };
  const accent = accents[modelKey];
  const textColor = textColors[modelKey];

  return (
    <div className={`bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg p-4 border-t-[3px] ${accent}`}>
      <div className="flex items-baseline justify-between mb-2.5">
        <span className={`text-base font-bold ${textColor}`}>
          {m.name}
        </span>
        <span className="text-sm font-mono text-neutral-400 dark:text-neutral-500">
          ${m.input}/${m.output}/MTok
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-sm text-neutral-500 dark:text-neutral-400 mb-3">
        <span>Speed: {m.speed}</span>
        <span>Context: {m.context}</span>
        <span>Latency: {m.latency}</span>
        <span>
          ID: <code className="text-xs bg-neutral-100 dark:bg-neutral-800 px-1 rounded">
            {/* Strip the date suffix (8 trailing digits prefixed with "-")
                if present. Truncating to 3 segments via .split("-").slice(0,3)
                used to drop the model's minor version too — `claude-haiku-4-5-20251001`
                became `claude-haiku-4`, hiding which generation was actually
                running. The regex preserves the version while dropping only
                the build-date tail. Models without a date suffix
                (`claude-sonnet-4-6`) pass through unchanged. */}
            {m.id.replace(/-\d{8}$/, "")}
          </code>
        </span>
      </div>

      <div className="mb-2">
        <div className="text-xs font-semibold text-green-600 dark:text-green-400 mb-1">
          Strengths
        </div>
        {m.strengths.slice(0, 4).map((s, i) => (
          <div key={i} className="text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed flex gap-1.5">
            <span className="text-green-500 flex-shrink-0">+</span>
            <span>{s}</span>
          </div>
        ))}
      </div>
      <div>
        <div className="text-xs font-semibold text-red-500 dark:text-red-400 mb-1">
          Weaknesses
        </div>
        {m.weaknesses.slice(0, 3).map((w, i) => (
          <div key={i} className="text-sm text-neutral-600 dark:text-neutral-300 leading-relaxed flex gap-1.5">
            <span className="text-red-400 flex-shrink-0">&minus;</span>
            <span>{w}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
