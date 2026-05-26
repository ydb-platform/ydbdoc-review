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


@dataclass(frozen=True)
class TextSegment:
    kind: Literal["prose", "list_tabs_verbatim", "list_tabs_translate"]
    text: str


def list_tabs_block_copy_verbatim(block: str) -> bool:
    """
    True for config-style tabs (ASCII labels + YAML only).

    Blocks with Cyrillic outside fences (e.g. ``- Вручную`` manual/systemd tabs)
    must be translated, not copied from RU.
    """
    in_fence = False
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and _CYRILLIC_RE.search(line):
            return False
    return True


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
