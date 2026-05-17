"""Cross-model review, section-level critic repair, and translator confirmation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

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
    for heading in (
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
) -> tuple[str | None, PairQaOutcome]:
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
            repair_enabled=repair_enabled,
            source_pr_number=source_pr_number,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
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
        repair_enabled=repair_enabled,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
    )


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
) -> tuple[str | None, PairQaOutcome]:
    """One file-level audit report; repairs applied per ``##`` section."""
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
    any_needs = review_needs_repair(review_md)

    if not any_needs or not repair_enabled:
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

    src_secs = split_markdown_sections(source_text)
    tgt_secs = split_markdown_sections(translated_text)
    aligned = align_sections_by_heading(src_secs, tgt_secs)
    to_repair = section_indices_touched_by_diff(ru_pr_diff or "", src_secs)
    if not to_repair:
        to_repair = {s.index for s in src_secs}

    out_secs: list[MarkdownSection] = []
    repaired_any = False
    confirm_parts: list[str] = []
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
            confirm_parts.append(
                f"##### {src_sec.heading or 'преамбула'}\n\n_Исправление не применено:_ {skip}\n"
            )
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
        try:
            conf = confirm_repair_pair(
                settings,
                translate_model=settings.model_translate,
                verify_model=settings.model_translation_verify,
                source_lang=source_lang,
                target_lang=target_lang,
                ru_path=ru_path,
                en_path=en_path,
                source_text=src_sec.content,
                translation_before=tgt_body,
                translation_after=fixed,
                review_before=review_md,
                en_on_main=en_on_main,
            )
            confirm_parts.append(
                f"##### {src_sec.heading or 'преамбула'}\n\n{conf.strip()}\n"
            )
        except Exception as exc:
            confirm_parts.append(
                f"##### {src_sec.heading or 'преамбула'}\n\n_Ошибка подтверждения:_ `{exc}`\n"
            )

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
            confirmation_md="\n".join(confirm_parts) if confirm_parts else None,
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
        confirmation_md="\n".join(confirm_parts).strip() if confirm_parts else None,
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
) -> tuple[str | None, PairQaOutcome]:
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
    needs = review_needs_repair(review_md)
    if not needs or not repair_enabled:
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
        try:
            confirmation = confirm_repair_pair(
                settings,
                translate_model=settings.model_translate,
                verify_model=settings.model_translation_verify,
                source_lang=source_lang,
                target_lang=target_lang,
                ru_path=ru_path,
                en_path=en_path,
                source_text=source_text,
                translation_before=translated_text,
                translation_after=translated_text,
                review_before=review_md,
                en_on_main=en_on_main,
            )
        except Exception as exc:
            confirmation = f"_Ошибка подтверждения:_ `{exc}`"
        return None, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=target_path,
            review_md=review_md,
            repair_attempted=True,
            repair_applied=False,
            repair_skip_reason=skip,
            confirmation_md=confirmation,
            repair_error=None,
        )

    try:
        confirmation = confirm_repair_pair(
            settings,
            translate_model=settings.model_translate,
            verify_model=settings.model_translation_verify,
            source_lang=source_lang,
            target_lang=target_lang,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translation_before=translated_text,
            translation_after=fixed,
            review_before=review_md,
            en_on_main=en_on_main,
        )
    except Exception as exc:
        confirmation = f"_Ошибка подтверждения переводчиком:_ `{exc}`"

    return fixed, PairQaOutcome(
        ru_path=ru_path,
        en_path=en_path,
        target_path=target_path,
        review_md=review_md,
        repair_attempted=True,
        repair_applied=True,
        repair_skip_reason=None,
        confirmation_md=confirmation,
        repair_error=None,
    )


def format_pair_qa_markdown(outcome: PairQaOutcome) -> str:
    lines = [
        f"## `{outcome.en_path}`",
        "",
        f"_RU:_ `{outcome.ru_path}`",
        "",
        outcome.review_md.strip(),
        "",
    ]
    if outcome.repair_error:
        lines.extend(
            [
                "##### Исправление критиком",
                "",
                f"_Ошибка:_ `{outcome.repair_error}`",
                "",
            ]
        )
    elif outcome.repair_attempted:
        if outcome.repair_applied:
            lines.extend(
                [
                    "##### Исправление критиком",
                    "",
                    "_Применено:_ файл перевода обновлён на диске и попадёт в коммит.",
                    "",
                ]
            )
        else:
            reason = outcome.repair_skip_reason or "не прошло проверки качества"
            lines.extend(
                [
                    "##### Исправление критиком",
                    "",
                    f"_Не применено:_ {reason}.",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "##### Исправление критиком",
                "",
                "_Не выполнялось_ — существенных проблем в ревью нет или repair отключён.",
                "",
            ]
        )
    if outcome.confirmation_md:
        lines.extend(
            [
                "##### Подтверждение (модель-переводчик)",
                "",
                outcome.confirmation_md.strip(),
                "",
            ]
        )
    return "\n".join(lines)


