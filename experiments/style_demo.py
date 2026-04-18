#!/usr/bin/env python
"""
Style-transfer demo — transfers vocabulary style between 5 Danish sample texts
and grades each transfer on meaning preservation and style match.

Usage:
    uv run python style_demo.py
"""

from __future__ import annotations

import time
from pathlib import Path

from experiments import (
    extract_style_lexicon,
    grade_meaning_preservation,
    grade_style_match,
    load_models,
    replace_keywords,
)

# ── load models ───────────────────────────────────────────────────────────────
fill_mask, tokenizer, nlp, device = load_models()

# ── load sample texts ─────────────────────────────────────────────────────────
SAMPLES_DIR = Path(__file__).parent / "samples"
STYLE_NAMES = ["casual", "corporate", "medical", "fairytale", "dim"]

styles: dict[str, str] = {}
for name in STYLE_NAMES:
    path = SAMPLES_DIR / f"{name}.txt"
    styles[name] = path.read_text().strip()
    wc = len(styles[name].split())
    print(f"  Loaded {name:12s}  ({wc:>3d} words)  {path}")

# ── pre-extract style lexicons ────────────────────────────────────────────────
lexicons = {name: extract_style_lexicon(text, nlp) for name, text in styles.items()}
print()
for name, lex in lexicons.items():
    print(f"  {name:12s}  lexicon: {len(lex.content_lemmas):>3d} content lemmas")

# ── transfer pairs ────────────────────────────────────────────────────────────
PAIRS = [
    ("casual", "corporate"),
    ("casual", "medical"),
    ("corporate", "casual"),
    ("corporate", "fairytale"),
    ("medical", "casual"),
    ("medical", "corporate"),
    ("fairytale", "casual"),
    ("fairytale", "corporate"),
    ("dim", "casual"),
    ("dim", "corporate"),
]

SEP = "=" * 78

print(f"\n{SEP}")
print("  Style transfers  (synonym replacement)")
print(SEP)

results: list[dict] = []

for i, (src_name, tgt_name) in enumerate(PAIRS, 1):
    src_text = styles[src_name]
    tgt_lexicon = lexicons[tgt_name]

    print(f"\n{'─' * 78}")
    print(f"  [{i:>2d}/10]  {src_name}  →  {tgt_name}")
    print(f"{'─' * 78}")

    # Use only the first ~100 words to keep runtime reasonable.
    words = src_text.split()
    if len(words) > 100:
        excerpt = " ".join(words[:100])
    else:
        excerpt = src_text

    print(f"\n  Source excerpt ({len(excerpt.split())} words):")
    print(f"    {excerpt[:200]}...")

    t0 = time.perf_counter()
    styled, replacements = replace_keywords(
        excerpt,
        tgt_lexicon,
        fill_mask,
        tokenizer,
        nlp,
        outlier_threshold=0.6,
    )
    elapsed = time.perf_counter() - t0

    print(f"\n  Styled ({len(replacements)} replacements, {elapsed:.1f}s):")
    print(f"    {styled[:200]}...")

    if replacements:
        print(f"\n  Replacements:")
        for r in replacements[:15]:
            flag = "*" if r["in_style"] else " "
            print(
                f"   {flag} '{r['original']}' -> '{r['replacement']}'  "
                f"(outlier={r['outlier_score']:.2f}, score={r['model_score']:.4f})"
            )
        if len(replacements) > 15:
            print(f"    ... and {len(replacements) - 15} more")

    meaning = grade_meaning_preservation(excerpt, styled, nlp)
    style_m = grade_style_match(styled, tgt_lexicon, nlp)

    print(f"\n  Grades:")
    print(f"    Meaning preservation:  {meaning:.0%}")
    print(f"    Style match:           {style_m:.0%}")

    results.append(
        {
            "from": src_name,
            "to": tgt_name,
            "n_replacements": len(replacements),
            "meaning": meaning,
            "style": style_m,
            "time": elapsed,
            "styled_text": styled,
            "source_excerpt": excerpt,
        }
    )

# ── write results to files ────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "results"
OUTPUT_DIR.mkdir(exist_ok=True)

for r in results:
    fname = f"{r['from']}_to_{r['to']}.txt"
    path = OUTPUT_DIR / fname
    path.write_text(
        f"Source style:  {r['from']}\n"
        f"Target style:  {r['to']}\n"
        f"Replacements:  {r['n_replacements']}\n"
        f"Meaning:       {r['meaning']:.0%}\n"
        f"Style match:   {r['style']:.0%}\n"
        f"\n--- SOURCE EXCERPT ---\n\n"
        f"{r['source_excerpt']}\n"
        f"\n--- STYLED OUTPUT ---\n\n"
        f"{r['styled_text']}\n"
    )
    print(f"  Wrote {path}")

print()

# ── summary table ─────────────────────────────────────────────────────────────
print(f"\n{SEP}")
print("  Summary")
print(SEP)
print(
    f"  {'From':<12s} {'To':<12s} {'Repl':>5s} {'Meaning':>8s} {'Style':>8s} {'Time':>6s}"
)
print(f"  {'─' * 12} {'─' * 12} {'─' * 5} {'─' * 8} {'─' * 8} {'─' * 6}")
for r in results:
    print(
        f"  {r['from']:<12s} {r['to']:<12s} {r['n_replacements']:>5d} "
        f"{r['meaning']:>7.0%} {r['style']:>7.0%} {r['time']:>5.1f}s"
    )

avg_m = sum(r["meaning"] for r in results) / len(results)
avg_s = sum(r["style"] for r in results) / len(results)
print(f"  {'─' * 12} {'─' * 12} {'─' * 5} {'─' * 8} {'─' * 8} {'─' * 6}")
print(f"  {'AVERAGE':<25s} {'':>5s} {avg_m:>7.0%} {avg_s:>7.0%}")
