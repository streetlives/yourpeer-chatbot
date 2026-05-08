"""
Pattern 8 — Empirical verification probe for TONE_ROUTES draft.

Runs in ~30 seconds against a local install with sentence-transformers.
Tells you whether `all-MiniLM-L6-v2` clusters the proposed tone categories
usefully BEFORE you commit engineering to wiring up Pattern 8.

Usage (from the repo root, with the chatbot venv active):
    python tools/tone_routes_probe.py

What it tells you:
    1. Per-category within- vs between-similarity gap
       (>0.15 = good separation, 0.08-0.15 = marginal, <0.08 = weak)
    2. Top-5 most-confused category pairs
    3. Outlier utterances closer to a different category's centroid
       than to their own (these need rephrasing or category reassignment)

What it does NOT tell you:
    Whether the routes match REAL user messages. That's the 100-message
    labeling experiment in Pattern 8 § "Empirical verification before
    committing." This probe is the much-cheaper precondition: if the
    canonical utterances themselves don't separate, the model definitely
    won't separate noisier real-world variants.

If this probe shows OVERALL gap < 0.08:
    Pivot to Alternative A in Pattern 8 — load a separate emotion-
    finetuned model (e.g., j-hartmann/emotion-english-distilroberta-base).

If the probe passes but specific categories are weak:
    Rework the weak categories' utterances before the labeling experiment.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

# Adjust this path to wherever the draft lives in your tree.
DRAFT_PATH = Path(__file__).parent.parent / "tools" / "tone_routes_draft.py"


def load_routes(path: Path) -> dict[str, list[str]]:
    spec = importlib.util.spec_from_file_location("draft", str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.TONE_ROUTES


def main() -> int:
    if not DRAFT_PATH.exists():
        print(f"Draft not found at {DRAFT_PATH}", file=sys.stderr)
        return 1

    routes = load_routes(DRAFT_PATH)
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

    all_utts: list[str] = []
    all_labels: list[str] = []
    for cat, utts in routes.items():
        for u in utts:
            all_utts.append(u)
            all_labels.append(cat)

    emb = model.encode(all_utts, normalize_embeddings=True, show_progress_bar=False)
    sim = emb @ emb.T
    labels = np.array(all_labels)

    # 1. Within vs between per category
    print("Within- vs between-category cosine similarity")
    print(f"{'category':<13} {'within':>8} {'between':>8} {'gap':>7}  separation")
    print("-" * 60)
    overall_within, overall_between = [], []
    for cat in routes:
        cat_idx = np.where(labels == cat)[0]
        other_idx = np.where(labels != cat)[0]
        within_block = sim[np.ix_(cat_idx, cat_idx)]
        n = len(cat_idx)
        within_mask = ~np.eye(n, dtype=bool)
        within_mean = within_block[within_mask].mean()
        between_mean = sim[np.ix_(cat_idx, other_idx)].mean()
        gap = within_mean - between_mean
        sep = "GOOD" if gap > 0.15 else ("MARGINAL" if gap > 0.08 else "WEAK")
        print(f"{cat:<13} {within_mean:>8.3f} {between_mean:>8.3f} {gap:>+7.3f}  {sep}")
        overall_within.append(within_mean)
        overall_between.append(between_mean)
    print("-" * 60)
    overall_gap = np.mean(overall_within) - np.mean(overall_between)
    print(
        f"{'OVERALL':<13} {np.mean(overall_within):>8.3f} "
        f"{np.mean(overall_between):>8.3f} {overall_gap:>+7.3f}"
    )

    # 2. Most-confused category pairs
    print()
    print("Top-5 cross-category mean similarity (potential confusion):")
    cross = []
    cats = list(routes.keys())
    for i, a in enumerate(cats):
        for b in cats[i + 1:]:
            ai = np.where(labels == a)[0]
            bi = np.where(labels == b)[0]
            cross.append((a, b, sim[np.ix_(ai, bi)].mean()))
    cross.sort(key=lambda x: -x[2])
    for a, b, s in cross[:5]:
        print(f"  {a:<13} <-> {b:<13} {s:>6.3f}")

    # 3. Outlier utterances
    print()
    print("Outlier utterances (closer to another category's centroid than own):")
    cat_means: dict[str, np.ndarray] = {
        c: emb[labels == c].mean(axis=0) for c in routes
    }
    for c in cat_means:
        cat_means[c] = cat_means[c] / np.linalg.norm(cat_means[c])

    outliers = []
    for i, u in enumerate(all_utts):
        own = labels[i]
        sims_to_centroids = {c: float(emb[i] @ cm) for c, cm in cat_means.items()}
        best = max(sims_to_centroids, key=sims_to_centroids.get)
        if best != own:
            outliers.append(
                (own, best, sims_to_centroids[own], sims_to_centroids[best], u)
            )
    outliers.sort(key=lambda x: x[3] - x[2], reverse=True)
    for own, best, own_s, best_s, u in outliers[:10]:
        print(
            f"  [{own} -> predicted {best}, gap +{best_s - own_s:.3f}] {u!r}"
        )
    if not outliers:
        print("  (none)")

    # Verdict
    print()
    if overall_gap > 0.15:
        print(f"VERDICT: GOOD separation ({overall_gap:+.3f}). Proceed to labeling experiment.")
        return 0
    if overall_gap > 0.08:
        print(f"VERDICT: MARGINAL ({overall_gap:+.3f}). Rework weak categories before labeling.")
        return 0
    print(f"VERDICT: WEAK ({overall_gap:+.3f}). Pivot to Alternative A (emotion-finetuned model).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
