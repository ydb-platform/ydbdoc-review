"""Heuristics and fixes after machine translation."""

from __future__ import annotations

import os
import re


def _markdown_code_fences_balanced(md: str) -> bool:
    open_fence = False
    for line in md.split("\n"):
        s = line.strip()
        if s.startswith("```"):
            open_fence = not open_fence
    return not open_fence

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_YANDEX_RU_DOCS_RE = re.compile(
    r"(https?://yandex\.cloud)/ru/docs/",
    re.IGNORECASE,
)
_LIST_TABS_RE = re.compile(r"\{%\s*list\s+tabs", re.IGNORECASE)
_FENCE_OPEN_RE = re.compile(r"^```(\w*)", re.MULTILINE)


def _count_list_tabs(text: str) -> int:
    return len(_LIST_TABS_RE.findall(text))


def tabs_missing_vs_source(
    source: str,
    translated: str,
    *,
    source_diff: str | None = None,
) -> bool:
    """
    True when translated text is missing ``{% list tabs %}`` blocks or tab items vs *source*.

    With *source_diff*, only sections touched by the PR diff are checked. Untouched
    sections may keep EN from main even when RU gained tabs elsewhere (minimal PR scope).
    """
    from ydbdoc_review.ru_en_structure import tab_items_missing_vs_source

    if tab_items_missing_vs_source(source, translated, source_diff=source_diff):
        return True
    if _count_list_tabs(source) == 0:
        return False
    if _count_list_tabs(translated) >= _count_list_tabs(source):
        return False
    if not source_diff or not source_diff.strip():
        return True
    from ydbdoc_review.markdown_sections import (
        align_sections_by_heading,
        section_indices_touched_by_diff,
        split_markdown_sections,
    )

    ru_sections = split_markdown_sections(source)
    en_sections = split_markdown_sections(translated)
    touched = section_indices_touched_by_diff(source_diff, ru_sections)
    if not touched or len(touched) >= len(ru_sections):
        return True
    aligned = align_sections_by_heading(ru_sections, en_sections)
    required = 0
    actual = 0
    for ru_sec in ru_sections:
        if ru_sec.index not in touched:
            continue
        required += _count_list_tabs(ru_sec.content)
        en_sec = aligned[ru_sec.index] if ru_sec.index < len(aligned) else None
        if en_sec is not None:
            actual += _count_list_tabs(en_sec.content)
    return required > 0 and actual < required


def en_contains_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def cyrillic_repair_enabled() -> bool:
    raw = os.environ.get("YDBDOC_REPAIR_CYRILLIC", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def repair_en_cyrillic_from_ru(
    settings: object,
    *,
    ru_path: str,
    ru_full: str,
    en_text: str,
) -> tuple[str, bool]:
    """
    Re-translate ``##`` sections that still contain Cyrillic in EN (common on long docs).

    Returns ``(markdown, changed)``.
    """
    if not cyrillic_repair_enabled() or not en_contains_cyrillic(en_text):
        return en_text, False

    from ydbdoc_review.llm import translate_markdown
    from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
    from ydbdoc_review.markdown_sections import (
        MarkdownSection,
        align_sections_by_heading,
        join_markdown_sections,
        split_markdown_sections,
    )

    ru_sections = split_markdown_sections(ru_full)
    en_sections = split_markdown_sections(en_text)
    aligned = align_sections_by_heading(ru_sections, en_sections)
    out: list[MarkdownSection] = []
    changed = False

    for ru_sec in ru_sections:
        en_sec = aligned[ru_sec.index] if ru_sec.index < len(aligned) else None
        body = en_sec.content if en_sec is not None else ""
        if en_sec is None or en_contains_cyrillic(body):
            text = translate_markdown(
                settings,
                source_lang="Russian",
                target_lang="English",
                source_path=ru_path,
                source_text=ru_sec.content,
            )
            text = restore_markdown_links_from_ru(ru_sec.content, text)
            text = apply_deterministic_cli_fixes(text, ru_source=ru_full)
            changed = True
        else:
            text = body
        out.append(
            MarkdownSection(
                index=ru_sec.index,
                heading=ru_sec.heading,
                content=text.strip(),
                start_line=ru_sec.start_line,
                end_line=ru_sec.end_line,
            )
        )

    merged = join_markdown_sections(out)
    merged = fix_unbalanced_fences(merged, reference=ru_full)
    return merged, changed


def fix_yandex_cloud_links_for_en(text: str) -> str:
    """Use English Yandex Cloud doc URLs in EN articles."""
    return _YANDEX_RU_DOCS_RE.sub(r"\1/en/docs/", text)


def _fence_lang_tags(text: str) -> set[str]:
    return {m.group(1).lower() for m in _FENCE_OPEN_RE.finditer(text) if m.group(1)}


def chunk_lost_yaml_fence(source_chunk: str, translated_chunk: str) -> bool:
    """True if source opens a fenced yaml block but translation likely dropped the opener."""
    src_tags = _fence_lang_tags(source_chunk)
    if "yaml" not in src_tags:
        return False
    out_tags = _fence_lang_tags(translated_chunk)
    if "yaml" in out_tags:
        return False
    # Source has ```yaml but translation has no fence at all in first lines
    head = translated_chunk.lstrip()[:800]
    return "```" not in head and len(translated_chunk) > 200


_CONFIG_DIR_NO_SPACE_RE = re.compile(r"--config-dir/[^\s]")
_TOKEN_FILENAME_RE = r"(?:token-file|auth_token)"
_REDIRECT_TOKEN_RE = re.compile(rf">\s*({_TOKEN_FILENAME_RE})\b")
_READ_TOKEN_RE = re.compile(rf"(?<=[-\s])f\s+({_TOKEN_FILENAME_RE})\b")
_TOKEN_FILE_ARG_RE = re.compile(rf"--token-file\s+({_TOKEN_FILENAME_RE})\b")


def _token_filenames_used(text: str) -> set[str]:
    names: set[str] = set()
    for pat in (_REDIRECT_TOKEN_RE, _READ_TOKEN_RE, _TOKEN_FILE_ARG_RE):
        names.update(pat.findall(text))
    return names


def _pick_canonical_token_filename(
    translated: str,
    *,
    en_main: str | None = None,
    ru_source: str | None = None,
) -> str:
    if ru_source:
        from ydbdoc_review.ru_en_alignment import canonical_token_filename_from_ru

        ru_canon = canonical_token_filename_from_ru(ru_source)
        if ru_canon:
            return ru_canon
    main_names = _token_filenames_used(en_main) if en_main else set()
    if len(main_names) == 1:
        return next(iter(main_names))
    cur = _token_filenames_used(translated)
    if "auth_token" in cur:
        return "auth_token"
    if "token-file" in cur:
        return "token-file"
    return "auth_token"


def fix_config_dir_spacing(text: str) -> str:
    """``--config-dir/path`` → ``--config-dir /path``."""
    return re.sub(r"--config-dir/(\S+)", r"--config-dir /\1", text)


def fix_token_file_inconsistency(
    text: str,
    *,
    en_main: str | None = None,
    ru_source: str | None = None,
    canonical: str | None = None,
) -> str:
    """Unify redirect / ``-f`` / ``--token-file`` operand to one basename."""
    pick = canonical or _pick_canonical_token_filename(
        text, en_main=en_main, ru_source=ru_source
    )
    names = _token_filenames_used(text)
    if names == {pick}:
        return text
    other = "auth_token" if pick == "token-file" else "token-file"
    out = re.sub(rf">\s*{re.escape(other)}\b", f"> {pick}", text)
    out = re.sub(rf"(?<=[-\s])f\s+{re.escape(other)}\b", f"f {pick}", out)
    out = re.sub(
        rf"--token-file\s+{re.escape(other)}\b",
        f"--token-file {pick}",
        out,
    )
    return out


def _close_trailing_fence_block(text: str) -> str:
    if _markdown_code_fences_balanced(text):
        return text
    open_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            open_fence = not open_fence
    if open_fence:
        return text.rstrip() + "\n```\n"
    return text


def fix_unbalanced_fences(text: str, *, reference: str | None = None) -> str:
    """Close dangling ``` per section (and whole file) after section-wise translation."""
    if _markdown_code_fences_balanced(text):
        return text
    if reference and reference.strip():
        from ydbdoc_review.markdown_sections import (
            MarkdownSection,
            align_sections_by_heading,
            join_markdown_sections,
            split_markdown_sections,
        )

        ru_sections = split_markdown_sections(reference)
        en_sections = split_markdown_sections(text)
        aligned = align_sections_by_heading(ru_sections, en_sections)
        fixed: list[MarkdownSection] = []
        for en_sec in en_sections:
            content = en_sec.content
            if not _markdown_code_fences_balanced(content):
                content = _close_trailing_fence_block(content)
            fixed.append(
                MarkdownSection(
                    index=en_sec.index,
                    heading=en_sec.heading,
                    content=content,
                    start_line=en_sec.start_line,
                    end_line=en_sec.end_line,
                )
            )
        text = join_markdown_sections(fixed)
    return _close_trailing_fence_block(text)


def apply_deterministic_cli_fixes(
    text: str,
    *,
    en_main: str | None = None,
    ru_source: str | None = None,
) -> str:
    """Fix known CLI copy-paste regressions without calling an LLM."""
    out = fix_config_dir_spacing(text)
    out = fix_token_file_inconsistency(out, en_main=en_main, ru_source=ru_source)
    return out


def apply_post_translation_fixes(
    text: str,
    *,
    en_main: str | None = None,
    ru_source: str | None = None,
    en_path: str = "",
) -> str:
    """Deterministic CLI + structural + fence repairs before quality gate."""
    out = apply_deterministic_cli_fixes(text, en_main=en_main, ru_source=ru_source)
    if ru_source:
        from ydbdoc_review.ru_en_structure import apply_structure_sync_from_ru

        out = apply_structure_sync_from_ru(ru_source, out, en_path=en_path)
    return fix_unbalanced_fences(out, reference=ru_source)


def cli_critical_issues(
    translated: str,
    *,
    en_main: str | None = None,
) -> list[str]:
    """CLI regressions that break commands (deterministic)."""
    issues: list[str] = []
    if _CONFIG_DIR_NO_SPACE_RE.search(translated):
        issues.append("config_dir_missing_space")
    if en_main and "--config-dir /" in en_main and _CONFIG_DIR_NO_SPACE_RE.search(translated):
        if "config_dir_missing_space" not in issues:
            issues.append("config_dir_missing_space")
    if len(_token_filenames_used(translated)) > 1:
        issues.append("token_file_inconsistent")
    if translated.rstrip().endswith("/opt") or translated.rstrip().endswith("/opt/"):
        issues.append("truncated_file")
    return issues


def translation_quality_issues(
    source: str,
    translated: str,
    *,
    target_lang: str,
    en_main: str | None = None,
    source_diff: str | None = None,
    ru_authority: str | None = None,
) -> list[str]:
    """Short issue codes; empty list means heuristics are satisfied."""
    issues: list[str] = []
    if len(source) < 500:
        return issues
    if not _markdown_code_fences_balanced(translated):
        issues.append("unbalanced_fences")
    lang = target_lang.strip().lower()
    if lang in ("english", "en"):
        from ydbdoc_review.ru_en_alignment import en_coverage_behind_ru, ru_authority_text

        ru_ref = ru_authority_text(source, ru_authority)
        if en_coverage_behind_ru(ru_ref, translated):
            issues.append("en_behind_ru")
        elif en_main and len(translated) < int(len(en_main) * 0.55):
            issues.append("too_short")
        elif not en_main and len(translated) < int(len(ru_ref) * 0.38):
            issues.append("too_short")
    elif len(translated) < int(len(source) * 0.62):
        issues.append("too_short")
    if tabs_missing_vs_source(source, translated, source_diff=source_diff):
        issues.append("missing_tabs")
    if lang in ("english", "en"):
        from ydbdoc_review.ru_en_structure import (
            index_bullets_behind_ru,
            tab_items_missing_vs_source,
        )

        ru_ref = ru_authority_text(source, ru_authority)
        if index_bullets_behind_ru(ru_ref, translated):
            issues.append("index_bullets_behind")
        if tab_items_missing_vs_source(
            ru_ref, translated, source_diff=source_diff
        ):
            if "missing_tabs" not in issues:
                issues.append("missing_tab_items")
    if target_lang.strip().lower() == "english" and en_contains_cyrillic(translated):
        issues.append("cyrillic_leak")
    if en_main and len(en_main) >= 400 and len(translated) < int(len(en_main) * 0.72):
        issues.append("truncated_file")
    issues.extend(cli_critical_issues(translated, en_main=en_main))
    if target_lang.strip().lower() == "english":
        from ydbdoc_review.ru_en_alignment import (
            critical_ru_en_mismatches,
            mismatch_gate_codes,
        )

        for code in critical_ru_en_mismatches(
            source,
            translated,
            en_reference=en_main or translated,
            ru_authority=ru_authority,
        ):
            if code in mismatch_gate_codes():
                issues.append(code)
    return issues


def translation_quality_gate_codes() -> frozenset[str]:
    return frozenset(
        {
            "too_short",
            "missing_tabs",
            "unbalanced_fences",
            "cyrillic_leak",
            "config_dir_missing_space",
            "token_file_inconsistent",
            "truncated_file",
            "config_dir_vs_yaml_config",
            "kafka_port_flags",
            "missing_kafka_prereq_ports",
            "ssd_group_count",
            "token_file_name",
            "en_behind_ru",
            "index_bullets_behind",
            "missing_tab_items",
        }
    )


def quality_gate_enabled() -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATION_QUALITY_GATE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def collect_quality_gate_failures(
    pairs: list[tuple[str, str, str, str | None, str | None, str | None]],
) -> list[str]:
    """
    *pairs*: ``(path, ru_text, en_text, en_on_main, ru_diff, ru_on_main)``.
    """
    if not quality_gate_enabled():
        return []
    out: list[str] = []
    for path, source, translated, en_main, source_diff, ru_authority in pairs:
        lang = "English" if "/en/" in path.replace("\\", "/") else "Russian"
        issues = translation_quality_issues(
            source,
            translated,
            target_lang=lang,
            en_main=en_main,
            source_diff=source_diff,
            ru_authority=ru_authority,
        )
        hit = sorted(translation_quality_gate_codes().intersection(issues))
        if hit:
            out.append(f"`{path}`: {', '.join(hit)}")
    return out


def should_retry_chunk(source_chunk: str, translated_chunk: str) -> bool:
    if len(source_chunk) < 400:
        return False
    if len(translated_chunk) < int(len(source_chunk) * 0.5):
        return True
    if chunk_lost_yaml_fence(source_chunk, translated_chunk):
        return True
    if not _markdown_code_fences_balanced(translated_chunk) and "```" in source_chunk:
        return True
    return False
