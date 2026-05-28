"""Deterministic RU→EN alignment: copy locale-neutral regions from SOURCE."""

from __future__ import annotations

import re

from ydbdoc_review.fence_repair import extract_fence_blocks
from ydbdoc_review.list_tabs_blocks import (
    _LIST_TABS_BLOCK_RE,
    list_tabs_block_copy_verbatim,
)
from ydbdoc_review.markdown_links import (
    fix_broken_fence_lines_from_ru,
    restore_markdown_links_from_ru,
    strip_duplicate_cyrillic_links,
)
from ydbdoc_review.tabs_repair import repair_tab_labels_from_source

_LIST_TABS_OPEN_RE = re.compile(r"\{%\s*list\s+tabs", re.IGNORECASE)
_FENCE_OPEN_RE = re.compile(r"^\s*(```|~~~).*$")


def sync_verbatim_list_tabs_from_source(source: str, translation: str) -> tuple[str, bool]:
    """
    Replace config-style ``{% list tabs %}`` blocks in *translation* with SOURCE bytes.

    YAML, tab ids (``mirror-3-dc-3nodes``), and fences inside those blocks must not
    pass through the LLM.
    """
    ru_parts = _LIST_TABS_BLOCK_RE.split(source)
    en_parts = _LIST_TABS_BLOCK_RE.split(translation)
    if len(ru_parts) != len(en_parts):
        return translation, False

    changed = False
    out_parts: list[str] = []
    for ru_p, en_p in zip(ru_parts, en_parts, strict=False):
        if _LIST_TABS_OPEN_RE.search(ru_p) and list_tabs_block_copy_verbatim(ru_p):
            if ru_p != en_p:
                changed = True
            out_parts.append(ru_p)
        else:
            out_parts.append(en_p)
    if not changed:
        return translation, False
    return "".join(out_parts), True


def sync_fenced_blocks_from_source(source: str, translation: str) -> tuple[str, bool]:
    """Replace fenced blocks in *translation* with SOURCE blocks (same count, in order)."""
    src_blocks = extract_fence_blocks(source)
    trn_blocks = extract_fence_blocks(translation)
    if not src_blocks or len(src_blocks) != len(trn_blocks):
        return translation, False

    out = translation
    changed = False
    for src_block, trn_block in zip(src_blocks, trn_blocks, strict=True):
        if src_block != trn_block:
            out = out.replace(trn_block, src_block, 1)
            changed = True
    return out, changed


def restore_fence_openers_from_source(source: str, translation: str) -> tuple[str, bool]:
    """
    Restore opening fence lines (```lang) from SOURCE by order.

    Useful when the model keeps fence bodies but drops `bash` / `text` info strings.
    """
    src_openers = [ln for ln in source.splitlines() if _FENCE_OPEN_RE.match(ln)]
    tr_lines = translation.splitlines()
    out: list[str] = []
    idx = 0
    changed = False
    for ln in tr_lines:
        if _FENCE_OPEN_RE.match(ln) and idx < len(src_openers):
            src_ln = src_openers[idx]
            if ln != src_ln:
                changed = True
            out.append(src_ln)
            idx += 1
            continue
        out.append(ln)
    return "\n".join(out), changed


def finalize_en_document_from_ru(ru_source: str, en_text: str) -> str:
    """
    Last pass after LLM merge for RU→EN: prose stays translated; code/config/tabs sync.

    Order: config tabs → fences → links → manual tab labels (translate-blocks only).
    """
    out = en_text
    out, _ = sync_verbatim_list_tabs_from_source(ru_source, out)
    out = strip_duplicate_cyrillic_links(out, ru_source)
    out = fix_broken_fence_lines_from_ru(ru_source, out)
    out, _ = sync_fenced_blocks_from_source(ru_source, out)
    out, _ = restore_fence_openers_from_source(ru_source, out)
    out = restore_markdown_links_from_ru(ru_source, out)
    out, _ = repair_tab_labels_from_source(ru_source, out)
    return out
