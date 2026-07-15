"""YFM heading anchors: parse Cyrillic ids and emit English anchors for EN docs."""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import unquote

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    Heading,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTabs,
)

_HEADING_ANCHOR_SUFFIX = re.compile(r"\s*\{#([^}]+)\}\s*$")


def split_heading_anchor_suffix(text: str) -> tuple[str, str | None]:
    """Split trailing ``{#anchor}`` from heading inline text."""
    match = _HEADING_ANCHOR_SUFFIX.search(text)
    if not match:
        return text, None
    return text[: match.start()].rstrip(), match.group(1)


def diplodoc_auto_slug(text: str) -> str:
    """Diplodoc-style auto anchor from visible heading text."""
    slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug


def english_yfm_anchor(ru_anchor: str | None, english_heading: str) -> str | None:
    """Map a RU/Cyrillic YFM anchor to an English id for EN output.

  Examples: ``fields-Описание`` + "Description of fields…" → ``fields-Description``.
  ASCII anchors are returned unchanged.
    """
    if not ru_anchor:
        return None
    if ru_anchor.isascii() and re.fullmatch(r"[A-Za-z0-9_\-.]+", ru_anchor):
        return ru_anchor

    prefix, sep, suffix = ru_anchor.partition("-")
    if sep and suffix and not suffix.isascii():
        word = re.match(r"([A-Za-z][A-Za-z0-9]*)", english_heading.strip())
        if word:
            return f"{prefix}-{word.group(1)}"

    return diplodoc_auto_slug(english_heading) or ru_anchor


def _heading_plain_text(heading: Heading) -> str:
    from ydbdoc_review.rendering.markdown_renderer import _render_inline

    return _render_inline(heading.children).strip()


def _iter_headings(blocks: list[BlockNode]) -> Iterator[Heading]:
    for block in blocks:
        if isinstance(block, Heading):
            yield block
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    yield from _iter_headings(item.children)
        elif isinstance(block, BlockQuote):
            yield from _iter_headings(block.children)
        elif isinstance(block, Table):
            for cell in block.header.cells:
                yield from _iter_headings([Paragraph(children=cell.children)])
            for row in block.rows:
                for cell in row.cells:
                    yield from _iter_headings([Paragraph(children=cell.children)])
        elif isinstance(block, YfmNote):
            yield from _iter_headings(block.children)
        elif isinstance(block, YfmTabs):
            for tab in block.children:
                yield from _iter_headings(tab.children)
        elif isinstance(block, YfmCut):
            yield from _iter_headings(block.children)
        elif isinstance(block, YfmIf):
            for branch in block.branches:
                yield from _iter_headings(branch.children)


def build_heading_anchor_map(source: Document, target: Document) -> dict[str, str]:
    """Map RU heading slugs/explicit anchors to EN counterparts."""
    mapping: dict[str, str] = {}
    for src_h, tgt_h in zip(
        list(_iter_headings(source.children)),
        list(_iter_headings(target.children)),
        strict=False,
    ):
        ru_text = _heading_plain_text(src_h)
        en_text = _heading_plain_text(tgt_h)
        ru_auto = diplodoc_auto_slug(ru_text)
        en_auto = diplodoc_auto_slug(en_text)
        if ru_auto and en_auto and ru_auto != en_auto:
            mapping[ru_auto] = en_auto
        if src_h.anchor:
            if tgt_h.anchor and tgt_h.anchor.isascii():
                en_explicit = tgt_h.anchor
            else:
                en_explicit = english_yfm_anchor(src_h.anchor, en_text) or en_auto
            if en_explicit:
                mapping[src_h.anchor] = en_explicit
                decoded = unquote(src_h.anchor)
                if decoded != src_h.anchor:
                    mapping[decoded] = en_explicit
    return mapping
