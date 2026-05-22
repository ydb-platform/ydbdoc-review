"""Split markdown into translatable prose vs preserved blocks (fences, Liquid)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

BlockKind = Literal["prose", "fence", "liquid"]


@dataclass(frozen=True)
class MarkdownBlock:
    kind: BlockKind
    text: str


def _is_fence_toggle(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") and len(s) >= 3


def _liquid_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("{%") or s.startswith("{#")


def split_markdown_blocks(text: str) -> list[MarkdownBlock]:
    """
    Split *text* into alternating blocks.

    - ``fence``: inside ``` … ``` (verbatim, including language tag line)
    - ``liquid``: consecutive ``{% … %}`` / ``{# … #}`` lines outside fences
    - ``prose``: everything else (sent to the translation model)
    """
    if not text:
        return []

    lines = text.split("\n")
    blocks: list[MarkdownBlock] = []
    buf: list[str] = []
    kind: BlockKind = "prose"
    in_fence = False

    def flush() -> None:
        nonlocal buf, kind
        if not buf:
            return
        blocks.append(MarkdownBlock(kind=kind, text="\n".join(buf)))
        buf = []

    for line in lines:
        if _is_fence_toggle(line):
            if in_fence:
                buf.append(line)
                flush()
                in_fence = False
                kind = "prose"
                continue
            flush()
            in_fence = True
            kind = "fence"
            buf.append(line)
            continue

        if in_fence:
            buf.append(line)
            continue

        if _liquid_line(line):
            if kind == "prose" and buf:
                flush()
            if kind != "liquid":
                flush()
                kind = "liquid"
            buf.append(line)
            continue

        if kind == "liquid" and buf:
            flush()
            kind = "prose"

        buf.append(line)

    flush()
    return blocks


def join_markdown_blocks(blocks: list[MarkdownBlock]) -> str:
    if not blocks:
        return ""
    return "\n".join(b.text for b in blocks)


_PLACEHOLDER = "⟦YDBDOC_BLOCK_{i}⟧"


def mask_non_prose_for_translate(text: str) -> tuple[str, list[MarkdownBlock]]:
    """
    Replace fence/liquid blocks with placeholders so the model sees structure
    but must not alter fenced content.
    """
    blocks = split_markdown_blocks(text)
    masked_parts: list[str] = []
    preserved: list[MarkdownBlock] = []
    idx = 0
    for b in blocks:
        if b.kind == "prose":
            masked_parts.append(b.text)
        else:
            ph = _PLACEHOLDER.format(i=idx)
            preserved.append(b)
            masked_parts.append(ph)
            idx += 1
    return "\n".join(masked_parts), preserved


def unmask_translated_prose(translated: str, preserved: list[MarkdownBlock]) -> str:
    """Restore fence/liquid blocks after translating masked prose."""
    out = translated
    for i, b in enumerate(preserved):
        ph = _PLACEHOLDER.format(i=i)
        if ph not in out:
            continue
        out = out.replace(ph, b.text, 1)
    return out


def prose_mask_enabled() -> bool:
    import os

    raw = os.environ.get("YDBDOC_TRANSLATE_PRESERVE_FENCES", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")
