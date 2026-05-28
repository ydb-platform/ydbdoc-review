"""Deterministic RU→EN alignment: copy locale-neutral regions from SOURCE."""

from __future__ import annotations

import re

from ydbdoc_review.fence_repair import (
    _fence_positions,
    _inner_line_count,
    extract_fence_blocks,
)
from ydbdoc_review.list_tabs_blocks import (
    _LIST_TABS_BLOCK_RE,
    list_tabs_block_copy_verbatim,
)
from ydbdoc_review.markdown_links import (
    fix_broken_fence_lines_from_ru,
    restore_markdown_links_from_ru,
    strip_duplicate_cyrillic_links,
)
from ydbdoc_review.table_ast import _TABLE_SEP_ROW_RE
from ydbdoc_review.table_ast import repair_table_rows_from_ru
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
    if not src_blocks:
        return translation, False

    trn_blocks = extract_fence_blocks(translation)
    if len(src_blocks) != len(trn_blocks):
        src_lines = source.splitlines()
        trn_lines = translation.splitlines()
        src_pos = _fence_positions(src_lines)
        trn_pos = _fence_positions(trn_lines)
        if len(src_pos) != len(trn_pos) or not src_pos:
            return translation, False
        new_lines = list(trn_lines)
        changed = False
        for (ss, se), (ts, te) in zip(src_pos, trn_pos, strict=True):
            ru_slice = src_lines[ss:se]
            if ru_slice != new_lines[ts:te]:
                new_lines[ts:te] = ru_slice
                changed = True
        if not changed:
            return translation, False
        return "\n".join(new_lines), True

    out = translation
    changed = False
    for src_block, trn_block in zip(src_blocks, trn_blocks, strict=True):
        src_inner = _inner_line_count(src_block)
        trn_inner = _inner_line_count(trn_block)
        inner_mismatch = src_inner != trn_inner
        truncated = trn_inner < src_inner
        if src_block != trn_block or inner_mismatch or truncated:
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


def sync_manual_tabs_fences_from_source(
    source: str, translation: str
) -> tuple[str, bool]:
    """Restore fenced blocks inside instructional ``{% list tabs %}`` from RU."""
    ru_parts = _LIST_TABS_BLOCK_RE.split(source)
    en_parts = _LIST_TABS_BLOCK_RE.split(translation)
    if len(ru_parts) != len(en_parts):
        return translation, False

    changed = False
    out_parts: list[str] = []
    for ru_p, en_p in zip(ru_parts, en_parts, strict=False):
        if not _LIST_TABS_OPEN_RE.search(ru_p):
            out_parts.append(en_p)
            continue
        if list_tabs_block_copy_verbatim(ru_p):
            out_parts.append(en_p)
            continue
        block = en_p
        block, c1 = sync_fenced_blocks_from_source(ru_p, block)
        block, c2 = restore_fence_openers_from_source(ru_p, block)
        changed = changed or c1 or c2
        out_parts.append(block)
    if not changed:
        return translation, False
    return "".join(out_parts), True


def repair_table_separator_rows_from_ru(ru_source: str, en_text: str) -> str:
    """Restore corrupted ``| --- |`` separator rows from RU (same line index)."""
    ru_lines = ru_source.splitlines()
    en_lines = en_text.splitlines()
    if not ru_lines or len(ru_lines) != len(en_lines):
        return en_text
    out = list(en_lines)
    changed = False
    for i, (ru_ln, en_ln) in enumerate(zip(ru_lines, en_lines, strict=True)):
        if not _TABLE_SEP_ROW_RE.match(ru_ln):
            continue
        if _TABLE_SEP_ROW_RE.match(en_ln):
            continue
        if en_ln.lstrip().startswith("|") and "---" in en_ln:
            out[i] = ru_ln
            changed = True
    if not changed:
        return en_text
    return "\n".join(out)


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
    out = repair_table_separator_rows_from_ru(ru_source, out)
    out = repair_table_rows_from_ru(ru_source, out)
    out, _ = sync_manual_tabs_fences_from_source(ru_source, out)
    out, _ = repair_tab_labels_from_source(ru_source, out)
    return out
