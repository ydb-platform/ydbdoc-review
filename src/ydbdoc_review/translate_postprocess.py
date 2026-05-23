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


def fix_config_dir_spacing(text: str) -> str:
    """``--config-dir/path`` → ``--config-dir /path``."""
    return re.sub(r"--config-dir/(\S+)", r"--config-dir /\1", text)


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


def apply_deterministic_cli_fixes(
    text: str,
    *,
    en_main: str | None = None,
    ru_source: str | None = None,
) -> str:
    """Fix known CLI copy-paste regressions without calling an LLM."""
    _ = en_main
    out = fix_config_dir_spacing(text)
    out = fix_cli_explain_commands(out)
    if ru_source:
        out = apply_semantic_fixes_from_ru(ru_source, out)
    return out
