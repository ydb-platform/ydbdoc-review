"""Cross-model review, section-level critic repair, and translator confirmation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from ydbdoc_review import git_local
from ydbdoc_review.config import Settings
from ydbdoc_review.llm import (
    confirm_repair_pair,
    fix_translation_pair,
    verify_translation_pair,
)
from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
from ydbdoc_review.markdown_sections import (
    MarkdownSection,
    align_sections_by_heading,
    join_markdown_sections,
    section_indices_touched_by_diff,
    split_markdown_sections,
)
from ydbdoc_review.section_translate import full_file_repair_max_chars
from ydbdoc_review.ru_en_alignment import critical_ru_en_mismatches
from ydbdoc_review.ru_en_sync import (
    deterministic_prepare_en,
    document_structure_broken,
    section_missing_h3,
    section_too_short,
)
from ydbdoc_review.translate_postprocess import (
    apply_deterministic_cli_fixes,
    translation_quality_gate_codes,
    translation_quality_issues,
)


@dataclass(frozen=True)
class PairQaOutcome:
    ru_path: str
    en_path: str
    target_path: str
    review_md: str
    repair_attempted: bool
    repair_applied: bool
    repair_skip_reason: str | None
    confirmation_md: str | None
    repair_error: str | None


def _section_body(text: str, heading_pattern: str) -> str | None:
    m = re.search(
        heading_pattern + r"\s*\n([\s\S]*?)(?:\n###|\Z)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    return m.group(1).strip()


def _parse_translator_file_accept(text: str) -> bool | None:
    """True = ПРИНИМАТЬ, False = НЕ ПРИНИМАТЬ, None = not parsed."""
    body = _section_body(text, r"###\s*вердикт\s+файла")
    if not body:
        body = _section_body(text, r"###\s*вердикт\s+для\s+мержа")
    if not body:
        return None
    low = body.lower()
    if "не принимать" in low or "отклонить" in low:
        return False
    if "принимать" in low or re.search(r"\bпринять\b", low):
        return True
    if "оговорк" in low:
        return True
    return None


def _parse_translator_merge_line(text: str) -> str | None:
    """Map translator report to merge / warn / reject / None."""
    accept = _parse_translator_file_accept(text)
    if accept is False:
        return "reject"
    if accept is True:
        remaining = _parse_translator_remaining_problems(text)
        if remaining and remaining.lower() not in ("_нет._", "нет.", "нет"):
            return "warn"
        return "merge"
    verdict_m = re.search(
        r"###\s*вердикт[^\n]*\n([^\n#]+)",
        text,
        re.IGNORECASE,
    )
    if not verdict_m:
        return None
    line = verdict_m.group(1).lower()
    if "отклонить" in line or "не принимать" in line:
        return "reject"
    if "оговорк" in line:
        return "warn"
    if "принять" in line or "принимать" in line:
        return "merge"
    return None


def _parse_translator_remaining_problems(text: str) -> str | None:
    body = _section_body(text, r"###\s*оставшиеся\s+проблемы")
    if not body:
        body = _section_body(text, r"###\s*блокеры")
    return body


def _parse_translator_pipeline(text: str) -> dict[str, str]:
    body = _section_body(text, r"###\s*ход\s+проверки")
    if not body:
        return {}
    out: dict[str, str] = {}
    for key, pat in (
        ("critic", r"\*\*критик:\*\*\s*(.+)"),
        ("fixer", r"\*\*исправитель:\*\*\s*(.+)"),
        ("translator", r"\*\*переводчик:\*\*\s*(.+)"),
    ):
        m = re.search(pat, body, re.IGNORECASE | re.DOTALL)
        if m:
            out[key] = " ".join(m.group(1).split())
    return out


def _critic_findings_summary(review_md: str) -> str:
    for pat in (
        r"###\s*найдено\s+критиком",
        r"###\s*критические\s+проблемы",
        r"###\s*найденные\s+проблемы",
    ):
        body = _section_body(review_md, pat)
        if body:
            one = " ".join(body.split())
            return one[:400] + ("…" if len(one) > 400 else "")
    return _one_line_summary(review_md) or "—"


def _fixer_status_line(outcome: PairQaOutcome) -> str:
    if outcome.repair_error:
        return f"ошибка: {outcome.repair_error}"
    if outcome.repair_applied:
        return "правки применены на диске"
    if outcome.repair_attempted:
        return f"правки не применены: {outcome.repair_skip_reason or 'quality check'}"
    if review_needs_repair(outcome.review_md):
        return "repair не запускался (отключён или не требовался по политике)"
    return "критик существенных проблем не зафиксировал — правки не требовались"


def _is_translator_api_error(confirmation_md: str | None) -> bool:
    if not confirmation_md or not confirmation_md.strip():
        return False
    low = confirmation_md.strip().lower()
    return (
        low.startswith("_ошибка вердикта переводчика")
        or "foundation models call failed" in low
        or "number of input tokens must be no more than" in low
    )


def _is_content_refusal(confirmation_md: str | None) -> bool:
    if not confirmation_md:
        return False
    low = confirmation_md.lower()
    return (
        "не могу обсуждать" in low
        or "давайте поговорим" in low
        or "can't discuss" in low
    )


def _fallback_translator_verdict(
    *,
    review_md: str,
    ru_full: str,
    en_after: str,
) -> str:
    """When Yandex refuses confirm call, derive verdict from critic + heuristics."""
    if critical_ru_en_mismatches(ru_full, en_after):
        return (
            "### Вердикт файла\n**НЕ ПРИНИМАТЬ**\n\n"
            "### Оставшиеся проблемы\n"
            "1. Эвристика: расхождение EN с RU (команды, флаги, числа, структура).\n\n"
            "### Ход проверки\n"
            "- **Критик:** _модель-переводчик недоступна; использован fallback._\n"
            "- **Исправитель:** —\n"
            "- **Переводчик:** **НЕ ПРИНИМАТЬ** по эвристике RU↔EN.\n"
        )
    if review_needs_repair(review_md):
        return (
            "### Вердикт файла\n**НЕ ПРИНИМАТЬ**\n\n"
            "### Оставшиеся проблемы\n"
            "1. В отчёте критика остаются существенные замечания после repair.\n\n"
            "### Ход проверки\n"
            "- **Критик:** см. REVIEW_BEFORE.\n"
            "- **Исправитель:** _модель-переводчик недоступна._\n"
            "- **Переводчик:** **НЕ ПРИНИМАТЬ** (fallback).\n"
        )
    return (
        "### Вердикт файла\n**ПРИНИМАТЬ**\n\n"
        "### Оставшиеся проблемы\n_Нет._\n\n"
        "### Ход проверки\n"
        "- **Критик:** существенных замечаний нет.\n"
        "- **Исправитель:** —\n"
        "- **Переводчик:** **ПРИНИМАТЬ** (fallback, API недоступен).\n"
    )


def file_merge_verdict(review_md: str, confirmation_md: str | None = None) -> str:
    """
    Per-file verdict from **translator** confirmation only.

    Returns ``merge`` | ``warn`` | ``reject`` | ``error`` (API/limits, not a content reject).
    """
    _ = review_md
    if not confirmation_md or not confirmation_md.strip():
        return "warn"
    if _is_translator_api_error(confirmation_md):
        return "error"
    if _is_content_refusal(confirmation_md):
        return "warn"
    parsed = _parse_translator_merge_line(confirmation_md)
    if parsed:
        return parsed
    conf = confirmation_md.lower()
    if "не готово" in conf or "остались блокеры" in conf:
        return "reject"
    if "готово к мержу" in conf or "можно мержить" in conf:
        return "merge"
    if "блокеров нет" in conf:
        return "merge"
    return "warn"


def format_translation_pr_summary(
    *,
    source_pr_number: int,
    outcomes: list[PairQaOutcome],
) -> str:
    """PR-level summary: можно ли мержить translation PR."""
    lines = [
        f"## Отчёт по переводу (исходный PR #{source_pr_number})",
        "",
    ]
    by_verdict: dict[str, list[str]] = {
        "merge": [],
        "warn": [],
        "reject": [],
        "error": [],
    }
    for o in outcomes:
        v = file_merge_verdict(o.review_md, o.confirmation_md)
        by_verdict.setdefault(v, []).append(o.en_path)
    if by_verdict["error"]:
        lines.append(
            "**Итог по translation PR:** вердикт переводчика **не получен** (API/лимит) для "
            + ", ".join(f"`{p}`" for p in by_verdict["error"])
            + ". Перезапустите `doc_translate`."
        )
    elif by_verdict["reject"]:
        lines.append(
            "**Итог по translation PR:** **нельзя мержить** — есть файлы с вердиктом «не принимать»."
        )
    elif by_verdict["warn"]:
        lines.append(
            "**Итог по translation PR:** **можно мержить** — все файлы принимаются; у части есть оговорки."
        )
    else:
        lines.append(
            "**Итог по translation PR:** **можно мержить** — все файлы принимаются."
        )
    lines.append("")
    if by_verdict["merge"]:
        lines.append(
            "**Принимать:** " + ", ".join(f"`{p}`" for p in by_verdict["merge"])
        )
    not_accept = by_verdict["reject"] + by_verdict["error"]
    if not_accept:
        lines.append(
            "**Не принимать:** " + ", ".join(f"`{p}`" for p in not_accept)
        )
    if by_verdict["warn"]:
        lines.append(
            "**Принимать с оговорками:** " + ", ".join(f"`{p}`" for p in by_verdict["warn"])
        )
    return "\n".join(lines).strip()


def _translator_one_line(confirmation_md: str | None) -> str:
    if not confirmation_md:
        return "Вердикт переводчика не получен."
    if _is_translator_api_error(confirmation_md):
        return "Переводчик: **вердикт не получен** (ошибка API)."
    accept = _parse_translator_file_accept(confirmation_md)
    if accept is True:
        return "**Принимать файл:** да"
    if accept is False:
        return "**Принимать файл:** нет"
    parsed = _parse_translator_merge_line(confirmation_md)
    if parsed == "merge":
        return "**Принимать файл:** да"
    if parsed == "reject":
        return "**Принимать файл:** нет"
    return "**Принимать файл:** да (с оговорками)"


def _one_line_summary(review_md: str) -> str:
    for pat in (
        r"###\s*критические проблемы[^\n]*\n([\s\S]*?)(?:\n###|\Z)",
        r"###\s*что соответствует[^\n]*\n([^\n#]+)",
    ):
        m = re.search(pat, review_md, re.IGNORECASE)
        if not m:
            continue
        body = " ".join(m.group(1).split())
        if "не выявлено" in body.lower():
            return "Блокеров нет."
        if len(body) > 220:
            return body[:217] + "…"
        return body
    return ""


def pr_merge_blocked(outcomes: list[PairQaOutcome]) -> bool:
    return any(file_merge_verdict(o.review_md, o.confirmation_md) == "reject" for o in outcomes)


def pr_merge_verdict_unavailable(outcomes: list[PairQaOutcome]) -> list[str]:
    """EN paths where translator verdict API failed (token limit, etc.)."""
    return [
        o.en_path
        for o in outcomes
        if file_merge_verdict(o.review_md, o.confirmation_md) == "error"
    ]


def _translator_final_verdict(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    source_lang: str,
    target_lang: str,
    source_text: str,
    translation_before: str,
    translation_after: str,
    review_md: str,
    en_on_main: str | None,
    ru_pr_diff: str | None,
) -> str:
    try:
        conf = confirm_repair_pair(
            settings,
            translate_model=settings.model_translate,
            verify_model=settings.model_translation_verify,
            source_lang=source_lang,
            target_lang=target_lang,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translation_before=translation_before,
            translation_after=translation_after,
            review_before=review_md,
            en_on_main=en_on_main,
            ru_pr_diff=ru_pr_diff,
        ).strip()
        if _is_content_refusal(conf):
            return _fallback_translator_verdict(
                review_md=review_md,
                ru_full=source_text,
                en_after=translation_after,
            )
        return conf
    except Exception as exc:
        return f"_Ошибка вердикта переводчика:_ `{exc}`"


def _critic_sections_text(review_md: str) -> str:
    parts: list[str] = []
    for heading in (
        r"###\s*найдено\s+критиком",
        r"###\s*блокеры",
        r"###\s*scope",
    ):
        m = re.search(heading + r"\s*\n([\s\S]*?)(?:\n###|\Z)", review_md, re.IGNORECASE)
        if m:
            parts.append(m.group(1))
    return "\n".join(parts).lower()


def critic_needs_structure_rebuild(review_md: str) -> bool:
    """True when the critic report calls for layout resync (not cyrillic-only cleanup)."""
    if not review_md.strip():
        return False
    body = _critic_sections_text(review_md)
    tl = review_md.lower()
    cyrillic_only = (
        "кириллиц" in body
        or "фактически" in body
        or ("русск" in body and "коммент" in body)
    ) and not any(
        x in body
        for x in (
            "структур",
            "дублир",
            "ydbdoc_block",
            "⟦",
            "отсутствует",
            "отсутствуют",
            "перемест",
            "нарушен",
            "порядок раздел",
        )
    )
    if cyrillic_only:
        return False
    markers = (
        "структур",
        "дублир",
        "отсутствует",
        "отсутствуют",
        "нарушен",
        "ydbdoc_block",
        "⟦",
        "неверное расположение",
        "неправильн",
        "порядок раздел",
    )
    if any(m in body for m in markers):
        return True
    return any(m in tl for m in ("дублир", "ydbdoc_block", "⟦")) and "структур" in tl


def review_needs_repair(review_md: str) -> bool:
    """True when the critic report lists substantive issues worth auto-fixing."""
    if not review_md.strip():
        return False
    tl = review_md.lower()
    if "критических проблем не выявлено" in tl and "блокер" not in tl:
        if "существенные расхождения" not in tl:
            return False
    if "существенные расхождения" in tl:
        return True
    if "соответствует с оговорками" in tl:
        return True
    if "существенных проблем не выявлено" in tl:
        return False
    for heading in (
        r"###\s*найдено\s+критиком",
        r"###\s*критические проблемы",
        r"###\s*найденные проблемы",
        r"###\s*регрессии",
    ):
        m = re.search(heading + r"\s*\n([\s\S]*?)(?:\n###|\Z)", review_md, re.IGNORECASE)
        if not m:
            continue
        body = m.group(1).strip().lower()
        if "не выявлено" in body and not re.search(r"^\s*\d+\.", m.group(1), re.MULTILINE):
            continue
        if re.search(r"^[\s]*[-*•]\s+\S", m.group(1), re.MULTILINE):
            return True
        if re.search(r"^\d+\.\s+\S", m.group(1), re.MULTILINE):
            return True
    return False


def _repair_should_apply(
    *,
    source_text: str,
    before: str,
    after: str,
    target_lang: str,
    en_on_main: str | None = None,
    source_diff: str | None = None,
) -> tuple[bool, str | None]:
    if not after.strip():
        return False, "пустой ответ модели-исправителя"
    if len(after) < int(len(source_text) * 0.55):
        return False, "исправленный текст слишком короткий относительно оригинала"
    if len(after) < int(len(before) * 0.75) and len(source_text) > 4000:
        return False, "исправленный текст короче предыдущего перевода >25%"
    issues = translation_quality_issues(
        source_text,
        after,
        target_lang=target_lang,
        en_main=en_on_main,
        source_diff=source_diff,
    )
    hard = translation_quality_gate_codes()
    hit = hard.intersection(issues)
    if hit:
        return False, f"эвристики после исправления: {', '.join(sorted(hit))}"
    return True, None


def _section_qa_use(source_text: str) -> bool:
    raw = os.environ.get("YDBDOC_TRANSLATION_QA_BY_SECTION", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    min_raw = os.environ.get("YDBDOC_TRANSLATE_BY_SECTION_MIN_CHARS", "").strip()
    threshold = int(min_raw) if min_raw.isdigit() else 8_000
    if len(source_text) < threshold:
        return False
    return len(split_markdown_sections(source_text)) > 1


def _is_en_target(target_lang: str) -> bool:
    return target_lang.strip().lower() in ("english", "en")


def _run_llm_repair(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    target_path: str,
    source_text: str,
    translated_text: str,
    review_md: str,
    source_lang: str,
    target_lang: str,
    repair_enabled: bool,
    source_pr_number: int | None,
    ru_pr_diff: str | None,
    en_on_main: str | None,
) -> tuple[str | None, PairQaOutcome]:
    """LLM fixer for prose/semantics only (not whole-file structure)."""
    if not review_needs_repair(review_md) or not repair_enabled:
        return None, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            review_md=review_md,
            repair_attempted=False,
            repair_applied=False,
            repair_skip_reason=None,
            confirmation_md=None,
            repair_error=None,
        )
    if _section_qa_use(source_text):
        return _run_pair_qa_repair_by_sections(
            settings,
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            source_text=source_text,
            translated_text=translated_text,
            source_lang=source_lang,
            target_lang=target_lang,
            repair_enabled=True,
            source_pr_number=source_pr_number,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
            review_md=review_md,
        )
    return _run_pair_qa_repair_whole_file(
        settings,
        ru_path=ru_path,
        en_path=en_path,
        target_path=target_path,
        source_text=source_text,
        translated_text=translated_text,
        source_lang=source_lang,
        target_lang=target_lang,
        repair_enabled=True,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
        review_md=review_md,
    )


def run_pair_qa_repair(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    target_path: str,
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    repair_enabled: bool,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> tuple[str, PairQaOutcome]:
    """
    Critic → deterministic prepare → structural rebuild → optional LLM repair
    → deterministic prepare → translator verdict.
    """
    translation_before = translated_text
    current = translated_text
    repair_applied = False
    repair_attempted = False
    repair_skip_reason: str | None = None
    repair_error: str | None = None

    if _is_en_target(target_lang):
        current = deterministic_prepare_en(
            settings,
            ru_path=ru_path,
            ru_full=source_text,
            en_text=current,
        )

    review_md = verify_translation_pair(
        settings,
        translate_model=settings.model_translate,
        verify_model=settings.model_translation_verify,
        source_lang=source_lang,
        target_lang=target_lang,
        ru_path=ru_path,
        en_path=en_path,
        source_text=source_text,
        translated_text=current,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
    )

    structural = _is_en_target(target_lang) and (
        critic_needs_structure_rebuild(review_md)
        or document_structure_broken(source_text, current)
    )

    if structural and repair_enabled:
        repair_attempted = True
        current = deterministic_prepare_en(
            settings,
            ru_path=ru_path,
            ru_full=source_text,
            en_text=current,
            force_structure_rebuild=True,
        )
        repair_applied = True
        repair_skip_reason = "структурный rebuild по RU (детерминированно)"
    elif review_needs_repair(review_md) and repair_enabled:
        llm_text, llm_outcome = _run_llm_repair(
            settings,
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            source_text=source_text,
            translated_text=current,
            review_md=review_md,
            source_lang=source_lang,
            target_lang=target_lang,
            repair_enabled=True,
            source_pr_number=source_pr_number,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )
        repair_attempted = llm_outcome.repair_attempted
        repair_applied = llm_outcome.repair_applied
        repair_skip_reason = llm_outcome.repair_skip_reason
        repair_error = llm_outcome.repair_error
        if llm_text is not None:
            current = llm_text

    if _is_en_target(target_lang):
        current = deterministic_prepare_en(
            settings,
            ru_path=ru_path,
            ru_full=source_text,
            en_text=current,
        )

    conf = _translator_final_verdict(
        settings,
        ru_path=ru_path,
        en_path=en_path,
        source_lang=source_lang,
        target_lang=target_lang,
        source_text=source_text,
        translation_before=translation_before,
        translation_after=current,
        review_md=review_md,
        en_on_main=en_on_main,
        ru_pr_diff=ru_pr_diff,
    )

    outcome = PairQaOutcome(
        ru_path=ru_path,
        en_path=en_path,
        target_path=target_path,
        review_md=review_md,
        repair_attempted=repair_attempted,
        repair_applied=repair_applied,
        repair_skip_reason=repair_skip_reason,
        confirmation_md=conf,
        repair_error=repair_error,
    )
    return current, outcome


def _run_pair_qa_repair_by_sections(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    target_path: str,
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    repair_enabled: bool,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
    review_md: str | None = None,
) -> tuple[str | None, PairQaOutcome]:
    """Repairs applied per ``##`` section (LLM fixer; critic report already computed)."""
    if review_md is None:
        review_md = verify_translation_pair(
            settings,
            translate_model=settings.model_translate,
            verify_model=settings.model_translation_verify,
            source_lang=source_lang,
            target_lang=target_lang,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translated_text=translated_text,
            source_pr_number=source_pr_number,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )

    src_secs = split_markdown_sections(source_text)
    tgt_secs = split_markdown_sections(translated_text)
    aligned = align_sections_by_heading(src_secs, tgt_secs)
    to_repair = section_indices_touched_by_diff(ru_pr_diff or "", src_secs)
    if not to_repair:
        to_repair = {s.index for s in src_secs}
    for src_sec in src_secs:
        tgt_sec = aligned[src_sec.index] if src_sec.index < len(aligned) else None
        tgt_body = tgt_sec.content if tgt_sec else ""
        if section_missing_h3(src_sec.content, tgt_body) or section_too_short(
            src_sec.content, tgt_body
        ):
            to_repair.add(src_sec.index)

    out_secs: list[MarkdownSection] = []
    repaired_any = False
    last_error: str | None = None

    for src_sec in src_secs:
        tgt_sec = aligned[src_sec.index] if src_sec.index < len(aligned) else None
        tgt_body = tgt_sec.content if tgt_sec else ""
        if src_sec.index not in to_repair:
            out_secs.append(tgt_sec if tgt_sec else src_sec)
            continue

        try:
            fixed_raw = fix_translation_pair(
                settings,
                verify_model=settings.model_translation_verify,
                source_lang=source_lang,
                target_lang=target_lang,
                ru_path=ru_path,
                en_path=en_path,
                source_text=src_sec.content,
                translated_text=tgt_body,
                review_report=review_md,
                ru_pr_diff=ru_pr_diff,
                en_on_main=en_on_main,
            )
        except Exception as exc:
            last_error = str(exc)
            out_secs.append(tgt_sec if tgt_sec else src_sec)
            continue

        fixed = fixed_raw
        if target_lang.strip().lower() in ("english", "en"):
            fixed = restore_markdown_links_from_ru(src_sec.content, fixed)
            fixed = apply_deterministic_cli_fixes(fixed, en_main=en_on_main)

        ok, skip = _repair_should_apply(
            source_text=src_sec.content,
            before=tgt_body,
            after=fixed,
            target_lang=target_lang,
            en_on_main=en_on_main,
        )
        if not ok:
            out_secs.append(tgt_sec if tgt_sec else src_sec)
            continue

        out_secs.append(
            MarkdownSection(
                index=src_sec.index,
                heading=src_sec.heading,
                content=fixed.strip(),
                start_line=src_sec.start_line,
                end_line=src_sec.end_line,
            )
        )
        repaired_any = True

    merged = join_markdown_sections(out_secs)

    if not repaired_any:
        return None, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            review_md=review_md,
            repair_attempted=True,
            repair_applied=False,
            repair_skip_reason="ни один раздел не прошёл проверку качества",
            confirmation_md=None,
            repair_error=last_error,
        )

    return merged, PairQaOutcome(
        ru_path=ru_path,
        en_path=en_path,
        target_path=target_path,
        review_md=review_md,
        repair_attempted=True,
        repair_applied=True,
        repair_skip_reason=None,
        confirmation_md=None,
        repair_error=last_error,
    )


def _run_pair_qa_repair_whole_file(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    target_path: str,
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    repair_enabled: bool,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
    review_md: str | None = None,
) -> tuple[str | None, PairQaOutcome]:
    if review_md is None:
        review_md = verify_translation_pair(
            settings,
            translate_model=settings.model_translate,
            verify_model=settings.model_translation_verify,
            source_lang=source_lang,
            target_lang=target_lang,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translated_text=translated_text,
            source_pr_number=source_pr_number,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )

    if len(source_text) > full_file_repair_max_chars():
        return None, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            review_md=review_md,
            repair_attempted=True,
            repair_applied=False,
            repair_skip_reason=(
                f"файл длиннее {full_file_repair_max_chars()} символов — "
                "целиком не исправляем (нужен section QA; включите длинный документ)"
            ),
            confirmation_md=None,
            repair_error=None,
        )

    try:
        fixed_raw = fix_translation_pair(
            settings,
            verify_model=settings.model_translation_verify,
            source_lang=source_lang,
            target_lang=target_lang,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translated_text=translated_text,
            review_report=review_md,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )
    except Exception as exc:
        return None, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            review_md=review_md,
            repair_attempted=True,
            repair_applied=False,
            repair_skip_reason=None,
            confirmation_md=None,
            repair_error=str(exc),
        )

    fixed = fixed_raw
    if target_lang.strip().lower() in ("english", "en"):
        fixed = restore_markdown_links_from_ru(source_text, fixed)
        fixed = apply_deterministic_cli_fixes(fixed, en_main=en_on_main)

    diff_for_gate = (
        ru_pr_diff if target_lang.strip().lower() in ("english", "en") else None
    )
    ok, skip = _repair_should_apply(
        source_text=source_text,
        before=translated_text,
        after=fixed,
        target_lang=target_lang,
        en_on_main=en_on_main,
        source_diff=diff_for_gate,
    )
    if not ok:
        return None, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            review_md=review_md,
            repair_attempted=True,
            repair_applied=False,
            repair_skip_reason=skip,
            confirmation_md=None,
            repair_error=None,
        )

    return fixed, PairQaOutcome(
        ru_path=ru_path,
        en_path=en_path,
        target_path=target_path,
        review_md=review_md,
        repair_attempted=True,
        repair_applied=True,
        repair_skip_reason=None,
        confirmation_md=None,
        repair_error=None,
    )


def format_pair_qa_markdown(outcome: PairQaOutcome) -> str:
    """Per-file report: accept yes/no, remaining issues, critic→fixer→translator chain."""
    verdict = file_merge_verdict(outcome.review_md, outcome.confirmation_md)
    accept_yes = verdict in ("merge", "warn")
    if verdict == "error":
        accept_label = "вердикт не получен (API)"
    elif accept_yes:
        accept_label = "да"
    else:
        accept_label = "нет"

    lines = [
        f"### `{outcome.en_path}`",
        "",
        f"**Принимать файл:** {accept_label}",
        "",
    ]

    remaining = (
        _parse_translator_remaining_problems(outcome.confirmation_md or "")
        if outcome.confirmation_md
        else None
    )
    if accept_yes and verdict != "error":
        lines.append("**Оставшиеся проблемы:** _Нет._")
    elif remaining and remaining.lower().strip().strip("_").rstrip(".") not in (
        "нет",
        "нет.",
    ):
        lines.append("**Оставшиеся проблемы:**")
        lines.append("")
        lines.append(remaining)
    elif verdict == "reject":
        lines.append("**Оставшиеся проблемы:**")
        lines.append("")
        lines.append(_critic_findings_summary(outcome.review_md) or "_См. ход проверки._")
    elif verdict == "error":
        lines.append(
            "**Оставшиеся проблемы:** _Не оценено — ошибка API переводчика._"
        )
    else:
        lines.append("**Оставшиеся проблемы:** _Нет._")

    lines.append("")
    lines.append("**Ход проверки:**")
    lines.append("")

    pipeline = (
        _parse_translator_pipeline(outcome.confirmation_md or "")
        if outcome.confirmation_md
        else {}
    )
    lines.append(
        "- **Критик:** "
        + (
            pipeline.get("critic")
            or _critic_findings_summary(outcome.review_md)
        )
    )
    lines.append(
        "- **Исправитель:** "
        + (pipeline.get("fixer") or _fixer_status_line(outcome))
    )
    lines.append(
        "- **Переводчик:** "
        + (
            pipeline.get("translator")
            or _translator_one_line(outcome.confirmation_md)
        )
    )

    lines.extend(
        [
            "",
            "<details><summary>Полный отчёт критика</summary>",
            "",
            outcome.review_md.strip(),
            "",
            "</details>",
        ]
    )
    if outcome.confirmation_md and not _is_translator_api_error(outcome.confirmation_md):
        lines.extend(
            [
                "<details><summary>Полный ответ переводчика</summary>",
                "",
                outcome.confirmation_md.strip(),
                "",
                "</details>",
            ]
        )
    return "\n".join(lines)


def run_pairs_qa_and_repair(
    settings: Settings,
    *,
    workdir: str,
    pairs: list[tuple[str, str]],
    pair_diffs: dict[tuple[str, str], tuple[str | None, str | None]],
    source_pr_number: int,
    base_ref_local: str | None,
    repair_enabled: bool | None = None,
) -> tuple[str | None, list[str], list[PairQaOutcome]]:
    """
    Same pipeline as after ``doc_translate``: critic → repair → translator.

    *pairs*: ``(ru_path, en_path)``; SOURCE/TRANSLATION read from *workdir* (PR branch).
    Returns ``(comment_markdown, repaired_en_paths, outcomes)``.
    """
    if not pairs:
        return None, [], []
    repair_on = (
        settings.translation_repair_enabled
        if repair_enabled is None
        else repair_enabled
    )
    lines: list[str] = []
    outcomes: list[PairQaOutcome] = []
    repaired_paths: list[str] = []

    for ru_p, en_p in pairs:
        source_text = git_local.read_text(workdir, ru_p) or ""
        translated_text = git_local.read_text(workdir, en_p) or ""
        ru_diff, _en_diff = pair_diffs.get((ru_p, en_p), (None, None))
        en_on_main: str | None = None
        if base_ref_local:
            en_on_main = git_local.read_text_at_ref(workdir, base_ref_local, en_p)

        initial_en = translated_text
        final_en, outcome = run_pair_qa_repair(
            settings,
            ru_path=ru_p,
            en_path=en_p,
            target_path=en_p,
            source_text=source_text,
            translated_text=translated_text,
            source_lang="Russian",
            target_lang="English",
            repair_enabled=repair_on,
            source_pr_number=source_pr_number,
            ru_pr_diff=ru_diff,
            en_on_main=en_on_main,
        )
        if final_en != initial_en:
            git_local.write_text(workdir, en_p, final_en)
            repaired_paths.append(en_p)
        outcomes.append(outcome)
        lines.append(format_pair_qa_markdown(outcome))

    summary = format_translation_pr_summary(
        source_pr_number=source_pr_number,
        outcomes=outcomes,
    )
    body = summary + "\n\n---\n\n" + "\n\n".join(lines) if lines else summary
    return body, repaired_paths, outcomes


