"""Renumber target-language placeholders to match source-language semantics.

In doc_verify, RU and EN segments are parsed independently and each gets a
fresh left-to-right placeholder numbering inside its own language. When word
order shifts in translation, the same inline atom (a code span, URL, image,
or YFM variable) ends up with different names — RU's ``⟦C1⟧=`episodes``` may
correspond to EN's ``⟦C3⟧=`episodes```. The critic LLM doesn't see what each
marker stands for; it assumes ``⟦C1⟧`` means the same thing in both languages
and flags ordering mismatches that are actually correct translations.

This module rebuilds target segments so that whenever an atom appears in
both source and target, both placeholders share the source's name. The
critic then sees a single coherent numbering across the pair, suggestions
target the right atoms, and the apply path can substitute through the
target AST without scrambling content.
"""

from __future__ import annotations

import re

from ydbdoc_review.parsing.ast_types import (
    InlineCode,
    InlineHTML,
    InlineImage,
    InlineLink,
    InlineNode,
    InlineVariable,
)
from ydbdoc_review.segmentation.types import ProtectedInline, Segment

_LOCALE_PREFIX_RE = re.compile(r"^(/(?:ru|en))(/|$)")
_PLACEHOLDER_NAME_RE = re.compile(r"⟦[CLIHVTUS]\d+⟧")


def _strip_locale_prefix(href: str) -> str:
    """Drop a leading ``/ru/`` or ``/en/`` so EN and RU URLs match by content."""
    return _LOCALE_PREFIX_RE.sub(r"/", href, count=1)


def _atom_key(node: InlineNode) -> tuple:
    """Language-neutral identity for an inline atom.

    Code spans and YFM variables don't translate, so they match by content.
    URLs match by href with locale prefix stripped. Other inline kinds fall
    back to ``(kind, raw)`` and rarely match across languages.
    """
    if isinstance(node, InlineCode):
        return ("code", node.content)
    if isinstance(node, InlineVariable):
        return ("var", node.name)
    if isinstance(node, InlineHTML):
        return ("html", node.content)
    if isinstance(node, InlineLink):
        if not node.children and node.href:
            return ("url", _strip_locale_prefix(node.href))
        return ("link", _strip_locale_prefix(node.href))
    if isinstance(node, InlineImage):
        return ("img", node.src)
    return ("other", node.kind, getattr(node, "content", ""))


def _placeholder_kind(name: str) -> str:
    return name[1]


def _placeholder_index(name: str) -> int:
    return int(name[2:-1])


def normalize_target_segments_to_source(
    source_segments: list[Segment],
    target_segments: list[Segment],
) -> list[Segment]:
    """Return target segments with placeholders renamed to source numbering.

    Segments at mismatched positions or with unequal counts are returned
    unchanged. The function never raises — failure modes fall back to the
    original target segments so callers can still proceed.
    """
    if len(source_segments) != len(target_segments):
        return list(target_segments)
    return [
        _renumber_segment(src, tgt)
        for src, tgt in zip(source_segments, target_segments, strict=True)
    ]


def _renumber_segment(src: Segment, tgt: Segment) -> Segment:
    if not tgt.placeholders:
        return tgt

    src_by_key: dict[tuple, list[str]] = {}
    for p in src.placeholders:
        src_by_key.setdefault(_atom_key(p.node), []).append(p.placeholder)
    src_names = {p.placeholder for p in src.placeholders}

    rename: dict[str, str] = {}
    used_src: set[str] = set()
    new_names_in_tgt: set[str] = set()
    unmatched: list[ProtectedInline] = []

    # Pass 1: same atom in src and tgt → tgt borrows src's name.
    for tp in tgt.placeholders:
        key = _atom_key(tp.node)
        match = next(
            (c for c in src_by_key.get(key, []) if c not in used_src), None
        )
        if match is None:
            unmatched.append(tp)
            continue
        used_src.add(match)
        new_names_in_tgt.add(match)
        if match != tp.placeholder:
            rename[tp.placeholder] = match

    # Pass 2: tgt-only atoms keep their name when it's free, otherwise get a
    # fresh non-clashing one. "Free" = not used by src and not already chosen
    # as a tgt new-name in pass 1.
    next_idx_by_kind: dict[str, int] = {}
    for p in src.placeholders:
        k = _placeholder_kind(p.placeholder)
        next_idx_by_kind[k] = max(
            next_idx_by_kind.get(k, 0), _placeholder_index(p.placeholder)
        )

    for tp in unmatched:
        if tp.placeholder not in src_names and tp.placeholder not in new_names_in_tgt:
            new_names_in_tgt.add(tp.placeholder)
            continue
        kind = _placeholder_kind(tp.placeholder)
        while True:
            next_idx_by_kind[kind] = next_idx_by_kind.get(kind, 0) + 1
            candidate = f"⟦{kind}{next_idx_by_kind[kind]}⟧"
            if candidate not in src_names and candidate not in new_names_in_tgt:
                rename[tp.placeholder] = candidate
                new_names_in_tgt.add(candidate)
                break

    if not rename:
        return tgt
    new_text = _rename_in_text(tgt.text, rename)
    new_placeholders = [
        ProtectedInline(
            placeholder=rename.get(p.placeholder, p.placeholder),
            node=p.node,
        )
        for p in tgt.placeholders
    ]
    return Segment(
        id=tgt.id,
        kind=tgt.kind,
        path=list(tgt.path),
        text=new_text,
        placeholders=new_placeholders,
        ast_path=list(tgt.ast_path),
    )


def _rename_in_text(text: str, rename: dict[str, str]) -> str:
    """Single-pass placeholder rename so ``⟦C1⟧→⟦C2⟧`` and ``⟦C2⟧→⟦C1⟧`` swap cleanly."""
    if not rename:
        return text

    def repl(m: re.Match[str]) -> str:
        return rename.get(m.group(0), m.group(0))

    return _PLACEHOLDER_NAME_RE.sub(repl, text)
