# ruff: noqa: RUF001
"""Collect residual Cyrillic in EN prose and inline backticks."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
_BACKTICK_CYR = re.compile(r"`([^`]*[а-яА-ЯёЁ][^`]*)`")
_CYRILLIC_WORD = re.compile(r"[а-яА-ЯёЁ][а-яА-ЯёЁ\-]*")


@dataclass(frozen=True)
class ProseCyrillicSpan:
    span_id: str
    text: str
    context: str


def _inside_backtick(line: str, index: int) -> bool:
    before = line[:index]
    return before.count("`") % 2 == 1


def collect_cyrillic_prose_spans(text: str) -> list[ProseCyrillicSpan]:
    """Ordered Cyrillic snippets in prose (fenced bodies excluded)."""
    found: list[ProseCyrillicSpan] = []
    seen: set[str] = set()
    in_fence = False
    fence_char = ""
    span_no = 0

    for line in text.splitlines():
        m = _FENCE_OPEN.match(line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if in_fence or not _CYRILLIC.search(line):
            continue

        for match in _BACKTICK_CYR.finditer(line):
            inner = match.group(1).strip()
            if not inner or inner in seen:
                continue
            seen.add(inner)
            span_no += 1
            found.append(
                ProseCyrillicSpan(
                    span_id=f"p{span_no}",
                    text=inner,
                    context=line.strip()[:240],
                )
            )

        for match in _CYRILLIC_WORD.finditer(line):
            if _inside_backtick(line, match.start()):
                continue
            word = match.group(0)
            if word in seen:
                continue
            seen.add(word)
            span_no += 1
            found.append(
                ProseCyrillicSpan(
                    span_id=f"p{span_no}",
                    text=word,
                    context=line.strip()[:240],
                )
            )

    return found
