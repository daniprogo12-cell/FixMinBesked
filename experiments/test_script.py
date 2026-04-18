"""
Danish text corrector, grammatical AST builder, and style transfer.

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
4.  Style transfer        – A grammatical style profile is extracted from an
    example sentence (dominant morphological features per POS class).  A
    target sentence is then transformed to match that profile by masking
    each inflectable word and selecting the same-lemma candidate whose
    morphological features best match the style.
"""

from __future__ import annotations

import ctypes
import json
import time
from collections import Counter, defaultdict
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


# ══════════════════════════════════════════════════════════════════════════════
#  Part 2 — Structural style transfer  (word-order reordering)
# ══════════════════════════════════════════════════════════════════════════════
#
# Danish is a V2 (verb-second) language: exactly one constituent may be
# "fronted" before the finite verb.  The choice of what gets fronted is a
# major stylistic lever — subject-first (neutral), adverbial-first
# (narrative), object-first (contrastive), etc.
#
# We capture this as a *structural style*:
#   1. Parse the example sentence into a dependency tree.
#   2. For every head token, record which dependency relations appear to the
#      LEFT versus RIGHT of the head, and in what order.
#   3. To apply the style to a target sentence, parse it, then re-linearize
#      its dependency tree so that children of each head appear in the same
#      left/right arrangement as in the example.
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class StructuralStyle:
    """Word-order template: for each head POS, which deps go left/right."""

    # Maps head POS -> (left_dep_sequence, right_dep_sequence)
    patterns: dict[str, tuple[list[str], list[str]]] = field(default_factory=dict)
    source_text: str = ""

    def pretty(self) -> str:
        lines = [f'Order from: "{self.source_text}"']
        for pos, (left, right) in sorted(self.patterns.items()):
            l_str = ", ".join(left) if left else "—"
            r_str = ", ".join(right) if right else "—"
            lines.append(f"  {pos:5s}  [{l_str}]  HEAD  [{r_str}]")
        return "\n".join(lines)


def extract_structural_style(text: str, nlp) -> StructuralStyle:
    """Parse *text* and record dep-label ordering around each head."""
    doc = nlp(text)
    patterns: dict[str, tuple[list[str], list[str]]] = {}

    for token in doc:
        children = list(token.children)
        if not children:
            continue
        left = [c.dep_ for c in children if c.i < token.i]
        right = [c.dep_ for c in children if c.i > token.i]
        patterns[token.pos_] = (left, right)

    return StructuralStyle(patterns=patterns, source_text=text)


def _dep_base(dep: str) -> str:
    """'advmod:lmod' -> 'advmod'."""
    return dep.split(":")[0]


def _linearize_styled(
    token,
    patterns: dict[str, tuple[list[str], list[str]]],
) -> list[str]:
    """Recursively linearize a subtree using the style's ordering patterns.

    When the template has N slots of a dep label on the left, we place at
    most N children of that dep on the left, choosing the *rightmost* ones
    in the original sentence (temporal/locative adverbials in Danish tend to
    sit sentence-finally and are the typical candidates for V2 fronting).
    Overflow stays on its original side.
    """
    children = list(token.children)
    if not children:
        return [token.text]

    pattern = patterns.get(token.pos_)

    if pattern:
        left_template, right_template = pattern

        # Count how many slots each dep-base has on each side.
        left_slots = Counter(_dep_base(d) for d in left_template)
        right_slots = Counter(_dep_base(d) for d in right_template)

        # Group children by base dep.
        by_dep: dict[str, list] = defaultdict(list)
        for child in children:
            by_dep[_dep_base(child.dep_)].append(child)

        left_children: list = []
        right_children: list = []

        for dep_base, dep_children in by_dep.items():
            n_left = left_slots.get(dep_base, 0)
            n_right = right_slots.get(dep_base, 0)

            # Sort rightmost-first so the sentence-final constituents
            # are picked first for fronting (typical V2 behaviour).
            dep_children.sort(key=lambda c: c.i, reverse=True)

            if n_left > 0:
                left_children.extend(dep_children[:n_left])
                remainder = dep_children[n_left:]
            else:
                remainder = dep_children

            if n_right > 0:
                right_children.extend(remainder[:n_right])
                remainder = remainder[n_right:]

            # Anything left over keeps its original side.
            for child in remainder:
                if child.i < token.i:
                    left_children.append(child)
                else:
                    right_children.append(child)

        # Sort within each side to match template ordering.
        def _order_key(template):
            base_tpl = [_dep_base(d) for d in template]

            def key(c):
                if c.dep_ in template:
                    return template.index(c.dep_)
                b = _dep_base(c.dep_)
                if b in base_tpl:
                    return base_tpl.index(b)
                return len(template)

            return key

        left_children.sort(key=_order_key(left_template))
        right_children.sort(key=_order_key(right_template))
    else:
        # No pattern for this POS — keep original order.
        left_children = [c for c in children if c.i < token.i]
        right_children = [c for c in children if c.i > token.i]

    result: list[str] = []
    for c in left_children:
        result.extend(_linearize_styled(c, patterns))
    result.append(token.text)
    for c in right_children:
        result.extend(_linearize_styled(c, patterns))
    return result


def apply_structural_style(
    text: str,
    style: StructuralStyle,
    nlp,
) -> tuple[str, list[str], GrammarNode]:
    """Reorder *text* to match the style's word-order patterns.

    Returns (styled_text, list_of_moves, ast).
    """
    doc = nlp(text)
    original_order = [t.text for t in doc]

    roots = [t for t in doc if t.dep_ == "ROOT"]
    if not roots:
        ast = build_sentence_ast(doc, [])
        return text, [], ast

    tokens_ordered: list[str] = []
    for root in roots:
        tokens_ordered.extend(_linearize_styled(root, style.patterns))

    # Detect what moved.
    moves: list[str] = []
    for i, (old, new) in enumerate(zip(original_order, tokens_ordered)):
        if old != new:
            moves.append(f"pos {i}: '{old}' -> '{new}'")

    styled_text = " ".join(tokens_ordered)
    result_doc = nlp(styled_text)
    ast = build_sentence_ast(result_doc, [])
    return styled_text, moves, ast


# ── structural style examples ─────────────────────────────────────────────────
#
# Danish V2 word order allows exactly one constituent before the finite verb.
# These examples demonstrate switching which constituent is fronted.

structural_pairs = [
    {
        "name": "Adverbial fronting (V2 inversion)",
        # Style example fronts the temporal adverbial before the verb,
        # pushing the subject after the verb.
        "example": "i går gik manden stille hjem",
        "targets": [
            "manden går stille hjem i dag",
            "kvinden kørte bilen hjem i morges",
        ],
    },
    {
        "name": "Subject-first (standard SVO)",
        # Style example keeps the subject before the verb (neutral order).
        "example": "manden gik stille hjem i går",
        "targets": [
            "i dag går kvinden stille hjem",
            "i morgen kører han bilen hjem",
        ],
    },
    {
        "name": "Object fronting (contrastive)",
        # Style example fronts the object for emphasis.
        "example": "bilen købte manden i går",
        "targets": [
            "manden købte bilen i går",
            "hun læste bogen i aftes",
        ],
    },
]

print("\n" + "=" * 72)
print("  Structural style transfer  (word-order reordering)")
print("=" * 72)

for pair in structural_pairs:
    style = extract_structural_style(pair["example"], nlp)
    print(f"\n  --- {pair['name']} ---")
    print(f"  {style.pretty()}")

    for target in pair["targets"]:
        print(f"\n  Input:     {target}")

        t0 = time.perf_counter()
        styled, moves, ast = apply_structural_style(target, style, nlp)
        elapsed = time.perf_counter() - t0

        print(f"  Styled:    {styled}")
        if moves:
            for m in moves:
                print(f"    {m}")
        else:
            print("    (no reordering needed)")
        print(f"\n  AST:\n{ast.pretty(indent=2)}")
        print(f"  Time: {elapsed:.3f}s")
