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
    <div className={`bg-white border border-neutral-200 rounded-lg p-4 border-t-[3px] ${accent}`}>
      <div className="flex items-baseline justify-between mb-2.5">
        <span className={`text-base font-bold ${textColor}`}>
          {m.name}
        </span>
        <span className="text-xs font-mono text-neutral-400">
          ${m.input}/${m.output}/MTok
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs text-neutral-500 mb-3">
        <span>Speed: {m.speed}</span>
        <span>Context: {m.context}</span>
        <span>Latency: {m.latency}</span>
        <span>
          ID: <code className="text-[0.65rem] bg-neutral-100 px-1 rounded">{m.id.split("-").slice(0, 3).join("-")}</code>
        </span>
      </div>

      <div className="mb-2">
        <div className="text-[0.65rem] font-bold uppercase tracking-wider text-green-600 mb-1">
          Strengths
        </div>
        {m.strengths.slice(0, 4).map((s, i) => (
          <div key={i} className="text-xs text-neutral-600 leading-relaxed flex gap-1.5">
            <span className="text-green-500 flex-shrink-0">+</span>
            <span>{s}</span>
          </div>
        ))}
      </div>
      <div>
        <div className="text-[0.65rem] font-bold uppercase tracking-wider text-red-500 mb-1">
          Weaknesses
        </div>
        {m.weaknesses.slice(0, 3).map((w, i) => (
          <div key={i} className="text-xs text-neutral-600 leading-relaxed flex gap-1.5">
            <span className="text-red-400 flex-shrink-0">&minus;</span>
            <span>{w}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
