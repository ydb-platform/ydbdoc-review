"""Split markdown into translatable prose vs preserved blocks (fences, Liquid)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

BlockKind = Literal["prose", "fence", "liquid"]

_BLOCK_PH_RE = re.compile(r"⟦YDBDOC_BLOCK_\d+[^⟧\n]*⟧?", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")


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


def prose_mask_enabled() -> bool:
    import os

    raw = os.environ.get("YDBDOC_TRANSLATE_PRESERVE_FENCES", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def has_translation_artifacts(text: str) -> bool:
    if _BLOCK_PH_RE.search(text) or "YDBDOC_BLOCK" in text:
        return True
    if "\ufffd" in text:
        return True
    lines = text.splitlines()
    h1 = [ln.strip() for ln in lines if ln.startswith("# ") and not ln.startswith("## ")]
    return len(h1) >= 2 and h1[0] == h1[1]


def strip_block_placeholder_lines(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if "YDBDOC_BLOCK" in line or _BLOCK_PH_RE.search(line):
            continue
        out.append(line.replace("\ufffd", ""))
    return "\n".join(out)


def dedupe_duplicate_h1_block(text: str) -> str:
    """Remove a duplicated title/intro block left by a failed placeholder unmask."""
    lines = text.splitlines()
    h1_idxs = [i for i, ln in enumerate(lines) if ln.startswith("# ") and not ln.startswith("## ")]
    if len(h1_idxs) < 2:
        return strip_block_placeholder_lines(text)
    title = lines[h1_idxs[0]].strip()
    if lines[h1_idxs[1]].strip() != title:
        return strip_block_placeholder_lines(text)
    end = h1_idxs[1]
    for j in range(h1_idxs[1], len(lines)):
        if "YDBDOC_BLOCK" in lines[j] or _BLOCK_PH_RE.search(lines[j] or ""):
            end = j + 1
            break
        if j > h1_idxs[1] + 2 and lines[j].startswith("## "):
            end = j
            break
    merged = lines[: h1_idxs[1]] + lines[end:]
    return strip_block_placeholder_lines("\n".join(merged))


def realign_en_prose_with_ru_blocks(ru_source: str, en_text: str) -> str:
    """
    Reassemble EN using RU fence/Liquid blocks verbatim and EN prose in order.

    Fixes leaked placeholders and mis-spliced chunks without calling an LLM.
    """
    ru_blocks = split_markdown_blocks(ru_source)
    en_blocks = split_markdown_blocks(en_text)
    en_prose = [
        strip_block_placeholder_lines(b.text)
        for b in en_blocks
        if b.kind == "prose" and strip_block_placeholder_lines(b.text).strip()
    ]
    out: list[MarkdownBlock] = []
    pi = 0
    for rb in ru_blocks:
        if rb.kind != "prose":
            out.append(rb)
            continue
        if pi < len(en_prose):
            out.append(MarkdownBlock("prose", en_prose[pi]))
            pi += 1
        else:
            out.append(rb)
    return join_markdown_blocks(out)


def _fence_blocks(text: str) -> list[MarkdownBlock]:
    return [b for b in split_markdown_blocks(text) if b.kind == "fence"]


def repair_cyrillic_in_fences_from_ru(
    settings: object,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
) -> str:
    """Re-translate fenced blocks that still contain Cyrillic (e.g. SQL ``--`` comments)."""
    if not _CYRILLIC_RE.search(en_text):
        return en_text
    from ydbdoc_review.llm import translate_ru_block_to_en

    ru_fences = _fence_blocks(ru_full)
    fi = 0
    out_blocks: list[MarkdownBlock] = []
    for block in split_markdown_blocks(en_text):
        if block.kind != "fence" or not _CYRILLIC_RE.search(block.text):
            out_blocks.append(block)
            continue
        ru_block = ru_fences[fi].text if fi < len(ru_fences) else block.text
        fi += 1
        if _CYRILLIC_RE.search(ru_block):
            out_blocks.append(
                MarkdownBlock(
                    "fence",
                    translate_ru_block_to_en(
                        settings,
                        ru_path=ru_path,
                        ru_block=ru_block,
                    ).strip(),
                )
            )
        else:
            out_blocks.append(block)
    return join_markdown_blocks(out_blocks)


def repair_block_translation_artifacts(ru_source: str, en_text: str) -> str:
    if not has_translation_artifacts(en_text):
        return en_text
    out = dedupe_duplicate_h1_block(en_text)
    out = realign_en_prose_with_ru_blocks(ru_source, out)
    return dedupe_duplicate_h1_block(out)


def translate_preserving_blocks(
    source_text: str,
    translate_prose: Callable[[str], str],
) -> str:
    """Translate only ``prose`` blocks; copy fence/Liquid blocks from source unchanged."""
    blocks = split_markdown_blocks(source_text)
    out: list[MarkdownBlock] = []
    for block in blocks:
        if block.kind != "prose" or not block.text.strip():
            out.append(block)
            continue
        out.append(MarkdownBlock("prose", translate_prose(block.text).strip()))
    return join_markdown_blocks(out)


# Legacy helpers (tests / backwards compatibility)
def mask_non_prose_for_translate(text: str) -> tuple[str, list[MarkdownBlock]]:
    blocks = split_markdown_blocks(text)
    masked_parts: list[str] = []
    preserved: list[MarkdownBlock] = []
    idx = 0
    for b in blocks:
        if b.kind == "prose":
            masked_parts.append(b.text)
        else:
            ph = f"⟦YDBDOC_BLOCK_{idx}⟧"
            preserved.append(b)
            masked_parts.append(ph)
            idx += 1
    return "\n".join(masked_parts), preserved


def unmask_translated_prose(translated: str, preserved: list[MarkdownBlock]) -> str:
    out = translated
    for i, b in enumerate(preserved):
        ph = f"⟦YDBDOC_BLOCK_{i}⟧"
        if ph in out:
            out = out.replace(ph, b.text, 1)
    return out
