"""Quality heuristics for translated documentation.

Reads ``prompts/09_quality_heuristics.md``: each ```yaml block is one rule.
Python implements known rules (length, cyrillic, fence balance, ...);
unknown rules are delegated to an LLM call that returns findings as JSON.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ydbdoc_review.config import Settings
from ydbdoc_review.fm_progress import fm_log


Severity = str  # "warning" | "critical"


@dataclass(frozen=True)
class Rule:
    name: str
    severity: Severity
    applies_to: str  # "ru_to_en" | "en_to_ru" | "any"
    description: str
    report_message: str


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    location: str
    detail: str


def _parse_yaml_block(block_text: str) -> dict[str, str]:
    """Minimal YAML subset: top-level keys with scalar or block (`|`) values."""
    out: dict[str, str] = {}
    lines = block_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key = m.group(1)
        rest = m.group(2)
        if rest.strip() == "|":
            i += 1
            body: list[str] = []
            indent: int | None = None
            while i < len(lines):
                bl = lines[i]
                if bl.strip() == "" and (indent is None or True):
                    body.append("")
                    i += 1
                    continue
                stripped = bl.lstrip()
                this_indent = len(bl) - len(stripped)
                if indent is None and stripped:
                    indent = this_indent
                if stripped and this_indent < (indent or 0):
                    break
                body.append(stripped if indent is None else bl[indent:])
                i += 1
            out[key] = "\n".join(body).strip()
            continue
        out[key] = rest.strip()
        i += 1
    return out


def load_rules(prompts_dir: str) -> list[Rule]:
    path = Path(prompts_dir) / "09_quality_heuristics.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n([\s\S]*?)\n```", text)
    rules: list[Rule] = []
    for block in blocks:
        fields = _parse_yaml_block(block)
        name = fields.get("name", "").strip()
        if not name:
            continue
        rules.append(
            Rule(
                name=name,
                severity=fields.get("severity", "warning").strip(),
                applies_to=fields.get("applies_to", "any").strip(),
                description=fields.get("description", "").strip(),
                report_message=fields.get(
                    "report_message", "{detail}"
                ).strip(),
            )
        )
    return rules


def _direction(source_lang: str, target_lang: str) -> str:
    s = source_lang.strip().lower()
    t = target_lang.strip().lower()
    if s.startswith("rus") and t in ("english", "en"):
        return "ru_to_en"
    if s.startswith("eng") and t in ("russian", "ru"):
        return "en_to_ru"
    return "any"


def _rule_applies(rule: Rule, direction: str) -> bool:
    return rule.applies_to in ("any", direction)


_CYRILLIC_RE = re.compile(r"[Ѐ-ӿѐ-џ]")


def _strip_code_fences(text: str) -> str:
    parts: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        s = line.lstrip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            parts.append(line)
    return "\n".join(parts)


def _strip_backtick_spans(text: str) -> str:
    # Drop inline-code (`...`) so identifiers in backticks do not raise the cyrillic flag.
    return re.sub(r"`[^`\n]+`", " ", text)


def _check_cyrillic_in_en(*, source: str, translation: str, **_: Any) -> Finding | None:
    matches = list(_CYRILLIC_RE.finditer(translation))
    if not matches:
        return None
    sample = []
    seen: set[str] = set()
    for m in matches:
        start = max(0, m.start() - 12)
        end = min(len(translation), m.end() + 12)
        snippet = translation[start:end].replace("\n", " ").strip()
        if snippet in seen:
            continue
        seen.add(snippet)
        sample.append(f"«…{snippet}…»")
        if len(sample) >= 3:
            break
    line_no = translation[: matches[0].start()].count("\n") + 1
    return Finding(
        rule="cyrillic_in_en",
        severity="warning",
        location=f"строка {line_no}",
        detail=f"{len(matches)} вхождений, например: " + "; ".join(sample),
    )


def _check_file_length_mismatch(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    s = len(source)
    t = len(translation)
    base = max(s, 1)
    ratio = abs(s - t) / base
    if ratio <= 0.25:
        return None
    direction = "длиннее" if t > s else "короче"
    return Finding(
        rule="file_length_mismatch",
        severity="critical",
        location="весь файл",
        detail=f"TRANSLATION {direction} SOURCE на {ratio * 100:.0f}% "
        f"({t} vs {s} символов)",
    )


def _count_headings(text: str, level: int) -> int:
    pat = re.compile(r"^" + (r"#" * level) + r"\s+\S", re.MULTILINE)
    return len(pat.findall(text))


def _check_heading_count_mismatch(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    s2 = _count_headings(source, 2)
    s3 = _count_headings(source, 3)
    t2 = _count_headings(translation, 2)
    t3 = _count_headings(translation, 3)
    if s2 == t2 and s3 == t3:
        return None
    return Finding(
        rule="heading_count_mismatch",
        severity="critical",
        location="весь файл",
        detail=f"## SOURCE={s2} TRANSLATION={t2}; ### SOURCE={s3} TRANSLATION={t3}",
    )


def _fence_delimiter_count(text: str) -> int:
    return sum(
        1 for line in text.splitlines() if line.lstrip().startswith("```")
    )


def _check_fence_parity(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    """RU↔EN fenced-block parity: count, closers, and EN delimiter balance."""
    from ydbdoc_review.fence_repair import extract_fence_blocks

    src_blocks = extract_fence_blocks(source)
    trn_blocks = extract_fence_blocks(translation)
    src_ticks = _fence_delimiter_count(source)
    trn_ticks = _fence_delimiter_count(translation)
    problems: list[str] = []

    if trn_ticks % 2 != 0:
        problems.append(
            f"в TRANSLATION нечётное число строк с ``` ({trn_ticks})"
        )
    if src_ticks % 2 != 0:
        problems.append(
            f"в SOURCE нечётное число строк с ``` ({src_ticks})"
        )
    if len(src_blocks) != len(trn_blocks):
        problems.append(
            f"число закрытых fenced-блоков SOURCE={len(src_blocks)}, "
            f"TRANSLATION={len(trn_blocks)}"
        )
    if trn_ticks % 2 == 0 and trn_ticks > 0:
        expected_closed = trn_ticks // 2
        if len(trn_blocks) < expected_closed:
            problems.append(
                f"в TRANSLATION незакрытые блоки: ```×{trn_ticks}, "
                f"закрытых блоков {len(trn_blocks)}"
            )
    if not problems:
        return None
    return Finding(
        rule="fence_parity",
        severity="critical",
        location="fenced-блоки",
        detail="; ".join(problems[:4]),
    )


def _check_fence_unbalanced(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    """Backward-compatible alias: delegates to ``fence_parity``."""
    return _check_fence_parity(source=source, translation=translation)


_SKIP_FENCE_CODE_LINE = re.compile(r"^SELECT\s*$", re.IGNORECASE)


def _extract_fence_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    in_fence = False
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if in_fence:
                blocks.append(current)
                current = []
                in_fence = False
            else:
                in_fence = True
            continue
        if in_fence:
            current.append(line)
    return blocks


def _normalize_fence_code_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("--") and "(" not in stripped.split("--", 1)[0]:
        return None
    code = re.split(r"\s--\s", line, maxsplit=1)[0].strip().rstrip(",").rstrip(";").strip()
    if not code or _SKIP_FENCE_CODE_LINE.match(code):
        return None
    return re.sub(r"\s+", " ", code)


def _block_code_line_keys(lines: list[str]) -> list[str]:
    keys: list[str] = []
    for line in lines:
        key = _normalize_fence_code_line(line)
        if key is not None:
            keys.append(key)
    return keys


def _fence_code_line_keys(text: str) -> list[list[str]]:
    return [_block_code_line_keys(block) for block in _extract_fence_blocks(text)]


def _check_fence_code_line_parity(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    src_blocks = _fence_code_line_keys(source)
    trn_blocks = _fence_code_line_keys(translation)
    if not src_blocks:
        return None
    if len(src_blocks) != len(trn_blocks):
        return Finding(
            rule="fence_code_line_parity",
            severity="critical",
            location="fenced-блоки",
            detail=(
                f"число fenced-блоков SOURCE={len(src_blocks)}, "
                f"EN={len(trn_blocks)}"
            ),
        )
    problems: list[str] = []
    for i, (src_keys, trn_keys) in enumerate(
        zip(src_blocks, trn_blocks, strict=True), start=1
    ):
        if src_keys == trn_keys:
            continue
        if len(src_keys) != len(trn_keys):
            missing = [k for k in src_keys if k not in trn_keys][:3]
            extra = [k for k in trn_keys if k not in src_keys][:3]
            msg = f"блок {i}: строк кода SOURCE={len(src_keys)}, EN={len(trn_keys)}"
            if missing:
                msg += "; нет в EN: " + "; ".join(missing)
            if extra:
                msg += "; лишние в EN: " + "; ".join(extra)
            problems.append(msg)
            continue
        for j, (src_line, trn_line) in enumerate(
            zip(src_keys, trn_keys, strict=True), start=1
        ):
            if src_line != trn_line:
                problems.append(
                    f"блок {i}, строка {j}: SOURCE={src_line[:60]!r}, "
                    f"EN={trn_line[:60]!r}"
                )
                break
    if not problems:
        return None
    return Finding(
        rule="fence_code_line_parity",
        severity="critical",
        location="fenced-блоки с кодом",
        detail=" | ".join(problems[:3]),
    )


_LIST_TABS_OPEN_RE = re.compile(r"\{%\s*list\s+tabs", re.IGNORECASE)
_LIST_TABS_CLOSE_RE = re.compile(r"\{%\s*endlist\s*%\}", re.IGNORECASE)
_NOTE_OPEN_RE = re.compile(r"\{%\s*note\b", re.IGNORECASE)
_NOTE_CLOSE_RE = re.compile(r"\{%\s*endnote\s*%\}", re.IGNORECASE)
_CUT_OPEN_RE = re.compile(r"\{%\s*cut\b", re.IGNORECASE)
_CUT_CLOSE_RE = re.compile(r"\{%\s*endcut\s*%\}", re.IGNORECASE)


def _check_list_tabs_mismatch(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    s = len(_LIST_TABS_OPEN_RE.findall(source))
    t = len(_LIST_TABS_OPEN_RE.findall(translation))
    if s == t:
        return None
    return Finding(
        rule="list_tabs_mismatch",
        severity="critical",
        location="весь файл",
        detail=f"`{{% list tabs %}}` блоков в SOURCE={s}, в TRANSLATION={t}",
    )


def _check_liquid_tags_balance(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    problems: list[str] = []
    for name, op, cl in (
        ("note", _NOTE_OPEN_RE, _NOTE_CLOSE_RE),
        ("cut", _CUT_OPEN_RE, _CUT_CLOSE_RE),
        ("list tabs", _LIST_TABS_OPEN_RE, _LIST_TABS_CLOSE_RE),
    ):
        a = len(op.findall(translation))
        b = len(cl.findall(translation))
        if a != b:
            problems.append(f"{name}: открыто {a}, закрыто {b}")
    if not problems:
        return None
    return Finding(
        rule="liquid_tags_balance",
        severity="critical",
        location="весь файл",
        detail="; ".join(problems),
    )


_WIKI_RU_RE = re.compile(r"ru\.wikipedia\.org", re.IGNORECASE)
_CYRILLIC_IN_URL_RE = re.compile(
    r"https?://[^\s\)]*[Ѐ-ӿѐ-џ][^\s\)]*", re.IGNORECASE
)
_BROKEN_MD_LINK_RE = re.compile(
    r"\[[^\]]*\]\(\)|(?<!\])\((https?://[^)]+)\)"
)
_HEADING_ANCHOR_LINE_RE = re.compile(r"^(#{1,3}\s+.+?)(\s*\{#([^}]+)\})?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_CHECKMARK = "✓"


@dataclass(frozen=True)
class _CheckmarkTableRow:
    table_index: int
    table_caption: str
    row_label: str
    columns: tuple[str, ...]
    marks: tuple[bool, ...]


def _heading_anchors(text: str) -> list[str]:
    anchors: list[str] = []
    for line in text.splitlines():
        m = _HEADING_ANCHOR_LINE_RE.match(line)
        if m and m.group(3):
            anchors.append(m.group(3))
    return anchors


def _row_label_from_cell(cell0: str) -> str:
    m = re.search(r"`([^`]+)`", cell0)
    if m:
        return m.group(1)
    cleaned = re.sub(r"\s+", " ", cell0.replace("<br/>", " ")).strip()
    return cleaned[:40] or "—"


def _is_checkmark_header_row(cells: list[str]) -> bool:
    if len(cells) < 3:
        return False
    first = re.sub(r"[`\s]", "", cells[0]).lower()
    if first in ("type", "тип", "algorithm", "алгоритм"):
        return True
    joined = "|".join(c.lower() for c in cells)
    return "csv" in joined or "json_each_row" in joined


def _guess_table_caption(columns: list[str], table_index: int) -> str:
    cols = {c.lower() for c in columns}
    if "json_as_string" in cols and "raw" in cols:
        return "чтение из S3"
    if "json_as_string" not in cols and "raw" in cols and "parquet" in cols:
        return "запись в S3"
    if "write" in cols or "запись" in cols:
        return "алгоритмы сжатия (запись)"
    if "read" in cols or "чтение" in cols:
        return "алгоритмы сжатия (чтение)"
    return f"таблица {table_index}"


def _parse_checkmark_tables(text: str) -> dict[str, _CheckmarkTableRow]:
    rows: dict[str, _CheckmarkTableRow] = {}
    table_index = 0
    columns: list[str] = []
    caption = "таблица"
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _TABLE_ROW_RE.match(line):
            i += 1
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            if _is_checkmark_header_row(cells):
                table_index += 1
                columns = cells[1:]
                caption = _guess_table_caption(cells, table_index)
            i += 2
            continue
        if _CHECKMARK not in line or not columns:
            i += 1
            continue
        row_label = _row_label_from_cell(cells[0])
        raw_marks = [_CHECKMARK in c for c in cells[1:]]
        col_count = len(columns)
        if len(raw_marks) < col_count:
            raw_marks.extend([False] * (col_count - len(raw_marks)))
        else:
            raw_marks = raw_marks[:col_count]
        marks = tuple(raw_marks)
        key = f"{table_index}:{row_label}"
        rows[key] = _CheckmarkTableRow(
            table_index=table_index,
            table_caption=caption,
            row_label=row_label,
            columns=tuple(columns),
            marks=marks,
        )
        i += 1
    return rows


def _format_checkmark_drift(
    src: _CheckmarkTableRow, trn: _CheckmarkTableRow
) -> str:
    parts: list[str] = []
    col_count = max(len(src.columns), len(trn.columns), len(src.marks), len(trn.marks))
    for idx in range(col_count):
        col = (
            src.columns[idx]
            if idx < len(src.columns)
            else (trn.columns[idx] if idx < len(trn.columns) else f"col{idx + 1}")
        )
        s = src.marks[idx] if idx < len(src.marks) else False
        t = trn.marks[idx] if idx < len(trn.marks) else False
        if s != t:
            parts.append(
                f"колонка «{col}»: SOURCE={'✓' if s else '—'}, EN={'✓' if t else '—'}"
            )
    if len(src.marks) != len(trn.marks) and not parts:
        parts.append(
            f"число столбцов с данными SOURCE={len(src.marks)}, EN={len(trn.marks)}"
        )
    joined = "; ".join(parts[:8])
    return (
        f"Таблица «{src.table_caption}», строка `{src.row_label}`: {joined}"
    )


def _check_table_checkmark_drift(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    src_rows = _parse_checkmark_tables(source)
    trn_rows = _parse_checkmark_tables(translation)
    drift_lines: list[str] = []
    for key, src in src_rows.items():
        trn = trn_rows.get(key)
        if trn is None:
            continue
        if src.marks != trn.marks:
            drift_lines.append(_format_checkmark_drift(src, trn))
    if not drift_lines:
        return None
    return Finding(
        rule="table_checkmark_drift",
        severity="critical",
        location="таблицы с ✓",
        detail=" | ".join(drift_lines[:3]),
    )


def _check_wikipedia_ru_in_en(*, source: str, translation: str, **_: Any) -> Finding | None:
    _ = source
    hits: list[str] = []
    for m in _WIKI_RU_RE.finditer(translation):
        start = max(0, m.start() - 20)
        end = min(len(translation), m.end() + 40)
        hits.append(translation[start:end].replace("\n", " "))
    for m in _CYRILLIC_IN_URL_RE.finditer(translation):
        hits.append(m.group(0))
    if not hits:
        return None
    sample = "; ".join(dict.fromkeys(hits[:3]))
    return Finding(
        rule="wikipedia_ru_in_en",
        severity="critical",
        location="ссылки",
        detail=sample,
    )


def _check_markdown_link_path_parity(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    from ydbdoc_review.markdown_link_paths import extract_relative_link_refs

    src_refs = extract_relative_link_refs(source)
    trn_refs = extract_relative_link_refs(translation)
    if not src_refs:
        return None
    if len(src_refs) != len(trn_refs):
        return Finding(
            rule="markdown_link_path_parity",
            severity="critical",
            location="markdown-ссылки",
            detail=(
                f"число относительных ссылок SOURCE={len(src_refs)}, "
                f"TRANSLATION={len(trn_refs)}"
            ),
        )
    problems: list[str] = []
    for i, (s, t) in enumerate(zip(src_refs, trn_refs, strict=True), start=1):
        if s.depth != t.depth:
            problems.append(
                f"#{i}: глубина `../` SOURCE={s.depth} TRANSLATION={t.depth} "
                f"({s.raw!r} vs {t.raw!r})"
            )
        elif s.suffix != t.suffix:
            problems.append(
                f"#{i}: путь SOURCE={s.suffix!r} TRANSLATION={t.suffix!r}"
            )
        if len(problems) >= 3:
            break
    if not problems:
        return None
    return Finding(
        rule="markdown_link_path_parity",
        severity="critical",
        location="относительные ссылки",
        detail="; ".join(problems),
    )


def _check_broken_markdown_link(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    _ = source
    problems: list[str] = []
    for m in _BROKEN_MD_LINK_RE.finditer(translation):
        snippet = m.group(0).replace("\n", " ")
        if snippet not in problems:
            problems.append(snippet)
        if len(problems) >= 3:
            break
    if not problems:
        return None
    return Finding(
        rule="broken_markdown_link",
        severity="critical",
        location="markdown-ссылки",
        detail="; ".join(problems),
    )


def _check_heading_anchor_mismatch(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    src = _heading_anchors(source)
    trn = _heading_anchors(translation)
    if not src or src == trn:
        return None
    if len(src) != len(trn):
        return Finding(
            rule="heading_anchor_mismatch",
            severity="critical",
            location="заголовки",
            detail=f"число якорей SOURCE={len(src)}, TRANSLATION={len(trn)}",
        )
    pairs = [
        f"{a}→{b}" for a, b in zip(src, trn, strict=True) if a != b
    ]
    if not pairs:
        return None
    return Finding(
        rule="heading_anchor_mismatch",
        severity="critical",
        location="заголовки",
        detail=", ".join(pairs[:5]),
    )


_DETERMINISTIC: dict[str, Callable[..., Finding | None]] = {
    "cyrillic_in_en": _check_cyrillic_in_en,
    "file_length_mismatch": _check_file_length_mismatch,
    "heading_count_mismatch": _check_heading_count_mismatch,
    "fence_unbalanced": _check_fence_unbalanced,
    "fence_parity": _check_fence_parity,
    "fence_code_line_parity": _check_fence_code_line_parity,
    "markdown_link_path_parity": _check_markdown_link_path_parity,
    "list_tabs_mismatch": _check_list_tabs_mismatch,
    "liquid_tags_balance": _check_liquid_tags_balance,
    "wikipedia_ru_in_en": _check_wikipedia_ru_in_en,
    "broken_markdown_link": _check_broken_markdown_link,
    "heading_anchor_mismatch": _check_heading_anchor_mismatch,
    "table_checkmark_drift": _check_table_checkmark_drift,
}


def _llm_rules_payload(
    *,
    rules: list[Rule],
    source: str,
    translation: str,
    source_lang: str,
    target_lang: str,
    cap: int,
) -> tuple[str, str]:
    """Build (instructions, user_input) for the LLM heuristics call."""
    rule_block = "\n\n".join(
        f"- name: {r.name}\n  severity: {r.severity}\n  description: {r.description}"
        for r in rules
    )
    instructions = (
        "Вы — модуль эвристической проверки перевода технической документации.\n"
        "На вход придут SOURCE и TRANSLATION, и список правил. Для каждого правила "
        "решите: нарушено или нет в TRANSLATION относительно SOURCE.\n\n"
        "Верните строго JSON-объект без пояснений и code fences:\n"
        '{ "findings": ['
        '{"rule": "<имя правила>", '
        '"severity": "warning" | "critical", '
        '"location": "<где, кратко>", '
        '"detail": "<что именно, одно предложение>"} ] }\n\n'
        "Если правило не нарушено — не добавляйте его в findings. "
        "Если нарушено в нескольких местах — добавьте несколько объектов с тем же rule."
    )
    src = source if len(source) <= cap else source[: cap // 2] + "\n…\n" + source[-cap // 2 :]
    trn = (
        translation
        if len(translation) <= cap
        else translation[: cap // 2] + "\n…\n" + translation[-cap // 2 :]
    )
    user_input = (
        f"SOURCE language: {source_lang}\n"
        f"TARGET language: {target_lang}\n\n"
        "Правила для проверки:\n\n"
        f"{rule_block}\n\n"
        f"--- SOURCE BEGIN ---\n{src}\n--- SOURCE END ---\n\n"
        f"--- TRANSLATION BEGIN ---\n{trn}\n--- TRANSLATION END ---\n"
    )
    return instructions, user_input


def _run_llm_rules(
    settings: Settings,
    *,
    rules: list[Rule],
    source: str,
    translation: str,
    source_lang: str,
    target_lang: str,
    file_label: str,
) -> list[Finding]:
    if not rules:
        return []
    from ydbdoc_review.llm import (
        call_yandex_responses,
        parse_json_object,
        translation_verify_model_fallbacks,
    )

    cap_raw = os.environ.get("YDBDOC_HEURISTICS_MAX_INPUT_CHARS", "").strip()
    cap = int(cap_raw) if cap_raw.isdigit() else 40_000
    instructions, user_input = _llm_rules_payload(
        rules=rules,
        source=source,
        translation=translation,
        source_lang=source_lang,
        target_lang=target_lang,
        cap=cap,
    )
    try:
        raw = call_yandex_responses(
            settings,
            settings.model_translation_verify,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=4096,
            model_fallbacks=translation_verify_model_fallbacks(),
            operation="heuristics",
            detail=file_label,
        )
    except Exception as exc:
        fm_log(f"heuristics LLM call failed | {file_label} | {exc}")
        return []

    try:
        data = parse_json_object(raw)
    except (json.JSONDecodeError, ValueError):
        fm_log(f"heuristics LLM returned non-JSON | {file_label}")
        return []
    items = data.get("findings", [])
    out: list[Finding] = []
    rule_lookup = {r.name: r for r in rules}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("rule", "")).strip()
        sev = str(item.get("severity", "")).strip()
        loc = str(item.get("location", "")).strip() or "—"
        det = str(item.get("detail", "")).strip() or "—"
        if not name:
            continue
        if not sev:
            sev = rule_lookup.get(name, Rule(name, "warning", "any", "", "")).severity
        out.append(
            Finding(
                rule=name,
                severity=sev if sev in ("warning", "critical") else "warning",
                location=loc,
                detail=det,
            )
        )
    return out


def run_heuristics(
    settings: Settings,
    *,
    source: str,
    translation: str,
    source_lang: str,
    target_lang: str,
    file_label: str = "",
) -> list[Finding]:
    """Run all heuristic rules from prompt 09 and return findings."""
    rules = load_rules(settings.prompts_dir)
    direction = _direction(source_lang, target_lang)
    findings: list[Finding] = []
    llm_only: list[Rule] = []
    for rule in rules:
        if not _rule_applies(rule, direction):
            continue
        det = _DETERMINISTIC.get(rule.name)
        if det is None:
            llm_only.append(rule)
            continue
        result = det(source=source, translation=translation)
        if result is not None:
            findings.append(result)
    if llm_only:
        findings.extend(
            _run_llm_rules(
                settings,
                rules=llm_only,
                source=source,
                translation=translation,
                source_lang=source_lang,
                target_lang=target_lang,
                file_label=file_label,
            )
        )
    return findings


def render_findings_markdown(
    findings: list[Finding], *, prompts_dir: str = "prompts"
) -> str:
    """Build the «Эвристики» block for the QA report."""
    if not findings:
        return "_Без замечаний._"
    icon = {"critical": "🔴", "warning": "🟡"}
    rule_msgs = {r.name: r.report_message for r in load_rules(prompts_dir)}
    lines: list[str] = []
    for f in findings:
        ic = icon.get(f.severity, "🟡")
        tmpl = rule_msgs.get(f.rule, "{detail} _({location})_")
        try:
            msg = tmpl.format(location=f.location, detail=f.detail).strip()
        except KeyError:
            msg = f"{f.detail} _({f.location})_"
        lines.append(f"- {ic} **{f.rule}** — {msg}")
    return "\n".join(lines)


def has_critical(findings: list[Finding]) -> bool:
    return any(f.severity == "critical" for f in findings)
