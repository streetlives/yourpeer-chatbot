"""
Tier 2 smoke test — chunked-MiniLM baseline vs j-hartmann on first-turn
eval messages.

Compares two classifiers on the same input set:
  (a) chunked MiniLM TONE_ROUTES — read from out_chunked.json baseline
      (the result of running tone_routes_eval_compare.py --chunk).
  (b) j-hartmann/emotion-english-distilroberta-base
      Ekman 6 + neutral, ~329 MB. Mature, 3/9 of our categories mapped.

  HISTORICAL NOTE: An earlier version of this script tested two
  additional pretrained classifiers in slot (c). Both were dropped
  for separate reasons that are worth preserving as foundation-tooling
  lessons.

  - kashyaparun/Mental-Health-Chatbot-using-RoBERTa-fine-tuned-on-GoEmotion
    is a broken HuggingFace upload: declares
    architectures=['RobertaForMaskedLM'], uses non-standard layer names
    (transformer.encoder.* instead of roberta.encoder.*) so all weights
    load as UNEXPECTED, missing classifier.dense / classifier.out_proj
    entirely. Result: a randomly-initialized classifier emitting
    near-uniform LABEL_0..27 outputs. Diagnosed May 7 2026. The strict
    load validator (_validate_loaded) was added in response.

  - shhossain/all-MiniLM-L6-v2-sentiment-classifier (which would have
    been the architectural-footprint argument: same MiniLM backbone the
    chatbot already uses, ~22.7M params) was tested briefly but breaks
    against transformers >= 5.0 with AttributeError on
    `all_tied_weights_keys`. The model uses custom code via
    trust_remote_code=True, was last updated ~2 years ago, and predates
    the v5 attribute requirement. Patching around it would mean owning
    a compatibility shim against an unmaintained upstream. The
    architectural argument that motivated testing shhossain (own the
    classifier, share MiniLM backbone) is better served by fine-tuning
    our own; see docs/design/CLASSIFIER_FINE_TUNE_PLAN.md.

Each model:
  - Runs per-chunk with the same chunker as tone_routes_eval_compare.py
  - Maps its native taxonomy → our 9-category taxonomy via fixed mapping
  - Fires the highest-confidence mapped category above threshold

Reports:
  - Per-bucket counts for both classifiers
  - WIN-set comparison: do the models catch the same cases or different ones?
  - Specific behavior on the named "killer" messages

Usage (from repo root, chatbot venv active, with `transformers` installed):
    python tools/tier2_model_smoketest.py --baseline out_chunked.json
    python tools/tier2_model_smoketest.py --baseline out_chunked.json --threshold 0.40
    python tools/tier2_model_smoketest.py --baseline out_chunked.json --json out_models.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

# -- Config -------------------------------------------------------------------

JHARTMANN_MODEL = "j-hartmann/emotion-english-distilroberta-base"
DEFAULT_THRESHOLD = 0.50
DEFAULT_MIN_CHUNK_TOKENS = 3

# Per-model load options. Empty for j-hartmann (vanilla
# RobertaForSequenceClassification, no special handling needed). Kept
# as a dict so future model additions have a clear extension point.
MODEL_LOAD_KWARGS: dict[str, dict[str, Any]] = {
    "jhartmann": {},
}

# Native-taxonomy → our-9-categories mapping for j-hartmann.
# Ekman 6 + neutral; covers anger, fear, sadness mappable. Disgust,
# joy, surprise, neutral do not map (joy/surprise are positive; disgust
# isn't anger; neutral is no-tone).
JHARTMANN_TO_OURS: dict[str, str] = {
    "anger": "angry",
    "fear": "scared",
    "sadness": "sad",
}

# Domain-specific categories no standard emotion taxonomy has.
# Both models will return None for clauses expressing these — lexicon
# remains the only source of truth for them.
UNMAPPED_CATEGORIES = ["alone", "undeserving", "distrust"]


# -- Chunking (kept identical to tone_routes_eval_compare.py) -----------------

_CHUNK_SPLIT_PATTERN = re.compile(
    r'[.!?,;]+\s*|\s+(?:but|and|or|because|so|yet)\s+',
    flags=re.IGNORECASE,
)


def split_into_chunks(text: str, min_tokens: int) -> list[str]:
    raw = _CHUNK_SPLIT_PATTERN.split(text)
    return [
        c.strip()
        for c in raw
        if c and c.strip() and len(c.strip().split()) >= min_tokens
    ]


# -- Model classification ------------------------------------------------------

def model_classify(
    text: str,
    classifier,
    mapping: dict[str, str],
    threshold: float,
    min_chunk_tokens: int,
) -> dict[str, Any]:
    """Run the pretrained model on each chunk; return best mapped fire.

    For each chunk, get the model's full class-probability distribution.
    Filter to classes that map to our 9 categories. Take the highest-
    probability mapped class above threshold across all chunks.

    Returns a dict with: cat (str|None), conf (float), winning_chunk (str|None),
    winning_native_label (str|None), n_chunks (int).
    """
    chunks = split_into_chunks(text, min_chunk_tokens)
    if not chunks:
        return {
            "cat": None, "conf": 0.0,
            "winning_chunk": None, "winning_native_label": None,
            "n_chunks": 0,
        }

    # Run model on each chunk. top_k=None returns all classes.
    # This works for both single-label (softmax) and multi-label (sigmoid)
    # since we only need per-class probabilities.
    best_cat: str | None = None
    best_conf: float = 0.0
    best_chunk: str | None = None
    best_native: str | None = None

    for chunk in chunks:
        try:
            results = classifier(chunk, top_k=None)
        except Exception:
            # Some pipeline versions use return_all_scores instead of top_k
            results = classifier(chunk, return_all_scores=True)
            # That call returns [[{'label':..., 'score':...}, ...]] (nested)
            if results and isinstance(results[0], list):
                results = results[0]

        for entry in results:
            label = entry["label"].lower()
            score = float(entry["score"])
            mapped = mapping.get(label)
            if mapped is None:
                continue
            if score < threshold:
                continue
            if score > best_conf:
                best_conf = score
                best_cat = mapped
                best_chunk = chunk
                best_native = label

    return {
        "cat": best_cat,
        "conf": round(best_conf, 3),
        "winning_chunk": best_chunk,
        "winning_native_label": best_native,
        "n_chunks": len(chunks),
    }


# -- Bucketing logic (mirrors tone_routes_eval_compare.py) --------------------

def categorize(lex_specific: str | None, model_cat: str | None) -> str:
    lex_classified = lex_specific is not None and lex_specific != "generic"
    model_classified = model_cat is not None
    if not lex_classified and model_classified:
        return "WIN"
    if lex_classified and model_classified and lex_specific != model_cat:
        return "CONFLICT"
    if lex_classified and model_classified and lex_specific == model_cat:
        return "AGREEMENT"
    if lex_classified and not model_classified:
        return "LEX_ONLY"
    return "DOUBLE_MISS"


# -- Reporting ----------------------------------------------------------------

def print_bucket_table(rows: list[dict], baselines: dict[str, list[dict]]) -> None:
    """Side-by-side bucket counts for all three classifiers."""
    from collections import Counter

    def buckets(records, key):
        return Counter(r[key] for r in records)

    minilm_buckets = buckets(rows, "bucket_minilm")
    jh_buckets = buckets(rows, "bucket_jhartmann")

    print(f"\n=== Bucket counts ({len(rows)} first-turn messages) ===\n")
    print(f"{'bucket':<13} {'minilm-chunked':>15} {'j-hartmann':>12}")
    print("-" * 42)
    for b in ["WIN", "CONFLICT", "LEX_ONLY", "AGREEMENT", "DOUBLE_MISS"]:
        print(f"{b:<13} {minilm_buckets[b]:>15} {jh_buckets[b]:>12}")


def print_win_overlap(rows: list[dict]) -> None:
    """Show whether the two classifiers catch the SAME cases or DIFFERENT ones."""
    minilm_wins = {r["id"] for r in rows if r["bucket_minilm"] == "WIN"}
    jh_wins = {r["id"] for r in rows if r["bucket_jhartmann"] == "WIN"}

    print("\n=== WIN-set overlap ===\n")
    print(f"  MiniLM-chunked wins:    {len(minilm_wins)} = {sorted(minilm_wins)}")
    print(f"  j-hartmann wins:        {len(jh_wins)} = {sorted(jh_wins)}")

    union = minilm_wins | jh_wins
    print(f"\n  Union of all wins:      {len(union)}")
    print(f"  Both agree on:          {len(minilm_wins & jh_wins)}")
    print(f"  Only MiniLM catches:    {len(minilm_wins - jh_wins)}")
    print(f"  Only j-hartmann:        {len(jh_wins - minilm_wins)}")


def print_win_details(rows: list[dict], model_key: str, label: str) -> None:
    print(f"\n=== {label} WINS ===\n")
    for r in rows:
        if r[f"bucket_{model_key}"] != "WIN":
            continue
        m = r[f"model_{model_key}"]
        print(f"  [{r['id']}]")
        print(f"    msg:    {r['text']!r}")
        print(f"    chunk:  {m['winning_chunk']!r}")
        print(f"    fired:  {m['cat']} (native: {m['winning_native_label']}) @{m['conf']:.2f}")
        print(f"    lex:    {r['lex_coarse']}/{r['lex_specific']}")
        print()


def print_killer_cases(rows: list[dict]) -> None:
    """How does each model handle the named killer cases?"""
    targets = [
        "multi_shame_single_service",       # MiniLM landed wrong category
        "multi_shame_food_bank_first_time", # MiniLM win — does j-hartmann catch it?
        "multi_shame_shelter_stigma",       # AGREEMENT in MiniLM
        "wa_mental_health_plus_housing",    # AGREEMENT in MiniLM
        "wa_rough_sleeper_urgent",          # alone-only — j-hartmann should miss
        "staten_island_mental_health",      # struggling — should be sad/rough_day
        "peer_aging_out_foster",            # Pattern 2 — all should miss
        "natural_new_to_nyc",               # Pattern 2 — all should miss
    ]
    print("\n=== Named killer cases — three-way comparison ===\n")
    for tid in targets:
        match = next((r for r in rows if r["id"] == tid), None)
        if not match:
            continue
        print(f"  [{tid}]")
        print(f"    msg: {match['text'][:100]!r}")
        print(f"    lex={match['lex_specific']!s:<14} "
              f"minilm={match['model_minilm']['cat']!s:<10} "
              f"jh={match['model_jhartmann']['cat']!s}")
        # If any model fired, show the chunk that triggered
        for k, lbl in [("minilm", "minilm"), ("jhartmann", "jh")]:
            m = match[f"model_{k}"]
            if m["cat"]:
                print(f"      {lbl} chunk: {m.get('winning_chunk')!r} "
                      f"(native: {m.get('winning_native_label')}, conf {m['conf']:.2f})")
        print()


# -- Main ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True,
                        help="Path to out_chunked.json (the chunked TONE_ROUTES "
                             "baseline)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Probability threshold for model fires "
                             f"(default {DEFAULT_THRESHOLD})")
    parser.add_argument("--min-chunk-tokens", type=int,
                        default=DEFAULT_MIN_CHUNK_TOKENS,
                        help="Minimum tokens per chunk (default 3)")
    parser.add_argument("--json", type=Path, default=None,
                        help="Optional path to write full JSON output")
    parser.add_argument("--skip-jhartmann", action="store_true",
                        help="Skip j-hartmann load (run baseline-only)")
    args = parser.parse_args()

    if not args.baseline.exists():
        print(f"Baseline not found: {args.baseline}", file=sys.stderr)
        return 1

    # Load chunked-MiniLM baseline
    baseline = json.loads(args.baseline.read_text())
    print(f"Loaded baseline: {len(baseline)} messages from {args.baseline}")

    # Load both models defensively. If one fails (commonly: missing
    # safetensors + old torch + recent transformers, see CVE-2025-32434),
    # report the failure and continue with whichever model(s) loaded
    # successfully. The user can act on partial results rather than
    # losing the whole run to one transitive dependency issue.
    from transformers import pipeline

    classifiers: dict[str, Any] = {}
    load_failures: list[str] = []

    def _validate_loaded(name: str, clf, mapping: dict[str, str]) -> bool:
        """Strict post-load validation. Catches the silent-failure mode
        where pipeline() returns successfully but the model is broken
        (kashyaparun lesson, May 2026): wrong layer names → all weights
        randomly-initialized → uniform-distribution noise output.

        Three checks:
          1. id2label is not the default 'LABEL_N' placeholder (which
             indicates the model card never declared label names — a
             strong signal the classifier head wasn't published).
          2. At least one model label overlaps the mapping keys (else
             the whole mapping is dead code and produces 0 fires).
          3. Smoke probe: model can classify a clear emotional input
             above 0.5. If a strongly emotional sentence yields top-1
             score below 0.5, the classifier head is likely random or
             the model is multi-label sigmoid (different threshold
             needed). Either way, surfacing this lets the user act.
        """
        config = getattr(clf.model, "config", None)
        id2label: dict = getattr(config, "id2label", {}) if config else {}

        # Check 1: id2label sanity
        label_values = [str(v) for v in id2label.values()]
        placeholders = [v for v in label_values if v.startswith("LABEL_")]
        if label_values and len(placeholders) == len(label_values):
            print(
                f"  ⚠ {name}: id2label is all 'LABEL_N' placeholders. "
                f"The model card didn't publish proper label names — "
                f"the classifier head may be missing or randomly initialized.",
                file=sys.stderr,
            )
            return False

        # Check 2: mapping overlap
        label_set = {v.lower() for v in label_values}
        mapping_keys = set(mapping)
        overlap = label_set & mapping_keys
        if not overlap:
            preview = sorted(label_set)[:6]
            print(
                f"  ⚠ {name}: model emits labels {preview}{'...' if len(label_set) > 6 else ''} "
                f"but mapping expects {sorted(mapping_keys)}. Mapping will produce 0 fires.",
                file=sys.stderr,
            )
            return False

        # Check 3: smoke probe — strongly emotional input should produce a
        # decisive top-1 score for any working classifier.
        try:
            probe = clf("I am scared and overwhelmed right now")
        except Exception as exc:
            print(f"  ⚠ {name}: smoke probe raised {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return False
        # Pipeline returns either [{label, score}] (default top_k=1) or
        # [[{label, score}, ...]] (top_k=None). Normalize.
        if not probe:
            print(f"  ⚠ {name}: smoke probe returned empty result", file=sys.stderr)
            return False
        first = probe[0]
        if isinstance(first, list):
            first = first[0] if first else None
        if not first or "score" not in first:
            print(f"  ⚠ {name}: smoke probe shape unexpected: {probe!r}", file=sys.stderr)
            return False
        top_score = float(first["score"])
        top_label = first.get("label", "?")
        if top_score < 0.5:
            print(
                f"  ⚠ {name}: smoke probe top-1 = {top_label!r} @ {top_score:.3f} "
                f"(below 0.5 on clear emotional input). Classifier may be "
                f"randomly initialized, or the model is multi-label sigmoid "
                f"and needs a lower threshold.",
                file=sys.stderr,
            )
            return False

        print(f"  ✓ {name} validated: labels={sorted(label_set)[:6]}{'...' if len(label_set) > 6 else ''} "
              f"smoke_probe={top_label!r}@{top_score:.2f}")
        return True

    def _try_load(key: str, model_id: str, mapping: dict[str, str]) -> None:
        print(f"Loading {key} ({model_id})...")
        kwargs = MODEL_LOAD_KWARGS.get(key, {})
        if kwargs:
            print(f"  load options: {kwargs}")
        t0 = time.time()
        try:
            clf = pipeline("text-classification", model=model_id, **kwargs)
            print(f"  loaded in {time.time()-t0:.1f}s")
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"  FAILED after {elapsed:.1f}s: {type(exc).__name__}", file=sys.stderr)
            # Surface the most common cause inline so the user doesn't
            # have to read a 50-line traceback to learn the fix.
            msg = str(exc)
            if "torch" in msg.lower() and ("2.6" in msg or "vulnerability" in msg.lower()):
                print(
                    "  hint: model ships pickle weights and transformers refuses\n"
                    "        to load them under torch < 2.6 (CVE-2025-32434).\n"
                    "        Fix: pip install -U \"torch>=2.6\"",
                    file=sys.stderr,
                )
            elif "404" in msg or "not found" in msg.lower():
                print("  hint: model id may be wrong or HF Hub is unreachable.",
                      file=sys.stderr)
            elif "trust_remote_code" in msg.lower():
                print(
                    f"  hint: model has custom code; ensure MODEL_LOAD_KWARGS[{key!r}]\n"
                    f"        includes 'trust_remote_code': True.",
                    file=sys.stderr,
                )
            else:
                # Print the brief error message but suppress the traceback —
                # the user can rerun with PYTHONFAULTHANDLER=1 if they want it.
                print(f"  details: {msg[:300]}", file=sys.stderr)
            load_failures.append(key)
            return

        # Strict validation: pipeline() returned, but is the model actually
        # working? This is the kashyaparun-lesson check.
        if _validate_loaded(key, clf, mapping):
            classifiers[key] = clf
        else:
            print(
                f"  {key} loaded but failed validation — excluded from the run.\n"
                f"  See the warnings above. To force-include despite warnings,\n"
                f"  comment out the validation gate in _try_load.",
                file=sys.stderr,
            )
            load_failures.append(key)

    if not args.skip_jhartmann:
        _try_load("jhartmann", JHARTMANN_MODEL, JHARTMANN_TO_OURS)

    if not classifiers:
        print(
            "\nERROR: no models could be loaded. Cannot proceed.\n"
            "Most common fix: pip install -U \"torch>=2.6\"",
            file=sys.stderr,
        )
        return 1

    if load_failures:
        print(
            f"\nProceeding with {len(classifiers)} of "
            f"{len(classifiers) + len(load_failures)} models. "
            f"Failed: {', '.join(load_failures)}\n"
        )

    print(f"Threshold: {args.threshold}, min_chunk_tokens: {args.min_chunk_tokens}")
    print()

    # Run each model on each baseline message
    rows: list[dict] = []
    for i, b in enumerate(baseline):
        if i and i % 50 == 0:
            print(f"  processed {i}/{len(baseline)}")

        # MiniLM result is already in baseline
        minilm_result = {
            "cat": b["sem_cat"],
            "conf": b["sem_conf"],
            "winning_chunk": b.get("winning_chunk"),
            "winning_native_label": None,  # N/A for MiniLM
            "n_chunks": b.get("n_chunks", 0),
        }

        row: dict[str, Any] = {
            "id": b["id"],
            "category": b["category"],
            "turn_idx": b["turn_idx"],
            "text": b["text"],
            "lex_coarse": b["lex_coarse"],
            "lex_specific": b["lex_specific"],
            "model_minilm": minilm_result,
            "bucket_minilm": b["bucket"],  # already computed
        }

        # j-hartmann
        if "jhartmann" in classifiers:
            jh = model_classify(b["text"], classifiers["jhartmann"],
                                JHARTMANN_TO_OURS, args.threshold,
                                args.min_chunk_tokens)
            row["model_jhartmann"] = jh
            row["bucket_jhartmann"] = categorize(b["lex_specific"], jh["cat"])
        else:
            row["model_jhartmann"] = {"cat": None, "conf": 0.0, "winning_chunk": None,
                                      "winning_native_label": None, "n_chunks": 0}
            row["bucket_jhartmann"] = "SKIPPED"

        rows.append(row)

    print(f"  done ({len(rows)} messages)")

    # Reports
    print_bucket_table(rows, {})
    print_win_overlap(rows)

    if not args.skip_jhartmann:
        print_win_details(rows, "jhartmann", "j-hartmann")

    print_killer_cases(rows)

    # Coverage summary
    print("\n=== Coverage summary ===\n")
    n = len(rows)
    n_lex = sum(1 for r in rows if r["lex_specific"] not in (None, "generic"))
    n_minilm = sum(1 for r in rows if r["bucket_minilm"] in ("WIN", "AGREEMENT"))
    n_jh = sum(1 for r in rows if r["bucket_jhartmann"] in ("WIN", "AGREEMENT"))

    print(f"  Lexicon specific tone:       {n_lex:>3}/{n} ({100*n_lex/n:.1f}%)")
    print(f"  + MiniLM-chunked:            {n_minilm:>3}/{n} ({100*n_minilm/n:.1f}%)")
    print(f"  + j-hartmann:                {n_jh:>3}/{n} ({100*n_jh/n:.1f}%)")

    # JSON output
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2))
        print(f"\nFull JSON written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
