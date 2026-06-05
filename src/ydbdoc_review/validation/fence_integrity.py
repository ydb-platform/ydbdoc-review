"""Guarantee fenced code blocks are copied from source, not model-translated."""

from __future__ import annotations

import re

from ydbdoc_review.parsing.ast_types import BlockNode, Document, FencedCode, IndentedCode
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.validation.homoglyphs import fix_russian_angle_placeholders_in_en_fences
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation

def _walk_blocks(blocks: list[BlockNode], out: list[FencedCode | IndentedCode]) -> None:
    for block in blocks:
        if isinstance(block, (FencedCode, IndentedCode)):
            out.append(block)
        children = getattr(block, "children", None)
        if children:
            _walk_blocks(children, out)


def collect_code_blocks(doc: Document) -> list[FencedCode | IndentedCode]:
    """Ordered fenced and indented code blocks in document order."""
    out: list[FencedCode | IndentedCode] = []
    _walk_blocks(doc.children, out)
    return out


def code_blocks_from_text(text: str) -> list[FencedCode | IndentedCode]:
    return collect_code_blocks(parse_markdown(text))


def _normalize_fence_content_for_compare(text: str) -> str:
    """Allow only EN angle-placeholder substitution inside otherwise identical fences."""
    return fix_russian_angle_placeholders_in_en_fences(
        f"```\n{text}\n```"
    ).strip().removeprefix("```\n").removesuffix("\n```")


def fence_content_matches_source(source_content: str, target_content: str) -> bool:
    """True when target fence body equals source, modulo RU→EN angle placeholders."""
    return _normalize_fence_content_for_compare(source_content) == _normalize_fence_content_for_compare(
        target_content
    )


def _source_text_for_fence_compare(source_text: str, *, source_lang: str) -> str:
    """RU workdir text as the pipeline sees it (after normalize, before translate)."""
    if source_lang.lower() in {"ru", "russian"}:
        return normalize_ru_source_for_translation(source_text)
    return source_text


def check_fence_body_copy(
    source_text: str, target_text: str, *, source_lang: str = "ru"
) -> list[str]:
    """Warn when any fenced/indented block body differs from source (pipeline corruption)."""
    source_text = _source_text_for_fence_compare(source_text, source_lang=source_lang)
    src_blocks = code_blocks_from_text(source_text)
    tgt_blocks = code_blocks_from_text(target_text)
    if len(src_blocks) != len(tgt_blocks):
        return [
            f"fence_body_copy: block count source {len(src_blocks)} vs target {len(tgt_blocks)}"
        ]
    warnings: list[str] = []
    for i, (src, tgt) in enumerate(zip(src_blocks, tgt_blocks, strict=True), start=1):
        if fence_content_matches_source(src.content, tgt.content):
            continue
        preview = tgt.content.strip().splitlines()[0][:80] if tgt.content.strip() else "(empty)"
        warnings.append(
            f"fence_body_copy: block {i} body changed by pipeline (first line: «{preview}»)"
        )
    return warnings


def enforce_source_fenced_blocks(target_text: str, source_text: str) -> str:
    """Re-render EN with every code block body copied verbatim from source."""
    src_doc = parse_markdown(source_text)
    tgt_doc = parse_markdown(target_text)
    src_blocks = collect_code_blocks(src_doc)
    tgt_blocks = collect_code_blocks(tgt_doc)
    if len(src_blocks) != len(tgt_blocks):
        return target_text
    for src, tgt in zip(src_blocks, tgt_blocks, strict=True):
        tgt.content = src.content
        if isinstance(src, FencedCode) and isinstance(tgt, FencedCode):
            tgt.info = src.info
            tgt.fence_char = src.fence_char
            tgt.fence_len = src.fence_len
    return render_markdown(tgt_doc)


def check_absolute_paths_in_fences(source_text: str, target_text: str) -> list[str]:
    """Warn when RU fence lines use /opt/ydb/... but EN counterpart line lost the prefix."""
    warnings: list[str] = []
    src_blocks = code_blocks_from_text(source_text)
    tgt_blocks = code_blocks_from_text(target_text)
    for i, (src, tgt) in enumerate(zip(src_blocks, tgt_blocks, strict=True), start=1):
        src_lines = src.content.splitlines()
        tgt_lines = tgt.content.splitlines()
        if len(src_lines) != len(tgt_lines):
            continue
        for line_no, (sl, tl) in enumerate(zip(src_lines, tgt_lines, strict=True), start=1):
            if "/opt/ydb/" in sl and "/opt/ydb/" not in tl and re.search(
                r"(?<!/opt/ydb/)(?:ca\.crt|node\.crt|node\.key)", tl
            ):
                warnings.append(
                    f"fence_path_stripped: block {i} line {line_no}: "
                    f"RU has absolute cert path, EN shortened to relative"
                )
    return warnings
