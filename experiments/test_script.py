"""
Danish text corrector & grammatical AST builder.

Pipeline
--------
1.  Spelling correction   – OOV words are masked and the closest-in-edit-
    distance prediction from the MLM is used as a replacement.
2.  Grammar correction    – In-vocabulary words in inflectable POS classes
    (ADJ, NOUN, VERB, DET, PRON) are masked; if the model's top prediction
    shares the same lemma but differs in surface form the word is likely a
    wrong inflection and gets corrected.
3.  AST construction      – The (corrected) sentence is parsed with spaCy's
    dependency parser and turned into a tree of GrammarNode dataclasses
    encoding POS, dependency relation, morphological features, and any
    corrections that were applied.
"""

from __future__ import annotations

import ctypes
import json
import time
from dataclasses import dataclass, field

import spacy
import torch
from transformers import AutoTokenizer, pipeline

# ── configuration ─────────────────────────────────────────────────────────────
MODEL = "KennethTM/bert-base-uncased-danish"
SPACY_MODEL = "da_core_news_sm"
TOP_K = 50
# POS tags whose surface form carries inflectional information.
INFLECTABLE_POS = {"ADJ", "NOUN", "VERB", "DET", "PRON", "AUX"}
# Minimum ratio  score(prediction) / score(2nd-best)  to accept a grammar fix.
GRAMMAR_SCORE_FLOOR = 0.01

# ── GPU diagnostics ───────────────────────────────────────────────────────────
print(f"PyTorch version: {torch.__version__}")
print(
    "CUDA built with: "
    f"cu{''.join(torch.version.cuda.split('.')) if torch.version.cuda else 'N/A'}"
)
print(f"CUDA available:  {torch.cuda.is_available()}")

if not torch.cuda.is_available():
    try:
        libcuda = ctypes.CDLL("libcuda.so")
        rc = libcuda.cuInit(0)
        cuda_errors = {
            0: "SUCCESS",
            100: "NO_DEVICE",
            999: "UNKNOWN (driver bad state — reboot or run: sudo nvidia-smi -r)",
        }
        print(f"cuInit status:   {cuda_errors.get(rc, rc)}")
    except OSError:
        print("cuInit status:   libcuda.so not found")
    print("WARNING: Falling back to CPU")
    device = -1
else:
    print(f"CUDA device:     {torch.cuda.get_device_name(0)}")
    print(
        f"VRAM:            "
        f"{torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GiB"
    )
    device = 0

# ── model loading (cold startup) ─────────────────────────────────────────────
t0 = time.perf_counter()
fill_mask = pipeline("fill-mask", model=MODEL, device=device, top_k=TOP_K)
tokenizer = AutoTokenizer.from_pretrained(MODEL)
nlp = spacy.load(SPACY_MODEL)
load_time = time.perf_counter() - t0
print(f"\nModel load time (cold start): {load_time:.3f}s")

if device >= 0:
    alloc = torch.cuda.memory_allocated(device) / 1024**2
    print(f"GPU memory after load:        {alloc:.0f} MiB")

# warm-up
fill_mask(f"Der var engang en {tokenizer.mask_token}")
print("Warm-up: done")


# ── AST definition ────────────────────────────────────────────────────────────
@dataclass
class GrammarNode:
    """One node in the grammatical AST (= one token)."""

    text: str
    lemma: str
    pos: str  # Universal POS tag
    dep: str  # Dependency relation to head
    morph: dict[str, str] = field(default_factory=dict)
    corrections: list[dict] = field(default_factory=list)
    children: list[GrammarNode] = field(default_factory=list)

    # ── serialisation helpers ─────────────────────────────────────────────
    def to_dict(self) -> dict:
        d: dict = {"text": self.text, "pos": self.pos, "dep": self.dep}
        if self.lemma != self.text:
            d["lemma"] = self.lemma
        if self.morph:
            d["morph"] = self.morph
        if self.corrections:
            d["corrections"] = self.corrections
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d

    def pretty(self, indent: int = 0) -> str:
        """Return an indented tree string."""
        tag = f"{self.dep}:{self.pos}"
        corr = ""
        if self.corrections:
            fixes = ", ".join(
                f"{c['original']}->{c['suggestion']}({c['kind']})"
                for c in self.corrections
            )
            corr = f"  << {fixes}"
        line = f'{"  " * indent}{tag} "{self.text}"'
        if self.morph:
            line += f"  {self.morph}"
        line += corr
        parts = [line]
        for child in self.children:
            parts.append(child.pretty(indent + 1))
        return "\n".join(parts)


def _build_ast(
    token,  # spacy Token
    corrections_by_idx: dict[int, list[dict]],
) -> GrammarNode:
    """Recursively build a GrammarNode tree from a spaCy Token."""
    morph = {}
    for feat in token.morph:
        key, _, val = feat.partition("=")
        if val:
            morph[key] = val

    node = GrammarNode(
        text=token.text,
        lemma=token.lemma_,
        pos=token.pos_,
        dep=token.dep_,
        morph=morph,
        corrections=corrections_by_idx.get(token.i, []),
    )
    for child in sorted(token.children, key=lambda t: t.i):
        node.children.append(_build_ast(child, corrections_by_idx))
    return node


def build_sentence_ast(
    doc,  # spacy Doc
    corrections: list[dict],
) -> GrammarNode:
    """Build the full AST for a parsed sentence."""
    by_idx: dict[int, list[dict]] = {}
    for c in corrections:
        by_idx.setdefault(c["pos"], []).append(c)

    roots = [t for t in doc if t.dep_ == "ROOT"]
    if len(roots) == 1:
        return _build_ast(roots[0], by_idx)

    # Multiple roots (rare) – wrap in a synthetic S node
    s = GrammarNode(text="", lemma="", pos="S", dep="ROOT")
    for root in roots:
        s.children.append(_build_ast(root, by_idx))
    return s


# ── helpers ───────────────────────────────────────────────────────────────────
def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        return _edit_distance(b, a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


def _is_real_word(tok: str) -> bool:
    """Filter out subword fragments and pure punctuation."""
    tok = tok.strip()
    return bool(tok) and not tok.startswith("##") and any(c.isalpha() for c in tok)


def _mask_at(words: list[str], idx: int, mask_tok: str) -> str:
    """Return the sentence with *words[idx]* replaced by [MASK]."""
    tmp = words.copy()
    tmp[idx] = mask_tok
    return " ".join(tmp)


# ── correction pipeline ──────────────────────────────────────────────────────
def correct_and_parse(
    sentence: str,
    fill_mask,
    tokenizer,
    nlp,
) -> tuple[str, list[dict], GrammarNode]:
    """
    Return (corrected_text, all_corrections, grammar_ast).

    Pass 1 – spelling:  mask OOV words, pick closest-edit-distance prediction.
    Pass 2 – grammar:   mask inflectable in-vocab words, accept the model's
             prediction when it shares the same lemma but differs in form.
    Finally parse the corrected text with spaCy and build the AST.
    """
    mask = tokenizer.mask_token
    words = sentence.split()
    result = words.copy()
    corrections: list[dict] = []

    # ── pass 1: spelling (OOV words) ──────────────────────────────────────
    for i, word in enumerate(words):
        toks = tokenizer.tokenize(word)
        is_known = len(toks) == 1 and not toks[0].startswith("##")
        if is_known:
            continue

        preds = fill_mask(_mask_at(words, i, mask))
        candidates = [
            (
                _edit_distance(word.lower(), p["token_str"].strip().lower()),
                p["score"],
                p["token_str"].strip(),
            )
            for p in preds
            if _is_real_word(p["token_str"])
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

    # ── pass 2: grammar (inflection errors on in-vocab words) ─────────────
    # Re-parse with spaCy to get POS + lemma for the (spelling-corrected) text.
    doc = nlp(" ".join(result))
    # Map spaCy token indices back to our word list indices.
    # (spaCy may tokenise differently, so we align by character offset.)
    spacy_tokens = list(doc)

    for st in spacy_tokens:
        if st.pos_ not in INFLECTABLE_POS:
            continue
        # Find matching word-index in our list
        wi = _align_spacy_to_word(st, result)
        if wi is None:
            continue
        word = result[wi]
        toks = tokenizer.tokenize(word)
        is_known = len(toks) == 1 and not toks[0].startswith("##")
        if not is_known:
            continue  # already handled in spelling pass

        preds = fill_mask(_mask_at(result, wi, mask))
        top = preds[0]
        top_word = top["token_str"].strip()
        if not _is_real_word(top_word):
            continue
        if top_word.lower() == word.lower():
            continue  # model agrees with the current form
        if top["score"] < GRAMMAR_SCORE_FLOOR:
            continue

        # Accept only if the prediction shares the lemma (= different
        # inflection of the same word, not a totally different word).
        pred_doc = nlp(top_word)
        if not pred_doc:
            continue
        pred_lemma = pred_doc[0].lemma_.lower()
        orig_lemma = st.lemma_.lower()
        if pred_lemma != orig_lemma and _edit_distance(pred_lemma, orig_lemma) > 1:
            continue  # different word entirely – skip

        result[wi] = top_word
        corrections.append(
            {
                "pos": wi,
                "original": word,
                "suggestion": top_word,
                "score": top["score"],
                "edit_dist": _edit_distance(word.lower(), top_word.lower()),
                "kind": "grammar",
            }
        )

    # ── build AST from corrected text ─────────────────────────────────────
    corrected_text = " ".join(result)
    final_doc = nlp(corrected_text)
    ast = build_sentence_ast(final_doc, corrections)

    return corrected_text, corrections, ast


def _align_spacy_to_word(spacy_token, words: list[str]) -> int | None:
    """Best-effort alignment of a spaCy token back to our word list index."""
    target = spacy_token.text.lower()
    # First try exact match at the same index
    idx = spacy_token.i
    if idx < len(words) and words[idx].lower() == target:
        return idx
    # Fall back to scanning
    for i, w in enumerate(words):
        if w.lower() == target:
            return i
    return None


# ── sample texts ──────────────────────────────────────────────────────────────
# Mix of spelling errors (OOV) and grammatical errors (wrong inflection).
samples = [
    # spelling: hjam -> hjem
    "jeg vil gerne gå hjam og spise middag",
    # spelling: storre -> store
    "der er mange storre byer i landet",
    # spelling: kviner -> kvinder
    "de to kviner gik en tur sammen",
    # grammar: "en stor hus" – article should be neuter "et" for "hus"
    "vi bor i en stor hus",
    # grammar: "de vil have en gammel biler" – determiner/number mismatch
    "de vil have en gammel biler",
    # spelling + grammar mix
    "han vile købe en storre bil",
]

# ── run ───────────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("  Spelling + Grammar correction  ·  Grammatical AST")
print("=" * 72)

total_time = 0.0
for text in samples:
    print(f"\n  Input:     {text}")

    if device >= 0:
        torch.cuda.synchronize()
    t0 = time.perf_counter()
    corrected, changes, ast = correct_and_parse(text, fill_mask, tokenizer, nlp)
    if device >= 0:
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - t0
    total_time += elapsed

    print(f"  Corrected: {corrected}")
    if changes:
        for c in changes:
            label = c["kind"].upper()
            print(
                f"    [{label:>7s}] '{c['original']}' -> '{c['suggestion']}'  "
                f"(edit_dist={c['edit_dist']}, score={c['score']:.4f})"
            )
    else:
        print("    (no changes)")

    print(f"\n  AST:\n{ast.pretty(indent=2)}")
    print(f"\n  Time: {elapsed:.3f}s")

print(f"\n{'=' * 72}")
print(f"Total correction time: {total_time:.3f}s")
print()

# Also dump the last AST as JSON for programmatic consumption.
print("Last AST as JSON:")
print(json.dumps(ast.to_dict(), indent=2, ensure_ascii=False))
