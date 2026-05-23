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


def _check_fence_unbalanced(
    *, source: str, translation: str, **_: Any
) -> Finding | None:
    n_open = 0
    for line in translation.split("\n"):
        if line.lstrip().startswith("```"):
            n_open += 1
    if n_open % 2 == 0:
        return None
    return Finding(
        rule="fence_unbalanced",
        severity="critical",
        location="весь файл",
        detail=f"найдено {n_open} строк с тройным бэктиком (нечётное число)",
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


_DETERMINISTIC: dict[str, Callable[..., Finding | None]] = {
    "cyrillic_in_en": _check_cyrillic_in_en,
    "file_length_mismatch": _check_file_length_mismatch,
    "heading_count_mismatch": _check_heading_count_mismatch,
    "fence_unbalanced": _check_fence_unbalanced,
    "list_tabs_mismatch": _check_list_tabs_mismatch,
    "liquid_tags_balance": _check_liquid_tags_balance,
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


def render_findings_markdown(findings: list[Finding]) -> str:
    """Build the «Эвристики» block for the QA report."""
    if not findings:
        return "_Без замечаний._"
    icon = {"critical": "🔴", "warning": "🟡"}
    lines: list[str] = []
    for f in findings:
        ic = icon.get(f.severity, "🟡")
        lines.append(f"- {ic} **{f.rule}** — {f.detail} _({f.location})_")
    return "\n".join(lines)


def has_critical(findings: list[Finding]) -> bool:
    return any(f.severity == "critical" for f in findings)
