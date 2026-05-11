// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useReducer, useState } from "react";
import { RotateCcw } from "lucide-react";
import { ModelBadge } from "./model-card";
import { CONFIGS, fmt, taskCost, type ConfigId } from "./model-data";

// ---------------------------------------------------------------------------
// Form subcomponents
// ---------------------------------------------------------------------------

function SliderField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  format,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
}) {
  return (
    <label className="block">
      <div className="flex justify-between mb-0.5">
        <span className="text-sm text-neutral-600 dark:text-neutral-300">{label}</span>
        <span className="text-sm font-mono font-semibold text-neutral-900 dark:text-neutral-100">
          {format ? format(value) : value}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-neutral-800"
      />
    </label>
  );
}

function ToggleField({
  label,
  detail,
  checked,
  onChange,
}: {
  label: string;
  detail: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2.5 cursor-pointer select-none">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="w-4 h-4 rounded border-neutral-300 dark:border-neutral-700 text-amber-500 dark:text-amber-500 accent-amber-500 cursor-pointer"
      />
      <div>
        <span className="text-sm font-medium text-neutral-700 dark:text-neutral-200">{label}</span>
        <span className="text-xs text-neutral-400 dark:text-neutral-500 ml-1.5">{detail}</span>
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// CostCalculator
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Calculator inputs — reducer + initial state
// ---------------------------------------------------------------------------
//
// Slider values and toggle states are managed via useReducer so the user
// can reset all of them in one click. activeConfig stays in useState
// because resetting to "recommended" implicitly on a slider reset would
// lose a deliberate config-comparison choice.

interface CalculatorState {
  monthlyUsers: number;
  turnsPerSession: number;
  llmSlotPct: number;
  crisisLlmPct: number;
  conversationalPct: number;
  classificationPct: number;
  emotionalPct: number;
  botQuestionPct: number;
  includeJury: boolean;
  includeMultilang: boolean;
}

const INITIAL_STATE: CalculatorState = {
  monthlyUsers: 2000,
  turnsPerSession: 5,
  llmSlotPct: 30,
  crisisLlmPct: 5,
  conversationalPct: 20,
  classificationPct: 25,
  emotionalPct: 5,
  botQuestionPct: 2,
  includeJury: false,
  includeMultilang: false,
};

type CalculatorAction =
  | { type: "set"; field: keyof CalculatorState; value: number | boolean }
  | { type: "reset" };

function calculatorReducer(
  state: CalculatorState,
  action: CalculatorAction,
): CalculatorState {
  switch (action.type) {
    case "set":
      return { ...state, [action.field]: action.value };
    case "reset":
      return INITIAL_STATE;
    default:
      return state;
  }
}

/**
 * Number of LLM calls in a single jury (eval) run, used as the `count`
 * passed to `taskCost("jury", ...)`. Independent of monthly user volume —
 * a jury run is the eval suite, not a per-user cost.
 *
 * Derivation: ~175 scenarios × ~9.5 judge calls per scenario (one per
 * scored dimension plus a small allowance for retries on initial parse
 * failures). Based on R39-era jury runs.
 *
 * Known discrepancy: the per-jury-run cost projected by this calculator
 * (juryTurns × per-call tokens × Opus pricing) comes out higher than the
 * `juryCost` note in `model-data.ts` ("$15–25 per single eval run"). The
 * mismatch most likely reflects inflated per-call input token estimates
 * in the jury TaskDef rather than a wrong scenario count here. Tracked
 * as finding #24 in `POST_REFACTOR_AUDIT.md` — needs investigation
 * against actual eval run logs.
 */
const JURY_TURNS_PER_RUN = 1660;

export function CostCalculator() {
  const [s, dispatch] = useReducer(calculatorReducer, INITIAL_STATE);
  const [activeConfig, setActiveConfig] = useState<ConfigId>("recommended");

  // Helpers — keep call sites concise and type-safe.
  const setNum = (field: keyof CalculatorState) => (value: number) =>
    dispatch({ type: "set", field, value });
  const setBool = (field: keyof CalculatorState) => (value: boolean) =>
    dispatch({ type: "set", field, value });

  const isDirty = (Object.keys(INITIAL_STATE) as Array<keyof CalculatorState>).some(
    (k) => s[k] !== INITIAL_STATE[k],
  );

  const totalTurns = s.monthlyUsers * s.turnsPerSession;
  const conversationalTurns = Math.round(totalTurns * (s.conversationalPct / 100));
  const slotTurns = Math.round(totalTurns * (s.llmSlotPct / 100));
  const crisisTurns = Math.round(totalTurns * (s.crisisLlmPct / 100));
  const classificationTurns = Math.round(totalTurns * (s.classificationPct / 100));
  const emotionalTurns = Math.round(totalTurns * (s.emotionalPct / 100));
  const botQuestionTurns = Math.round(totalTurns * (s.botQuestionPct / 100));
  const juryTurns = s.includeJury ? JURY_TURNS_PER_RUN : 0;
  const multilangTurns = s.includeMultilang ? conversationalTurns : 0;

  const configsWithCost = CONFIGS.map((c) => {
    const bd = {
      conv: taskCost(c.models.conv, "conversational", conversationalTurns),
      slots: taskCost(c.models.slots, "slotExtraction", slotTurns),
      classification: taskCost(c.models.classification, "classification", classificationTurns),
      crisis: taskCost(c.models.crisis, "crisisDetection", crisisTurns),
      emotionalAck: taskCost(c.models.emotionalAck, "emotionalAck", emotionalTurns),
      botQuestion: taskCost(c.models.botQuestion, "botQuestion", botQuestionTurns),
      jury: s.includeJury ? taskCost(c.models.jury, "jury", juryTurns) : 0,
      futureMultilang: s.includeMultilang ? taskCost(c.models.futureMultilang, "futureMultilang", multilangTurns) : 0,
    };
    return { ...c, breakdown: bd, total: Object.values(bd).reduce((a, b) => a + b, 0) };
  });

  const config = configsWithCost.find((c) => c.id === activeConfig)!;

  const activeTasks: { key: string; label: string; model: "haiku" | "sonnet" | "opus"; cost: number; count: number }[] = [
    { key: "conv", label: "Conversational", model: config.models.conv, cost: config.breakdown.conv, count: conversationalTurns },
    { key: "slots", label: "Slot extraction", model: config.models.slots, cost: config.breakdown.slots, count: slotTurns },
    { key: "classification", label: "Unified gate", model: config.models.classification, cost: config.breakdown.classification, count: classificationTurns },
    { key: "crisis", label: "Crisis detection", model: config.models.crisis, cost: config.breakdown.crisis, count: crisisTurns },
    { key: "emotionalAck", label: "Emotional ack.", model: config.models.emotionalAck, cost: config.breakdown.emotionalAck, count: emotionalTurns },
    { key: "botQuestion", label: "Bot questions", model: config.models.botQuestion, cost: config.breakdown.botQuestion, count: botQuestionTurns },
  ];
  if (s.includeJury) {
    activeTasks.push({ key: "jury", label: "Jury evaluation", model: config.models.jury, cost: config.breakdown.jury, count: juryTurns });
  }
  if (s.includeMultilang) {
    activeTasks.push({ key: "futureMultilang", label: "Multi-language", model: config.models.futureMultilang, cost: config.breakdown.futureMultilang, count: multilangTurns });
  }

  return (
    <>
      {/* Sliders */}
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg px-4 py-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-semibold text-neutral-700 dark:text-neutral-300">
            Inputs
          </span>
          <button
            type="button"
            onClick={() => dispatch({ type: "reset" })}
            disabled={!isDirty}
            aria-label="Reset all calculator inputs to defaults"
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium text-neutral-500 dark:text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800 transition disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
          >
            <RotateCcw size={12} />
            Reset
          </button>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <SliderField label="Monthly users" value={s.monthlyUsers} onChange={setNum("monthlyUsers")} min={100} max={50000} step={100} format={(v) => v.toLocaleString()} />
          <SliderField label="Turns per session" value={s.turnsPerSession} onChange={setNum("turnsPerSession")} min={1} max={20} step={1} />
          <SliderField label="% needing LLM slots" value={s.llmSlotPct} onChange={setNum("llmSlotPct")} min={0} max={100} step={5} format={(v) => v + "%"} />
          <SliderField label="% hitting LLM crisis" value={s.crisisLlmPct} onChange={setNum("crisisLlmPct")} min={0} max={30} step={1} format={(v) => v + "%"} />
          <SliderField label="% conversational LLM" value={s.conversationalPct} onChange={setNum("conversationalPct")} min={0} max={60} step={5} format={(v) => v + "%"} />
          <SliderField label="% unified gate (regex miss rate)" value={s.classificationPct} onChange={setNum("classificationPct")} min={0} max={50} step={5} format={(v) => v + "%"} />
          <SliderField label="% emotional responses" value={s.emotionalPct} onChange={setNum("emotionalPct")} min={0} max={20} step={1} format={(v) => v + "%"} />
          <SliderField label="% bot questions" value={s.botQuestionPct} onChange={setNum("botQuestionPct")} min={0} max={10} step={1} format={(v) => v + "%"} />
        </div>

        <div className="flex gap-4 mt-4 pt-3 border-t border-neutral-100 dark:border-neutral-800">
          <ToggleField label="LLM-as-a-jury evaluation" checked={s.includeJury} onChange={setBool("includeJury")} detail="~$45-55 per monthly run" />
          <ToggleField label="Future: multi-language" checked={s.includeMultilang} onChange={setBool("includeMultilang")} detail="Sonnet for non-English" />
        </div>

        <div className="text-sm text-neutral-400 dark:text-neutral-500 mt-3">
          {totalTurns.toLocaleString()} total turns &middot;{" "}
          {conversationalTurns.toLocaleString()} conv &middot;{" "}
          {slotTurns.toLocaleString()} slot &middot;{" "}
          {classificationTurns.toLocaleString()} classify &middot;{" "}
          {crisisTurns.toLocaleString()} crisis &middot;{" "}
          {emotionalTurns.toLocaleString()} emotional &middot;{" "}
          {botQuestionTurns.toLocaleString()} bot Q
          {s.includeJury && <> &middot; {juryTurns.toLocaleString()} jury</>}
          {s.includeMultilang && <> &middot; {multilangTurns.toLocaleString()} multilang</>}
        </div>
      </div>

      {/* Config selector buttons */}
      <div className="grid grid-cols-4 gap-2 mb-4">
        {configsWithCost.map((c) => (
          <button
            key={c.id}
            onClick={() => setActiveConfig(c.id)}
            className={`py-2.5 px-2 rounded-lg text-center transition-all ${
              activeConfig === c.id
                ? "bg-neutral-900 text-white border-2 border-neutral-900 dark:bg-white dark:text-neutral-900 dark:border-white"
                : "bg-white text-neutral-700 border border-neutral-200 hover:border-neutral-300 dark:bg-neutral-900 dark:text-neutral-200 dark:border-neutral-800 dark:hover:border-neutral-700"
            }`}
          >
            <div className="text-sm font-semibold">{c.name}</div>
            <div className="text-xs opacity-70 mt-0.5">{c.tag}</div>
            <div className="text-base font-bold font-mono mt-1">{fmt(c.total)}</div>
          </button>
        ))}
      </div>

      {/* Config detail grid */}
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg overflow-hidden mb-6">
        <div className="px-4 py-3 border-b border-neutral-100 dark:border-neutral-800 flex items-center justify-between">
          <div>
            <div className="text-sm font-bold">{config.name}</div>
            <div className="text-sm text-neutral-400 dark:text-neutral-500 mt-0.5">{config.desc}</div>
          </div>
          <div className="text-right">
            <span className="text-2xl font-bold font-mono">{fmt(config.total)}</span>
            <span className="text-sm text-neutral-400 dark:text-neutral-500">/mo</span>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
          {activeTasks.map((item, idx) => (
            <div
              key={item.key}
              className={`px-4 py-3 border-b border-neutral-100 dark:border-neutral-800 ${(idx + 1) % 4 !== 0 ? "border-r" : ""} last:border-b-0`}
            >
              <div className="text-xs font-semibold text-neutral-500 dark:text-neutral-400 mb-1">
                {item.label}
              </div>
              <ModelBadge model={item.model} />
              <div className="text-lg font-bold font-mono mt-1.5">
                {fmt(item.cost)}
              </div>
              <div className="text-xs text-neutral-400 dark:text-neutral-500">
                {item.count.toLocaleString()} calls
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Post-results savings note */}
      <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-900/40 rounded-lg px-4 py-3 mb-4 text-sm text-emerald-800 dark:text-emerald-200">
        <span className="font-semibold">Cost optimization:</span>{" "}
        Post-results questions (&ldquo;are any open now?&rdquo;, &ldquo;tell me about the first one&rdquo;)
        are handled deterministically from stored card data — zero LLM calls.
        The <code className="bg-emerald-100 dark:bg-emerald-900/40 px-1 rounded text-xs">skip_llm</code> optimization
        also eliminates Sonnet crisis detection calls on short safe actions (≤ 4 words).
      </div>

      {/* Scale projection */}
      <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-lg px-4 py-3 mb-6">
        <div className="text-sm font-semibold mb-2">
          Scale projection (recommended config)
        </div>
        <div className="grid grid-cols-4 gap-2">
          {[2000, 10000, 36000, 50000].map((users) => {
            // Project monthly cost at this user count by recomputing turn
            // counts and running them through taskCost() for the recommended
            // config. Reuses the same pricing data as the main calculator,
            // so this stays in sync with model-data.ts pricing forever.
            const recommended = configsWithCost.find((c) => c.id === "recommended")!;
            const turns = users * s.turnsPerSession;
            const projectedCost =
              taskCost(recommended.models.conv, "conversational",
                Math.round(turns * (s.conversationalPct / 100))) +
              taskCost(recommended.models.slots, "slotExtraction",
                Math.round(turns * (s.llmSlotPct / 100))) +
              taskCost(recommended.models.classification, "classification",
                Math.round(turns * (s.classificationPct / 100))) +
              taskCost(recommended.models.crisis, "crisisDetection",
                Math.round(turns * (s.crisisLlmPct / 100))) +
              taskCost(recommended.models.emotionalAck, "emotionalAck",
                Math.round(turns * (s.emotionalPct / 100))) +
              taskCost(recommended.models.botQuestion, "botQuestion",
                Math.round(turns * (s.botQuestionPct / 100))) +
              (s.includeJury
                ? taskCost(recommended.models.jury, "jury", juryTurns)
                : 0) +
              (s.includeMultilang
                ? taskCost(recommended.models.futureMultilang, "futureMultilang",
                    Math.round(turns * (s.conversationalPct / 100)))
                : 0);
            return (
              <div key={users} className="text-center py-1.5">
                <div className="text-sm text-neutral-500 dark:text-neutral-400">
                  {users === 36000 ? "AI capacity" : users.toLocaleString() + " users"}
                </div>
                <div className="text-lg font-bold font-mono">{fmt(projectedCost)}</div>
                <div className="text-xs text-neutral-400 dark:text-neutral-500">/month</div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
