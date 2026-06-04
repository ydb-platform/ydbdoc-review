"""Deterministic post-translation heuristics (Phase E)."""

from __future__ import annotations

import re
from typing import Literal

from ydbdoc_review.parsing.ast_types import FencedCode
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.redirects import (
    RedirectValidationIssue,
    validate_redirect_merge,
)
from ydbdoc_review.navigation.toc import TocValidationIssue, validate_toc_merge

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_TABS = re.compile(r"\{%\s*list\s+tabs\b")
_PLACEHOLDER = re.compile(r"⟦[^⟧]+⟧")

_LENGTH_RATIO_MIN = 0.55
_LENGTH_RATIO_MAX = 1.85
_LENGTH_RATIO_BORDERLINE_MIN = 0.45
_LENGTH_RATIO_BORDERLINE_MAX = 2.2


def _strip_fenced_blocks(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    fence_char = ""
    for line in lines:
        m = re.match(r"^(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if not in_fence:
            out.append(line)
    return "".join(out)


def _plain_text_length(text: str) -> int:
    body = _strip_fenced_blocks(text)
    body = _PLACEHOLDER.sub("", body)
    return len(re.sub(r"\s+", "", body))


def check_length_ratio(
    source_text: str,
    target_text: str,
    *,
    source_lang: str,
    target_lang: str,
) -> list[str]:
    """RU↔EN length ratio on prose-like content (fences stripped)."""
    src_len = _plain_text_length(source_text)
    tgt_len = _plain_text_length(target_text)
    if src_len < 40 or tgt_len < 40:
        return []
    ratio = tgt_len / src_len if src_len else 0.0
    if _LENGTH_RATIO_MIN <= ratio <= _LENGTH_RATIO_MAX:
        return []
    label = f"{source_lang}→{target_lang}"
    if _LENGTH_RATIO_BORDERLINE_MIN <= ratio < _LENGTH_RATIO_MIN:
        return [f"length_ratio: {label} ratio {ratio:.2f} (short vs source, borderline)"]
    if _LENGTH_RATIO_MAX < ratio <= _LENGTH_RATIO_BORDERLINE_MAX:
        return [f"length_ratio: {label} ratio {ratio:.2f} (long vs source, borderline)"]
    return [f"length_ratio: {label} ratio {ratio:.2f} outside sane bounds"]


def check_cyrillic_in_en(target_text: str, *, target_lang: str) -> list[str]:
    """Cyrillic letters in English target outside fenced code."""
    if target_lang.lower() != "en":
        return []
    body = _strip_fenced_blocks(target_text)
    matches = list(_CYRILLIC.finditer(body))
    if not matches:
        return []
    warnings: list[str] = []
    seen_snippets: set[str] = set()
    for match in matches[:12]:
        start = max(0, match.start() - 25)
        end = min(len(body), match.end() + 25)
        snippet = body[start:end].replace("\n", " ").strip()
        if snippet in seen_snippets:
            continue
        seen_snippets.add(snippet)
        line = body.count("\n", 0, match.start()) + 1
        warnings.append(
            f"Кириллица в EN-тексте (строка ~{line}): «{snippet}»"
        )
    if len(matches) > 12:
        warnings.append(
            f"… и ещё {len(matches) - 12} вхождений кириллицы "
            f"(всего {len(matches)} символов)"
        )
    return warnings


def count_fence_markers(text: str) -> int:
    """Opening fence markers inside one segment's text (paragraph/table cells)."""
    return len(_FENCE_OPEN.findall(text))


def _count_fenced_code_blocks(text: str) -> int:
    """Fenced code blocks in a full markdown file (AST), not ``` lines inside blocks."""
    doc = parse_markdown(text)
    count = 0

    def walk(blocks: list) -> None:
        nonlocal count
        for block in blocks:
            if isinstance(block, FencedCode):
                count += 1
            children = getattr(block, "children", None)
            if children:
                walk(children)

    walk(doc.children)
    return count


def check_fence_parity(source_text: str, target_text: str) -> list[str]:
    src = _count_fenced_code_blocks(source_text)
    tgt = _count_fenced_code_blocks(target_text)
    if src == tgt:
        return []
    return [f"fence_parity: source {src} fenced blocks vs target {tgt}"]


def check_heading_parity(source_text: str, target_text: str) -> list[str]:
    src = len(_HEADING.findall(source_text))
    tgt = len(_HEADING.findall(target_text))
    if src == tgt:
        return []
    return [f"heading_parity: source {src} headings vs target {tgt}"]


def check_list_tab_parity(source_text: str, target_text: str) -> list[str]:
    src = len(_LIST_TABS.findall(source_text))
    tgt = len(_LIST_TABS.findall(target_text))
    if src == tgt:
        return []
    return [f"list_tab_parity: source {src} tab blocks vs target {tgt}"]


def run_file_heuristics(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
) -> list[str]:
    """Run all markdown file heuristics; return human-readable warning strings."""
    from ydbdoc_review.validation.fence_integrity import (
        check_absolute_paths_in_fences,
        check_fence_body_copy,
    )
    from ydbdoc_review.validation.ru_source_bugs import (
        check_required_anchor_lines,
        detect_ru_source_bugs,
    )

    warnings: list[str] = []
    if source_lang.lower() in {"ru", "russian"}:
        warnings.extend(detect_ru_source_bugs(source_text))
    warnings.extend(
        check_length_ratio(
            source_text, target_text, source_lang=source_lang, target_lang=target_lang
        )
    )
    warnings.extend(check_cyrillic_in_en(target_text, target_lang=target_lang))
    warnings.extend(check_fence_parity(source_text, target_text))
    warnings.extend(check_fence_body_copy(source_text, target_text))
    warnings.extend(check_absolute_paths_in_fences(source_text, target_text))
    warnings.extend(check_required_anchor_lines(source_text, target_text))
    warnings.extend(check_heading_parity(source_text, target_text))
    warnings.extend(check_list_tab_parity(source_text, target_text))
    return warnings


def _issue_strings(issues: list[TocValidationIssue] | list[RedirectValidationIssue]) -> list[str]:
    return [f"{issue.kind}: {issue.detail}" for issue in issues]


def validate_toc_merge_warnings(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_hrefs: set[str],
    en_main_yaml: str,
) -> list[str]:
    """Wrap ``validate_toc_merge`` for reporting."""
    return _issue_strings(
        validate_toc_merge(
            ru_pr_yaml,
            en_merged_yaml,
            translate_hrefs=translate_hrefs,
            en_main_yaml=en_main_yaml,
        )
    )


def validate_redirect_merge_warnings(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_from_paths: set[str],
    en_main_yaml: str,
) -> list[str]:
    """Wrap ``validate_redirect_merge`` for reporting."""
    return _issue_strings(
        validate_redirect_merge(
            ru_pr_yaml,
            en_merged_yaml,
            translate_from_paths=translate_from_paths,
            en_main_yaml=en_main_yaml,
        )
    )


def validate_navigation_merge_warnings(
    path: str,
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    en_main_yaml: str,
    translate_scope: set[str],
) -> list[str]:
    """TOC or redirect merge validation based on ``path`` kind."""
    kind = navigation_yaml_kind(path)
    if kind == "toc":
        return validate_toc_merge_warnings(
            ru_pr_yaml,
            en_merged_yaml,
            translate_hrefs=translate_scope,
            en_main_yaml=en_main_yaml,
        )
    if kind == "redirect":
        return validate_redirect_merge_warnings(
            ru_pr_yaml,
            en_merged_yaml,
            translate_from_paths=translate_scope,
            en_main_yaml=en_main_yaml,
        )
    return []


def bump_verdict_for_heuristics(verdict: Literal["ok", "warnings", "blocked"], warnings: list[str]) -> Literal["ok", "warnings", "blocked"]:
    if warnings and verdict == "ok":
        return "warnings"
    return verdict
