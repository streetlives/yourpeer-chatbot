// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { ExternalLink } from "lucide-react";
import { ModelCard } from "./model-card";
import { TaskRow } from "./task-row";
import { CostCalculator } from "./cost-calculator";
import { TASKS, SOURCES } from "./model-data";

// ---------------------------------------------------------------------------
// MAIN PAGE COMPONENT
// ---------------------------------------------------------------------------

export function ModelAnalysis() {
  return (
    <>
      {/* Intro banner */}
      <div className="bg-white border border-neutral-200 rounded-lg px-3.5 py-2.5 text-sm text-neutral-500 mb-6">
        Claude model cost and capability analysis for each LLM-powered task in
        the chatbot. All pricing from{" "}
        <a
          href="https://docs.anthropic.com/en/about-claude/pricing"
          target="_blank"
          rel="noopener noreferrer"
          className="text-amber-600 hover:underline"
        >
          Anthropic official docs
        </a>
        . Recommendations are preliminary &mdash; run the jury evaluation to
        validate before deploying major changes.
      </div>

      {/* Model cards */}
      <h2 className="text-xs font-bold uppercase tracking-widest text-neutral-400 mb-3 pb-2 border-b border-neutral-200">
        Model comparison
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-8">
        <ModelCard modelKey="haiku" />
        <ModelCard modelKey="sonnet" />
        <ModelCard modelKey="opus" />
      </div>

      {/* Per-task recommendations */}
      <h2 className="text-xs font-bold uppercase tracking-widest text-neutral-400 mb-3 pb-2 border-b border-neutral-200">
        Per-task model recommendations
      </h2>
      <div className="flex flex-col gap-2 mb-8">
        {TASKS.map((t) => (
          <TaskRow key={t.id} task={t} />
        ))}
      </div>

      {/* Cost calculator */}
      <h2 className="text-xs font-bold uppercase tracking-widest text-neutral-400 mb-3 pb-2 border-b border-neutral-200">
        Monthly cost calculator
      </h2>
      <CostCalculator />

      {/* Sources */}
      <details className="mb-4">
        <summary className="text-xs font-bold uppercase tracking-widest text-neutral-400 cursor-pointer hover:text-neutral-600 pb-2 border-b border-neutral-200">
          Sources &amp; methodology
        </summary>
        <div className="mt-3 space-y-1.5">
          {SOURCES.map((s) => (
            <div key={s.id} className="text-xs text-neutral-500 leading-relaxed">
              <span className="text-neutral-400">[{s.id}]</span>{" "}
              <a
                href={s.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-amber-600 hover:underline"
              >
                {s.text}
                <ExternalLink size={10} className="inline ml-0.5 -mt-0.5" />
              </a>
              {" \u2014 "}
              {s.note}
            </div>
          ))}
          <div className="text-xs text-neutral-400 mt-2 pt-2 border-t border-neutral-100">
            Token estimates derived from inspecting the YourPeer codebase system
            prompts, tool schemas, and response constraints.
          </div>
        </div>
      </details>
    </>
  );
}
