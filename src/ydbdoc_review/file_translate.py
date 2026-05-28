"""File-level translation: structure plan + one or few LLM requests per file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ydbdoc_review.config import Settings
from ydbdoc_review.annotated_translate import (
    AnnotatedChunk,
    build_annotated_chunks,
    format_annotated_source,
    merge_copy_regions_from_source,
    refine_tab_regions,
    summarize_chunk_regions,
)
from ydbdoc_review.document_structure import (
    StructureRegion,
    analyze_document_structure,
    split_by_h3_sections,
)
from ydbdoc_review.fm_progress import fm_log
from ydbdoc_review.llm import (
    _strip_code_fence,
    call_yandex_responses,
    clamp_max_output_tokens,
    load_annotated_chunk_instructions,
    translation_model_fallbacks,
)
from ydbdoc_review.translate_postprocess import (
    apply_en_postprocess_from_ru,
    fix_yandex_cloud_links_for_en,
)
from ydbdoc_review.masked_translate import (
    build_masked_segments,
    count_masked_stats,
    translate_with_mask,
)
from ydbdoc_review.placeholder_translate import (
    count_placeholder_stats,
    translate_with_placeholders,
)
from ydbdoc_review.translate_scope import TranslateScope, compute_translate_scope


def _use_legacy_annotated_translate() -> bool:
    return os.environ.get("YDBDOC_LEGACY_ANNOTATED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _use_legacy_line_placeholder() -> bool:
    return os.environ.get("YDBDOC_LEGACY_LINE_JSON", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

_STRIP_REQUEST_HEADER_RE = re.compile(
    r"^#\s*Translation request.*\n+",
    re.IGNORECASE | re.MULTILINE,
)

def _join_text_segments(parts: list[str]) -> str:
    """Join translated segments without gluing headings to ``{% list tabs %}``."""
    if not parts:
        return ""
    merged = parts[0]
    for part in parts[1:]:
        if not part:
            continue
        if not merged:
            merged = part
            continue
        if merged.endswith("\n\n") or (merged.endswith("\n") and part.startswith("\n")):
            merged += part
            continue
        if merged.rstrip().endswith("}") and part.lstrip().startswith("{%"):
            merged = merged.rstrip() + "\n\n" + part.lstrip("\n")
        elif not merged.endswith("\n"):
            merged += "\n\n" + part.lstrip("\n")
        else:
            merged += "\n" + part.lstrip("\n")
    return merged


def _join_translated_chunks(parts: list[str]) -> str:
    """Join file-translate chunk outputs (same rules as segment join)."""
    return _join_text_segments(parts)


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
    """Region-aligned chunks (compat wrapper around :func:`build_annotated_chunks`)."""
    annotated = build_annotated_chunks(text, regions, max_chars=max_chars)
    return [
        TranslateChunk(
            index=c.index,
            total=c.total,
            start_line=c.start_line,
            end_line=c.end_line,
            source_text=_slice_lines(text, c.start_line, c.end_line),
            regions=c.regions,
        )
        for c in annotated
    ]


_ANNOTATED_LINE_RE = re.compile(r"^L\d{5}\|\s?(.*)$")
_ANNOTATED_MARKER_RE = re.compile(r"^L\d{5}\s+@(BEGIN|END)\s+")


def _strip_annotated_line_prefixes(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if _ANNOTATED_MARKER_RE.match(line.strip()):
            continue
        m = _ANNOTATED_LINE_RE.match(line)
        if m:
            out.append(m.group(1))
        else:
            out.append(line)
    return "\n".join(out)


def _build_annotated_chunk_user_input(
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk: AnnotatedChunk,
    full_source: str,
    all_regions: list[StructureRegion],
) -> str:
    region_map = summarize_chunk_regions(full_source, chunk.regions)
    annotated = format_annotated_source(
        full_source,
        all_regions,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
    )
    return (
        f"## Translation request {chunk.index} of {chunk.total}\n\n"
        f"File: `{source_path}`\n"
        f"SOURCE language: {source_lang}\n"
        f"TARGET language: {target_lang}\n"
        f"Translate **only** lines **{chunk.start_line}–{chunk.end_line}** "
        f"(inclusive, 1-based).\n\n"
        f"### REGION MAP (this chunk)\n\n"
        f"```\n"
        f"{region_map}\n"
        f"```\n\n"
        f"### SOURCE (numbered, with @BEGIN/@END markers)\n\n"
        f"```\n"
        f"{annotated}\n"
        f"```\n"
    )


def _translate_one_chunk(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk: TranslateChunk,
    plan_header: str,
    full_source: str = "",
    all_regions: list[StructureRegion] | None = None,
) -> str:
    """Translate one region-aligned chunk (annotated SOURCE + per-chunk instructions)."""
    _ = plan_header
    if not full_source:
        full_source = chunk.source_text
    if all_regions is None:
        all_regions = chunk.regions
    annotated = AnnotatedChunk(
        index=chunk.index,
        total=chunk.total,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        regions=chunk.regions,
    )
    return _translate_annotated_chunk(
        settings,
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        chunk=annotated,
        full_source=full_source,
        all_regions=all_regions,
    )


def _translate_annotated_chunk(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    chunk: AnnotatedChunk,
    full_source: str,
    all_regions: list[StructureRegion],
) -> str:
    instructions = load_annotated_chunk_instructions(
        settings,
        source_lang=source_lang,
        target_lang=target_lang,
    ).strip()
    user_input = _build_annotated_chunk_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        chunk=chunk,
        full_source=full_source,
        all_regions=all_regions,
    )
    ru_slice = _slice_lines(full_source, chunk.start_line, chunk.end_line)
    model = settings.model_translate
    cap = clamp_max_output_tokens(
        max(2048, min(len(ru_slice) * 2 + 1024, 32_768)),
        model,
    )
    fm_log(
        f"annotated-translate chunk {chunk.index}/{chunk.total} | "
        f"{source_path} | lines {chunk.start_line}-{chunk.end_line}"
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=cap,
            model_fallbacks=translation_model_fallbacks(),
            operation="translate:annotated-chunk",
            detail=f"{source_path}:{chunk.start_line}-{chunk.end_line}",
        ).strip()
    )
    out = _STRIP_REQUEST_HEADER_RE.sub("", out).strip()
    out = _strip_annotated_line_prefixes(out)
    out = merge_copy_regions_from_source(
        ru_slice,
        out,
        chunk.regions,
        chunk_start_line=chunk.start_line,
    )
    if target_lang.strip().lower() in ("english", "en"):
        out = fix_yandex_cloud_links_for_en(out)
    return out


def _translate_annotated_file(
    settings: Settings,
    *,
    source_path: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, int]:
    """Translate full file via region map, annotated chunks, deterministic COPY merge."""
    source_is_russian = source_lang.lower().startswith("rus")
    regions = refine_tab_regions(
        source_text,
        analyze_document_structure(source_text, source_is_russian=source_is_russian),
    )
    chunks = build_annotated_chunks(source_text, regions)
    if not chunks:
        return source_text, 0

    parts: list[str] = []
    llm_calls = 0
    for ch in chunks:
        ru_slice = _slice_lines(source_text, ch.start_line, ch.end_line)
        if ch.copy_only():
            fm_log(
                f"annotated-translate copy-only chunk {ch.index}/{ch.total} | "
                f"{source_path} | lines {ch.start_line}-{ch.end_line}"
            )
            parts.append(ru_slice)
            continue
        tc = TranslateChunk(
            index=ch.index,
            total=ch.total,
            start_line=ch.start_line,
            end_line=ch.end_line,
            source_text=ru_slice,
            regions=ch.regions,
        )
        en_slice = _translate_one_chunk(
            settings,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            chunk=tc,
            plan_header="",
            full_source=source_text,
            all_regions=regions,
        )
        llm_calls += 1
        parts.append(en_slice)
    merged = _join_translated_chunks(parts)
    return merged, llm_calls


def translate_text_with_plan(
    settings: Settings,
    *,
    source_path: str,
    source_text: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, int]:
    """Translate *source_text* via mask→unmask (default), line-JSON, or annotated legacy."""
    if _use_legacy_annotated_translate():
        return _translate_annotated_file(
            settings,
            source_path=source_path,
            source_text=source_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
    if _use_legacy_line_placeholder():
        return translate_with_placeholders(
            settings,
            source_path=source_path,
            source_text=source_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
    return translate_with_mask(
        settings,
        source_path=source_path,
        source_text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
    )


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
        if source_is_russian and target_lang.strip().lower() in ("english", "en"):
            merged = apply_en_postprocess_from_ru(source_full, merged)
        return (
            merged,
            f"file-plan-scoped-h3={','.join(str(x) for x in sorted(scope.changed_h3))}"
            f"+llm={llm_calls}",
        )

    regions = refine_tab_regions(
        source_full,
        analyze_document_structure(source_full, source_is_russian=source_is_russian),
    )
    merged, llm_calls = translate_text_with_plan(
        settings,
        source_path=source_path,
        source_text=source_full,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    if source_is_russian and target_lang.strip().lower() in ("english", "en"):
        merged = apply_en_postprocess_from_ru(source_full, merged)
    if _use_legacy_annotated_translate():
        chunks = build_annotated_chunks(source_full, regions)
        copy_chunks = sum(1 for c in chunks if c.copy_only())
        fm_log(
            f"annotated-translate {source_lang}→{target_lang} | {source_path} | "
            f"{len(regions)} region(s) | {len(chunks)} chunk(s)"
        )
        mode = f"annotated-{len(regions)}-regions+llm={llm_calls}+copy_chunks={copy_chunks}"
        if chunks and chunks[0].total > 1:
            mode += f"+chunks={chunks[0].total}"
    elif _use_legacy_line_placeholder():
        from ydbdoc_review.placeholder_translate import build_placeholder_segments

        segments = build_placeholder_segments(
            source_full, regions, source_is_russian=source_is_russian
        )
        copy_segs, tr_segs, units = count_placeholder_stats(segments)
        fm_log(
            f"placeholder-translate {source_lang}→{target_lang} | {source_path} | "
            f"{len(regions)} region(s) | copy={copy_segs} translate={tr_segs} "
            f"units={units} llm_batches={llm_calls}"
        )
        mode = (
            f"placeholder-{len(regions)}-regions+units={units}"
            f"+copy_segs={copy_segs}+llm={llm_calls}"
        )
    else:
        from ydbdoc_review.document_mask import MaskRegistry

        registry = MaskRegistry()
        segments = build_masked_segments(
            source_full, regions, registry, source_is_russian=source_is_russian
        )
        copy_segs, tr_segs, _ = count_masked_stats(segments)
        fm_log(
            f"masked-translate {source_lang}→{target_lang} | {source_path} | "
            f"{len(regions)} region(s) | copy={copy_segs} translate={tr_segs} "
            f"placeholders={len(registry.atoms)} llm={llm_calls}"
        )
        mode = (
            f"masked-{len(regions)}-regions+ph={len(registry.atoms)}"
            f"+copy_segs={copy_segs}+llm={llm_calls}"
        )
    return merged, mode
