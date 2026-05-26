"""Deterministic post-translate CLI fixes (no LLM calls).

Heuristic *quality* checks (cyrillic, length, fence balance, …) live in
``ydbdoc_review.heuristics``; this module is only for safe text edits that
fix common CLI/markdown copy-paste regressions.
"""

from __future__ import annotations

import re


_YANDEX_RU_DOCS_RE = re.compile(
    r"(https?://yandex\.cloud)/ru/docs/",
    re.IGNORECASE,
)


def fix_yandex_cloud_links_for_en(text: str) -> str:
    """Use English Yandex Cloud doc URLs in EN articles."""
    return _YANDEX_RU_DOCS_RE.sub(r"\1/en/docs/", text)


_EXPLAIN_EN_WRONG = (
    ("ydb table query explain --ast", "ydb sql --explain-ast"),
    ("ydb table query explain", "ydb sql --explain"),
)


def fix_cli_explain_commands(en_text: str) -> str:
    """Known stale EN CLI doc strings (interactive mode / special commands)."""
    out = en_text
    for wrong, right in _EXPLAIN_EN_WRONG:
        out = out.replace(wrong, right)
        out = out.replace(f"`{wrong}`", f"`{right}`")
    return out


_LLM_PROMPT_LEAK_TAIL_RE = re.compile(
    r"\s+--\s*(?:Please provide the text to translate\.?|"
    r"Пожалуйста,?\s+(?:предоставьте|переведите).*)$",
    re.IGNORECASE,
)
_YDB_SQL_S_LINE_RE = re.compile(
    r"^(\s*)(ydb(?:\s+-p\s+<[^>]+>)?\s+sql\s+-s\s+'[^']+')(\s+.*)?$",
    re.IGNORECASE,
)
_DEFAULT_YDB_SQL_STATS = "--stats full --format json-unicode"


def fix_llm_prompt_leaks_in_cli(en_text: str) -> str:
    """Remove comment-translation prompt text accidentally pasted into shell commands."""
    out_lines: list[str] = []
    for line in en_text.splitlines():
        line = _LLM_PROMPT_LEAK_TAIL_RE.sub("", line)
        m = _YDB_SQL_S_LINE_RE.match(line)
        if m and not (m.group(3) or "").strip():
            line = f"{m.group(1)}{m.group(2)} {_DEFAULT_YDB_SQL_STATS}"
        out_lines.append(line)
    return "\n".join(out_lines)


def fix_ydb_sql_flags_from_ru(ru_source: str, en_text: str) -> str:
    """Copy ``--stats ... --format ...`` (and similar tails) from RU ``ydb sql -s`` lines."""
    ru_tails: dict[str, str] = {}
    for ru_line in ru_source.splitlines():
        m = _YDB_SQL_S_LINE_RE.match(ru_line)
        if not m:
            continue
        tail = (m.group(3) or "").strip()
        if tail and "--stats" in tail:
            ru_tails[m.group(2)] = tail

    if not ru_tails:
        return en_text

    out_lines: list[str] = []
    for line in en_text.splitlines():
        m = _YDB_SQL_S_LINE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        prefix = m.group(2)
        tail = (m.group(3) or "").strip()
        ru_tail = ru_tails.get(prefix)
        bad_tail = (
            not tail
            or "please provide" in tail.lower()
            or ("--stats" not in tail and ru_tail is not None)
        )
        if bad_tail and ru_tail:
            out_lines.append(f"{m.group(1)}{prefix} {ru_tail}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def fix_grant_classifier_use_from_ru(ru_source: str, en_text: str) -> str:
    if not re.search(r"GRANT\s+USE\b", ru_source, re.IGNORECASE):
        return en_text
    if "classifier" not in ru_source.lower():
        return en_text
    return re.sub(
        r"(GRANT\s+)ALL(\s+ON\s+`[^`]*classifier[^`]*`)",
        r"\1USE\2",
        en_text,
        flags=re.IGNORECASE,
    )


def apply_semantic_fixes_from_ru(ru_source: str, en_text: str) -> str:
    """Copy command/ACL semantics from RU without an LLM call."""
    out = fix_llm_prompt_leaks_in_cli(en_text)
    out = fix_ydb_sql_flags_from_ru(ru_source, out)
    out = fix_cli_explain_commands(out)
    out = fix_grant_classifier_use_from_ru(ru_source, out)
    return out


_WIKI_RU_HOST_RE = re.compile(r"https?://ru\.wikipedia\.org/wiki/", re.IGNORECASE)
_WIKI_SLUG_MAP = {
    "Snappy_(библиотека)": "Snappy_(library)",
    "библиотека": "library",
}

_HEADING_LINE_RE = re.compile(r"^(#{1,3}\s+.+?)(\s*\{#([^}]+)\})?\s*$")


def fix_wikipedia_links_for_en(text: str) -> str:
    """Use English Wikipedia URLs and slugs in EN articles."""
    out = _WIKI_RU_HOST_RE.sub("https://en.wikipedia.org/wiki/", text)
    for ru_slug, en_slug in _WIKI_SLUG_MAP.items():
        out = out.replace(ru_slug, en_slug)
    return out


def fix_heading_anchors_from_ru(ru_source: str, en_text: str) -> str:
    """Copy ``{#anchor}`` ids from RU headings onto EN headings (same order)."""
    ru_heads = _heading_anchor_lines(ru_source)
    en_heads = _heading_anchor_lines(en_text)
    if not ru_heads or len(ru_heads) != len(en_heads):
        return en_text
    en_lines = en_text.splitlines()
    for (_, _, ru_anchor), (line_idx, en_prefix, en_anchor) in zip(
        ru_heads, en_heads, strict=True
    ):
        if ru_anchor and ru_anchor != en_anchor:
            en_lines[line_idx] = f"{en_prefix} {{#{ru_anchor}}}"
    return "\n".join(en_lines)


def _heading_anchor_lines(text: str) -> list[tuple[int, str, str | None]]:
    result: list[tuple[int, str, str | None]] = []
    for i, line in enumerate(text.splitlines()):
        m = _HEADING_LINE_RE.match(line)
        if m:
            result.append((i, m.group(1), m.group(3)))
    return result


def fix_dashed_cli_flags(text: str) -> str:
    """``-- uuid`` → ``--uuid`` (space wrongly inserted inside a flag name)."""
    return re.sub(
        r"(?<![\w-])--\s+([a-z][a-z0-9-]*)(\s+(?=<|\$|/|[A-Za-z0-9_./]))",
        r"--\1\2",
        text,
    )


_FQDN_VM_COMMENT_RE = re.compile(
    r"(#\s*)FQDN\s+ВМ\b",
    re.IGNORECASE,
)


def fix_common_ru_leaks_in_en(text: str) -> str:
    """RU placeholders and comment fragments that must not remain in EN docs."""
    out = text.replace("<строка>", "<string>")
    out = out.replace("<число>", "<number>")
    out = _FQDN_VM_COMMENT_RE.sub(r"\1VM FQDN", out)
    return out


def apply_en_postprocess_from_ru(ru_source: str, en_text: str) -> str:
    """Deterministic EN cleanup after segment merge (no LLM)."""
    from ydbdoc_review.markdown_links import restore_markdown_links_from_ru

    out = restore_markdown_links_from_ru(ru_source, en_text)
    out = apply_deterministic_cli_fixes(out, ru_source=ru_source)
    out = fix_dashed_cli_flags(out)
    out = fix_common_ru_leaks_in_en(out)
    from ydbdoc_review.tabs_repair import repair_tab_labels_from_source

    out, _ = repair_tab_labels_from_source(ru_source, out)
    out = fix_yandex_cloud_links_for_en(out)
    out = fix_wikipedia_links_for_en(out)
    out = fix_heading_anchors_from_ru(ru_source, out)
    return out


def apply_deterministic_cli_fixes(
    text: str,
    *,
    en_main: str | None = None,
    ru_source: str | None = None,
) -> str:
    """Fix known CLI copy-paste regressions without calling an LLM."""
    _ = en_main
    out = fix_cli_explain_commands(text)
    if ru_source:
        out = apply_semantic_fixes_from_ru(ru_source, out)
    return out
