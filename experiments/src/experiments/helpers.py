"""Shared utility functions."""

from __future__ import annotations


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two strings."""
    if len(a) < len(b):
        return edit_distance(b, a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


def is_real_word(tok: str) -> bool:
    """Filter out subword fragments and pure punctuation."""
    tok = tok.strip()
    return bool(tok) and not tok.startswith("##") and any(c.isalpha() for c in tok)


def mask_at(words: list[str], idx: int, mask_tok: str) -> str:
    """Return the sentence with *words[idx]* replaced by [MASK]."""
    tmp = words.copy()
    tmp[idx] = mask_tok
    return " ".join(tmp)


def spacy_morph_to_dict(token) -> dict[str, str]:
    """Convert a spaCy token's morph to a plain dict."""
    d: dict[str, str] = {}
    for feat in token.morph:
        key, _, val = feat.partition("=")
        if val:
            d[key] = val
    return d
