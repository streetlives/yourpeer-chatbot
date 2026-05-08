"""
Pattern 8 — Empirical comparison of TONE_ROUTES vs lexicon on real eval inputs.

Runs every user message in the eval suite through both:
  (1) the existing lexicon classifier (`_classify_tone` +
      `_pick_emotional_response`), and
  (2) the proposed semantic layer (TONE_ROUTES + MiniLM embeddings,
      with the per-route distrust-threshold override).

Categorizes each comparison and reports:
  - WINS: lexicon misses tone, semantic catches it (the value-add)
  - CONFLICTS: both fire above threshold but disagree on category
  - LEX-ONLY: lexicon fires, semantic doesn't (semantic too conservative)
  - DOUBLE-MISSES: neither fires (out of tone scope, or genuine miss)
  - AGREEMENTS: both classify same way (sanity check)

This is the precondition for the full 100-message labeling experiment.
It tells you whether the layer adds value on inputs the team already
cares about, before investing in human labeling.

The --chunk flag splits messages into clauses (sentence-bounded plus
commas/semicolons plus coordinating conjunctions) and takes the MAX
similarity across chunks. This addresses the dilution problem: a
message like "I'm ashamed to be asking but I need food in the Bronx"
embeds the whole thing into a vague distress signal at low confidence,
but chunking surfaces "I'm ashamed to be asking" as its own clause
which embeds near shame canonicals at high confidence.

Usage (from repo root with chatbot venv active):
    python tools/tone_routes_eval_compare.py
    python tools/tone_routes_eval_compare.py --chunk
    python tools/tone_routes_eval_compare.py --chunk --all-turns
    python tools/tone_routes_eval_compare.py --threshold 0.60
    python tools/tone_routes_eval_compare.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np

# -- Paths ---------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
DRAFT_PATH = REPO_ROOT / "tools" / "tone_routes_draft.py"
EVAL_PATH = REPO_ROOT / "tests" / "eval" / "eval_llm_judge.py"
sys.path.insert(0, str(REPO_ROOT / "backend"))

# -- Threshold config ----------------------------------------------------------

DEFAULT_THRESHOLD = 0.65
PER_ROUTE_THRESHOLDS = {
    # Distrust is irreducibly heterogeneous in expression (questions,
    # statements, past-experience). Lower threshold + lexicon hybrid.
    "distrust": 0.55,
}

# -- Chunking ------------------------------------------------------------------

# Splits on sentence terminators (.!?), comma/semicolon, and
# coordinating conjunctions at word boundaries. Conjunctions: but, and,
# or, because, so, yet. The case-insensitive match handles "But"/"And"
# at sentence starts when split on the period before them.
_CHUNK_SPLIT_PATTERN = re.compile(
    r'[.!?,;]+\s*|\s+(?:but|and|or|because|so|yet)\s+',
    flags=re.IGNORECASE,
)


def split_into_chunks(text: str, min_tokens: int = 3) -> list[str]:
    """Split a message into clause-level chunks.

    Returns chunks that have >= min_tokens whitespace-delimited tokens
    after stripping. Empty list when the message is too short to split
    meaningfully — caller should treat as "skip semantic on this message"
    rather than fall back to whole-message embedding (short messages
    pick up noise classifications, not signal).
    """
    raw = _CHUNK_SPLIT_PATTERN.split(text)
    return [
        c.strip()
        for c in raw
        if c and c.strip() and len(c.strip().split()) >= min_tokens
    ]

# -- Scenario extraction -------------------------------------------------------

def extract_scenarios(path: Path) -> list[dict]:
    """AST-parse the eval file, extract the SCENARIOS list.

    We don't import the module — it has heavy deps and side effects. We
    only need each scenario's id, category, and user_turns list, which
    are all literal strings/lists in the source.
    """
    src = path.read_text()
    tree = ast.parse(src)
    scenarios = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (node.targets and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SCENARIOS"):
            continue
        if not isinstance(node.value, ast.List):
            continue
        for elt in node.value.elts:
            if not isinstance(elt, ast.Dict):
                continue
            d = {}
            for k, v in zip(elt.keys, elt.values):
                if not isinstance(k, ast.Constant):
                    continue
                key = k.value
                if isinstance(v, ast.Constant):
                    d[key] = v.value
                elif isinstance(v, ast.List):
                    items = []
                    for c in v.elts:
                        if isinstance(c, ast.Constant):
                            items.append(c.value)
                    d[key] = items
            if "id" in d and "user_turns" in d and d["user_turns"]:
                scenarios.append({
                    "id": d["id"],
                    "category": d.get("category", ""),
                    "user_turns": d["user_turns"],
                })
    return scenarios


# -- Lexicon classification ----------------------------------------------------

def lexicon_classify(text: str) -> tuple[str | None, str | None]:
    """Return (coarse_tone, specific_emo_key).

    coarse_tone is None | 'emotional' | 'frustrated' | 'urgent' | 'confused'
    (or whatever `_classify_tone` returns).

    specific_emo_key is None unless coarse_tone == 'emotional', in
    which case it's the matching `_EMOTIONAL_RESPONSES` key, or
    'generic' if the if-chain fell through.
    """
    from app.services.classifier import _classify_tone
    from app.services.responses import (
        _pick_emotional_response,
        _EMOTIONAL_RESPONSE,
        _EMOTIONAL_RESPONSES,
    )
    copy_to_key = {v: k for k, v in _EMOTIONAL_RESPONSES.items()}

    coarse = _classify_tone(text)
    if coarse == "emotional":
        result = _pick_emotional_response(text)
        if result == _EMOTIONAL_RESPONSE:
            return coarse, "generic"
        return coarse, copy_to_key.get(result, "?")
    return coarse, None


# -- Semantic classification ---------------------------------------------------

def load_routes(path: Path) -> dict[str, list[str]]:
    spec = importlib.util.spec_from_file_location("draft", str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.TONE_ROUTES


def semantic_classify(
    text: str,
    route_embeddings: dict[str, np.ndarray],
    model,
    default_threshold: float,
    per_route_thresholds: dict[str, float],
    chunk: bool = False,
    min_chunk_tokens: int = 3,
) -> tuple[str | None, float, list[tuple[str, float]], str | None, list[str]]:
    """Return (best_category_above_threshold, top1_similarity, top3_pairs,
    winning_chunk, all_chunks).

    Matches the production router's logic for the per-canonical max:
    each route's score is the MAX cosine similarity over all utterances
    in that route, not the centroid similarity. With chunk=True, an
    additional outer max is taken over message clauses, and the chunk
    that produced the winning category is returned for diagnostics.

    When chunk=True and the message is too short to split (no chunks
    survive the min_tokens filter), returns category=None — short
    messages picked up noise classifications in the v1 run, so we
    treat them as "skip semantic" rather than falling back to the
    full message.

    winning_chunk is the chunk text that produced the top-1 category's
    score (whether or not that score crossed the threshold). When
    chunk=False, winning_chunk is the full message.
    """
    if chunk:
        chunks = split_into_chunks(text, min_chunk_tokens)
        if not chunks:
            # Short message — don't classify
            return None, 0.0, [], None, []
        texts = chunks
    else:
        texts = [text]
        chunks = []

    embs = model.encode(texts, normalize_embeddings=True)  # (n_texts, dim)

    sims: dict[str, float] = {}
    best_chunk_per_cat: dict[str, str] = {}
    for cat, route_emb in route_embeddings.items():
        # Cosine similarity matrix: (n_texts, n_canonicals)
        sim_matrix = embs @ route_emb.T
        # Max over the whole matrix (best chunk × best canonical)
        flat_max_idx = int(np.argmax(sim_matrix))
        chunk_idx = flat_max_idx // sim_matrix.shape[1]
        sims[cat] = float(sim_matrix.flat[flat_max_idx])
        best_chunk_per_cat[cat] = texts[chunk_idx]

    sorted_cats = sorted(sims.items(), key=lambda x: -x[1])
    top3 = sorted_cats[:3]
    best_cat, best_sim = sorted_cats[0]
    winning_chunk = best_chunk_per_cat[best_cat]
    threshold = per_route_thresholds.get(best_cat, default_threshold)
    if best_sim >= threshold:
        return best_cat, best_sim, top3, winning_chunk, chunks
    return None, best_sim, top3, winning_chunk, chunks


# -- Comparison and categorization ---------------------------------------------

def categorize(lex_coarse, lex_specific, sem_cat) -> str:
    """Bucket a single comparison into one of five outcomes."""
    lex_classified = lex_specific is not None and lex_specific != "generic"
    sem_classified = sem_cat is not None

    if not lex_classified and sem_classified:
        return "WIN"
    if lex_classified and sem_classified and lex_specific != sem_cat:
        return "CONFLICT"
    if lex_classified and sem_classified and lex_specific == sem_cat:
        return "AGREEMENT"
    if lex_classified and not sem_classified:
        return "LEX_ONLY"
    return "DOUBLE_MISS"


# -- Reporting -----------------------------------------------------------------

def fmt_top3(top3):
    return ", ".join(f"{cat}@{sim:.2f}" for cat, sim in top3)


def print_section(title: str, items: list[dict], max_n: int | None = None) -> None:
    print(f"\n=== {title} (n={len(items)}) ===\n")
    for r in (items[:max_n] if max_n else items):
        sem_str = (
            f"{r['sem_cat']}@{r['sem_conf']:.2f}"
            if r["sem_cat"]
            else f"none (top: {fmt_top3(r['top3'])})"
        )
        msg_short = (r["text"][:120] + "…") if len(r["text"]) > 120 else r["text"]
        print(f"  [{r['id']} cat={r['category']} turn={r['turn_idx']}]")
        print(f"    msg: {msg_short!r}")
        # Surface winning chunk if it differs from full message
        # (only meaningful when --chunk was used and chunking occurred)
        wc = r.get("winning_chunk")
        if wc and wc != r["text"] and r.get("n_chunks", 0) > 1:
            print(f"    chunk that fired: {wc!r}")
        print(f"    lex={r['lex_coarse']}/{r['lex_specific']}  |  sem={sem_str}")
        print()


# -- Main ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-turns", action="store_true",
                        help="Include every user turn (default: first only)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"Default semantic threshold (default {DEFAULT_THRESHOLD})")
    parser.add_argument("--chunk", action="store_true",
                        help="Split messages into clauses and take max similarity "
                             "across chunks (addresses signal dilution from "
                             "multi-content messages)")
    parser.add_argument("--min-chunk-tokens", type=int, default=3,
                        help="Minimum tokens per chunk (default 3); messages "
                             "with no chunks meeting this minimum get skipped "
                             "by semantic classification")
    parser.add_argument("--draft", type=Path, default=DRAFT_PATH,
                        help="Path to tone_routes_draft.py")
    parser.add_argument("--eval", type=Path, default=EVAL_PATH,
                        help="Path to eval_llm_judge.py")
    parser.add_argument("--json", type=Path, default=None,
                        help="Optional path to write full JSON output")
    parser.add_argument("--show-agreements", type=int, default=5,
                        help="Show N agreement examples in output (default 5)")
    parser.add_argument("--show-double-misses", type=int, default=10,
                        help="Show N double-miss examples (default 10)")
    args = parser.parse_args()

    if not args.draft.exists():
        print(f"Draft not found: {args.draft}", file=sys.stderr)
        return 1
    if not args.eval.exists():
        print(f"Eval file not found: {args.eval}", file=sys.stderr)
        return 1

    # Load routes + model
    routes = load_routes(args.draft)
    print(f"Loaded TONE_ROUTES: {len(routes)} categories, "
          f"{sum(len(v) for v in routes.values())} utterances")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    route_embeddings = {
        cat: model.encode(utts, normalize_embeddings=True)
        for cat, utts in routes.items()
    }

    # Extract scenarios
    scenarios = extract_scenarios(args.eval)
    print(f"Extracted {len(scenarios)} scenarios from eval suite")

    # Build the message list
    messages: list[dict] = []
    for s in scenarios:
        turns = s["user_turns"] if args.all_turns else [s["user_turns"][0]]
        for i, t in enumerate(turns):
            messages.append({
                "id": s["id"],
                "category": s["category"],
                "turn_idx": i,
                "text": t,
            })
    print(f"Comparing {len(messages)} messages "
          f"({'all turns' if args.all_turns else 'first turn only'}, "
          f"{'chunked' if args.chunk else 'full-message'} embedding"
          f"{f', min_tokens={args.min_chunk_tokens}' if args.chunk else ''})")

    # Run both classifiers on each
    results = []
    for m in messages:
        try:
            lex_coarse, lex_specific = lexicon_classify(m["text"])
        except Exception as e:
            lex_coarse, lex_specific = "ERROR", str(e)[:50]
        sem_cat, sem_conf, top3, winning_chunk, chunks = semantic_classify(
            m["text"], route_embeddings, model,
            args.threshold, PER_ROUTE_THRESHOLDS,
            chunk=args.chunk, min_chunk_tokens=args.min_chunk_tokens,
        )
        bucket = categorize(lex_coarse, lex_specific, sem_cat)
        results.append({
            **m,
            "lex_coarse": lex_coarse,
            "lex_specific": lex_specific,
            "sem_cat": sem_cat,
            "sem_conf": round(sem_conf, 3),
            "top3": [(c, round(s, 3)) for c, s in top3],
            "winning_chunk": winning_chunk,
            "n_chunks": len(chunks),
            "bucket": bucket,
        })

    # Bucket
    buckets = {b: [] for b in ["WIN", "CONFLICT", "LEX_ONLY", "AGREEMENT", "DOUBLE_MISS"]}
    for r in results:
        buckets[r["bucket"]].append(r)

    # Print sections
    print_section("WINS — lexicon misses tone, semantic catches it",
                  buckets["WIN"])
    print_section("CONFLICTS — both classify but disagree on category",
                  buckets["CONFLICT"])
    print_section("LEX-ONLY — lexicon fires, semantic doesn't",
                  buckets["LEX_ONLY"])
    print_section("AGREEMENTS (sample) — both classify same way",
                  buckets["AGREEMENT"], max_n=args.show_agreements)
    print_section("DOUBLE-MISSES (sample) — neither fires",
                  buckets["DOUBLE_MISS"], max_n=args.show_double_misses)

    # Summary
    print("\n=== SUMMARY ===")
    print(f"Total messages: {len(results)}")
    n = max(len(results), 1)
    for b in ["WIN", "CONFLICT", "LEX_ONLY", "AGREEMENT", "DOUBLE_MISS"]:
        c = len(buckets[b])
        print(f"  {b:<13} {c:>4}  ({100*c/n:>5.1f}%)")

    # Wins by category — where does semantic add the most value?
    if buckets["WIN"]:
        print("\nWins by semantic category:")
        from collections import Counter
        win_cats = Counter(r["sem_cat"] for r in buckets["WIN"])
        for cat, cnt in win_cats.most_common():
            print(f"  {cat:<14} {cnt}")

    # Conflicts that need adjudication
    if buckets["CONFLICT"]:
        print("\nConflict pairs (lex → sem):")
        from collections import Counter
        pairs = Counter((r["lex_specific"], r["sem_cat"]) for r in buckets["CONFLICT"])
        for (lex, sem), cnt in pairs.most_common():
            print(f"  {lex:<14} → {sem:<14} ({cnt})")

    # JSON output for further analysis
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
        print(f"\nFull JSON written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
