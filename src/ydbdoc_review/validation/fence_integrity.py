"""Guarantee fenced code blocks are copied from source, not model-translated."""

from __future__ import annotations

import re

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    Document,
    FencedCode,
    IndentedCode,
    YfmIf,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.validation.homoglyphs import (
    fix_cyrillic_homoglyphs_in_en,
    fix_russian_angle_placeholders_in_en_fences,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


def _walk_blocks(blocks: list[BlockNode], out: list[FencedCode | IndentedCode]) -> None:
    """Recurse into nested blocks, including ``YfmIf.branches`` (#48009 / §6.139)."""
    for block in blocks:
        if isinstance(block, (FencedCode, IndentedCode)):
            out.append(block)
        # YfmIf stores body in ``branches``, not ``.children`` (empty/absent).
        if isinstance(block, YfmIf):
            for branch in block.branches:
                _walk_blocks(branch.children, out)
            continue
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


def fence_structure_is_round_trip_stable(text: str, *, lang: str = "ru") -> bool:
    """Whether our renderer preserves the source's fenced-block count."""
    raw = len(code_blocks_from_text(text))
    rendered = render_markdown(parse_markdown(text), target_lang=lang)
    return len(code_blocks_from_text(rendered)) == raw


def fence_marker_tokens(text: str) -> list[str]:
    marker = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
    return [
        match.group(1) + match.group(2).strip()
        for line in text.splitlines()
        if (match := marker.match(line))
    ]


def _normalize_fence_content_for_compare(text: str) -> str:
    """Normalize fence body for compare: angle placeholders + YAML homoglyphs."""
    inner = fix_russian_angle_placeholders_in_en_fences(f"```\n{text}\n```")
    inner = inner.strip().removeprefix("```\n").removesuffix("\n```")
    return fix_cyrillic_homoglyphs_in_en(inner)


_MERMAID_START = re.compile(
    r"^(?:sequenceDiagram|graph\s|flowchart\s|classDiagram|stateDiagram|erDiagram|gantt|pie\s)",
    re.IGNORECASE,
)
_MERMAID_ARROW = re.compile(r"(--x|->>|->|--)")
# Collapse label tokens; keep arrows, punctuation, and mermaid keywords.
_MERMAID_LABEL = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")
# Quoted node/subgraph labels: RU hyphens vs EN spaces must not differ
# structurally («Дата-центр» → ``*-*`` vs «Data center» → ``* *``; #49578).
_MERMAID_QUOTED_LABEL = re.compile(r"""\["(?:\\.|[^"\\])*"\]|\['(?:\\.|[^'\\])*'\]""")


def _is_mermaid_fence(content: str) -> bool:
    first = content.strip().splitlines()[0].strip() if content.strip() else ""
    return bool(_MERMAID_START.match(first))


def _mermaid_structure_line(line: str) -> str:
    """Normalize a mermaid line for structural compare (labels → ``*``)."""
    stripped = line.strip()
    if not stripped:
        return ""
    if _MERMAID_START.match(stripped):
        return stripped.split()[0].lower()
    # Whole quoted label → one token (word count / hyphens inside must not matter).
    stripped = _MERMAID_QUOTED_LABEL.sub("[*]", stripped)
    if stripped.startswith("participant "):
        rest = stripped[len("participant ") :]
        if " as " in rest:
            return "participant * as *"
        return "participant *"
    if stripped.startswith("Note over "):
        colon = stripped.find(": ")
        if colon >= 0:
            header = _MERMAID_LABEL.sub("*", stripped[:colon])
            return f"{header}: *"
    if ": " in stripped and _MERMAID_ARROW.search(stripped):
        prefix = stripped.split(": ", 1)[0]
        return _MERMAID_LABEL.sub("*", prefix) + ": *"
    return _MERMAID_LABEL.sub("*", stripped)


def _fence_diff_is_mermaid_label_translation(
    source_content: str,
    target_content: str,
) -> bool:
    """True when EN mermaid differs from RU only in participant/label text."""
    if not _is_mermaid_fence(source_content):
        return False
    src_lines = source_content.strip().splitlines()
    tgt_lines = target_content.strip().splitlines()
    if len(src_lines) != len(tgt_lines) or not src_lines:
        return False
    return all(
        _mermaid_structure_line(sl) == _mermaid_structure_line(tl)
        for sl, tl in zip(src_lines, tgt_lines, strict=True)
    )


def _fence_diff_is_comment_translation_only(
    source_content: str,
    target_content: str,
) -> bool:
    """True when EN differs from RU only on ``//`` / ``#`` / ``--`` lines that had Cyrillic."""
    from ydbdoc_review.validation.fence_comments import (
        _CYRILLIC,
        _comment_body_if_cyrillic,
        trailing_comment_code_prefix,
    )

    src_lines = source_content.splitlines()
    tgt_lines = target_content.splitlines()
    # Trailing blank lines often differ after render (§6.156 / group-by fence).
    while src_lines and not src_lines[-1].strip():
        src_lines.pop()
    while tgt_lines and not tgt_lines[-1].strip():
        tgt_lines.pop()
    if len(src_lines) != len(tgt_lines):
        return False
    saw_diff = False
    for src_line, tgt_line in zip(src_lines, tgt_lines, strict=True):
        if src_line == tgt_line:
            continue
        saw_diff = True
        if _comment_body_if_cyrillic(src_line) is None:
            return False
        src_prefix = trailing_comment_code_prefix(src_line)
        if src_prefix is not None:
            if trailing_comment_code_prefix(tgt_line) != src_prefix:
                return False
        if _comment_body_if_cyrillic(tgt_line) is not None and _CYRILLIC.search(tgt_line):
            return False
    return saw_diff


_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_ANGLE_PLACEHOLDER = re.compile(r"<([^<>]+)>")


def _collapse_translated_angle_placeholders(
    source_content: str,
    target_content: str,
) -> tuple[str, str] | None:
    """Collapse RU→EN ``<…>`` placeholder pairs to ``<>`` for structural compare.

    Returns ``None`` when line counts differ or a Cyrillic angle in source has no
    matching angle slot on the same target line.
    """
    src_lines = source_content.splitlines()
    tgt_lines = target_content.splitlines()
    if len(src_lines) != len(tgt_lines):
        return None
    out_src: list[str] = []
    out_tgt: list[str] = []
    for src_line, tgt_line in zip(src_lines, tgt_lines, strict=True):
        src_angles = list(_ANGLE_PLACEHOLDER.finditer(src_line))
        tgt_angles = list(_ANGLE_PLACEHOLDER.finditer(tgt_line))
        if not any(_CYRILLIC.search(m.group(1)) for m in src_angles):
            out_src.append(src_line)
            out_tgt.append(tgt_line)
            continue
        if len(src_angles) != len(tgt_angles):
            return None
        src_parts: list[str] = []
        tgt_parts: list[str] = []
        src_pos = 0
        tgt_pos = 0
        for sm, tm in zip(src_angles, tgt_angles, strict=True):
            src_parts.append(src_line[src_pos : sm.start()])
            tgt_parts.append(tgt_line[tgt_pos : tm.start()])
            if _CYRILLIC.search(sm.group(1)):
                if _CYRILLIC.search(tm.group(1)):
                    return None
                src_parts.append("<>")
                tgt_parts.append("<>")
            else:
                src_parts.append(sm.group(0))
                tgt_parts.append(tm.group(0))
            src_pos = sm.end()
            tgt_pos = tm.end()
        src_parts.append(src_line[src_pos:])
        tgt_parts.append(tgt_line[tgt_pos:])
        out_src.append("".join(src_parts))
        out_tgt.append("".join(tgt_parts))
    return "\n".join(out_src), "\n".join(out_tgt)


def _fence_diff_is_angle_placeholder_translation(
    source_content: str,
    target_content: str,
) -> bool:
    """True when EN differs from RU only in translated ``<…>`` placeholders."""
    collapsed = _collapse_translated_angle_placeholders(source_content, target_content)
    if collapsed is None:
        return False
    src_n, tgt_n = collapsed
    if src_n == source_content and tgt_n == target_content:
        return False
    return _normalize_fence_content_for_compare(src_n) == _normalize_fence_content_for_compare(
        tgt_n
    )


def _fence_diff_is_text_diagram_label_translation(
    source_content: str,
    target_content: str,
) -> bool:
    """True when EN `` ```text `` `` diagram differs from RU only in translated labels."""
    src_lines = source_content.strip().splitlines()
    tgt_lines = target_content.strip().splitlines()
    if len(src_lines) != len(tgt_lines) or not src_lines:
        return False
    saw_diff = False
    for src_line, tgt_line in zip(src_lines, tgt_lines, strict=True):
        if src_line == tgt_line:
            continue
        saw_diff = True
        if "←" in src_line or "←" in tgt_line:
            if src_line.split("←", 1)[0].rstrip() != tgt_line.split("←", 1)[0].rstrip():
                return False
            ru_label = src_line.split("←", 1)[-1]
            en_label = tgt_line.split("←", 1)[-1]
            if _CYRILLIC.search(ru_label) and not _CYRILLIC.search(en_label):
                continue
            return False
        if _CYRILLIC.search(src_line) and not _CYRILLIC.search(tgt_line):
            continue
        return False
    return saw_diff


def _fence_diff_is_whitespace_only(
    source_content: str,
    target_content: str,
) -> bool:
    """True when bodies differ only by blank lines or trailing spaces."""

    def _lines(text: str) -> list[str]:
        return [line.rstrip() for line in text.splitlines() if line.strip()]

    return _lines(source_content) == _lines(target_content)


def _fence_lang(info: str) -> str:
    parts = (info or "").strip().split()
    return parts[0].lower() if parts else ""


def fence_content_matches_source(
    source_content: str,
    target_content: str,
    *,
    fence_info: str = "",
) -> bool:
    """True when target fence body equals source, modulo allowed pipeline edits."""
    if _normalize_fence_content_for_compare(source_content) == _normalize_fence_content_for_compare(
        target_content
    ):
        return True
    if _fence_diff_is_whitespace_only(source_content, target_content):
        return True
    if _fence_lang(fence_info) == "text" and _fence_diff_is_text_diagram_label_translation(
        source_content, target_content
    ):
        return True
    if _fence_diff_is_mermaid_label_translation(source_content, target_content):
        return True
    if _fence_diff_is_comment_translation_only(source_content, target_content):
        return True
    if _fence_diff_is_angle_placeholder_translation(source_content, target_content):
        return True
    # Comments + angle placeholders in one fence (YAML examples, #47164).
    collapsed = _collapse_translated_angle_placeholders(source_content, target_content)
    if collapsed is None:
        return False
    src_n, tgt_n = collapsed
    return _fence_diff_is_comment_translation_only(src_n, tgt_n)


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
    if not fence_structure_is_round_trip_stable(
        source_text, lang=source_lang
    ) and fence_marker_tokens(source_text) == fence_marker_tokens(target_text):
        # The target preserves the only unambiguous contract available for a
        # malformed legacy file: the exact ordered marker sequence. Our AST
        # cannot reliably pair bodies in that case; Diplodoc build is the gate.
        return []
    src_blocks = code_blocks_from_text(source_text)
    tgt_blocks = code_blocks_from_text(target_text)
    if len(src_blocks) != len(tgt_blocks):
        return [
            f"fence_body_copy: block count source {len(src_blocks)} vs target {len(tgt_blocks)}"
        ]
    warnings: list[str] = []
    for i, (src, tgt) in enumerate(zip(src_blocks, tgt_blocks, strict=True), start=1):
        fence_info = src.info if isinstance(src, FencedCode) else ""
        if fence_content_matches_source(src.content, tgt.content, fence_info=fence_info):
            continue
        preview = tgt.content.strip().splitlines()[0][:80] if tgt.content.strip() else "(empty)"
        warnings.append(
            f"fence_body_copy: block {i} body changed by pipeline (first line: «{preview}»)"
        )
    return warnings


def check_absolute_paths_in_fences(source_text: str, target_text: str) -> list[str]:
    """Warn when RU fence lines use /opt/ydb/... but EN counterpart line lost the prefix."""
    warnings: list[str] = []
    src_blocks = code_blocks_from_text(source_text)
    tgt_blocks = code_blocks_from_text(target_text)
    if len(src_blocks) != len(tgt_blocks):
        return warnings
    for i, (src, tgt) in enumerate(zip(src_blocks, tgt_blocks, strict=True), start=1):
        src_lines = src.content.splitlines()
        tgt_lines = tgt.content.splitlines()
        if len(src_lines) != len(tgt_lines):
            continue
        for line_no, (sl, tl) in enumerate(zip(src_lines, tgt_lines, strict=True), start=1):
            if (
                "/opt/ydb/" in sl
                and "/opt/ydb/" not in tl
                and re.search(r"(?<!/opt/ydb/)(?:ca\.crt|node\.crt|node\.key)", tl)
            ):
                warnings.append(
                    f"fence_path_stripped: block {i} line {line_no}: "
                    f"RU has absolute cert path, EN shortened to relative"
                )
    return warnings
