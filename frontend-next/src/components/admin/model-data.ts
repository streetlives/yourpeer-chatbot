// Copyright (c) 2024 Streetlives, Inc.
//
// Use of this source code is governed by an MIT-style
// license that can be found in the LICENSE file or at
// https://opensource.org/licenses/MIT.

import {
  Zap,
  Brain,
  Shield,
  Languages,
  Scale,
  Route,
  Heart,
  HelpCircle,
} from "lucide-react";
import { SCENARIO_COUNT_APPROX } from "@/lib/admin/eval-dimensions";

// ---------------------------------------------------------------------------
// MODEL VERSIONS — single source of truth within the frontend
// ---------------------------------------------------------------------------
//
// These IDs and display names must stay in sync with the backend's
// `claude_client.py` (chatbot models) and `tests/eval/eval_llm_judge.py`
// (judge model). At present, drift between this file and those Python
// files is unguarded — updating one without the others silently desyncs
// the dashboard from production.
//
// TODO(eval-plan Foundation 4): The eval engineering plan v2 calls for
// (a) recording every model version in the eval report at run time, and
// (b) the dashboard reading model versions from the backend's /api/health
// endpoint rather than hardcoding them here. Once that backend exposure
// lands, replace this constant block with a hook that fetches versions
// at runtime, and have `model-data.ts` consume the runtime values.
// See docs/ops/EVAL_QUALITY_ENGINEERING_PLAN.md §"Foundation 4" for the
// full discussion of why pinning + recording is the right durable fix.

// Pricing in USD per million tokens, as published in Anthropic's docs.
// Coupled to the version block above so a version bump that changes
// pricing stays atomic — previously, MODELS hardcoded prices inline,
// which meant updating `id` without updating `input`/`output` would
// silently desync the cost calculator from the actual API charges.
export const MODEL_VERSIONS = {
  haiku: {
    id: "claude-haiku-4-5-20251001",
    name: "Haiku 4.5",
    inputPerMTok: 1.0,
    outputPerMTok: 5.0,
  },
  sonnet: {
    id: "claude-sonnet-4-6",
    name: "Sonnet 4.6",
    inputPerMTok: 3.0,
    outputPerMTok: 15.0,
  },
  opus: {
    id: "claude-opus-4-6",
    name: "Opus 4.6",
    inputPerMTok: 5.0,
    outputPerMTok: 25.0,
  },
} as const;

// ---------------------------------------------------------------------------
// TYPES
// ---------------------------------------------------------------------------

export interface ModelInfo {
  id: string;
  name: string;
  input: number;
  output: number;
  speed: string;
  latency: string;
  context: string;
  strengths: string[];
  weaknesses: string[];
}

export interface TaskDef {
  id: string;
  name: string;
  desc: string;
  icon: typeof Zap;
  inputTokens: number;
  outputTokens: number;
  requirements: string[];
  recommendation: "haiku" | "sonnet" | "opus";
  rationale: string;
  isJury?: boolean;
  jurySteps?: { name: string; detail: string }[];
  juryCost?: string;
  juryInfra?: string;
}

export type ConfigId = "recommended" | "allHaiku" | "allSonnet" | "sonnetHeavy";

export interface ConfigModels {
  conv: "haiku" | "sonnet";
  slots: "haiku" | "sonnet";
  classification: "haiku" | "sonnet"; // unified gate (pre-routing classification + extraction)
  crisis: "haiku" | "sonnet";
  emotionalAck: "haiku" | "sonnet";
  botQuestion: "haiku" | "sonnet";
  jury: "opus";
  futureMultilang: "sonnet";
}

export interface ConfigDef {
  id: ConfigId;
  name: string;
  tag: string;
  desc: string;
  models: ConfigModels;
}

export interface SourceDef {
  id: number;
  text: string;
  url: string;
  note: string;
}

// ---------------------------------------------------------------------------
// MODELS
// ---------------------------------------------------------------------------

export const MODELS: Record<string, ModelInfo> = {
  haiku: {
    id: MODEL_VERSIONS.haiku.id,
    name: MODEL_VERSIONS.haiku.name,
    input: MODEL_VERSIONS.haiku.inputPerMTok,
    output: MODEL_VERSIONS.haiku.outputPerMTok,
    speed: "4-5x faster than Sonnet [1]",
    latency: "Est. ~0.4s TTFT [3]",
    context: "200K",
    strengths: [
      "Anthropic\u2019s safest model \u2014 lowest misaligned behavior rate vs Sonnet 4.5 and Opus 4.1 [1]",
      "4-5x faster than Sonnet [1]",
      "Strong classification accuracy (on par with Sonnet 4 generation) [1]",
      "Excellent instruction following for structured tasks [5]",
      "90% of Sonnet 4.5\u2019s coding performance per Augment\u2019s agentic eval [1]",
      "Low prompt injection vulnerability [2]",
    ],
    weaknesses: [
      "Less nuanced reasoning on ambiguous / indirect language",
      "May over-engineer or be verbose on loosely specified tasks [5]",
      "No adaptive thinking \u2014 supports extended thinking only [4]",
    ],
  },
  sonnet: {
    id: MODEL_VERSIONS.sonnet.id,
    name: MODEL_VERSIONS.sonnet.name,
    input: MODEL_VERSIONS.sonnet.inputPerMTok,
    output: MODEL_VERSIONS.sonnet.outputPerMTok,
    speed: "Moderate",
    latency: "Est. ~0.8s TTFT [3]",
    context: "1M (beta)",
    strengths: [
      "Strongest reasoning and nuance among mid-tier models [6]",
      "Adaptive thinking \u2014 adjusts reasoning depth to complexity [4]",
      "94% accuracy on Anthropic\u2019s internal insurance computer use benchmark [6]",
      "Tool-calling reliability: follows schemas more consistently [6]",
      "Fewer hallucinated links in computer use evals (per partner report) [7]",
      "70% more token-efficient than Sonnet 4.5 on partner filesystem benchmark [7]",
    ],
    weaknesses: [
      "3x more expensive than Haiku [8]",
      "Slower response time than Haiku [6]",
      "Overkill for simple classification / extraction tasks",
      "Higher latency matters for real-time chat UX",
    ],
  },
  opus: {
    id: MODEL_VERSIONS.opus.id,
    name: MODEL_VERSIONS.opus.name,
    input: MODEL_VERSIONS.opus.inputPerMTok,
    output: MODEL_VERSIONS.opus.outputPerMTok,
    speed: "Slowest (deepest reasoning)",
    latency: "Est. ~1.5s TTFT [3]",
    context: "1M (beta)",
    strengths: [
      "Most capable reasoning model in the Claude family [9]",
      "1M token context window \u2014 processes entire codebases in one session [9]",
      "Adaptive thinking with deepest reasoning depth [9]",
      "State-of-the-art on Humanity\u2019s Last Exam, Terminal-Bench 2.0, BrowseComp [9]",
      "Outperforms GPT-5.2 by ~144 Elo on GDPval-AA (finance, legal, knowledge work) [9]",
      "Lowest over-refusal rate among recent Claude models [9]",
    ],
    weaknesses: [
      "5x input / 5x output more expensive than Haiku [8]",
      "Highest latency \u2014 unsuitable for real-time chat",
      "Overkill for classification, extraction, and short-form generation",
      "Used only for evaluation judging, not production tasks",
    ],
  },
};

// ---------------------------------------------------------------------------
// TASKS
// ---------------------------------------------------------------------------

export const TASKS: TaskDef[] = [
  {
    id: "conversational",
    name: "Conversational fallback",
    icon: Zap,
    desc: "General chat when the user\u2019s message doesn\u2019t match service keywords. Uses Claude Haiku for speed.",
    inputTokens: 200,
    outputTokens: 80,
    requirements: [
      "Warm, empathetic tone for vulnerable population",
      "Short responses (1-3 sentences)",
      "Gently steer toward service queries",
      "Never fabricate service info",
    ],
    recommendation: "haiku",
    rationale:
      "Simplest LLM task \u2014 short prompt, short output, no tool calling. Haiku\u2019s 4-5x speed advantage directly improves chat UX. The 1-3 sentence output constraint means Sonnet\u2019s deeper reasoning adds no value. Haiku\u2019s instruction following is sufficient for the guardrails.",
  },
  {
    id: "slotExtraction",
    name: "Slot extraction (tool calling)",
    icon: Brain,
    desc: "LLM-based structured extraction of service type, location, age, urgency, gender from natural language via Claude function calling. Runs after routing when the message is classified as a service request, with conversation history for multi-turn context. The unified classification gate handles first-pass extraction for messages regex misses; this call adds conversation-history enrichment.",
    inputTokens: 450,
    outputTokens: 60,
    requirements: [
      "Accurate tool calling with structured JSON output",
      "Handle indirect language (\u2018just got out of hospital\u2019 \u2192 shelter)",
      "Resolve conflicting signals (\u2018in Queens but need food in Brooklyn\u2019)",
      "Extract from context (\u2018my son is 12\u2019 \u2192 age=12)",
      "Only extract what\u2019s stated, don\u2019t guess",
      "Use conversation history for multi-turn slot accumulation",
    ],
    recommendation: "haiku",
    rationale:
      "Simple schema (5 optional fields), codebase routes only complex messages to LLM (regex handles simple cases), and Haiku\u2019s tool-calling is reliable for bounded schemas. The unified gate already extracted slots pre-routing; this call re-extracts with conversation history for better multi-turn accuracy. Monitor accuracy \u2014 first task to upgrade to Sonnet if edge cases increase.",
  },
  {
    id: "classification",
    name: "Unified classification gate",
    icon: Route,
    desc: "When regex finds no service type, no action, and no tone on a 4+ word message, a single Haiku call returns all classification dimensions: service_type, location, tone, action, additional_services, urgency, age, and family_status. Replaces the old separate classification + slot enrichment calls.",
    inputTokens: 400,
    outputTokens: 120,
    requirements: [
      "Return structured JSON with all classification dimensions in one call",
      "Distinguish intent from mention (\u2018I saw a doctor on TV\u2019 \u2192 null, not medical)",
      "Handle indirect service needs (\u2018roof over my head\u2019 \u2192 shelter, \u2018I\u2019m starving\u2019 \u2192 food)",
      "Detect tone (emotional, frustrated, urgent, confused) from natural language",
      "Detect action (confirm_yes, escalation, etc.) from informal language (\u2018bet\u2019, \u2018aight\u2019)",
      "Extract NYC location from context",
      "Only fires when regex found nothing (~25% of messages)",
    ],
    recommendation: "haiku",
    rationale:
      "This is the regex safety net \u2014 it only fires when regex found nothing useful, so the bar is \u2018better than nothing\u2019 not \u2018perfect.\u2019 The JSON output schema is bounded (9 fields, all with finite valid values). Haiku\u2019s structured output and instruction following are strong for this type of task [5]. Speed matters because the gate adds latency to ~25% of messages. The existing classify_message_llm fallback is kept as a secondary safety net.",
  },
  {
    id: "crisisDetection",
    name: "Crisis detection",
    icon: Shield,
    desc: "Classify whether a message indicates suicide, DV, trafficking, medical emergency, or safety concern. Only invoked when regex misses. LLM call is skipped entirely for short safe actions (\u2264 4 words like \u201Cyes\u201D, \u201Cstart over\u201D) \u2014 the regex check still runs.",
    inputTokens: 350,
    outputTokens: 20,
    requirements: [
      "Must not miss genuine crises (false negatives are dangerous)",
      "Catch indirect / paraphrased crisis language",
      "Handle culturally specific expressions",
      "Simple JSON output: {crisis: true/false, category: string}",
      "Fail-open: if uncertain, classify as crisis",
      "Always runs before post-results handler (eval P10 safety requirement)",
    ],
    recommendation: "sonnet",
    rationale:
      "Safety-critical classification where false negatives have real consequences. Sonnet\u2019s deeper reasoning catches indirect crisis language Haiku may miss. Volume is very low (~3-5% of turns reach LLM stage after the skip_llm optimization for short safe actions) so the 3x cost premium adds negligible total cost.",
  },
  {
    id: "emotionalAck",
    name: "Emotional acknowledgment",
    icon: Heart,
    desc: "Generate a warm, empathetic response when the user shares emotional distress that isn\u2019t crisis-level (\u201CI\u2019m feeling really down\u201D, \u201Chaving a rough day\u201D). Regex catches common phrases; the LLM classifier routes indirect expressions.",
    inputTokens: 280,
    outputTokens: 70,
    requirements: [
      "Lead with acknowledgment \u2014 validate the user\u2019s feeling before anything else",
      "Do NOT list service categories or show a menu of options",
      "Do NOT give medical, psychological, legal, or financial advice",
      "Do NOT diagnose, minimize, or suggest treatments",
      "Mention peer navigator as a human support option",
      "Keep it to 2-3 sentences that feel genuine, not scripted",
    ],
    recommendation: "haiku",
    rationale:
      "This is a short-form generation task (2-3 sentences) with clear guardrails. The output constraints are explicit: don\u2019t list services, don\u2019t give advice, acknowledge the feeling. Haiku\u2019s instruction following is strong for well-specified tasks [5], and its 4-5x speed advantage matters because emotional messages deserve an immediate response, not a noticeable delay. The guardrails are enforced by the prompt, not by model reasoning depth \u2014 Sonnet wouldn\u2019t produce a measurably warmer response given the same constraints. If tone quality becomes a concern, this is a good candidate for A/B testing via the judge eval.",
  },
  {
    id: "botQuestion",
    name: "Bot capability questions",
    icon: HelpCircle,
    desc: "Answer user questions about how the bot works (\u201CWhy couldn\u2019t you get my location?\u201D, \u201CWhat can you search for?\u201D, \u201CDo you work outside NYC?\u201D). Facts about the bot\u2019s capabilities are provided in the prompt.",
    inputTokens: 350,
    outputTokens: 60,
    requirements: [
      "Answer the user\u2019s specific question directly and honestly",
      "Draw from a closed set of facts provided in the system prompt",
      "Be honest about limitations (NYC only, browser location requires permission, etc.)",
      "Keep response to 2-3 sentences",
      "Do not fabricate capabilities the bot doesn\u2019t have",
    ],
    recommendation: "haiku",
    rationale:
      "Factual QA over a closed domain \u2014 all facts about the bot\u2019s capabilities are provided directly in the prompt. The model doesn\u2019t need to reason or infer; it needs to select the relevant fact and phrase it clearly. This is Haiku\u2019s sweet spot: structured input, short output, strong instruction following [5]. Volume is very low (~1-2% of turns), so even if Sonnet were marginally better, the cost and latency difference would not be justified. A static fallback response covers the case when the LLM is unavailable.",
  },
  {
    id: "jury",
    name: "LLM-as-Judge evaluation",
    icon: Scale,
    desc: `Run the existing eval suite (${SCENARIO_COUNT_APPROX} scenarios across 20 categories) with both Haiku and Sonnet performing each LLM task, then have Opus score both. Produces empirical model-selection data to validate or override the recommendations above.`,
    inputTokens: 3000,
    outputTokens: 1500,
    requirements: [
      "Run each eval scenario twice: once with Haiku, once with Sonnet on each LLM task",
      "Judge model (Opus) scores both runs on the 11 rubric dimensions (8 core + 3 domain-specific)",
      "Compare per-task deltas: where does Haiku match Sonnet? Where does it fall short?",
      "Focus on crisis-category scenarios \u2014 the highest-stakes decisions",
      "Produce a per-task recommendation backed by data, not just reasoning",
    ],
    recommendation: "opus",
    rationale:
      "The judge model should be MORE capable than the models being evaluated to avoid same-tier scoring bias. Claude Opus serves as the judge \u2014 it sits above both Haiku and Sonnet in Anthropic\u2019s model hierarchy. The PoLL research (Verga et al., 2024) shows a diverse jury of smaller models can outperform a single large judge, but for same-family comparison (Haiku vs Sonnet), Opus is the right choice. Use this to validate model choices before deploying \u2014 a single eval run costs ~$15\u201325 in API credits [8].",
    isJury: true,
    jurySteps: [
      {
        name: "Fork the eval runner",
        detail:
          "Modify eval_llm_judge.py to accept a --model-config flag that controls which Claude model handles each LLM task.",
      },
      {
        name: "Run paired evaluations",
        detail:
          `Execute the full scenario suite (${SCENARIO_COUNT_APPROX}) under two configs: (A) All-Haiku and (B) Recommended mix. Each run produces per-scenario scores across 11 dimensions.`,
      },
      {
        name: "Head-to-head judging",
        detail:
          "For each scenario, present both transcripts to the judge model side-by-side. Eliminates absolute-score bias.",
      },
      {
        name: "Focus on crisis + edge cases",
        detail:
          "The 10 crisis scenarios, 14 edge-case scenarios, 30 multi-intent scenarios, and 18 natural-language scenarios are where model differences are most likely. Flag any scenario where Haiku scored \u22643 on safety_crisis.",
      },
      {
        name: "Produce decision matrix",
        detail:
          "Output: task \u00d7 scenario-category \u2192 Haiku win / Sonnet win / tie. If Haiku matches Sonnet on \u226590% of non-crisis scenarios, the mixed config is validated.",
      },
    ],
    // The "$15-25 per run" estimate predates the current scenario count
    // and per-call token measurements. The cost calculator's projection
    // (juryTurns × per-call tokens × Opus pricing) comes out 3-4× higher.
    // Tracked as audit finding #24 — investigate against actual run logs
    // to reconcile. Until then, the range here is a lower bound.
    juryCost:
      `~$15\u201325 per single eval run (${SCENARIO_COUNT_APPROX} scenarios \u00d7 Opus judge calls at $5/$25 per MTok [8]). A full paired comparison (two configs + head-to-head judging) \u2248 $50\u201375 total.`,
    juryInfra:
      `eval_llm_judge.py already has the scenario bank (${SCENARIO_COUNT_APPROX} scenarios across 20 categories), conversation simulator, 11-dimension rubric (8 core + 3 domain-specific), weighted scoring, and Opus judge prompt. Main change: parameterize which model handles each LLM call.`,
  },
  {
    id: "futureMultilang",
    name: "Future: multi-language",
    icon: Languages,
    desc: "The spec calls for English + Spanish minimum. LLM-driven conversation in non-English languages.",
    inputTokens: 250,
    outputTokens: 100,
    requirements: [
      "Natural conversational Spanish",
      "Slot extraction from Spanish input",
      "Cultural competence in language",
    ],
    recommendation: "sonnet",
    rationale:
      "Sonnet 4.6 has significantly stronger multilingual capabilities. When multi-language ships, re-run the judge eval with Spanish-language scenarios to validate empirically.",
  },
];

// ---------------------------------------------------------------------------
// SOURCES
// ---------------------------------------------------------------------------

export const SOURCES: SourceDef[] = [
  { id: 1, text: "Anthropic \u2014 Introducing Claude Haiku 4.5 (Oct 2025)", url: "https://www.anthropic.com/news/claude-haiku-4-5", note: "Safety alignment, speed claims, Augment coding eval." },
  { id: 2, text: "Claude Haiku 4.5 System Card (Oct 2025)", url: "https://anthropic.com/claude-haiku-4-5-system-card", note: "ASL-2 classification, prompt injection rates." },
  { id: 3, text: "Artificial Analysis (third-party benchmarking)", url: "https://artificialanalysis.ai", note: "Latency estimates. Anthropic does not publish specific latency figures." },
  { id: 4, text: "Claude API Docs \u2014 Extended thinking", url: "https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking", note: "Adaptive thinking is Opus 4.6 / Sonnet 4.6 only. Haiku 4.5 supports extended thinking with budget_tokens." },
  { id: 5, text: "MindStudio \u2014 GPT-5.4 Mini vs Haiku comparison (third-party)", url: "https://www.mindstudio.ai/blog/gpt-54-mini-vs-claude-haiku-sub-agent-comparison", note: "Instruction-following and verbosity observations." },
  { id: 6, text: "Anthropic \u2014 Introducing Claude Sonnet 4.6 (Feb 2026)", url: "https://www.anthropic.com/news/claude-sonnet-4-6", note: "94% insurance benchmark is Anthropic-internal. Tool-calling and safety claims." },
  { id: 7, text: "Anthropic \u2014 Claude Sonnet product page", url: "https://www.anthropic.com/claude/sonnet", note: "\u201cZero hallucinated links\u201d and \u201c70% more token-efficient\u201d are partner/user quotes." },
  { id: 8, text: "Claude API Pricing", url: "https://docs.anthropic.com/en/about-claude/pricing", note: "Haiku 4.5: $1/$5 per MTok. Sonnet 4.6: $3/$15 per MTok. Opus 4.6: $5/$25 per MTok. Verified April 2026." },
  { id: 9, text: "Anthropic \u2014 Introducing Claude Opus 4.6 (Feb 2026)", url: "https://www.anthropic.com/news/claude-opus-4-6", note: "1M context window, adaptive thinking, agent teams. State-of-the-art on Terminal-Bench 2.0, HLE, BrowseComp. GDPval-AA: +144 Elo vs GPT-5.2. Used as LLM-as-judge evaluator." },
];

// ---------------------------------------------------------------------------
// CONFIGS
// ---------------------------------------------------------------------------

export const CONFIGS: ConfigDef[] = [
  { id: "recommended", name: "Recommended", tag: "Best balance", desc: "Haiku conv + slots + classification + emotional + bot Q, Sonnet crisis", models: { conv: "haiku", slots: "haiku", classification: "haiku", crisis: "sonnet", emotionalAck: "haiku", botQuestion: "haiku", jury: "opus", futureMultilang: "sonnet" } },
  { id: "allHaiku", name: "All Haiku", tag: "Cheapest", desc: "Haiku for everything", models: { conv: "haiku", slots: "haiku", classification: "haiku", crisis: "haiku", emotionalAck: "haiku", botQuestion: "haiku", jury: "opus", futureMultilang: "sonnet" } },
  { id: "allSonnet", name: "All Sonnet", tag: "Highest quality", desc: "Sonnet for everything", models: { conv: "sonnet", slots: "sonnet", classification: "sonnet", crisis: "sonnet", emotionalAck: "sonnet", botQuestion: "sonnet", jury: "opus", futureMultilang: "sonnet" } },
  { id: "sonnetHeavy", name: "Sonnet heavy", tag: "Current pattern", desc: "Sonnet conv + slots, Haiku crisis", models: { conv: "sonnet", slots: "sonnet", classification: "sonnet", crisis: "haiku", emotionalAck: "sonnet", botQuestion: "sonnet", jury: "opus", futureMultilang: "sonnet" } },
];

// ---------------------------------------------------------------------------
// HELPERS
// ---------------------------------------------------------------------------

export function fmt(n: number): string {
  if (n === 0) return "$0.00";
  if (n < 0.01) return "<$0.01";
  if (n < 1) return "$" + n.toFixed(3);
  return "$" + n.toFixed(2);
}

export function taskCost(
  modelKey: "haiku" | "sonnet" | "opus",
  taskId: string,
  count: number,
): number {
  const m = MODELS[modelKey];
  const t = TASKS.find((x) => x.id === taskId);
  if (!t) return 0;
  return ((t.inputTokens / 1e6) * m.input + (t.outputTokens / 1e6) * m.output) * count;
}
