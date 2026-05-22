"""Sync missing ``##`` / ``###`` blocks from RU into EN after machine translation."""

from __future__ import annotations

import os
import re
from typing import Callable

from ydbdoc_review.config import Settings
from ydbdoc_review.markdown_sections import (
    MarkdownSection,
    align_sections_by_heading,
    join_markdown_sections,
    split_markdown_sections,
)

_H3_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)


def _norm_heading(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t)
    return re.sub(r"\s+", " ", t)


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
    """Rebuild one ``##`` section: keep EN blocks, translate missing ``###`` from RU."""
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


def structure_sync_enabled() -> bool:
    raw = os.environ.get("YDBDOC_STRUCTURE_SYNC", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def structure_sync_needed(ru_full: str, en_text: str) -> bool:
    from ydbdoc_review.ru_en_alignment import en_coverage_behind_ru

    if en_coverage_behind_ru(ru_full, en_text):
        return True
    ru_secs = split_markdown_sections(ru_full)
    en_secs = split_markdown_sections(en_text)
    aligned = align_sections_by_heading(ru_secs, en_secs)
    for ru_sec in ru_secs:
        en_sec = aligned[ru_sec.index] if ru_sec.index < len(aligned) else None
        en_body = en_sec.content if en_sec else ""
        if en_sec is None:
            return True
        if section_missing_h3(ru_sec.content, en_body) or section_too_short(
            ru_sec.content, en_body
        ):
            return True
    return False


def sync_document_structure_from_ru(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
) -> str:
    """
    Fill missing ``###`` / ``##`` sections in EN from RU (source of truth).

    Uses block-preserving translation for inserted fragments.
    """
    if not structure_sync_enabled():
        return en_text

    from ydbdoc_review.llm import translate_ru_block_to_en

    ru_secs = split_markdown_sections(ru_full)
    en_secs = split_markdown_sections(en_text)
    aligned = align_sections_by_heading(ru_secs, en_secs)
    out: list[MarkdownSection] = []

    def translate_block(block: str) -> str:
        return translate_ru_block_to_en(
            settings,
            ru_path=ru_path,
            ru_block=block,
        )

    for ru_sec in ru_secs:
        en_sec = aligned[ru_sec.index] if ru_sec.index < len(aligned) else None
        en_body = en_sec.content if en_sec else ""
        if en_sec is None:
            out.append(
                MarkdownSection(
                    index=ru_sec.index,
                    heading=ru_sec.heading,
                    content=translate_block(ru_sec.content).strip(),
                    start_line=ru_sec.start_line,
                    end_line=ru_sec.end_line,
                )
            )
            continue
        if section_missing_h3(ru_sec.content, en_body) or section_too_short(
            ru_sec.content, en_body
        ):
            merged = merge_section_h3_from_ru(
                ru_sec.content, en_body, translate_block
            )
            out.append(
                MarkdownSection(
                    index=en_sec.index,
                    heading=en_sec.heading,
                    content=merged.strip(),
                    start_line=en_sec.start_line,
                    end_line=en_sec.end_line,
                )
            )
        else:
            out.append(en_sec)

    return join_markdown_sections(out)


def en_path_from_ru(ru_path: str) -> str:
    return ru_path.replace("/docs/ru/", "/docs/en/", 1).replace("\\ru\\", "\\en\\")


def finalize_en_from_ru(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
) -> str:
    from ydbdoc_review.translate_postprocess import apply_post_translation_fixes

    en_path = en_path_from_ru(ru_path)
    out = apply_post_translation_fixes(
        en_text, ru_source=ru_full, en_path=en_path
    )
    if structure_sync_needed(ru_full, out):
        out = sync_document_structure_from_ru(
            settings,
            ru_path=ru_path,
            ru_full=ru_full,
            en_text=out,
        )
        out = apply_post_translation_fixes(
            out, ru_source=ru_full, en_path=en_path
        )
    return out
