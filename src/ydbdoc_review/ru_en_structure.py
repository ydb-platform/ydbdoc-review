"""Structural RU→EN alignment: Diplodoc tab items, index bullets, link order."""

from __future__ import annotations

import re
from typing import Callable

_BULLET_LINK_RE = re.compile(r"^(\s*)- \[([^\]]+)\]\(([^)]+)\)\s*$")
_LIST_TABS_START_RE = re.compile(r"\{%\s*list\s+tabs", re.IGNORECASE)
_LIST_TABS_END_RE = re.compile(r"\{%\s*endlist\s*%\}", re.IGNORECASE)
_TOP_TAB_ITEM_RE = re.compile(r"^- ([^\[\n].+?)\s*$")
_RU_NOT_SUPPORTED = "Функциональность на данный момент не поддерживается."
_EN_NOT_SUPPORTED = "This functionality is not currently supported."


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


def _href_title_defaults(href: str) -> str:
    stem = href.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ")
    return stem.title() if stem else href


def markdown_bullet_hrefs(text: str) -> list[tuple[str, str]]:
    """Ordered ``(title, href)`` for top-level ``- [title](href)`` lines."""
    out: list[tuple[str, str]] = []
    for line in _lines(text):
        m = _BULLET_LINK_RE.match(line)
        if m:
            out.append((m.group(2).strip(), m.group(3).strip()))
    return out


def index_bullets_behind_ru(ru_full: str, en_text: str) -> bool:
    """True when EN index-style page is missing bullet links present in RU."""
    ru_hrefs = {h for _, h in markdown_bullet_hrefs(ru_full)}
    en_hrefs = {h for _, h in markdown_bullet_hrefs(en_text)}
    return bool(ru_hrefs - en_hrefs)


def reorder_bullet_links_like_ru(ru_full: str, en_text: str) -> str:
    """
    Reorder a contiguous run of ``- [title](href)`` lines in *en_text* to match RU href order.

    Only applies when both texts share the same href set in their first bullet run.
    """
    ru_order = [h for _, h in markdown_bullet_hrefs(ru_full)]
    if len(ru_order) < 2:
        return en_text
    lines = _lines(en_text)
    indices: list[int] = []
    en_by_href: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = _BULLET_LINK_RE.match(line)
        if m:
            indices.append(i)
            en_by_href[m.group(3).strip()] = line
    if not indices:
        return en_text
    en_hrefs = [h for _, h in markdown_bullet_hrefs(en_text)]
    if set(ru_order) != set(en_hrefs) or len(en_hrefs) != len(indices):
        return en_text
    reordered = [en_by_href[h] for h in ru_order if h in en_by_href]
    if reordered == [lines[i] for i in indices]:
        return en_text
    out = list(lines)
    for idx, new_line in zip(indices, reordered):
        out[idx] = new_line
    return "\n".join(out)


def sync_index_bullets_from_ru(
    ru_full: str,
    en_text: str,
    *,
    title_for_new: Callable[[str, str], str] | None = None,
) -> str:
    """Insert missing ``- [title](href)`` bullets; keep RU href order among shared links."""
    ru_bullets = markdown_bullet_hrefs(ru_full)
    if not ru_bullets:
        return en_text
    en_lines = _lines(en_text)
    en_hrefs = {h for _, h in markdown_bullet_hrefs(en_text)}
    missing = [(t, h) for t, h in ru_bullets if h not in en_hrefs]
    if not missing and not index_bullets_behind_ru(ru_full, en_text):
        return reorder_bullet_links_like_ru(ru_full, en_text)

    # Find indent from first EN bullet or default two spaces (nested under section).
    indent = "  "
    insert_at = len(en_lines)
    for i, line in enumerate(en_lines):
        m = _BULLET_LINK_RE.match(line)
        if m:
            indent = m.group(1)
            insert_at = i
            break
        if line.strip().startswith("#") and i + 1 < len(en_lines):
            insert_at = i + 1

    new_lines: list[str] = []
    for _ru_title, href in missing:
        en_title = (
            title_for_new(href, _ru_title)
            if title_for_new
            else _href_title_defaults(href)
        )
        new_lines.append(f"{indent}- [{en_title}]({href})")

    if new_lines:
        en_lines = en_lines[:insert_at] + new_lines + en_lines[insert_at:]
    merged = "\n".join(en_lines)
    return reorder_bullet_links_like_ru(ru_full, merged)


def _list_tabs_span(lines: list[str]) -> tuple[int, int] | None:
    """Inclusive line range of the outermost ``{% list tabs %}`` … ``{% endlist %}``."""
    start = end = None
    nest = 0
    for i, line in enumerate(lines):
        if _LIST_TABS_START_RE.search(line):
            if nest == 0:
                start = i + 1
            nest += 1
        elif _LIST_TABS_END_RE.search(line):
            nest -= 1
            if nest == 0 and start is not None:
                end = i
                break
    if start is None or end is None:
        return None
    return start, end


def _outer_list_tab_item_blocks(text: str) -> list[tuple[str, str]]:
    """
    Top-level ``- Label`` blocks inside the outermost ``{% list tabs %}`` … ``{% endlist %}`` span.

    Ignores nested tab groups (e.g. Go → Native SDK).
    """
    lines = _lines(text)
    span = _list_tabs_span(lines)
    if span is None:
        return []
    start, end = span

    items: list[tuple[str, str]] = []
    cur_label: str | None = None
    cur_lines: list[str] = []
    nest = 0

    for i in range(start, end):
        line = lines[i]
        if _LIST_TABS_START_RE.search(line):
            nest += 1
        elif _LIST_TABS_END_RE.search(line):
            nest = max(0, nest - 1)
        if nest > 0:
            if cur_label is not None:
                cur_lines.append(line)
            continue
        m = _TOP_TAB_ITEM_RE.match(line)
        if m:
            if cur_label is not None:
                items.append((cur_label, "\n".join(cur_lines).rstrip()))
            cur_label = m.group(1).strip()
            cur_lines = [line]
        elif cur_label is not None:
            cur_lines.append(line)

    if cur_label is not None:
        items.append((cur_label, "\n".join(cur_lines).rstrip()))
    return items


def list_tab_item_labels(text: str) -> list[str]:
    return [label for label, _ in _outer_list_tab_item_blocks(text)]


def tab_items_missing_vs_source(
    source: str,
    translated: str,
    *,
    source_diff: str | None = None,
) -> bool:
    """
    True when EN is missing top-level SDK tab entries (``- Go``, ``- C++``, …) vs RU.

    Unlike ``tabs_missing_vs_source``, compares tab *labels*, not only ``{% list tabs %}`` count.
    """
    ru_labels = list_tab_item_labels(source)
    if not ru_labels:
        return False
    en_labels = set(list_tab_item_labels(translated))
    missing = [lb for lb in ru_labels if lb not in en_labels]
    if not missing:
        return False
    if not source_diff or not source_diff.strip():
        return True
    from ydbdoc_review.markdown_sections import (
        align_sections_by_heading,
        section_indices_touched_by_diff,
        split_markdown_sections,
    )

    ru_sections = split_markdown_sections(source)
    en_sections = split_markdown_sections(translated)
    touched = section_indices_touched_by_diff(source_diff, ru_sections)
    if not touched or len(touched) >= len(ru_sections):
        return True
    aligned = align_sections_by_heading(ru_sections, en_sections)
    for ru_sec in ru_sections:
        if ru_sec.index not in touched:
            continue
        ru_l = list_tab_item_labels(ru_sec.content)
        en_sec = aligned[ru_sec.index] if ru_sec.index < len(aligned) else None
        en_l = set(list_tab_item_labels(en_sec.content if en_sec else ""))
        if any(lb not in en_l for lb in ru_l):
            return True
    return False


def _localize_tab_block(label: str, block: str) -> str:
    """Minimal RU→EN for copied tab blocks (SDK names and stock phrases stay)."""
    out = block
    if label in ("C++", "C#", "Go", "Java", "Python", "JavaScript", "Rust", "PHP"):
        out = out.replace(_RU_NOT_SUPPORTED, _EN_NOT_SUPPORTED)
    return out


def sync_list_tab_items_from_ru(ru_full: str, en_text: str) -> str:
    """Append missing top-level tab items from RU before the closing ``{% endlist %}``."""
    ru_items = _outer_list_tab_item_blocks(ru_full)
    if not ru_items:
        return en_text
    en_labels = set(list_tab_item_labels(en_text))
    missing = [(lb, blk) for lb, blk in ru_items if lb not in en_labels]
    if not missing:
        return en_text

    lines = _lines(en_text)
    span = _list_tabs_span(lines)
    if span is None:
        return en_text
    insert_idx = span[1]

    chunks = [_localize_tab_block(lb, blk) for lb, blk in missing]
    if not chunks:
        return en_text
    block = "\n\n".join(chunks)
    insert_lines = [""] + block.split("\n") + [""]
    for offset, ln in enumerate(insert_lines):
        lines.insert(insert_idx + offset, ln)
    return "\n".join(lines)


def apply_structure_sync_from_ru(
    ru_full: str,
    en_text: str,
    *,
    en_path: str = "",
    title_for_new_bullet: Callable[[str, str], str] | None = None,
) -> str:
    """Deterministic structural repairs after LLM translation."""
    out = en_text
    if list_tab_item_labels(ru_full):
        out = sync_list_tab_items_from_ru(ru_full, out)
    path = en_path.replace("\\", "/")
    if path.endswith("/index.md") or index_bullets_behind_ru(ru_full, out):
        out = sync_index_bullets_from_ru(
            ru_full, out, title_for_new=title_for_new_bullet
        )
    out = reorder_bullet_links_like_ru(ru_full, out)
    return out
