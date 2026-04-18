"""Spelling and grammar correction via masked-language-model probing."""

from __future__ import annotations

from .grammar_ast import GrammarNode, build_sentence_ast
from .helpers import edit_distance, is_real_word, mask_at

INFLECTABLE_POS = {"ADJ", "NOUN", "VERB", "DET", "PRON", "AUX"}
GRAMMAR_SCORE_FLOOR = 0.01


def _align_spacy_to_word(spacy_token, words: list[str]) -> int | None:
    target = spacy_token.text.lower()
    idx = spacy_token.i
    if idx < len(words) and words[idx].lower() == target:
        return idx
    for i, w in enumerate(words):
        if w.lower() == target:
            return i
    return None


def correct_and_parse(
    sentence: str,
    fill_mask,
    tokenizer,
    nlp,
) -> tuple[str, list[dict], GrammarNode]:
    """Return (corrected_text, corrections, ast).

    Pass 1 — spelling (OOV words).
    Pass 2 — grammar (inflection errors on in-vocab words).
    """
    mask = tokenizer.mask_token
    words = sentence.split()
    result = words.copy()
    corrections: list[dict] = []

    # pass 1: spelling
    for i, word in enumerate(words):
        toks = tokenizer.tokenize(word)
        if len(toks) == 1 and not toks[0].startswith("##"):
            continue
        preds = fill_mask(mask_at(words, i, mask))
        candidates = [
            (
                edit_distance(word.lower(), p["token_str"].strip().lower()),
                p["score"],
                p["token_str"].strip(),
            )
            for p in preds
            if is_real_word(p["token_str"])
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda x: (x[0], -x[1]))
        best_dist, best_score, best_word = candidates[0]
        result[i] = best_word
        corrections.append(
            {
                "pos": i,
                "original": word,
                "suggestion": best_word,
                "score": best_score,
                "edit_dist": best_dist,
                "kind": "spelling",
            }
        )

    # pass 2: grammar
    doc = nlp(" ".join(result))
    for st in doc:
        if st.pos_ not in INFLECTABLE_POS:
            continue
        wi = _align_spacy_to_word(st, result)
        if wi is None:
            continue
        word = result[wi]
        toks = tokenizer.tokenize(word)
        if not (len(toks) == 1 and not toks[0].startswith("##")):
            continue
        preds = fill_mask(mask_at(result, wi, mask))
        top = preds[0]
        top_word = top["token_str"].strip()
        if not is_real_word(top_word) or top_word.lower() == word.lower():
            continue
        if top["score"] < GRAMMAR_SCORE_FLOOR:
            continue
        pred_doc = nlp(top_word)
        if not pred_doc:
            continue
        if (
            pred_doc[0].lemma_.lower() != st.lemma_.lower()
            and edit_distance(pred_doc[0].lemma_.lower(), st.lemma_.lower()) > 1
        ):
            continue
        result[wi] = top_word
        corrections.append(
            {
                "pos": wi,
                "original": word,
                "suggestion": top_word,
                "score": top["score"],
                "edit_dist": edit_distance(word.lower(), top_word.lower()),
                "kind": "grammar",
            }
        )

    corrected_text = " ".join(result)
    final_doc = nlp(corrected_text)
    ast = build_sentence_ast(final_doc, corrections)
    return corrected_text, corrections, ast
