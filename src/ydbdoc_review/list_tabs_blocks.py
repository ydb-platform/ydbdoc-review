"""Split markdown around ``{% list tabs %}…{% endlist %}`` blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_LIST_TABS_BLOCK_RE = re.compile(
    r"(\{%\s*list\s+tabs[\s\S]*?\{%\s*endlist\s*%\})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TextSegment:
    kind: Literal["prose", "list_tabs"]
    text: str


def split_preserving_list_tabs(text: str) -> list[TextSegment]:
    """
    Split *text* into prose and list-tabs segments.

    List-tabs blocks are kept verbatim (YAML + tab labels are locale-neutral).
    """
    if not text:
        return []
    segments: list[TextSegment] = []
    last = 0
    for match in _LIST_TABS_BLOCK_RE.finditer(text):
        if match.start() > last:
            segments.append(TextSegment("prose", text[last : match.start()]))
        segments.append(TextSegment("list_tabs", match.group(1)))
        last = match.end()
    if last < len(text):
        segments.append(TextSegment("prose", text[last:]))
    if not segments:
        return [TextSegment("prose", text)]
    return segments
