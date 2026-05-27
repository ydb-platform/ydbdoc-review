"""Annotated line-based translation: region-aligned chunks and per-chunk prompts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ydbdoc_review.document_structure import StructureRegion, regions_for_line_range
from ydbdoc_review.list_tabs_blocks import list_tabs_block_copy_verbatim

_COPY_ACTIONS = frozenset({"copy_verbatim", "fence_comments"})
_TRANSLATE_ACTIONS = frozenset(
    {"translate", "translate_diplodoc", "translate_table", "translate_tabs"}
)

_REGION_TAG: dict[str, str] = {
    "prose": "PROSE",
    "fence": "FENCE",
    "note": "NOTE",
    "cut": "CUT",
    "table": "TABLE",
    "tabs": "TABS",
}

_ACTION_INSTRUCTION: dict[str, str] = {
    "translate": "перевести prose на TARGET (заголовки, списки, ссылки — по правилам)",
    "translate_diplodoc": "перевести только текст внутри note/cut; директивы {% %} не менять",
    "translate_table": "перевести текст ячеек; ✓ и идентификаторы типов не сдвигать",
    "translate_tabs": "перевести prose и метки вкладок; каждый fenced-блок внутри — дословно",
    "copy_verbatim": "скопировать дословно из SOURCE (байт-в-байт для этих строк)",
    "fence_comments": "скопировать код дословно; перевести только комментарии на указанных строках",
}


@dataclass(frozen=True)
class AnnotatedChunk:
    """One translation unit: whole regions only, never split mid-fence or mid-tab."""

    index: int
    total: int
    start_line: int
    end_line: int
    regions: list[StructureRegion]

    def needs_llm(self) -> bool:
        return any(r.action in _TRANSLATE_ACTIONS for r in self.regions)

    def copy_only(self) -> bool:
        return self.needs_llm() is False


def _max_chunk_chars() -> int:
    raw = os.environ.get("YDBDOC_FILE_TRANSLATE_MAX_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 12_000


def _slice_lines(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    s = max(1, start_line) - 1
    e = min(len(lines), end_line)
    return "\n".join(lines[s:e])


def _fence_lang(line: str) -> str:
    m = re.match(r"^\s*```(\S+)", line.strip())
    return m.group(1) if m else "code"


def summarize_chunk_regions(
    text: str, regions: list[StructureRegion]
) -> str:
    lines_out: list[str] = []
    for r in regions:
        tag = _REGION_TAG.get(r.kind, r.kind.upper())
        action = _ACTION_INSTRUCTION.get(r.action, r.detail)
        if r.kind == "fence":
            block = _slice_lines(text, r.start_line, r.end_line)
            first = block.splitlines()[0] if block else "```"
            lang = _fence_lang(first)
            extra = f" ({lang})"
        elif r.kind == "tabs":
            extra = " (config verbatim)" if r.action == "copy_verbatim" else " (manual translate)"
        else:
            extra = ""
        lines_out.append(
            f"  {r.start_line:5d}-{r.end_line:<5d}  {tag:6s}{extra}  → {action}"
        )
    return "\n".join(lines_out)


def format_annotated_source(
    text: str,
    regions: list[StructureRegion],
    *,
    start_line: int,
    end_line: int,
) -> str:
    """
    Numbered SOURCE with inline region markers at region boundaries.

    Example::

        L0076  @BEGIN FENCE bash | COPY_VERBATIM
        L0076| ```bash
        ...
        L0079  @END FENCE
    """
    clipped = regions_for_line_range(regions, start_line, end_line)
    region_by_line: dict[int, StructureRegion] = {}
    for r in clipped:
        for ln in range(r.start_line, r.end_line + 1):
            region_by_line[ln] = r

    lines = text.splitlines()
    out: list[str] = []
    prev_r: StructureRegion | None = None
    for ln in range(start_line, end_line + 1):
        r = region_by_line.get(ln)
        if r is not None and r != prev_r:
            tag = _REGION_TAG.get(r.kind, r.kind)
            act = "COPY" if r.action in _COPY_ACTIONS else "TRANSLATE"
            lang = ""
            if r.kind == "fence":
                lang = " " + _fence_lang(lines[ln - 1])
            out.append(
                f"L{ln:05d}  @BEGIN {tag}{lang} | {act} | lines {r.start_line}-{r.end_line}"
            )
            prev_r = r
        body = lines[ln - 1] if ln <= len(lines) else ""
        out.append(f"L{ln:05d}| {body}")
        if r is not None and ln == r.end_line:
            out.append(f"L{ln:05d}  @END {tag}")
            prev_r = None
    return "\n".join(out)


def split_oversized_prose_region(
    region: StructureRegion, text: str, *, max_chars: int
) -> list[StructureRegion]:
    """Split a prose region only at blank-line boundaries (never mid-paragraph)."""
    if region.kind != "prose" or region.action != "translate":
        return [region]
    body = _slice_lines(text, region.start_line, region.end_line)
    if len(body) <= max_chars:
        return [region]

    lines = body.splitlines()
    base = region.start_line
    parts: list[StructureRegion] = []
    chunk_start = 0
    chunk_len = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        line_len = len(line) + 1
        if (
            chunk_len + line_len > max_chars
            and chunk_start < i
            and not line.strip()
        ):
            parts.append(
                StructureRegion(
                    start_line=base + chunk_start,
                    end_line=base + i - 1,
                    kind="prose",
                    action="translate",
                    detail=region.detail,
                )
            )
            chunk_start = i
            chunk_len = 0
        chunk_len += line_len
        i += 1
    if chunk_start < len(lines):
        parts.append(
            StructureRegion(
                start_line=base + chunk_start,
                end_line=base + len(lines) - 1,
                kind="prose",
                action="translate",
                detail=region.detail,
            )
        )
    return parts if parts else [region]


def build_annotated_chunks(
    text: str,
    regions: list[StructureRegion],
    *,
    max_chars: int | None = None,
) -> list[AnnotatedChunk]:
    """
    Pack whole regions into chunks; never split inside a region.

    Oversized prose is split only on blank lines. Single non-prose regions
    larger than *max_chars* still form one chunk (one LLM call).
    """
    budget = max_chars if max_chars is not None else _max_chunk_chars()
    if not text.strip():
        return []

    expanded: list[StructureRegion] = []
    for r in regions:
        if r.kind == "prose" and len(_slice_lines(text, r.start_line, r.end_line)) > budget:
            expanded.extend(split_oversized_prose_region(r, text, max_chars=budget))
        else:
            expanded.append(r)

    if not expanded:
        n = len(text.splitlines()) or 1
        expanded = [
            StructureRegion(1, n, "prose", "translate", "Translate entire file.")
        ]

    chunks: list[AnnotatedChunk] = []
    batch: list[StructureRegion] = []
    batch_start = expanded[0].start_line
    batch_end = expanded[0].end_line
    batch_chars = 0

    def flush() -> None:
        nonlocal batch, batch_start, batch_end, batch_chars
        if not batch:
            return
        chunks.append(
            AnnotatedChunk(
                index=0,
                total=0,
                start_line=batch_start,
                end_line=batch_end,
                regions=list(batch),
            )
        )
        batch = []
        batch_chars = 0

    lines = text.splitlines()
    n = len(lines)

    def _starts_h2(line_no: int) -> bool:
        idx = line_no - 1
        return 0 <= idx < n and lines[idx].startswith("## ")

    def _needs_llm(r: StructureRegion) -> bool:
        return r.action in _TRANSLATE_ACTIONS

    for reg in expanded:
        reg_text = _slice_lines(text, reg.start_line, reg.end_line)
        reg_len = len(reg_text)
        batch_llm = any(_needs_llm(r) for r in batch)
        if batch and _needs_llm(reg) != batch_llm:
            flush()
            batch_start = reg.start_line
        if batch and _starts_h2(reg.start_line):
            flush()
            batch_start = reg.start_line
        if batch and batch_chars + reg_len > budget:
            flush()
            batch_start = reg.start_line
        if not batch:
            batch_start = reg.start_line
        batch.append(reg)
        batch_end = reg.end_line
        batch_chars += reg_len + 2

    flush()

    total = len(chunks)
    return [
        AnnotatedChunk(
            index=i + 1,
            total=total,
            start_line=c.start_line,
            end_line=c.end_line,
            regions=c.regions,
        )
        for i, c in enumerate(chunks)
    ]


def merge_copy_regions_from_source(
    ru_chunk: str,
    en_chunk: str,
    regions: list[StructureRegion],
    *,
    chunk_start_line: int,
) -> str:
    """Overwrite COPY_VERBATIM / fence lines in EN with exact RU bytes."""
    ru_lines = ru_chunk.splitlines()
    en_lines = en_chunk.splitlines()
    if len(en_lines) < len(ru_lines):
        en_lines.extend([""] * (len(ru_lines) - len(en_lines)))
    elif len(en_lines) > len(ru_lines):
        en_lines = en_lines[: len(ru_lines)]

    for r in regions:
        if r.action not in _COPY_ACTIONS:
            continue
        for ln in range(r.start_line, r.end_line + 1):
            idx = ln - chunk_start_line
            if 0 <= idx < len(ru_lines):
                en_lines[idx] = ru_lines[idx]
    return "\n".join(en_lines)


def tabs_region_action(text: str, region: StructureRegion) -> StructureRegion:
    """Set copy_verbatim vs translate_tabs for a list-tabs region."""
    block = _slice_lines(text, region.start_line, region.end_line)
    if list_tabs_block_copy_verbatim(block):
        return StructureRegion(
            start_line=region.start_line,
            end_line=region.end_line,
            kind=region.kind,
            action="copy_verbatim",
            detail="Config/SDK tabs: copy entire {% list tabs %} block verbatim from SOURCE.",
        )
    return StructureRegion(
        start_line=region.start_line,
        end_line=region.end_line,
        kind=region.kind,
        action="translate_tabs",
        detail="Translate tab labels and prose; copy every fenced block inside verbatim.",
    )


def refine_tab_regions(text: str, regions: list[StructureRegion]) -> list[StructureRegion]:
    return [
        tabs_region_action(text, r) if r.kind == "tabs" else r for r in regions
    ]
