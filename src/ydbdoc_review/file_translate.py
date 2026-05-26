"""File-level translation: structure plan + one or few LLM requests per file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ydbdoc_review.config import Settings
from ydbdoc_review.document_structure import (
    StructureRegion,
    analyze_document_structure,
    format_region_plan,
    split_by_h3_sections,
)
from ydbdoc_review.fm_progress import fm_log
from ydbdoc_review.llm import (
    _strip_code_fence,
    call_yandex_responses,
    clamp_max_output_tokens,
    load_translate_file_instructions,
)
from ydbdoc_review.translate_postprocess import (
    apply_en_postprocess_from_ru,
    fix_yandex_cloud_links_for_en,
)
from ydbdoc_review.list_tabs_blocks import split_preserving_list_tabs
from ydbdoc_review.tabs_repair import repair_tab_labels_from_source
from ydbdoc_review.translate_scope import TranslateScope, compute_translate_scope

_STRIP_REQUEST_HEADER_RE = re.compile(
    r"^#\s*Translation request.*\n+",
    re.IGNORECASE | re.MULTILINE,
)

_FENCE_OPEN_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class _MaskedChunk:
    masked_text: str
    fence_blocks: dict[str, str]


def _mask_fences(source_text: str) -> _MaskedChunk:
    """Replace fenced blocks with placeholders so the model cannot mutate code."""
    lines = source_text.splitlines()
    out: list[str] = []
    fence_blocks: dict[str, str] = {}
    i = 0
    fence_i = 0

    while i < len(lines):
        line = lines[i]
        if _FENCE_OPEN_RE.match(line.strip()):
            start = i
            i += 1
            while i < len(lines):
                if _FENCE_OPEN_RE.match(lines[i].strip()):
                    i += 1
                    break
                i += 1
            block = "\n".join(lines[start:i])
            fence_i += 1
            key = f"<<FENCE_BLOCK_{fence_i:03d}>>"
            fence_blocks[key] = block
            out.append(key)
            continue
        out.append(line)
        i += 1

    return _MaskedChunk(masked_text="\n".join(out), fence_blocks=fence_blocks)


def _unmask_fences(text: str, masked: _MaskedChunk) -> str:
    out = text
    for key, val in masked.fence_blocks.items():
        out = out.replace(key, val)
    return out


@dataclass(frozen=True)
class TranslateChunk:
    """One LLM request covering a contiguous line range."""

    index: int
    total: int
    start_line: int
    end_line: int
    source_text: str
    regions: list[StructureRegion]


def _max_chunk_chars() -> int:
    raw = os.environ.get("YDBDOC_FILE_TRANSLATE_MAX_CHARS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 28_000


def _slice_lines(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    s = max(1, start_line) - 1
    e = min(len(lines), end_line)
    return "\n".join(lines[s:e])


def build_translate_chunks(
    text: str,
    regions: list[StructureRegion],
    *,
    max_chars: int | None = None,
) -> list[TranslateChunk]:
    """
    Split *text* into chunks that respect region boundaries and *max_chars*.

    Never splits inside a single :class:`StructureRegion`.
    """
    budget = max_chars if max_chars is not None else _max_chunk_chars()
    if not text.strip():
        return []

    lines = text.splitlines()
    n = len(lines)
    if not regions:
        regions = [
            StructureRegion(
                1,
                n,
                "prose",
                "translate",
                "Translate entire fragment.",
            )
        ]

    chunks: list[TranslateChunk] = []
    batch_regions: list[StructureRegion] = []
    batch_start = regions[0].start_line
    batch_end = regions[0].end_line
    batch_chars = 0

    def flush_batch() -> None:
        nonlocal batch_regions, batch_start, batch_end, batch_chars
        if not batch_regions:
            return
        src = _slice_lines(text, batch_start, batch_end)
        chunks.append(
            TranslateChunk(
                index=0,
                total=0,
                start_line=batch_start,
                end_line=batch_end,
                source_text=src,
                regions=list(batch_regions),
            )
        )
        batch_regions = []
        batch_chars = 0

    for reg in regions:
        reg_text = _slice_lines(text, reg.start_line, reg.end_line)
        reg_len = len(reg_text)
        if batch_regions and batch_chars + reg_len > budget:
            flush_batch()
            batch_start = reg.start_line
        if not batch_regions:
            batch_start = reg.start_line
        batch_regions.append(reg)
        batch_end = reg.end_line
        batch_chars += reg_len + 2

    flush_batch()

    total = len(chunks)
    return [
        TranslateChunk(
            index=i + 1,
            total=total,
            start_line=c.start_line,
            end_line=c.end_line,
            source_text=c.source_text,
            regions=c.regions,
        )
        for i, c in enumerate(chunks)
    ]


def _build_chunk_user_input(
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk: TranslateChunk,
    plan_header: str,
) -> str:
    plan = format_region_plan(chunk.regions)
    req = (
        f"## Translation request {chunk.index} of {chunk.total}\n\n"
        f"File: `{source_path}`\n"
        f"SOURCE language: {source_lang}\n"
        f"TARGET language: {target_lang}\n"
        f"Translate **only** source lines **{chunk.start_line}–{chunk.end_line}** "
        f"(inclusive, 1-based line numbers as in the plan).\n"
        f"Output only the translated markdown for this line range — no commentary.\n\n"
        f"{plan_header}\n"
        f"### Region plan (lines {chunk.start_line}–{chunk.end_line})\n\n"
        f"{plan}\n\n"
        f"--- SOURCE BEGIN ---\n"
        f"{chunk.source_text}\n"
        f"--- SOURCE END ---\n"
    )
    return req


def _translate_one_chunk(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk: TranslateChunk,
    plan_header: str,
) -> str:
    masked = _mask_fences(chunk.source_text)
    masked_chunk = TranslateChunk(
        index=chunk.index,
        total=chunk.total,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        source_text=masked.masked_text,
        regions=chunk.regions,
    )
    instructions = load_translate_file_instructions(
        settings,
        source_lang=source_lang,
        target_lang=target_lang,
    ).strip()
    user_input = _build_chunk_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        chunk=masked_chunk,
        plan_header=plan_header,
    )
    model = settings.model_translate
    cap = clamp_max_output_tokens(
        max(2048, min(len(chunk.source_text) * 2 + 1024, 32_768)),
        model,
    )
    fm_log(
        f"file-translate chunk {chunk.index}/{chunk.total} | "
        f"{source_path} | lines {chunk.start_line}-{chunk.end_line}"
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=cap,
            operation="translate:file-chunk",
            detail=f"{source_path}:{chunk.start_line}-{chunk.end_line}",
        ).strip()
    )
    out = _STRIP_REQUEST_HEADER_RE.sub("", out).strip()
    out = _unmask_fences(out, masked)
    if target_lang.strip().lower() in ("english", "en"):
        out = fix_yandex_cloud_links_for_en(out)
    return out


def _translate_prose_with_plan(
    settings: Settings,
    *,
    source_path: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, int]:
    """Translate prose only (no ``{% list tabs %}`` blocks)."""
    source_is_russian = source_lang.lower().startswith("rus")
    regions = analyze_document_structure(
        source_text, source_is_russian=source_is_russian
    )
    chunks = build_translate_chunks(source_text, regions)
    if not chunks:
        return source_text, 0

    multi_note = ""
    if chunks[0].total > 1:
        multi_note = (
            f"This file is split into **{chunks[0].total}** translation requests. "
            "You see one chunk at a time; follow the region plan for its line range only.\n\n"
        )

    parts: list[str] = []
    for chunk in chunks:
        parts.append(
            _translate_one_chunk(
                settings,
                source_lang=source_lang,
                target_lang=target_lang,
                source_path=source_path,
                chunk=chunk,
                plan_header=multi_note,
            )
        )
    merged = "\n".join(parts)
    if source_is_russian and target_lang.strip().lower() in ("english", "en"):
        merged = apply_en_postprocess_from_ru(source_text, merged)
    return merged, len(chunks)


def translate_text_with_plan(
    settings: Settings,
    *,
    source_path: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, int]:
    """
    Translate *source_text* using a line plan; return ``(result, num_llm_calls)``.

    Entire ``{% list tabs %}…{% endlist %}`` blocks are copied verbatim from SOURCE.
    """
    segments = split_preserving_list_tabs(source_text)
    parts: list[str] = []
    llm_calls = 0
    for seg in segments:
        if seg.kind == "list_tabs":
            fm_log(
                f"file-translate copy list-tabs verbatim | {source_path} | "
                f"{len(seg.text)} chars"
            )
            parts.append(seg.text)
            continue
        if not seg.text.strip():
            parts.append(seg.text)
            continue
        translated, n = _translate_prose_with_plan(
            settings,
            source_path=source_path,
            source_text=seg.text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        llm_calls += n
        parts.append(translated)
    merged = "".join(parts)
    if source_lang.lower().startswith("rus") and target_lang.strip().lower() in (
        "english",
        "en",
    ):
        merged, _ = repair_tab_labels_from_source(source_text, merged)
    return merged, llm_calls


def _merge_h3_sections(
    section_texts: dict[int, str],
    *,
    max_index: int,
) -> str:
    """Join sections 0..max_index in order."""
    parts: list[str] = []
    for idx in range(max_index + 1):
        if idx in section_texts:
            parts.append(section_texts[idx])
    return "\n\n".join(p for p in parts if p is not None)


def translate_document_file_level(
    settings: Settings,
    *,
    source_path: str,
    source_full: str,
    source_lang: str,
    target_lang: str,
    en_on_main: str | None = None,
    ru_pr_diff: str | None = None,
) -> tuple[str, str]:
    """
    File-level translation with optional ###-scoped updates from *en_on_main*.

    Returns ``(markdown, mode_label)``.
    """
    scope = compute_translate_scope(
        ru_text=source_full,
        en_on_main=en_on_main,
        ru_pr_diff=ru_pr_diff,
    )
    source_is_russian = source_lang.lower().startswith("rus")

    if (
        scope.mode == "sections"
        and scope.changed_h3
        and en_on_main
        and source_is_russian
    ):
        ru_secs = split_by_h3_sections(source_full)
        en_secs = split_by_h3_sections(en_on_main)
        max_idx = max(ru_secs.keys())
        out_secs: dict[int, str] = {}
        llm_calls = 0
        for idx in range(max_idx + 1):
            if idx in scope.changed_h3:
                sec = ru_secs.get(idx, "")
                if not sec.strip():
                    out_secs[idx] = en_secs.get(idx, "")
                    continue
                fm_log(
                    f"file-translate scoped h3-{idx} | {source_path} | "
                    f"{len(sec)} chars"
                )
                translated, n = translate_text_with_plan(
                    settings,
                    source_path=f"{source_path}#h3-{idx}",
                    source_text=sec,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                llm_calls += n
                out_secs[idx] = translated
            else:
                out_secs[idx] = en_secs.get(idx, ru_secs.get(idx, ""))
        merged = _merge_h3_sections(out_secs, max_index=max_idx)
        merged = apply_en_postprocess_from_ru(source_full, merged)
        merged, _ = repair_tab_labels_from_source(source_full, merged)
        return (
            merged,
            f"file-plan-scoped-h3={','.join(str(x) for x in sorted(scope.changed_h3))}"
            f"+llm={llm_calls}",
        )

    regions = analyze_document_structure(
        source_full, source_is_russian=source_is_russian
    )
    chunks = build_translate_chunks(source_full, regions)
    fm_log(
        f"file-translate {source_lang}→{target_lang} | {source_path} | "
        f"{len(regions)} region(s) | {len(chunks)} request(s)"
    )
    merged, llm_calls = translate_text_with_plan(
        settings,
        source_path=source_path,
        source_text=source_full,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    mode = f"file-plan-{len(regions)}-regions+llm={llm_calls}"
    if chunks and chunks[0].total > 1:
        mode += f"+requests={chunks[0].total}"
    return merged, mode
