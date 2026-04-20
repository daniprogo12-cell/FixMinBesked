"""Structural style transfer — word-order reordering via dep-tree templates."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .grammar_ast import GrammarNode, build_sentence_ast


@dataclass
class StructuralStyle:
    """Word-order template: for each head POS, which deps go left/right."""

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
    return dep.split(":")[0]


def _linearize_styled(token, patterns):
    children = list(token.children)
    if not children:
        return [token.text]

    pattern = patterns.get(token.pos_)
    if pattern:
        left_template, right_template = pattern
        left_slots = Counter(_dep_base(d) for d in left_template)
        right_slots = Counter(_dep_base(d) for d in right_template)

        by_dep: dict[str, list] = defaultdict(list)
        for child in children:
            by_dep[_dep_base(child.dep_)].append(child)

        left_children: list = []
        right_children: list = []

        for dep_base, dep_children in by_dep.items():
            n_left = left_slots.get(dep_base, 0)
            n_right = right_slots.get(dep_base, 0)
            dep_children.sort(key=lambda c: c.i, reverse=True)

            if n_left > 0:
                left_children.extend(dep_children[:n_left])
                remainder = dep_children[n_left:]
            else:
                remainder = dep_children
            if n_right > 0:
                right_children.extend(remainder[:n_right])
                remainder = remainder[n_right:]
            for child in remainder:
                if child.i < token.i:
                    left_children.append(child)
                else:
                    right_children.append(child)

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
    doc = nlp(text)
    original_order = [t.text for t in doc]
    roots = [t for t in doc if t.dep_ == "ROOT"]
    if not roots:
        return text, [], build_sentence_ast(doc, [])

    tokens_ordered: list[str] = []
    for root in roots:
        tokens_ordered.extend(_linearize_styled(root, style.patterns))

    moves = [
        f"pos {i}: '{old}' -> '{new}'"
        for i, (old, new) in enumerate(zip(original_order, tokens_ordered))
        if old != new
    ]
    styled_text = " ".join(tokens_ordered)
    result_doc = nlp(styled_text)
    ast = build_sentence_ast(result_doc, [])
    return styled_text, moves, ast
