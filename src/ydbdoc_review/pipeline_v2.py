"""Single translation pipeline: segment translate + critic QA + heuristics.

Same code path for ``doc_translate`` (translate then QA) and ``doc_verify``
(QA only on existing PR files).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ydbdoc_review.config import Settings
from ydbdoc_review.document_segments import (
    DocumentUnit,
    assemble_document_units,
    parse_document_units,
)
from ydbdoc_review.fm_progress import fm_log
from ydbdoc_review.heuristics import (
    Finding,
    has_critical,
    render_findings_markdown,
    run_heuristics,
)
from ydbdoc_review.llm import (
    _read_prompt,
    _strip_code_fence,
    call_yandex_responses,
    clamp_max_output_tokens,
    fix_translation_pair,
    load_translate_segment_instructions,
    revalidate_translation_pair,
    verify_translation_pair,
)
from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
from ydbdoc_review.translate_postprocess import (
    apply_deterministic_cli_fixes,
    fix_yandex_cloud_links_for_en,
)

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿѐ-џ]")


# ---------------------------------------------------------------------------
# Translate: segment-based, generic for RU↔EN
# ---------------------------------------------------------------------------


def _segment_user_input(
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    unit: DocumentUnit,
    body: str,
) -> str:
    return (
        f"File: `{source_path}`\n"
        f"Fragment type: `{unit.kind}`\n"
        f"Fragment label: `{unit.label}`\n"
        f"SOURCE language: {source_lang}\n"
        f"TARGET language: {target_lang}\n\n"
        f"--- SOURCE BEGIN ---\n{body}\n--- SOURCE END ---\n"
    )


def _call_translate_segment(
    settings: Settings,
    *,
    source_lang: str,
    target_lang: str,
    source_path: str,
    unit: DocumentUnit,
    body: str,
    operation: str,
) -> str:
    instructions = load_translate_segment_instructions(settings).strip()
    user_input = _segment_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        unit=unit,
        body=body,
    )
    model = settings.model_translate
    cap = clamp_max_output_tokens(max(4096, min(len(body) * 3, 32_768)), model)
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=cap,
            operation=operation,
            detail=unit.label,
        ).strip()
    )
    if target_lang.strip().lower() in ("english", "en"):
        out = fix_yandex_cloud_links_for_en(out)
    return out


def _fence_comment_rows(fence_text: str, *, source_lang: str) -> list[tuple[int, str, str]]:
    """Lines in *fence_text* that look like comments needing translation.

    Returns ``(line_index, marker, body)``. Only flags lines whose body contains
    characters of the source language script (Cyrillic for RU→EN, Latin for EN→RU).
    """
    from ydbdoc_review.fence_comments import (
        comment_body_on_line,
        inline_sql_comment_tail,
    )

    is_ru_source = source_lang.lower().startswith("rus")
    rows: list[tuple[int, str, str]] = []
    lines = fence_text.split("\n")
    for idx, line in enumerate(lines):
        full = comment_body_on_line(line)
        if full is not None:
            marker, body = full
            if body and (
                _CYRILLIC_RE.search(body) if is_ru_source else re.search(r"[A-Za-z]", body)
            ):
                rows.append((idx, marker, body))
            continue
        inline = inline_sql_comment_tail(line)
        if inline is not None:
            _prefix, body = inline
            if body and (
                _CYRILLIC_RE.search(body) if is_ru_source else re.search(r"[A-Za-z]", body)
            ):
                rows.append((idx, "--", body))
    return rows


def translate_unit(
    settings: Settings,
    unit: DocumentUnit,
    *,
    source_path: str,
    source_lang: str,
    target_lang: str,
) -> DocumentUnit:
    if unit.kind == "fence":
        if not source_lang.lower().startswith("rus"):
            # EN→RU comment translation is rare; skip and let fence pass verbatim.
            return unit
        rows = _fence_comment_rows(unit.text, source_lang=source_lang)
        if not rows:
            return unit
        payload = json.dumps(
            [{"line": i, "marker": m, "text": t} for i, m, t in rows],
            ensure_ascii=False,
        )
        instructions = (
            f"Translate each JSON object's `text` from {source_lang} to {target_lang}. "
            "Return JSON array with same `line` and `marker`; only `text` changes. "
            "Code syntax unchanged."
        )
        out = _strip_code_fence(
            call_yandex_responses(
                settings,
                settings.model_translate,
                instructions=instructions,
                user_input=payload,
                max_output_tokens=4096,
                operation="translate:fence-comments",
                detail=unit.label,
            ).strip()
        )
        try:
            translated = json.loads(out)
        except json.JSONDecodeError:
            fm_log(f"fence-comments parse failed, keeping verbatim | {unit.label}")
            return unit
        lines = unit.text.split("\n")
        for item in translated:
            i = int(item.get("line", -1))
            if 0 <= i < len(lines):
                marker = item.get("marker", "#")
                lines[i] = f"{marker} {item['text']}".rstrip()
        return DocumentUnit(unit.kind, "\n".join(lines), unit.label)

    body = _call_translate_segment(
        settings,
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        unit=unit,
        body=unit.text,
        operation=f"translate:{unit.kind}",
    )
    return DocumentUnit(unit.kind, body, unit.label)


def translate_document(
    settings: Settings,
    *,
    source_path: str,
    source_full: str,
    source_lang: str,
    target_lang: str,
) -> tuple[str, str]:
    """Segment-based translation. Returns ``(translated_markdown, mode_label)``."""
    units = parse_document_units(source_full, doc_label=source_path)
    fm_log(
        f"translate {source_lang}→{target_lang} | {source_path} | {len(units)} unit(s)"
    )
    translated: list[DocumentUnit] = []
    for i, unit in enumerate(units, start=1):
        fm_log(f"unit {i}/{len(units)} | {unit.kind} | {unit.label}")
        translated.append(
            translate_unit(
                settings,
                unit,
                source_path=source_path,
                source_lang=source_lang,
                target_lang=target_lang,
            )
        )
    merged = assemble_document_units(translated)
    if source_lang.lower().startswith("rus"):
        merged = restore_markdown_links_from_ru(source_full, merged)
        merged = apply_deterministic_cli_fixes(merged, ru_source=source_full)
    return merged, f"segment-{len(units)}-units"


# ---------------------------------------------------------------------------
# QA: compare → optional fix-diff → optional re-validate → heuristics
# ---------------------------------------------------------------------------


VERDICT_ACCEPT = "accept"
VERDICT_ACCEPT_WITH_NOTES = "accept_with_notes"
VERDICT_REJECT = "reject"
VERDICT_ERROR = "error"

_VERDICT_RE = re.compile(
    r"###\s*Вердикт\s*\n+\s*\*?\*?\s*(НЕ\s+ПРИНИМАТЬ|ПРИНИМАТЬ\s+С\s+ОГОВОРКАМИ|ПРИНИМАТЬ)\b",
    re.IGNORECASE,
)


def parse_verdict(review_md: str) -> str:
    """Return one of accept | accept_with_notes | reject | error."""
    if not review_md or not review_md.strip():
        return VERDICT_ERROR
    if "Ошибка вызова" in review_md or "QA pipeline failed" in review_md:
        return VERDICT_ERROR
    m = _VERDICT_RE.search(review_md)
    if not m:
        # Permissive fallback: scan the whole text for the verdict word.
        low = review_md.lower()
        if re.search(r"не\s+принимать", low):
            return VERDICT_REJECT
        if re.search(r"принимать\s+с\s+оговорками", low):
            return VERDICT_ACCEPT_WITH_NOTES
        if re.search(r"\bпринимать\b", low):
            return VERDICT_ACCEPT
        return VERDICT_ERROR
    word = " ".join(m.group(1).upper().split())
    if word == "НЕ ПРИНИМАТЬ":
        return VERDICT_REJECT
    if word == "ПРИНИМАТЬ С ОГОВОРКАМИ":
        return VERDICT_ACCEPT_WITH_NOTES
    return VERDICT_ACCEPT


@dataclass(frozen=True)
class FixApplyResult:
    applied: int
    skipped: list[str]
    new_text: str


def apply_fix_diff(text: str, fixes: list[dict]) -> FixApplyResult:
    """Apply ``{find, replace}`` entries. Each ``find`` must occur exactly once."""
    out = text
    applied = 0
    skipped: list[str] = []
    for item in fixes:
        find = item.get("find", "")
        replace = item.get("replace", "")
        reason = item.get("reason", "")
        if not find:
            skipped.append(f"пустой `find` (reason: {reason})")
            continue
        count = out.count(find)
        if count == 0:
            snippet = (find[:60] + "…") if len(find) > 60 else find
            skipped.append(
                f"`find` не найден в EN: «{snippet}» (reason: {reason})"
            )
            continue
        if count > 1:
            snippet = (find[:60] + "…") if len(find) > 60 else find
            skipped.append(
                f"`find` встречается {count} раз — нужен уникальный контекст: «{snippet}» (reason: {reason})"
            )
            continue
        out = out.replace(find, replace, 1)
        applied += 1
    return FixApplyResult(applied=applied, skipped=skipped, new_text=out)


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
    findings: list[Finding] = field(default_factory=list)
    fix_skipped_notes: list[str] = field(default_factory=list)


def run_pair_qa(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    source_text: str,
    translated_text: str,
    source_lang: str = "Russian",
    target_lang: str = "English",
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
    repair_enabled: bool = True,
) -> tuple[str, PairQaOutcome]:
    """One QA cycle: compare → optional fix-diff → optional re-validate → heuristics.

    Returns ``(final_translation_text, outcome)``.
    """
    current = translated_text
    repair_attempted = False
    repair_applied = False
    repair_skip_reason: str | None = None
    repair_error: str | None = None
    fix_skipped_notes: list[str] = []
    confirmation_md: str | None = None

    fm_log(f"QA compare | {ru_path}")
    try:
        review_md = verify_translation_pair(
            settings,
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
    except Exception as exc:
        review_md = (
            "### Вердикт\n**ПРИНИМАТЬ С ОГОВОРКАМИ**\n\n"
            "### Блокеры\n_Нет._\n\n"
            "### Оговорки\n"
            f"- Ошибка вызова модели-критика: `{exc}` — оценка не получена.\n\n"
            "### Кратко\n"
            "Критик не отработал из-за ошибки API. Перевод закоммичен; "
            "проверьте вручную или перезапустите job."
        )
        return current, PairQaOutcome(
            ru_path=ru_path,
            en_path=en_path,
            target_path=en_path,
            review_md=review_md,
            repair_attempted=False,
            repair_applied=False,
            repair_skip_reason="api_error",
            confirmation_md=None,
            repair_error=str(exc),
            findings=_safe_heuristics(
                settings,
                source=source_text,
                translation=current,
                source_lang=source_lang,
                target_lang=target_lang,
                label=ru_path,
            ),
        )

    verdict = parse_verdict(review_md)

    if repair_enabled and verdict == VERDICT_REJECT:
        repair_attempted = True
        fm_log(f"QA fix-diff | {ru_path}")
        try:
            fix_payload = fix_translation_pair(
                settings,
                source_lang=source_lang,
                target_lang=target_lang,
                ru_path=ru_path,
                en_path=en_path,
                source_text=source_text,
                translated_text=current,
                review_report=review_md,
                ru_pr_diff=ru_pr_diff,
                en_on_main=en_on_main,
            )
        except Exception as exc:
            repair_error = str(exc)
            repair_skip_reason = "api_error"
            fix_payload = {"fixes": []}

        fixes = fix_payload.get("fixes", [])
        if fixes:
            result = apply_fix_diff(current, fixes)
            fix_skipped_notes = result.skipped
            if result.applied > 0:
                current = result.new_text
                if target_lang.strip().lower() in ("english", "en"):
                    current = fix_yandex_cloud_links_for_en(current)
                repair_applied = True
                repair_skip_reason = None
            else:
                repair_skip_reason = repair_skip_reason or (
                    "ни одна замена не применилась "
                    "(см. пропущенные fixes в отчёте)"
                )
        else:
            repair_skip_reason = repair_skip_reason or "модель не вернула fixes"

        if repair_applied:
            fm_log(f"QA re-validate | {ru_path}")
            try:
                confirmation_md = revalidate_translation_pair(
                    settings,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    ru_path=ru_path,
                    en_path=en_path,
                    source_text=source_text,
                    translated_text=current,
                    review_before=review_md,
                    ru_pr_diff=ru_pr_diff,
                    en_on_main=en_on_main,
                )
            except Exception as exc:
                confirmation_md = (
                    "### Вердикт\n**ПРИНИМАТЬ С ОГОВОРКАМИ**\n\n"
                    "### Блокеры\n_Нет._\n\n"
                    "### Оговорки\n"
                    f"- Ошибка вызова повторной проверки: `{exc}`.\n\n"
                    "### Кратко\n"
                    "Повторная проверка не отработала после fix-diff."
                )

    findings = _safe_heuristics(
        settings,
        source=source_text,
        translation=current,
        source_lang=source_lang,
        target_lang=target_lang,
        label=ru_path,
    )

    return current, PairQaOutcome(
        ru_path=ru_path,
        en_path=en_path,
        target_path=en_path,
        review_md=review_md,
        repair_attempted=repair_attempted,
        repair_applied=repair_applied,
        repair_skip_reason=repair_skip_reason,
        confirmation_md=confirmation_md,
        repair_error=repair_error,
        findings=findings,
        fix_skipped_notes=fix_skipped_notes,
    )


def _safe_heuristics(
    settings: Settings,
    *,
    source: str,
    translation: str,
    source_lang: str,
    target_lang: str,
    label: str,
) -> list[Finding]:
    try:
        return run_heuristics(
            settings,
            source=source,
            translation=translation,
            source_lang=source_lang,
            target_lang=target_lang,
            file_label=label,
        )
    except Exception as exc:
        fm_log(f"heuristics failed | {label} | {exc}")
        return []


def final_verdict(outcome: PairQaOutcome) -> str:
    """Verdict shown to the user — preferring confirmation_md if present."""
    if outcome.confirmation_md and outcome.confirmation_md.strip():
        v = parse_verdict(outcome.confirmation_md)
        if v != VERDICT_ERROR:
            return v
    return parse_verdict(outcome.review_md)


# ---------------------------------------------------------------------------
# Report formatting (uniform between doc_translate and doc_verify)
# ---------------------------------------------------------------------------


_VERDICT_LABEL_RU = {
    VERDICT_ACCEPT: "ПРИНИМАТЬ",
    VERDICT_ACCEPT_WITH_NOTES: "ПРИНИМАТЬ С ОГОВОРКАМИ",
    VERDICT_REJECT: "НЕ ПРИНИМАТЬ",
    VERDICT_ERROR: "вердикт не получен (API)",
}


def format_pair_qa_markdown(outcome: PairQaOutcome) -> str:
    """Per-file report block. Identical shape for doc_translate and doc_verify."""
    verdict = final_verdict(outcome)
    label = _VERDICT_LABEL_RU[verdict]

    lines = [
        f"### `{outcome.en_path}`",
        "",
        f"**Вердикт:** {label}",
        "",
    ]

    if outcome.repair_attempted:
        if outcome.repair_applied:
            lines.append(
                f"**Исправления:** применено fix-diff правок"
                + (" (есть пропущенные)" if outcome.fix_skipped_notes else "")
                + "."
            )
        elif outcome.repair_error:
            lines.append(f"**Исправления:** ошибка API критика: `{outcome.repair_error}`")
        else:
            lines.append(
                f"**Исправления:** не применены — {outcome.repair_skip_reason or '—'}."
            )
        if outcome.fix_skipped_notes:
            lines.append("")
            lines.append("Пропущенные fixes:")
            for note in outcome.fix_skipped_notes:
                lines.append(f"- {note}")
        lines.append("")

    lines.append("**Эвристики:**")
    lines.append("")
    lines.append(render_findings_markdown(outcome.findings))
    lines.append("")

    if outcome.confirmation_md and outcome.confirmation_md.strip():
        lines.extend(
            [
                "<details><summary>Повторная проверка после fix-diff</summary>",
                "",
                outcome.confirmation_md.strip(),
                "",
                "</details>",
                "",
            ]
        )

    lines.extend(
        [
            "<details><summary>Полный отчёт сравнения (RU vs EN)</summary>",
            "",
            outcome.review_md.strip(),
            "",
            "</details>",
        ]
    )
    return "\n".join(lines)


def format_translation_pr_summary(
    *,
    source_pr_number: int | None,
    outcomes: list[PairQaOutcome],
) -> str:
    """High-level summary: who said what. The user decides on merge."""
    if source_pr_number is not None:
        lines = [f"## Отчёт по переводу (исходный PR #{source_pr_number})", ""]
    else:
        lines = ["## Отчёт по переводу", ""]

    by_verdict: dict[str, list[str]] = {
        VERDICT_ACCEPT: [],
        VERDICT_ACCEPT_WITH_NOTES: [],
        VERDICT_REJECT: [],
        VERDICT_ERROR: [],
    }
    critical_heuristics: list[str] = []
    for o in outcomes:
        by_verdict.setdefault(final_verdict(o), []).append(o.en_path)
        if has_critical(o.findings):
            critical_heuristics.append(o.en_path)

    lines.append(
        "**Коммит создаётся всегда.** Решение о мерже translation PR — за вами; "
        "ниже сводный вердикт критика и список найденных проблем."
    )
    lines.append("")

    if by_verdict[VERDICT_ACCEPT]:
        lines.append(
            "**Принимать:** "
            + ", ".join(f"`{p}`" for p in by_verdict[VERDICT_ACCEPT])
        )
    if by_verdict[VERDICT_ACCEPT_WITH_NOTES]:
        lines.append(
            "**С оговорками:** "
            + ", ".join(f"`{p}`" for p in by_verdict[VERDICT_ACCEPT_WITH_NOTES])
        )
    if by_verdict[VERDICT_REJECT]:
        lines.append(
            "**Не принимать (есть блокеры):** "
            + ", ".join(f"`{p}`" for p in by_verdict[VERDICT_REJECT])
        )
    if by_verdict[VERDICT_ERROR]:
        lines.append(
            "**Без вердикта (ошибка API критика):** "
            + ", ".join(f"`{p}`" for p in by_verdict[VERDICT_ERROR])
        )
    if critical_heuristics:
        lines.append(
            "**Эвристики — критично:** "
            + ", ".join(f"`{p}`" for p in critical_heuristics)
        )
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Backwards-compatible wrappers used by callers
# ---------------------------------------------------------------------------


def run_pair_qa_repair(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    target_path: str = "",
    source_text: str,
    translated_text: str,
    source_lang: str = "Russian",
    target_lang: str = "English",
    repair_enabled: bool = True,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
) -> tuple[str, PairQaOutcome]:
    """Compatibility wrapper kept for callers that pass *target_path*."""
    _ = target_path
    return run_pair_qa(
        settings,
        ru_path=ru_path,
        en_path=en_path,
        source_text=source_text,
        translated_text=translated_text,
        source_lang=source_lang,
        target_lang=target_lang,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
        repair_enabled=repair_enabled,
    )
