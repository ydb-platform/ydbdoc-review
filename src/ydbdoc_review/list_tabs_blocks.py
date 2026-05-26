"""Split markdown around ``{% list tabs %}…{% endlist %}`` blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_LIST_TABS_BLOCK_RE = re.compile(
    r"(\{%\s*list\s+tabs[\s\S]*?\{%\s*endlist\s*%\})",
    re.IGNORECASE,
)
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_TAB_LABEL_LINE_RE = re.compile(r"^-\s+([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$")
_KNOWN_SDK_TAB_NAMES = frozenset(
    {
        "oss",
        "go",
        "python",
        "java",
        "javascript",
        "csharp",
        "node",
        "php",
        "ruby",
    }
)


@dataclass(frozen=True)
class TextSegment:
    kind: Literal["prose", "list_tabs_verbatim", "list_tabs_translate"]
    text: str


def _is_config_style_tab_label(line: str) -> bool:
    """Config tab ids (``mirror-3-dc-3nodes``, ``block-4-2``, ``OSS``), not ``Manually``."""
    m = _TAB_LABEL_LINE_RE.match(line)
    if not m:
        return False
    name = m.group(1)
    if _CYRILLIC_RE.search(name):
        return False
    if name.lower() in _KNOWN_SDK_TAB_NAMES:
        return True
    if "-" in name and re.search(r"\d", name):
        return True
    return False


def list_tabs_block_copy_verbatim(block: str) -> bool:
    """
    True for config-style tabs (ASCII ids + YAML only).

    Blocks with Cyrillic or manual tab names (``- Вручную``, ``- Manually``)
    must be translated, not copied from RU.
    """
    in_fence = False
    has_config_label = False
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and _CYRILLIC_RE.search(line):
            return False
        if not in_fence and _TAB_LABEL_LINE_RE.match(line):
            if _is_config_style_tab_label(line):
                has_config_label = True
            else:
                return False
    return has_config_label


def split_preserving_list_tabs(text: str) -> list[TextSegment]:
    """
    Split *text* into prose and list-tabs segments.

    Config tabs (no Cyrillic outside fences) are tagged ``list_tabs_verbatim``;
    instructional tabs (Russian labels/prose) — ``list_tabs_translate``.
    """
    if not text:
        return []
    segments: list[TextSegment] = []
    last = 0
    for match in _LIST_TABS_BLOCK_RE.finditer(text):
        if match.start() > last:
            segments.append(TextSegment("prose", text[last : match.start()]))
        block = match.group(1)
        kind: Literal["list_tabs_verbatim", "list_tabs_translate"] = (
            "list_tabs_verbatim"
            if list_tabs_block_copy_verbatim(block)
            else "list_tabs_translate"
        )
        segments.append(TextSegment(kind, block))
        last = match.end()
    if last < len(text):
        segments.append(TextSegment("prose", text[last:]))
    if not segments:
        return [TextSegment("prose", text)]
    return segments
