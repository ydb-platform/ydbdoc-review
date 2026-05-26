"""Restore ``{% list tabs %}`` tab labels from SOURCE."""

from __future__ import annotations

import re

from ydbdoc_review.list_tabs_blocks import list_tabs_block_copy_verbatim

_LIST_TABS_OPEN_RE = re.compile(r"\{%\s*list\s+tabs", re.IGNORECASE)
_LIST_TABS_CLOSE_RE = re.compile(r"\{%\s*endlist\s*%\}", re.IGNORECASE)
# Diplodoc tab label at column 0: ``- mirror-3-dc-3nodes`` (not indented YAML ``  - legacy``).
_TAB_LABEL_LINE_RE = re.compile(
    r"^-\s+([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$"
)


def _tab_label_lines(text: str) -> list[str]:
    """Ordered tab label lines inside list-tabs blocks (top-level ``- id`` only)."""
    out: list[str] = []
    in_tabs = False
    in_fence = False
    for line in text.splitlines():
        if _LIST_TABS_OPEN_RE.search(line):
            in_tabs = True
            in_fence = False
            continue
        if in_tabs and _LIST_TABS_CLOSE_RE.search(line):
            in_tabs = False
            in_fence = False
            continue
        if not in_tabs:
            continue
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and _TAB_LABEL_LINE_RE.match(line):
            out.append(line.rstrip())
    return out


def is_tab_label_line(line: str) -> bool:
    return bool(_TAB_LABEL_LINE_RE.match(line))


def _repair_block(ru_block: str, en_block: str) -> tuple[str, bool]:
    if list_tabs_block_copy_verbatim(ru_block):
        if ru_block == en_block:
            return en_block, False
        return ru_block, True

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
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
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
        out.append(line)
        i += 1

    return "\n".join(out), changed or label_i < len(ru_labels)


def repair_tab_labels_from_source(source: str, translation: str) -> tuple[str, bool]:
    """Sync tab label lines in EN list-tabs blocks with RU (manual/systemd tabs only)."""
    ru_parts = re.split(
        r"(\{%\s*list\s+tabs[\s\S]*?\{%\s*endlist\s*%\})", source, flags=re.IGNORECASE
    )
    en_parts = re.split(
        r"(\{%\s*list\s+tabs[\s\S]*?\{%\s*endlist\s*%\})",
        translation,
        flags=re.IGNORECASE,
    )

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
    in_fence = False
    for line in en_lines:
        if _LIST_TABS_OPEN_RE.search(line):
            in_tabs = True
            in_fence = False
            out.append(line)
            continue
        if in_tabs and _LIST_TABS_CLOSE_RE.search(line):
            while label_i < len(ru_labels):
                out.append(ru_labels[label_i])
                label_i += 1
                changed = True
            in_tabs = False
            in_fence = False
            out.append(line)
            continue
        if not in_tabs:
            out.append(line)
            continue
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        if _TAB_LABEL_LINE_RE.match(line):
            if label_i < len(ru_labels) and line.rstrip() != ru_labels[label_i]:
                out.append(ru_labels[label_i])
                changed = True
            else:
                out.append(line.rstrip() if label_i < len(ru_labels) else line)
            if label_i < len(ru_labels):
                label_i += 1
            continue
        out.append(line)
    return "\n".join(out), changed
