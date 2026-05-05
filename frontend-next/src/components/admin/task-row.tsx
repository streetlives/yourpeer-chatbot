// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { ModelBadge } from "./model-card";
import { MODELS, fmt, taskCost, type TaskDef } from "./model-data";

export function TaskRow({ task }: { task: TaskDef }) {
  const [open, setOpen] = useState(false);
  const Icon = task.icon;

  return (
    <div
      className={`border rounded-lg overflow-hidden ${
        task.isJury ? "border-amber-300 bg-white" : "bg-white border-neutral-200"
      }`}
    >
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-neutral-50/60 transition-colors"
      >
        <Icon size={16} className="text-neutral-400 flex-shrink-0" />
        <span className="text-sm font-semibold flex-1">{task.name}</span>
        <ModelBadge model={task.recommendation} />
        <ChevronDown
          size={14}
          className={`text-neutral-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-neutral-100">
          <p className="text-xs text-neutral-500 mt-3 mb-3 leading-relaxed">
            {task.desc}
          </p>

          {/* Requirements / methodology */}
          <div className="text-[0.65rem] font-bold uppercase tracking-wider text-neutral-400 mb-1.5">
            {task.isJury ? "Methodology" : "Requirements"}
          </div>
          <div className="mb-3">
            {task.requirements.map((r, i) => (
              <div key={i} className="text-xs text-neutral-600 leading-relaxed pl-3 -indent-3">
                &bull; {r}
              </div>
            ))}
          </div>

          {/* Jury steps */}
          {task.isJury && task.jurySteps && (
            <div className="mb-4">
              <div className="text-[0.65rem] font-bold uppercase tracking-wider text-neutral-400 mb-2">
                Evaluation design
              </div>
              <div className="flex flex-col gap-2">
                {task.jurySteps.map((step, i) => (
                  <div key={i} className="flex gap-3 items-start">
                    <span className="flex-shrink-0 w-5 h-5 rounded-full bg-amber-500 text-white text-[0.65rem] font-bold flex items-center justify-center mt-0.5">
                      {i + 1}
                    </span>
                    <div>
                      <div className="text-xs font-semibold">{step.name}</div>
                      <div className="text-xs text-neutral-500 leading-relaxed">
                        {step.detail}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              {task.juryCost && (
                <div className="mt-3 text-xs bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                  <strong className="text-amber-700">Est. cost:</strong>{" "}
                  <span className="text-neutral-600">{task.juryCost}</span>
                </div>
              )}
              {task.juryInfra && (
                <div className="mt-1.5 text-xs bg-amber-50/60 border border-amber-200/60 rounded-md px-3 py-2">
                  <strong className="text-amber-700">Existing infrastructure:</strong>{" "}
                  <span className="text-neutral-600">{task.juryInfra}</span>
                </div>
              )}
            </div>
          )}

          {/* Rationale */}
          <div
            className={`rounded-md px-3 py-2.5 ${
              task.isJury
                ? "bg-amber-50 border border-amber-200"
                : task.recommendation === "haiku"
                  ? "bg-green-50/60 border border-green-200/60"
                  : "bg-violet-50/60 border border-violet-200/60"
            }`}
          >
            <div
              className={`text-[0.65rem] font-bold uppercase tracking-wider mb-1 ${
                task.isJury
                  ? "text-amber-600"
                  : task.recommendation === "haiku"
                    ? "text-green-600"
                    : "text-violet-600"
              }`}
            >
              {task.isJury
                ? "Why run this"
                : `Why ${MODELS[task.recommendation].name}`}
            </div>
            <div className="text-xs text-neutral-700 leading-relaxed">
              {task.rationale}
            </div>
          </div>

          {/* Token info */}
          {!task.isJury && (
            <div className="flex gap-4 mt-2.5 text-[0.65rem] text-neutral-400">
              <span>~{task.inputTokens} input tok/call</span>
              <span>~{task.outputTokens} output tok/call</span>
              <span>
                Cost/call:{" "}
                {fmt(taskCost(task.recommendation, task.id, 1))}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
