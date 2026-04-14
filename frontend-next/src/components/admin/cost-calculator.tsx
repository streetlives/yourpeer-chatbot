// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

"use client";

import { useState } from "react";
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
        <span className="text-xs text-neutral-600">{label}</span>
        <span className="text-xs font-mono font-semibold text-neutral-900">
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
        className="w-4 h-4 rounded border-neutral-300 text-amber-500 accent-amber-500 cursor-pointer"
      />
      <div>
        <span className="text-xs font-medium text-neutral-700">{label}</span>
        <span className="text-[0.65rem] text-neutral-400 ml-1.5">{detail}</span>
      </div>
    </label>
  );
}

// ---------------------------------------------------------------------------
// CostCalculator
// ---------------------------------------------------------------------------

export function CostCalculator() {
  const [monthlyUsers, setMonthlyUsers] = useState(2000);
  const [turnsPerSession, setTurnsPerSession] = useState(5);
  const [llmSlotPct, setLlmSlotPct] = useState(30);
  const [crisisLlmPct, setCrisisLlmPct] = useState(5);
  const [conversationalPct, setConversationalPct] = useState(20);
  const [classificationPct, setClassificationPct] = useState(25);
  const [emotionalPct, setEmotionalPct] = useState(5);
  const [botQuestionPct, setBotQuestionPct] = useState(2);
  const [includeJury, setIncludeJury] = useState(false);
  const [includeMultilang, setIncludeMultilang] = useState(false);
  const [activeConfig, setActiveConfig] = useState<ConfigId>("recommended");

  const totalTurns = monthlyUsers * turnsPerSession;
  const conversationalTurns = Math.round(totalTurns * (conversationalPct / 100));
  const slotTurns = Math.round(totalTurns * (llmSlotPct / 100));
  const crisisTurns = Math.round(totalTurns * (crisisLlmPct / 100));
  const classificationTurns = Math.round(totalTurns * (classificationPct / 100));
  const emotionalTurns = Math.round(totalTurns * (emotionalPct / 100));
  const botQuestionTurns = Math.round(totalTurns * (botQuestionPct / 100));
  const juryTurns = includeJury ? 1660 : 0;
  const multilangTurns = includeMultilang ? conversationalTurns : 0;

  const configsWithCost = CONFIGS.map((c) => {
    const bd = {
      conv: taskCost(c.models.conv, "conversational", conversationalTurns),
      slots: taskCost(c.models.slots, "slotExtraction", slotTurns),
      classification: taskCost(c.models.classification, "classification", classificationTurns),
      crisis: taskCost(c.models.crisis, "crisisDetection", crisisTurns),
      emotionalAck: taskCost(c.models.emotionalAck, "emotionalAck", emotionalTurns),
      botQuestion: taskCost(c.models.botQuestion, "botQuestion", botQuestionTurns),
      jury: includeJury ? taskCost(c.models.jury, "jury", juryTurns) : 0,
      futureMultilang: includeMultilang ? taskCost(c.models.futureMultilang, "futureMultilang", multilangTurns) : 0,
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
  if (includeJury) {
    activeTasks.push({ key: "jury", label: "Jury evaluation", model: config.models.jury, cost: config.breakdown.jury, count: juryTurns });
  }
  if (includeMultilang) {
    activeTasks.push({ key: "futureMultilang", label: "Multi-language", model: config.models.futureMultilang, cost: config.breakdown.futureMultilang, count: multilangTurns });
  }

  return (
    <>
      {/* Sliders */}
      <div className="bg-white border border-neutral-200 rounded-lg px-4 py-4 mb-4">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3">
          <SliderField label="Monthly users" value={monthlyUsers} onChange={setMonthlyUsers} min={100} max={50000} step={100} format={(v) => v.toLocaleString()} />
          <SliderField label="Turns per session" value={turnsPerSession} onChange={setTurnsPerSession} min={1} max={20} step={1} />
          <SliderField label="% needing LLM slots" value={llmSlotPct} onChange={setLlmSlotPct} min={0} max={100} step={5} format={(v) => v + "%"} />
          <SliderField label="% hitting LLM crisis" value={crisisLlmPct} onChange={setCrisisLlmPct} min={0} max={30} step={1} format={(v) => v + "%"} />
          <SliderField label="% conversational LLM" value={conversationalPct} onChange={setConversationalPct} min={0} max={60} step={5} format={(v) => v + "%"} />
          <SliderField label="% unified gate (regex miss rate)" value={classificationPct} onChange={setClassificationPct} min={0} max={50} step={5} format={(v) => v + "%"} />
          <SliderField label="% emotional responses" value={emotionalPct} onChange={setEmotionalPct} min={0} max={20} step={1} format={(v) => v + "%"} />
          <SliderField label="% bot questions" value={botQuestionPct} onChange={setBotQuestionPct} min={0} max={10} step={1} format={(v) => v + "%"} />
        </div>

        <div className="flex gap-4 mt-4 pt-3 border-t border-neutral-100">
          <ToggleField label="LLM-as-a-jury evaluation" checked={includeJury} onChange={setIncludeJury} detail="~$45-55 per monthly run" />
          <ToggleField label="Future: multi-language" checked={includeMultilang} onChange={setIncludeMultilang} detail="Sonnet for non-English" />
        </div>

        <div className="text-xs text-neutral-400 mt-3">
          {totalTurns.toLocaleString()} total turns &middot;{" "}
          {conversationalTurns.toLocaleString()} conv &middot;{" "}
          {slotTurns.toLocaleString()} slot &middot;{" "}
          {classificationTurns.toLocaleString()} classify &middot;{" "}
          {crisisTurns.toLocaleString()} crisis &middot;{" "}
          {emotionalTurns.toLocaleString()} emotional &middot;{" "}
          {botQuestionTurns.toLocaleString()} bot Q
          {includeJury && <> &middot; {juryTurns.toLocaleString()} jury</>}
          {includeMultilang && <> &middot; {multilangTurns.toLocaleString()} multilang</>}
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
                ? "bg-neutral-900 text-white border-2 border-neutral-900"
                : "bg-white text-neutral-700 border border-neutral-200 hover:border-neutral-300"
            }`}
          >
            <div className="text-xs font-semibold">{c.name}</div>
            <div className="text-[0.65rem] opacity-70 mt-0.5">{c.tag}</div>
            <div className="text-base font-bold font-mono mt-1">{fmt(c.total)}</div>
          </button>
        ))}
      </div>

      {/* Config detail grid */}
      <div className="bg-white border border-neutral-200 rounded-lg overflow-hidden mb-6">
        <div className="px-4 py-3 border-b border-neutral-100 flex items-center justify-between">
          <div>
            <div className="text-sm font-bold">{config.name}</div>
            <div className="text-xs text-neutral-400 mt-0.5">{config.desc}</div>
          </div>
          <div className="text-right">
            <span className="text-2xl font-bold font-mono">{fmt(config.total)}</span>
            <span className="text-xs text-neutral-400">/mo</span>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
          {activeTasks.map((item, idx) => (
            <div
              key={item.key}
              className={`px-4 py-3 border-b border-neutral-100 ${(idx + 1) % 4 !== 0 ? "border-r" : ""} last:border-b-0`}
            >
              <div className="text-[0.65rem] font-bold uppercase tracking-wider text-neutral-400 mb-1">
                {item.label}
              </div>
              <ModelBadge model={item.model} />
              <div className="text-lg font-bold font-mono mt-1.5">
                {fmt(item.cost)}
              </div>
              <div className="text-[0.65rem] text-neutral-400">
                {item.count.toLocaleString()} calls
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Post-results savings note */}
      <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3 mb-4 text-sm text-emerald-800">
        <span className="font-semibold">Cost optimization:</span>{" "}
        Post-results questions (&ldquo;are any open now?&rdquo;, &ldquo;tell me about the first one&rdquo;)
        are handled deterministically from stored card data — zero LLM calls.
        The <code className="bg-emerald-100 px-1 rounded text-xs">skip_llm</code> optimization
        also eliminates Sonnet crisis detection calls on short safe actions (≤ 4 words).
      </div>

      {/* Scale projection */}
      <div className="bg-white border border-neutral-200 rounded-lg px-4 py-3 mb-6">
        <div className="text-xs font-semibold mb-2">
          Scale projection (recommended config)
        </div>
        <div className="grid grid-cols-4 gap-2">
          {[2000, 10000, 36000, 50000].map((users) => {
            const turns = users * turnsPerSession;
            let t =
              ((200 / 1e6) * 1 + (80 / 1e6) * 5) *
                Math.round(turns * (conversationalPct / 100)) +
              ((450 / 1e6) * 1 + (60 / 1e6) * 5) *
                Math.round(turns * (llmSlotPct / 100)) +
              ((300 / 1e6) * 1 + (10 / 1e6) * 5) *
                Math.round(turns * (classificationPct / 100)) +
              ((350 / 1e6) * 3 + (20 / 1e6) * 15) *
                Math.round(turns * (crisisLlmPct / 100)) +
              ((280 / 1e6) * 1 + (70 / 1e6) * 5) *
                Math.round(turns * (emotionalPct / 100)) +
              ((350 / 1e6) * 1 + (60 / 1e6) * 5) *
                Math.round(turns * (botQuestionPct / 100));
            if (includeJury) t += ((800 / 1e6) * 3 + (400 / 1e6) * 15) * juryTurns;
            if (includeMultilang) t += ((250 / 1e6) * 3 + (100 / 1e6) * 15) * Math.round(turns * (conversationalPct / 100));
            return (
              <div key={users} className="text-center py-1.5">
                <div className="text-xs text-neutral-500">
                  {users === 36000 ? "AI capacity" : users.toLocaleString() + " users"}
                </div>
                <div className="text-lg font-bold font-mono">{fmt(t)}</div>
                <div className="text-[0.65rem] text-neutral-400">/month</div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
