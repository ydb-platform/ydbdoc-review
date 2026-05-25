"""Restore ``{% list tabs %}`` tab labels from SOURCE."""

from __future__ import annotations

import re

_LIST_TABS_OPEN_RE = re.compile(r"\{%\s*list\s+tabs", re.IGNORECASE)
_LIST_TABS_CLOSE_RE = re.compile(r"\{%\s*endlist\s*%\}", re.IGNORECASE)
_TAB_LABEL_LINE_RE = re.compile(
    r"^(\s*-\s+)([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$"
)


def _tab_label_lines(text: str) -> list[str]:
    """Ordered tab label lines (e.g. ``- mirror-3-dc-3nodes``) inside list-tabs blocks."""
    out: list[str] = []
    in_tabs = False
    for line in text.splitlines():
        if _LIST_TABS_OPEN_RE.search(line):
            in_tabs = True
            continue
        if in_tabs and _LIST_TABS_CLOSE_RE.search(line):
            in_tabs = False
            continue
        if in_tabs and _TAB_LABEL_LINE_RE.match(line):
            out.append(line.rstrip())
    return out


def is_tab_label_line(line: str) -> bool:
    return bool(_TAB_LABEL_LINE_RE.match(line))


def _repair_block(ru_block: str, en_block: str) -> tuple[str, bool]:
    ru_labels = [ln for ln in ru_block.splitlines() if _TAB_LABEL_LINE_RE.match(ln)]
    if not ru_labels:
        return en_block, False

    en_lines = en_block.splitlines()
    out: list[str] = []
    label_i = 0
    changed = False
    in_fence = False
    i = 0
    while i < len(en_lines):
        line = en_lines[i]
        if _TAB_LABEL_LINE_RE.match(line):
            if label_i < len(ru_labels):
                fixed = ru_labels[label_i]
                if line.rstrip() != fixed:
                    changed = True
                out.append(fixed)
                label_i += 1
            else:
                out.append(line)
            i += 1
            continue
        if line.lstrip().startswith("```"):
            if not in_fence and label_i < len(ru_labels):
                out.append(ru_labels[label_i])
                label_i += 1
                changed = True
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1

    return "\n".join(out), changed or label_i < len(ru_labels)


def repair_tab_labels_from_source(source: str, translation: str) -> tuple[str, bool]:
    """Sync tab label lines in EN list-tabs blocks with RU (order and spelling)."""
    if _tab_label_lines(source) == _tab_label_lines(translation):
        return translation, False

    ru_parts = re.split(r"(\{%\s*list\s+tabs[\s\S]*?\{%\s*endlist\s*%\})", source, flags=re.IGNORECASE)
    en_parts = re.split(r"(\{%\s*list\s+tabs[\s\S]*?\{%\s*endlist\s*%\})", translation, flags=re.IGNORECASE)

    if len(ru_parts) != len(en_parts):
        return _repair_whole_file(source, translation)

    changed = False
    out_parts: list[str] = []
    for ru_p, en_p in zip(ru_parts, en_parts, strict=False):
        if _LIST_TABS_OPEN_RE.search(ru_p):
            fixed, block_changed = _repair_block(ru_p, en_p)
            out_parts.append(fixed)
            changed = changed or block_changed
        else:
            out_parts.append(en_p)
    return "".join(out_parts), changed


def _repair_whole_file(source: str, translation: str) -> tuple[str, bool]:
    ru_labels = _tab_label_lines(source)
    if not ru_labels:
        return translation, False
    en_lines = translation.splitlines()
    out: list[str] = []
    label_i = 0
    changed = False
    in_tabs = False
    for line in en_lines:
        if _LIST_TABS_OPEN_RE.search(line):
            in_tabs = True
            out.append(line)
            continue
        if in_tabs and _LIST_TABS_CLOSE_RE.search(line):
            while label_i < len(ru_labels):
                out.append(ru_labels[label_i])
                label_i += 1
                changed = True
            in_tabs = False
            out.append(line)
            continue
        if in_tabs and _TAB_LABEL_LINE_RE.match(line):
            if label_i < len(ru_labels) and line.rstrip() != ru_labels[label_i]:
                out.append(ru_labels[label_i])
                changed = True
            else:
                out.append(line.rstrip() if label_i < len(ru_labels) else line)
            if label_i < len(ru_labels):
                label_i += 1
            continue
        if in_tabs and line.lstrip().startswith("```") and label_i < len(ru_labels):
            out.append(ru_labels[label_i])
            label_i += 1
            changed = True
        out.append(line)
    return "\n".join(out), changed
