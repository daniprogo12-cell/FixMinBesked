"""Keyword synonym replacement matching a reference text's style.

The approach:
1.  Extract a *style lexicon* from the reference text — the set of content-word
    lemmas that characterise its register.
2.  For each content word in the target whose lemma is NOT in the style lexicon,
    score how much it "sticks out" by masking it and checking the model's
    confidence that it belongs there.
3.  For the most outlying words, replace them with the fill-mask prediction that
    best fits the reference style (i.e. whose lemma IS in the style lexicon, or
    failing that, the highest-scoring contextually appropriate candidate).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .helpers import is_real_word, mask_at

CONTENT_POS = {"NOUN", "VERB", "ADJ", "ADV"}


@dataclass
class StyleLexicon:
    """Vocabulary profile of a reference text."""

    content_lemmas: set[str] = field(default_factory=set)
    content_words: set[str] = field(default_factory=set)
    word_freq: Counter = field(default_factory=Counter)


def extract_style_lexicon(text: str, nlp) -> StyleLexicon:
    """Build a style lexicon from *text*."""
    doc = nlp(text)
    lemmas: set[str] = set()
    words: set[str] = set()
    freq: Counter = Counter()
    for token in doc:
        if token.pos_ in CONTENT_POS and not token.is_stop:
            lemmas.add(token.lemma_.lower())
            words.add(token.text.lower())
            freq[token.lemma_.lower()] += 1
    return StyleLexicon(content_lemmas=lemmas, content_words=words, word_freq=freq)


def score_outliers(
    text: str,
    lexicon: StyleLexicon,
    fill_mask,
    tokenizer,
    nlp,
) -> list[dict]:
    """Return content words sorted by how much they stick out from *lexicon*.

    Only words whose lemma is absent from the reference lexicon are considered.
    Among those, the fill-mask model's confidence that the word belongs in
    context is used as a secondary ranking signal (low confidence → big outlier).
    """
    doc = nlp(text)
    words = [t.text for t in doc]
    outliers: list[dict] = []

    for token in doc:
        if token.pos_ not in CONTENT_POS or token.is_stop:
            continue
        lemma = token.lemma_.lower()
        if lemma in lexicon.content_lemmas:
            continue  # already on-style

        # Mask this word and check model confidence.
        masked = mask_at(words, token.i, tokenizer.mask_token)
        preds = fill_mask(masked)

        original_score = 0.0
        for p in preds:
            if p["token_str"].strip().lower() == token.text.lower():
                original_score = p["score"]
                break

        outliers.append(
            {
                "idx": token.i,
                "word": token.text,
                "lemma": lemma,
                "pos": token.pos_,
                "outlier_score": 1.0 - original_score,
                "model_score": original_score,
            }
        )

    outliers.sort(key=lambda x: -x["outlier_score"])
    return outliers


def replace_keywords(
    text: str,
    lexicon: StyleLexicon,
    fill_mask,
    tokenizer,
    nlp,
    *,
    max_replacements: int = 0,
    outlier_threshold: float = 0.7,
) -> tuple[str, list[dict]]:
    """Replace the most-outlying keywords with synonyms fitting *lexicon*.

    Returns (modified_text, list_of_replacements).

    *max_replacements* = 0 means no cap (all above threshold).
    *outlier_threshold* controls sensitivity (0.0 = replace everything,
    1.0 = replace nothing).
    """
    outliers = score_outliers(text, lexicon, fill_mask, tokenizer, nlp)
    outliers = [o for o in outliers if o["outlier_score"] > outlier_threshold]
    if max_replacements > 0:
        outliers = outliers[:max_replacements]

    doc = nlp(text)
    words = [t.text for t in doc]
    result = words.copy()
    replacements: list[dict] = []

    # Process by position (left→right) to keep context coherent.
    for outlier in sorted(outliers, key=lambda o: o["idx"]):
        idx = outlier["idx"]
        masked = mask_at(result, idx, tokenizer.mask_token)
        preds = fill_mask(masked)

        best_in_style = None
        best_any = None

        for p in preds:
            cand = p["token_str"].strip()
            if not is_real_word(cand) or cand.lower() == result[idx].lower():
                continue
            cand_doc = nlp(cand)
            cand_lemma = cand_doc[0].lemma_.lower() if cand_doc else cand.lower()
            in_style = cand_lemma in lexicon.content_lemmas

            entry = {
                "word": cand,
                "lemma": cand_lemma,
                "score": p["score"],
                "in_style": in_style,
            }

            if in_style and best_in_style is None:
                best_in_style = entry
            if best_any is None:
                best_any = entry
            if best_in_style:
                break  # good enough

        pick = best_in_style or best_any
        if pick:
            result[idx] = pick["word"]
            replacements.append(
                {
                    "idx": idx,
                    "original": outlier["word"],
                    "replacement": pick["word"],
                    "outlier_score": outlier["outlier_score"],
                    "in_style": pick["in_style"],
                    "model_score": pick["score"],
                }
            )

    return " ".join(result), replacements


# ── grading helpers ───────────────────────────────────────────────────────────


def grade_meaning_preservation(original: str, transformed: str, nlp) -> float:
    """Jaccard similarity of content-word lemma sets (0.0–1.0)."""

    def _lemmas(text):
        doc = nlp(text)
        return {
            t.lemma_.lower() for t in doc if t.pos_ in CONTENT_POS and not t.is_stop
        }

    a = _lemmas(original)
    b = _lemmas(transformed)
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def grade_style_match(transformed: str, lexicon: StyleLexicon, nlp) -> float:
    """Fraction of content-word lemmas in *transformed* that are in *lexicon*."""
    doc = nlp(transformed)
    content = [t for t in doc if t.pos_ in CONTENT_POS and not t.is_stop]
    if not content:
        return 1.0
    hits = sum(1 for t in content if t.lemma_.lower() in lexicon.content_lemmas)
    return hits / len(content)
