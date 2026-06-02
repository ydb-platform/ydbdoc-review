"""Deterministic EN locale fixes for link and image URLs after translation."""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import unquote

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    Document,
    InlineImage,
    InlineLink,
    InlineNode,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTabs,
)

_HOST_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("ru.wikipedia.org", "en.wikipedia.org"),
    ("www.ru.wikipedia.org", "en.wikipedia.org"),
)

_PATH_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)yandex\.cloud/ru/docs"), "yandex.cloud/en/docs"),
    (re.compile(r"(?i)kubernetes\.io/ru/docs"), "kubernetes.io/docs"),
    (re.compile(r"(?i)(/docs/)ru/"), r"\1en/"),
    (re.compile(r"/ydb/docs/ru/"), "/ydb/docs/en/"),
    (re.compile(r"(?i)json-ru\.html"), "index.html"),
)


def mirror_link_href(href: str) -> str:
    """Apply deterministic RU→EN URL locale fixes to a single href."""
    if not href or href.startswith("#"):
        return href
    out = href
    for old, new in _HOST_REPLACEMENTS:
        out = out.replace(old, new)
    for pattern, repl in _PATH_REPLACEMENTS:
        out = pattern.sub(repl, out)
    return out


def _walk_inline(nodes: Iterable[InlineNode]) -> None:
    for node in nodes:
        if isinstance(node, InlineLink):
            node.href = mirror_link_href(node.href)
        elif isinstance(node, InlineImage):
            node.src = mirror_link_href(node.src)
        elif hasattr(node, "children") and isinstance(node.children, list):
            _walk_inline(node.children)


def _walk_blocks(blocks: Iterable[BlockNode]) -> None:
    from ydbdoc_review.parsing.ast_types import Heading, TermDefinition

    for block in blocks:
        if isinstance(block, (Paragraph, Heading, TermDefinition)):
            _walk_inline(block.children)
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    for child in item.children:
                        _walk_blocks([child])
        elif isinstance(block, BlockQuote):
            _walk_blocks(block.children)
        elif isinstance(block, Table):
            for cell in block.header.cells:
                _walk_inline(cell.children)
            for row in block.rows:
                for cell in row.cells:
                    _walk_inline(cell.children)
        elif isinstance(block, YfmNote):
            _walk_blocks(block.children)
        elif isinstance(block, YfmTabs):
            for tab in block.children:
                _walk_inline(tab.title)
                _walk_blocks(tab.children)
        elif isinstance(block, YfmCut):
            _walk_blocks(block.children)
        elif isinstance(block, YfmIf):
            for branch in block.branches:
                _walk_blocks(branch.children)


def localize_links_in_document(doc: Document) -> None:
    """Rewrite link/image URLs in-place for EN locale (safety net after reinsert)."""
    _walk_blocks(doc.children)
