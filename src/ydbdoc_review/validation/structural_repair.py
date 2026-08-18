"""Deterministic EN structural repairs from the RU twin (§6.191).

Covers gaps differential translate leaves behind: missing explicit ``{#id}`` on
headings, missing ``### Signature`` + `` ```yql`` blocks under ``##`` sections,
and (via verify partial realign) untranslated table cells.
"""

from __future__ import annotations

import re

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import _render_inline
from ydbdoc_review.validation.yfm_anchor import (
    _iter_headings,
    split_heading_anchor_suffix,
)

_H2_SECTION = re.compile(
    r"^(## [^\n]+)\n(.*?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_SIGNATURE_BLOCK = re.compile(
    r"(\s*)### (?:Signature|Сигнатура)\s*\n+(```yql\n.*?\n```)\s*",
    re.DOTALL | re.IGNORECASE,
)


def _heading_plain(heading) -> str:
    return _render_inline(heading.children).strip()


def restore_explicit_heading_anchors(translated: str, source: str) -> str:
    """Copy missing explicit ``{#id}`` from RU headings onto EN twins by position."""
    if not translated or not source:
        return translated

    ru_doc = parse_markdown(source)
    en_doc = parse_markdown(translated)
    ru_heads = list(_iter_headings(ru_doc.children))
    en_heads = list(_iter_headings(en_doc.children))
    if not ru_heads or not en_heads:
        return translated

    out = translated
    for ru_h, en_h in zip(ru_heads, en_heads, strict=False):
        ru_anchor = ru_h.anchor
        en_anchor = en_h.anchor
        if not ru_anchor or en_anchor:
            continue
        if ru_h.level != en_h.level:
            continue
        en_title = _heading_plain(en_h)
        en_title, _ = split_heading_anchor_suffix(en_title)
        pattern = re.compile(
            rf"^(#{{{ru_h.level}}}\s+{re.escape(en_title)})\s*$",
            re.MULTILINE,
        )
        repl = rf"\1 {{#{ru_anchor}}}"
        new_out, n = pattern.subn(repl, out, count=1)
        if n:
            out = new_out
    return out


def _signature_block_en(ru_block: str) -> str:
    match = _SIGNATURE_BLOCK.match(ru_block)
    if not match:
        return ru_block.strip() + "\n\n"
    return f"### Signature\n\n{match.group(2).strip()}\n\n"


def _section_signature_block(body: str) -> str | None:
    match = _SIGNATURE_BLOCK.match(body)
    if not match:
        return None
    return match.group(0)


def sync_missing_signature_sections(translated: str, source: str) -> str:
    """Insert missing ``### Signature`` + `` ```yql`` blocks from RU ``##`` sections."""
    if not translated or not source:
        return translated

    ru_sections = list(_H2_SECTION.finditer(source))
    if not ru_sections:
        return translated

    out = translated
    for ru_match in ru_sections:
        ru_header = ru_match.group(1)
        ru_body = ru_match.group(2)
        ru_sig = _section_signature_block(ru_body)
        if not ru_sig:
            continue
        anchor_match = re.search(r"\{#([^}]+)\}", ru_header)
        if not anchor_match:
            continue
        anchor = anchor_match.group(1)
        en_section = re.search(
            rf"^(## [^\n]*\{{#{re.escape(anchor)}\}}[^\n]*)\n(.*?)(?=^## |\Z)",
            out,
            re.MULTILINE | re.DOTALL,
        )
        if not en_section:
            continue
        en_header, en_body = en_section.group(1), en_section.group(2)
        if _section_signature_block(en_body):
            continue
        insert = _signature_block_en(ru_sig)
        new_body = insert + en_body.lstrip("\n")
        old_chunk = f"{en_header}\n{en_body}"
        new_chunk = f"{en_header}\n{new_body}"
        if old_chunk in out:
            out = out.replace(old_chunk, new_chunk, 1)
    return out


def repair_en_structure_from_ru(en_text: str, ru_text: str) -> str:
    """Run deterministic structural repairs (anchors + signature blocks)."""
    out = restore_explicit_heading_anchors(en_text, ru_text)
    out = sync_missing_signature_sections(out, ru_text)
    return out
