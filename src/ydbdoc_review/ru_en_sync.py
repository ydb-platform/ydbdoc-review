"""Rebuild EN documentation structure from RU (source of truth)."""

from __future__ import annotations

import os
import re
from typing import Callable

from ydbdoc_review.config import Settings
from ydbdoc_review.markdown_sections import (
    MarkdownSection,
    join_markdown_sections,
    split_markdown_sections,
)


def _norm_heading(title: str) -> str:
    t = title.strip().lower()
    if t.startswith("##"):
        t = t[2:].strip()
    t = re.sub(r"[^\w\s-]", "", t)
    return re.sub(r"\s+", " ", t)


def section_key(sec: MarkdownSection) -> str:
    if not sec.heading:
        return f"__preamble_{sec.index}"
    return _norm_heading(sec.heading)


def split_h3_blocks(section_text: str) -> list[tuple[str, str]]:
    """
    Split a ``##`` section body into blocks keyed by normalized ``###`` title.

    The first block uses key ``""`` for content before the first ``###``.
    """
    lines = section_text.split("\n")
    blocks: list[tuple[str, str]] = []
    cur_key = ""
    cur: list[str] = []

    def flush() -> None:
        nonlocal cur, cur_key
        if not cur:
            return
        blocks.append((cur_key, "\n".join(cur).strip()))
        cur = []

    for line in lines:
        m = re.match(r"^###\s+(.+?)\s*$", line)
        if m:
            flush()
            cur_key = _norm_heading(m.group(1))
            cur = [line]
        else:
            cur.append(line)
    flush()
    return blocks


def section_missing_h3(ru_section: str, en_section: str) -> bool:
    ru_keys = {k for k, _ in split_h3_blocks(ru_section) if k}
    en_keys = {k for k, _ in split_h3_blocks(en_section) if k}
    return bool(ru_keys - en_keys)


def section_too_short(ru_section: str, en_section: str) -> bool:
    if len(ru_section) < 400:
        return False
    return len(en_section.strip()) < int(len(ru_section.strip()) * 0.55)


def merge_section_h3_from_ru(
    ru_section: str,
    en_section: str,
    translate_block: Callable[[str], str],
) -> str:
    """Rebuild one ``##`` section in RU ``###`` order without duplicating EN blocks."""
    ru_parts = split_h3_blocks(ru_section)
    en_map = {k: v for k, v in split_h3_blocks(en_section)}
    out: list[str] = []

    for key, ru_block in ru_parts:
        en_block = en_map.get(key, "")
        if not key:
            if en_block.strip() and len(en_block) >= int(len(ru_block) * 0.45):
                out.append(en_block)
            else:
                out.append(translate_block(ru_block))
            continue
        if (
            not en_block.strip()
            or section_too_short(ru_block, en_block)
            or key not in en_map
        ):
            out.append(translate_block(ru_block))
        else:
            out.append(en_block)

    return "\n\n".join(p for p in out if p.strip())


def en_sections_by_heading(
    en_sections: list[MarkdownSection],
) -> dict[str, MarkdownSection]:
    """Map ``##`` heading → best EN section (longest body wins duplicates)."""
    by: dict[str, MarkdownSection] = {}
    for sec in en_sections:
        key = section_key(sec)
        prev = by.get(key)
        if prev is None or len(sec.content) > len(prev.content):
            by[key] = sec
    return by


def duplicate_h2_sections(text: str) -> bool:
    seen: set[str] = set()
    for sec in split_markdown_sections(text):
        if not sec.heading:
            continue
        key = section_key(sec)
        if key in seen:
            return True
        seen.add(key)
    return False


def document_structure_broken(ru_full: str, en_text: str) -> bool:
    from ydbdoc_review.ru_en_alignment import en_coverage_behind_ru

    if en_coverage_behind_ru(ru_full, en_text):
        return True
    if duplicate_h2_sections(en_text):
        return True
    ru_secs = split_markdown_sections(ru_full)
    en_secs = split_markdown_sections(en_text)
    if len(en_secs) > len(ru_secs) + 1:
        return True
    en_map = en_sections_by_heading(en_secs)
    for ru_sec in ru_secs:
        key = section_key(ru_sec)
        en_sec = en_map.get(key)
        if en_sec is None:
            return True
        if section_missing_h3(ru_sec.content, en_sec.content) or section_too_short(
            ru_sec.content, en_sec.content
        ):
            return True
    return False


def structure_sync_enabled() -> bool:
    raw = os.environ.get("YDBDOC_STRUCTURE_SYNC", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def structure_sync_needed(ru_full: str, en_text: str) -> bool:
    return document_structure_broken(ru_full, en_text)


def rebuild_en_document_from_ru(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
    translate_block: Callable[[str], str] | None = None,
) -> str:
    """
    Replace EN with RU ``##`` order: one section per RU heading, no extra EN sections.

    Missing or short sections are translated from RU; ``###`` blocks follow RU order.
    """
    if translate_block is None:
        from ydbdoc_review.llm import translate_ru_block_to_en

        def _tb(block: str) -> str:
            return translate_ru_block_to_en(settings, ru_path=ru_path, ru_block=block)

        translate_block = _tb

    ru_secs = split_markdown_sections(ru_full)
    en_map = en_sections_by_heading(split_markdown_sections(en_text))
    out: list[MarkdownSection] = []

    for ru_sec in ru_secs:
        key = section_key(ru_sec)
        en_sec = en_map.get(key)
        if en_sec is None:
            body = translate_block(ru_sec.content)
            heading = ru_sec.heading
        elif section_missing_h3(ru_sec.content, en_sec.content) or section_too_short(
            ru_sec.content, en_sec.content
        ):
            body = merge_section_h3_from_ru(
                ru_sec.content, en_sec.content, translate_block
            )
            heading = en_sec.heading or ru_sec.heading
        else:
            body = en_sec.content
            heading = en_sec.heading or ru_sec.heading

        out.append(
            MarkdownSection(
                index=ru_sec.index,
                heading=heading,
                content=body.strip(),
                start_line=ru_sec.start_line,
                end_line=ru_sec.end_line,
            )
        )

    return join_markdown_sections(out)


def sync_document_structure_from_ru(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
) -> str:
    """Alias for :func:`rebuild_en_document_from_ru` (strict RU section order)."""
    if not structure_sync_enabled():
        return en_text
    return rebuild_en_document_from_ru(
        settings, ru_path=ru_path, ru_full=ru_full, en_text=en_text
    )


def en_path_from_ru(ru_path: str) -> str:
    return ru_path.replace("/docs/ru/", "/docs/en/", 1).replace("\\ru\\", "\\en\\")


def finalize_en_from_ru(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
    force_structure_rebuild: bool = False,
) -> str:
    from ydbdoc_review.translate_postprocess import apply_post_translation_fixes

    en_path = en_path_from_ru(ru_path)
    out = apply_post_translation_fixes(
        en_text, ru_source=ru_full, en_path=en_path
    )
    if structure_sync_enabled() and (
        force_structure_rebuild or structure_sync_needed(ru_full, out)
    ):
        out = rebuild_en_document_from_ru(
            settings,
            ru_path=ru_path,
            ru_full=ru_full,
            en_text=out,
        )
        out = apply_post_translation_fixes(
            out, ru_source=ru_full, en_path=en_path
        )
    return out


def deterministic_prepare_en(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
    force_structure_rebuild: bool = False,
) -> str:
    """Artifacts, CLI semantics, and optional RU-order rebuild (no critic LLM)."""
    return finalize_en_from_ru(
        settings,
        ru_path=ru_path,
        ru_full=ru_full,
        en_text=en_text,
        force_structure_rebuild=force_structure_rebuild,
    )
