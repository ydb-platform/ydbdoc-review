"""Translate documentation pairs section-by-section (``##`` boundaries)."""

from __future__ import annotations

import os

from ydbdoc_review.config import Settings
from ydbdoc_review.llm import (
    translate_en_update_from_ru_diff,
    translate_markdown,
    translate_ru_update_from_en_diff,
)
from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
from ydbdoc_review.markdown_sections import (
    MarkdownSection,
    align_sections_by_heading,
    extract_diff_hunks_for_line_range,
    join_markdown_sections,
    section_indices_touched_by_diff,
    split_markdown_sections,
)
from ydbdoc_review.translate_postprocess import translation_quality_issues


def translate_by_section_enabled(*, source_len: int) -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATE_BY_SECTION", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    threshold = section_mode_min_chars()
    return source_len >= threshold


def section_mode_min_chars() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATE_BY_SECTION_MIN_CHARS", "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return 8_000


def full_file_repair_max_chars() -> int:
    raw = os.environ.get("YDBDOC_TRANSLATION_REPAIR_FULL_FILE_MAX_CHARS", "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return 30_000


def translate_ru_to_en_by_sections(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_reference: str,
    ru_diff: str,
) -> tuple[str, str]:
    ru_sections = split_markdown_sections(ru_full)
    en_sections = split_markdown_sections(en_reference)
    aligned = align_sections_by_heading(ru_sections, en_sections)
    touched = section_indices_touched_by_diff(ru_diff, ru_sections)
    if ru_diff.strip() and not touched:
        touched = {s.index for s in ru_sections}

    out_sections: list[MarkdownSection] = []
    updated = 0
    for ru_sec in ru_sections:
        en_sec = aligned[ru_sec.index] if ru_sec.index < len(aligned) else None
        if ru_sec.index not in touched and en_sec is not None:
            out_sections.append(en_sec)
            continue
        en_ref = en_sec.content if en_sec is not None else ""
        sec_diff = extract_diff_hunks_for_line_range(
            ru_diff,
            start_line=ru_sec.start_line,
            end_line=ru_sec.end_line,
        )
        try:
            if sec_diff.strip() and en_ref.strip():
                out_text = translate_en_update_from_ru_diff(
                    settings,
                    en_reference=en_ref,
                    ru_diff=sec_diff,
                    ru_path=ru_path,
                    ru_full=ru_sec.content,
                )
            else:
                out_text = translate_markdown(
                    settings,
                    source_lang="Russian",
                    target_lang="English",
                    source_path=ru_path,
                    source_text=ru_sec.content,
                )
        except Exception:
            raise
        out_text = restore_markdown_links_from_ru(ru_sec.content, out_text)
        updated += 1
        out_sections.append(
            MarkdownSection(
                index=ru_sec.index,
                heading=ru_sec.heading,
                content=out_text.strip(),
                start_line=ru_sec.start_line,
                end_line=ru_sec.end_line,
            )
        )

    merged = join_markdown_sections(out_sections)
    mode = f"sections-{updated}/{len(ru_sections)}"
    return merged, mode


def translate_en_to_ru_by_sections(
    settings: Settings,
    *,
    en_path: str,
    en_full: str,
    ru_reference: str,
    en_diff: str,
) -> tuple[str, str]:
    en_sections = split_markdown_sections(en_full)
    ru_sections = split_markdown_sections(ru_reference)
    aligned = align_sections_by_heading(en_sections, ru_sections)
    touched = section_indices_touched_by_diff(en_diff, en_sections)
    if en_diff.strip() and not touched:
        touched = {s.index for s in en_sections}

    out_sections: list[MarkdownSection] = []
    updated = 0
    for en_sec in en_sections:
        ru_sec = aligned[en_sec.index] if en_sec.index < len(aligned) else None
        if en_sec.index not in touched and ru_sec is not None:
            out_sections.append(ru_sec)
            continue
        ru_ref = ru_sec.content if ru_sec is not None else ""
        sec_diff = extract_diff_hunks_for_line_range(
            en_diff,
            start_line=en_sec.start_line,
            end_line=en_sec.end_line,
        )
        if sec_diff.strip() and ru_ref.strip():
            out_text = translate_ru_update_from_en_diff(
                settings,
                ru_reference=ru_ref,
                en_diff=sec_diff,
                en_path=en_path,
                en_full=en_sec.content,
            )
        else:
            out_text = translate_markdown(
                settings,
                source_lang="English",
                target_lang="Russian",
                source_path=en_path,
                source_text=en_sec.content,
            )
        updated += 1
        out_sections.append(
            MarkdownSection(
                index=en_sec.index,
                heading=en_sec.heading,
                content=out_text.strip(),
                start_line=en_sec.start_line,
                end_line=en_sec.end_line,
            )
        )

    return join_markdown_sections(out_sections), f"sections-{updated}/{len(en_sections)}"


def translate_full_source_by_sections(
    settings: Settings,
    *,
    source_path: str,
    source_full: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, str]:
    """Full-file translation path split per ``##`` (avoids huge single completion)."""
    sections = split_markdown_sections(source_full)
    out: list[MarkdownSection] = []
    for sec in sections:
        text = translate_markdown(
            settings,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            source_text=sec.content,
        )
        if target_lang.strip().lower() in ("english", "en") and source_lang.lower().startswith("rus"):
            text = restore_markdown_links_from_ru(sec.content, text)
        out.append(
            MarkdownSection(
                index=sec.index,
                heading=sec.heading,
                content=text.strip(),
                start_line=sec.start_line,
                end_line=sec.end_line,
            )
        )
    merged = join_markdown_sections(out)
    if target_lang.strip().lower() in ("english", "en") and source_lang.lower().startswith(
        "rus"
    ):
        from ydbdoc_review.translate_postprocess import apply_post_translation_fixes

        merged = apply_post_translation_fixes(merged, ru_source=source_full)
    issues = translation_quality_issues(source_full, merged, target_lang=target_lang)
    suffix = f"-issues-{','.join(issues)}" if issues else ""
    return merged, f"sections-full-{len(sections)}{suffix}"
