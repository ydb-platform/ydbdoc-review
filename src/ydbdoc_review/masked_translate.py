"""Mask → translate → unmask pipeline for file-level translation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

from ydbdoc_review.annotated_translate import refine_tab_regions
from ydbdoc_review.config import Settings
from ydbdoc_review.document_mask import (
    PLACEHOLDER_RE,
    MaskRegistry,
    has_broken_placeholder_tokens,
    mask_translatable_text,
    placeholder_sequence_matches,
    restore_missing_placeholders,
    unmask_text,
    validate_placeholders,
)
from ydbdoc_review.document_structure import (
    StructureRegion,
    analyze_document_structure,
)
from ydbdoc_review.document_segments import _is_fence_toggle
from ydbdoc_review.fm_progress import fm_log
from ydbdoc_review.llm import (
    _strip_code_fence,
    call_yandex_responses,
    clamp_max_output_tokens,
    load_masked_document_instructions,
    translation_model_fallbacks,
)
from ydbdoc_review.masked_chunking import chunk_masked_text as _split_masked_text
from ydbdoc_review.placeholder_translate import (
    CopySegment,
    LineUnit,
    _join_segments,
    _unit_id,
    _slice_lines,
    build_placeholder_segments,
    translate_line_units,
)
from ydbdoc_review.table_ast import build_table_row_plan, render_table_row_plan

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_RAW_FENCE_LINE_RE = re.compile(r"^\s*```", re.MULTILINE)


def _segment_regions(
    regions: list[StructureRegion], start_line: int, end_line: int
) -> list[StructureRegion]:
    return [
        r
        for r in regions
        if r.start_line <= end_line and r.end_line >= start_line
    ]


def _force_mask_raw_fences(body: str, registry: MaskRegistry) -> str:
    """Line-based fence mask when regex ``_FENCE_BLOCK_RE`` missed a block."""
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_fence_toggle(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines) and not _is_fence_toggle(lines[i]):
            i += 1
        if i < len(lines):
            i += 1
        block = "\n".join(lines[start:i])
        out.append(registry.reserve("FENCE", block))
    return "\n".join(out)


def _mask_body_for_translate(
    body: str,
    registry: MaskRegistry,
    regions: list[StructureRegion],
    *,
    start_line: int,
    end_line: int,
) -> str:
    """Mask prose for LLM; fenced blocks become ``⟦FENCE:n⟧``, never raw `` ``` ``."""
    _ = _segment_regions(regions, start_line, end_line)
    masked = mask_translatable_text(body, registry, include_fences=True)
    if _RAW_FENCE_LINE_RE.search(masked):
        masked = _force_mask_raw_fences(body, registry)
        masked = mask_translatable_text(
            masked, registry, include_fences=False, mask_links=True
        )
    return masked

@dataclass(frozen=True)
class MaskedTranslateSegment:
    kind: Literal["translate"] = "translate"
    start_line: int = 0
    end_line: int = 0
    source_text: str = ""
    masked_text: str = ""
    action: str = "translate"


MaskedSegment = CopySegment | MaskedTranslateSegment


def _max_chunk_chars() -> int:
    raw = os.environ.get("YDBDOC_MASKED_CHUNK_CHARS", "").strip()
    if not raw:
        raw = os.environ.get("YDBDOC_FILE_TRANSLATE_MAX_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 12_000


def _prose_needs_translation(masked: str, *, source_is_russian: bool) -> bool:
    prose = PLACEHOLDER_RE.sub("", masked)
    if not prose.strip():
        return False
    if source_is_russian:
        if _CYRILLIC_RE.search(prose):
            return True
        return bool(re.search(r"\[[^\]]*[\u0400-\u04FF]", prose))
    return bool(_CYRILLIC_RE.search(prose))


def _segment_action(
    regions: list[StructureRegion], start_line: int, end_line: int
) -> str:
    for r in regions:
        if r.start_line == start_line and r.end_line == end_line:
            return r.action
    for r in regions:
        if r.start_line <= end_line and r.end_line >= start_line:
            return r.action
    return "translate"


def build_masked_segments(
    text: str,
    regions: list[StructureRegion],
    registry: MaskRegistry,
    *,
    source_is_russian: bool = True,
) -> list[MaskedSegment]:
    """COPY regions verbatim; TRANSLATE regions get inline-masked bodies."""
    placeholder_segs = build_placeholder_segments(
        text, regions, source_is_russian=source_is_russian
    )
    out: list[MaskedSegment] = []
    for seg in placeholder_segs:
        if isinstance(seg, CopySegment):
            out.append(seg)
            continue
        body = _slice_lines(text, seg.start_line, seg.end_line)
        masked = _mask_body_for_translate(
            body,
            registry,
            regions,
            start_line=seg.start_line,
            end_line=seg.end_line,
        )
        out.append(
            MaskedTranslateSegment(
                start_line=seg.start_line,
                end_line=seg.end_line,
                source_text=body,
                masked_text=masked,
                action=_segment_action(regions, seg.start_line, seg.end_line),
            )
        )
    return out


def chunk_masked_text_for_translate(
    text: str, *, max_chars: int | None = None
) -> list[str]:
    """Split masked markdown using :func:`masked_chunking.chunk_masked_text`."""
    limit = max_chars if max_chars is not None else _max_chunk_chars()
    return _split_masked_text(text, max_chars=limit)


# Backward-compatible alias for tests and scripts.
chunk_masked_text = chunk_masked_text_for_translate


def _build_masked_user_input(
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    masked: str,
    chunk_index: int,
    chunk_total: int,
    start_line: int,
    end_line: int,
) -> str:
    header = (
        f"File: `{source_path}`\n"
        f"SOURCE language: {source_lang}\n"
        f"TARGET language: {target_lang}\n"
        f"Lines (1-based): {start_line}–{end_line}\n"
    )
    if chunk_total > 1:
        header += f"Chunk: {chunk_index}/{chunk_total}\n"
    return (
        f"{header}\n"
        f"Translate the markdown below. Keep every `⟦KIND:n⟧` placeholder "
        f"byte-identical (count, order, spelling).\n\n"
        f"---\n\n"
        f"{masked}\n"
    )


def translate_masked_chunk(
    settings: Settings,
    masked: str,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk_index: int = 1,
    chunk_total: int = 1,
    start_line: int = 1,
    end_line: int = 1,
) -> str:
    """One LLM call on a masked fragment; returns still-masked text."""
    instructions = load_masked_document_instructions(
        settings, source_lang=source_lang, target_lang=target_lang
    ).strip()
    user_input = _build_masked_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        masked=masked,
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        start_line=start_line,
        end_line=end_line,
    )
    model = settings.model_translate
    cap = clamp_max_output_tokens(
        max(2048, min(len(masked) * 2 + 1024, 32_768)),
        model,
    )
    raw = call_yandex_responses(
        settings,
        model,
        instructions=instructions,
        user_input=user_input,
        max_output_tokens=cap,
        model_fallbacks=translation_model_fallbacks(),
        operation="translate:masked-chunk",
        detail=f"{source_path}:{start_line}-{end_line}:{chunk_index}",
    )
    return _strip_code_fence(raw).strip()


def _translate_table_segment_line_json(
    settings: Settings,
    segment: MaskedTranslateSegment,
    registry: MaskRegistry,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    source_is_russian: bool,
) -> tuple[str, int]:
    """
    Translate table cells as independent units with masked markup.

    This keeps table pipes and cell boundaries byte-stable while allowing prose
    translation inside each cell.
    """
    lines = segment.source_text.splitlines()
    units: list[LineUnit] = []
    plans: dict[int, object] = {}
    for line_no, line in enumerate(lines, start=segment.start_line):
        built = build_table_row_plan(
            line,
            line_no=line_no,
            registry=registry,
            source_is_russian=source_is_russian,
        )
        if built is None:
            continue
        plan, row_units = built
        plans[line_no] = plan
        units.extend(row_units)

    if not units:
        return segment.source_text, 0

    translations = translate_line_units(
        settings,
        units,
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=f"{source_path}#table-cells",
    )

    out: list[str] = []
    for line_no, line in enumerate(lines, start=segment.start_line):
        plan = plans.get(line_no)
        if plan is None:
            out.append(line)
        else:
            out.append(
                render_table_row_plan(
                    plan,
                    translations=translations,
                    registry=registry,
                )
            )
    return "\n".join(out), 1


def _translate_chunk_with_placeholder_guard(
    settings: Settings,
    chunk: str,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk_index: int,
    chunk_total: int,
    start_line: int,
    end_line: int,
) -> str:
    """
    Translate one chunk with a retry when placeholders were corrupted.

    If retry still breaks placeholder structure, return source chunk unchanged.
    """
    attempts = 2
    last = chunk
    for _ in range(attempts):
        out = translate_masked_chunk(
            settings,
            chunk,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
            start_line=start_line,
            end_line=end_line,
        )
        repaired = restore_missing_placeholders(chunk, out)
        missing = validate_placeholders(chunk, repaired)
        broken = has_broken_placeholder_tokens(repaired)
        ordered = placeholder_sequence_matches(chunk, repaired)
        last = repaired
        if not missing and not broken and ordered:
            return repaired
    return chunk if has_broken_placeholder_tokens(last) else last


def translate_masked_segment(
    settings: Settings,
    segment: MaskedTranslateSegment,
    registry: MaskRegistry,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    source_is_russian: bool,
) -> tuple[str, int]:
    """
    Translate one region's masked body and unmask.

    Returns ``(english_text, num_llm_calls)``.
    """
    if segment.action == "translate_table":
        return _translate_table_segment_line_json(
            settings,
            segment,
            registry,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            source_is_russian=source_is_russian,
        )

    if not _prose_needs_translation(segment.masked_text, source_is_russian=source_is_russian):
        return segment.source_text, 0

    chunks = chunk_masked_text_for_translate(segment.masked_text)
    translated_parts: list[str] = []
    llm_calls = 0
    for i, chunk in enumerate(chunks, start=1):
        if _RAW_FENCE_LINE_RE.search(chunk):
            fm_log(
                f"masked-translate raw ``` in chunk {i}/{len(chunks)} | {source_path}"
            )
        fm_log(
            f"masked-translate chunk {i}/{len(chunks)} | {source_path} | "
            f"lines {segment.start_line}-{segment.end_line}"
        )
        out = _translate_chunk_with_placeholder_guard(
            settings,
            chunk,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            chunk_index=i,
            chunk_total=len(chunks),
            start_line=segment.start_line,
            end_line=segment.end_line,
        )
        llm_calls += 1
        missing = validate_placeholders(chunk, out)
        broken = has_broken_placeholder_tokens(out)
        if missing or broken or not placeholder_sequence_matches(chunk, out):
            fm_log(
                f"masked-translate placeholder guard | {source_path} | "
                f"missing={len(missing)} broken={int(broken)}"
            )
        translated_parts.append(out)

    masked_merged = "".join(translated_parts)
    masked_merged = restore_missing_placeholders(segment.masked_text, masked_merged)
    return unmask_text(masked_merged, registry), llm_calls


def translate_with_mask(
    settings: Settings,
    *,
    source_path: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, int]:
    """
    Translate *source_text* via mask → LLM → unmask.

    Returns ``(english_markdown, num_llm_calls)``.
    """
    source_is_russian = source_lang.lower().startswith("rus")
    regions = refine_tab_regions(
        source_text,
        analyze_document_structure(source_text, source_is_russian=source_is_russian),
    )
    registry = MaskRegistry()
    segments = build_masked_segments(
        source_text, regions, registry, source_is_russian=source_is_russian
    )

    parts: list[str] = []
    llm_calls = 0
    for seg in segments:
        if isinstance(seg, CopySegment):
            fm_log(
                f"masked-translate copy | {source_path} | "
                f"lines {seg.start_line}-{seg.end_line}"
            )
            parts.append(seg.text)
            continue
        en_body, n = translate_masked_segment(
            settings,
            seg,
            registry,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            source_is_russian=source_is_russian,
        )
        llm_calls += n
        parts.append(en_body)

    merged = _join_segments(parts)
    if source_is_russian and target_lang.lower().startswith("en"):
        from ydbdoc_review.structural_resync import resync_en_structure_from_ru

        merged = resync_en_structure_from_ru(source_text, merged)
    if source_text.endswith("\n") and merged and not merged.endswith("\n"):
        merged += "\n"
    return merged, llm_calls


def count_masked_stats(segments: list[MaskedSegment]) -> tuple[int, int, int]:
    """Return ``(copy_segments, translate_segments, placeholder_count)``."""
    copy_n = sum(1 for s in segments if isinstance(s, CopySegment))
    tr_n = sum(1 for s in segments if isinstance(s, MaskedTranslateSegment))
    # placeholder count filled by caller with registry size
    return copy_n, tr_n, 0
