"""Grammatical AST built from spaCy dependency parses."""

from __future__ import annotations

from dataclasses import dataclass, field

from .helpers import spacy_morph_to_dict


@dataclass
class GrammarNode:
    """One node in the grammatical AST (= one token)."""

    text: str
    lemma: str
    pos: str
    dep: str
    morph: dict[str, str] = field(default_factory=dict)
    corrections: list[dict] = field(default_factory=list)
    children: list[GrammarNode] = field(default_factory=list)

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


def _build_ast(token, corrections_by_idx: dict[int, list[dict]]) -> GrammarNode:
    morph = spacy_morph_to_dict(token)
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


def build_sentence_ast(doc, corrections: list[dict] | None = None) -> GrammarNode:
    """Build the full AST for a parsed sentence."""
    corrections = corrections or []
    by_idx: dict[int, list[dict]] = {}
    for c in corrections:
        by_idx.setdefault(c["pos"], []).append(c)

    roots = [t for t in doc if t.dep_ == "ROOT"]
    if len(roots) == 1:
        return _build_ast(roots[0], by_idx)

    s = GrammarNode(text="", lemma="", pos="S", dep="ROOT")
    for root in roots:
        s.children.append(_build_ast(root, by_idx))
    return s
