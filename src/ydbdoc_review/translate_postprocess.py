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


def en_contains_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


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
    if "token-file" in translated and re.search(
        r"[-\s]f\s+auth_token\b|--token-file\s+auth_token\b", translated
    ):
        if re.search(r">\s*token-file\b", translated):
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
) -> list[str]:
    """Short issue codes; empty list means heuristics are satisfied."""
    issues: list[str] = []
    if len(source) < 500:
        return issues
    if not _markdown_code_fences_balanced(translated):
        issues.append("unbalanced_fences")
    if len(translated) < int(len(source) * 0.62):
        issues.append("too_short")
    src_tabs = len(_LIST_TABS_RE.findall(source))
    out_tabs = len(_LIST_TABS_RE.findall(translated))
    if src_tabs > 0 and out_tabs < src_tabs:
        issues.append("missing_tabs")
    if target_lang.strip().lower() == "english" and en_contains_cyrillic(translated):
        issues.append("cyrillic_leak")
    issues.extend(cli_critical_issues(translated, en_main=en_main))
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
        }
    )


def quality_gate_enabled() -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATION_QUALITY_GATE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def collect_quality_gate_failures(
    pairs: list[tuple[str, str, str, str | None]],
) -> list[str]:
    """
    *pairs*: ``(path, source_text, translated_text, en_on_main_or_none)``.
    """
    if not quality_gate_enabled():
        return []
    out: list[str] = []
    for path, source, translated, en_main in pairs:
        lang = "English" if "/en/" in path.replace("\\", "/") else "Russian"
        issues = translation_quality_issues(
            source, translated, target_lang=lang, en_main=en_main
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
