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
_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}\s+)(.*)$")
_HEADING_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_DIPLODOC_T_HEADING = "{#T}"


def fix_wikipedia_links_for_en(text: str) -> str:
    """Use English Wikipedia URLs and slugs in EN articles."""
    out = _WIKI_RU_HOST_RE.sub("https://en.wikipedia.org/wiki/", text)
    for ru_slug, en_slug in _WIKI_SLUG_MAP.items():
        out = out.replace(ru_slug, en_slug)
    return out


_HEADING_BEFORE_LIST_TABS_RE = re.compile(
    r"^(?P<heading>#{1,6}\s+.+?\{#[^}]+\})\s*"
    r"(?:\{#[^}]+\}\s*)?"
    r"(?P<tabs>\{%\s*list\s+tabs)",
    re.MULTILINE | re.IGNORECASE,
)
_ENDLIST_GLUE_RE = re.compile(
    r"(\{%\s*endlist\s*%\})(?=[^\s\n])",
    re.IGNORECASE,
)
_LIST_TABS_TRAILING_ANCHOR_RE = re.compile(
    r"(\{%\s*list\s+tabs[^%]*%\})\s*\{#[^}]+\}\s*",
    re.IGNORECASE,
)


def fix_list_tabs_markdown_layout(text: str) -> str:
    """Blank line before ``{% list tabs %}``; newline after ``{% endlist %}``."""
    out = _HEADING_BEFORE_LIST_TABS_RE.sub(r"\g<heading>\n\n\g<tabs>", text)
    out = _ENDLIST_GLUE_RE.sub(r"\1\n\n", out)
    out = _LIST_TABS_TRAILING_ANCHOR_RE.sub(r"\1\n\n", out)
    return out


def fix_heading_structure_from_ru(ru_source: str, en_text: str) -> str:
    """
    When EN glued a section title and body into one heading line, split using RU anchors.
    """
    ru_by_anchor: dict[str, str] = {}
    for line in ru_source.splitlines():
        m = re.match(r"^(#{1,6}\s+)(.+?)(\s*\{#[^}]+\})\s*$", line)
        if m:
            ru_by_anchor[m.group(3).strip()] = m.group(2).strip()

    out_lines: list[str] = []
    for line in en_text.splitlines():
        m = re.match(r"^(#{1,6}\s+)(.+)$", line)
        if not m:
            out_lines.append(line)
            continue
        prefix, body = m.group(1), m.group(2)
        anchor_m = re.search(r"(\{#[^}]+\})\s*$", body)
        if not anchor_m:
            out_lines.append(line)
            continue
        anchor = anchor_m.group(1)
        ru_title = ru_by_anchor.get(anchor)
        if not ru_title:
            out_lines.append(line)
            continue
        before_anchor = body[: body.rfind(anchor)].strip()
        if len(before_anchor) <= len(ru_title) + 40:
            out_lines.append(line)
            continue
        split_m = re.match(r"^(.{8,140}?[.!?])\s+(.+)$", before_anchor, re.DOTALL)
        if split_m:
            out_lines.append(f"{prefix}{split_m.group(1).strip()} {anchor}")
            rest = split_m.group(2).strip()
            if rest:
                out_lines.append(rest)
            continue
        words = before_anchor.split()
        if len(words) >= 8:
            title_part = " ".join(words[: min(8, len(words) - 1)])
            rest = " ".join(words[len(title_part.split()) :])
            out_lines.append(f"{prefix}{title_part} {anchor}")
            if rest.strip():
                out_lines.append(rest.strip())
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def fix_en_heading_lines(text: str) -> str:
    """Strip markdown links and duplicate anchors from heading lines."""
    out_lines: list[str] = []
    for line in text.splitlines():
        m = _HEADING_PREFIX_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        prefix, body = m.group(1), m.group(2)
        body = _HEADING_LINK_RE.sub("", body)
        body = body.replace("[{#T}]", "").replace(_DIPLODOC_T_HEADING, "")
        anchors = re.findall(r"\{#[^}]+\}", body)
        body = re.sub(r"\s*\{#[^}]+\}", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        body = re.sub(r"\s+`([a-z][a-z0-9_-]*)`\s*", " ", body, flags=re.IGNORECASE)
        body = re.sub(r"\s+", " ", body).strip()
        unique_anchors: list[str] = []
        for a in anchors:
            if a not in unique_anchors:
                unique_anchors.append(a)
        anchor = unique_anchors[0] if unique_anchors else ""
        new_line = f"{prefix}{body}"
        if anchor:
            new_line = f"{new_line} {anchor}"
        out_lines.append(new_line.rstrip())
    return "\n".join(out_lines)


def normalize_en_spacing_after_slots(text: str) -> str:
    """Fix missing spaces after diplodoc macros (avoid generic backtick rules)."""
    return re.sub(r"\}\}([A-Za-z])", r"}} \1", text)


def fix_spurious_backtick_padding(text: str) -> str:
    """`` ` stdin ` `` → `` `stdin` ``; ``stdin`or`stdout`` → spaced form."""
    out = re.sub(r"`\s+([^`\n]+?)\s+`", r"`\1`", text)
    out = re.sub(
        r"([a-zA-Z0-9])`([^`\n]{1,8})`([a-zA-Z0-9])",
        r"\1 `\2` \3",
        out,
    )
    return out


def fix_space_before_markdown_link(text: str) -> str:
    """``is[disabled]`` → ``is [disabled]``."""
    return re.sub(r"(\w)\[([^\]]+)\]\(", r"\1 [\2](", text)


_RU_LINK_LABEL_MAP: dict[str, str] = {
    "выключено": "disabled",
}


def fix_ru_link_labels_in_en(text: str) -> str:
    """Deterministic EN labels for common RU link text left untranslated."""
    out = text
    for ru_label, en_label in _RU_LINK_LABEL_MAP.items():
        out = out.replace(f"[{ru_label}]", f"[{en_label}]")
    return out


_STRAY_ANCHOR_GLUE_RE = re.compile(r"([\w)\]])\{#([a-zA-Z0-9_-]+)\}(\.)")


def strip_stray_heading_anchors_in_prose(text: str) -> str:
    """Remove ``{#anchor}`` glued to prose (e.g. ``injections{#one-request}.``)."""
    return _STRAY_ANCHOR_GLUE_RE.sub(r"\1\3", text)


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
    """Deterministic EN cleanup after file merge (no LLM)."""
    from ydbdoc_review.ru_en_sync import finalize_en_document_from_ru

    out = finalize_en_document_from_ru(ru_source, en_text)
    out = apply_deterministic_cli_fixes(out, ru_source=ru_source)
    out = fix_dashed_cli_flags(out)
    out = fix_common_ru_leaks_in_en(out)
    out = fix_ru_link_labels_in_en(out)
    out = fix_spurious_backtick_padding(out)
    out = normalize_en_spacing_after_slots(out)
    out = fix_space_before_markdown_link(out)
    out = strip_stray_heading_anchors_in_prose(out)
    out = fix_yandex_cloud_links_for_en(out)
    out = fix_wikipedia_links_for_en(out)
    out = fix_heading_structure_from_ru(ru_source, out)
    out = fix_en_heading_lines(out)
    out = fix_heading_anchors_from_ru(ru_source, out)
    out = fix_list_tabs_markdown_layout(out)
    from ydbdoc_review.table_ast import repair_table_rows_from_ru

    out = repair_table_rows_from_ru(ru_source, out)
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
