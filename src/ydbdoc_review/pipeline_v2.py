"""Pipeline v2: segment → translate → assemble; QA on two full files."""

from __future__ import annotations

import json
import re

from ydbdoc_review.config import Settings
from ydbdoc_review.document_segments import (
    DocumentUnit,
    assemble_document_units,
    parse_document_units,
)
from ydbdoc_review.fm_progress import fm_log
from ydbdoc_review.llm import (
    _read_prompt,
    _strip_code_fence,
    call_yandex_responses,
    clamp_max_output_tokens,
    confirm_repair_pair,
    fix_translation_pair,
    verify_translation_pair,
)
from ydbdoc_review.markdown_links import restore_markdown_links_from_ru
from ydbdoc_review.translation_qa import (
    PairQaOutcome,
    file_merge_verdict,
    qa_repair_max_rounds,
    repair_report_for_fixer,
    review_needs_repair,
)
from ydbdoc_review.translate_postprocess import (
    apply_deterministic_cli_fixes,
    fix_yandex_cloud_links_for_en,
)

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")


def load_translate_segment_instructions(settings: Settings) -> str:
    from pathlib import Path

    return _read_prompt(Path(settings.prompts_dir) / "08_translate_segment.txt")


def _translate_segment_instructions(settings: Settings) -> str:
    return load_translate_segment_instructions(settings).strip()


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
    instructions = _translate_segment_instructions(settings)
    user_input = _segment_user_input(
        source_lang=source_lang,
        target_lang=target_lang,
        source_path=source_path,
        unit=unit,
        body=body,
    )
    model = settings.model_translate
    cap = clamp_max_output_tokens(
        max(4096, min(len(body) * 3, 32_768)), model
    )
    out = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=cap,
            operation=operation,
            detail=f"{unit.label}",
        ).strip()
    )
    if target_lang.strip().lower() in ("english", "en"):
        out = fix_yandex_cloud_links_for_en(out)
    return out


def _fence_comment_lines(fence_text: str) -> list[tuple[int, str, str]]:
    """(line_index, marker '--' or '#', comment body) for translatable comment lines."""
    from ydbdoc_review.fence_comments import (
        comment_body_on_line,
        inline_sql_comment_tail,
    )

    rows: list[tuple[int, str, str]] = []
    lines = fence_text.split("\n")
    for idx, line in enumerate(lines):
        full = comment_body_on_line(line)
        if full is not None:
            marker, body = full
            if body and _CYRILLIC_RE.search(body):
                rows.append((idx, marker, body))
            continue
        inline = inline_sql_comment_tail(line)
        if inline is not None:
            _prefix, body = inline
            if body and _CYRILLIC_RE.search(body):
                rows.append((idx, "--", body))
    return rows


def _translate_fence_unit(
    settings: Settings,
    *,
    source_path: str,
    unit: DocumentUnit,
    target_lang: str,
) -> str:
    comments = _fence_comment_lines(unit.text)
    if not comments or target_lang.strip().lower() not in ("english", "en"):
        return unit.text

    payload = json.dumps(
        [{"id": i, "text": body} for i, (_li, _m, body) in enumerate(comments)],
        ensure_ascii=False,
    )
    instructions = _translate_segment_instructions(settings)
    user_input = (
        f"File: `{source_path}`\n"
        f"Fragment type: `fence-comments`\n"
        f"Translate each JSON ``text`` field from Russian to English.\n"
        f"Return JSON array with same ``id`` values and translated ``text``.\n"
        f"Only JSON, no markdown fence.\n\n{payload}"
    )
    model = settings.model_translate
    raw = _strip_code_fence(
        call_yandex_responses(
            settings,
            model,
            instructions=instructions,
            user_input=user_input,
            max_output_tokens=4096,
            operation="translate:fence-comments-batch",
            detail=unit.label,
        ).strip()
    )
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("expected JSON array")
        by_id = {int(item["id"]): str(item["text"]) for item in data if "id" in item}
    except (json.JSONDecodeError, TypeError, ValueError):
        fm_log(f"fence-comments batch parse failed, keeping fence verbatim | {unit.label}")
        return unit.text

    lines = unit.text.split("\n")
    for i, (line_idx, marker, _body) in enumerate(comments):
        if i not in by_id:
            continue
        translated = by_id[i].strip()
        line = lines[line_idx]
        indent = line[: len(line) - len(line.lstrip())]
        if marker == "--" and line.lstrip().startswith("--"):
            lines[line_idx] = f"{indent}-- {translated}"
        elif marker == "#":
            lines[line_idx] = f"{indent}# {translated}"
    return "\n".join(lines)


def translate_unit(
    settings: Settings,
    unit: DocumentUnit,
    *,
    source_path: str,
    source_lang: str,
    target_lang: str,
) -> DocumentUnit:
    """Translate one parsed unit."""
    if unit.kind == "fence":
        text = _translate_fence_unit(
            settings,
            source_path=source_path,
            unit=unit,
            target_lang=target_lang,
        )
        return DocumentUnit(kind="fence", text=text, label=unit.label)

    body = unit.text
    if unit.kind in ("prose", "table", "diplodoc"):
        text = _call_translate_segment(
            settings,
            source_lang=source_lang,
            target_lang=target_lang,
            source_path=source_path,
            unit=unit,
            body=body,
            operation=f"translate:{unit.kind}",
        )
        return DocumentUnit(kind=unit.kind, text=text, label=unit.label)

    return unit


def translate_ru_document_v2(
    settings: Settings,
    *,
    ru_path: str,
    ru_full: str,
) -> tuple[str, str]:
    """
    RU→EN: parse into units, one FM call per unit (batched fence comments), assemble.
    """
    units = parse_document_units(ru_full, doc_label=ru_path)
    fm_log(f"pipeline-v2 translate | {ru_path} | {len(units)} unit(s)")
    translated: list[DocumentUnit] = []
    for i, unit in enumerate(units, start=1):
        fm_log(f"pipeline-v2 unit {i}/{len(units)} | {unit.kind} | {unit.label}")
        translated.append(
            translate_unit(
                settings,
                unit,
                source_path=ru_path,
                source_lang="Russian",
                target_lang="English",
            )
        )
    merged = assemble_document_units(translated)
    merged = restore_markdown_links_from_ru(ru_full, merged)
    merged = apply_deterministic_cli_fixes(merged, ru_source=ru_full)
    return merged, f"pipeline-v2-{len(units)}-units"


def _apply_whole_file_repair(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    source_text: str,
    current: str,
    review_report: str,
    ru_pr_diff: str | None,
    en_on_main: str | None,
) -> tuple[str, bool, str | None, str | None]:
    """Returns (en_text, applied, skip_reason, error)."""
    try:
        fixed = fix_translation_pair(
            settings,
            verify_model=settings.model_translation_verify,
            source_lang="Russian",
            target_lang="English",
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translated_text=current,
            review_report=review_report,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )
        fixed = restore_markdown_links_from_ru(source_text, fixed)
        fixed = apply_deterministic_cli_fixes(fixed, ru_source=source_text)
        if fixed.strip() and fixed.strip() != current.strip():
            return fixed, True, None, None
        return current, False, "repair не изменил файл", None
    except Exception as exc:
        return current, False, "api_error", str(exc)


def run_pair_qa_v2(
    settings: Settings,
    *,
    ru_path: str,
    en_path: str,
    source_text: str,
    translated_text: str,
    source_pr_number: int | None = None,
    ru_pr_diff: str | None = None,
    en_on_main: str | None = None,
    repair_enabled: bool = True,
) -> tuple[str, PairQaOutcome]:
    """
    Exactly up to 3 FM calls per file: critic (RU+EN whole files), optional repair,
    translator verdict. No section loops, no deterministic_prepare, no cyrillic repair.
    """
    translation_before = translated_text
    current = translated_text

    fm_log(f"pipeline-v2 QA critic (2 full files, 1 request) | {ru_path}")
    review_md = verify_translation_pair(
        settings,
        translate_model=settings.model_translate,
        verify_model=settings.model_translation_verify,
        source_lang="Russian",
        target_lang="English",
        ru_path=ru_path,
        en_path=en_path,
        source_text=source_text,
        translated_text=current,
        source_pr_number=source_pr_number,
        ru_pr_diff=ru_pr_diff,
        en_on_main=en_on_main,
    )

    repair_applied = False
    repair_attempted = False
    repair_skip_reason: str | None = None
    repair_error: str | None = None

    if repair_enabled and review_needs_repair(review_md):
        repair_attempted = True
        fm_log(f"pipeline-v2 QA repair (1 whole file, 1 request) | {ru_path}")
        current, applied, skip, err = _apply_whole_file_repair(
            settings,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            current=current,
            review_report=review_md,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )
        repair_applied = applied
        repair_skip_reason = skip
        repair_error = err

    extra_after_reject = 0
    max_extra = qa_repair_max_rounds() if repair_enabled else 0
    confirmation_md: str | None = None
    while True:
        fm_log(f"pipeline-v2 QA translator (1 request) | {ru_path}")
        confirmation_md = confirm_repair_pair(
            settings,
            translate_model=settings.model_translate,
            verify_model=settings.model_translation_verify,
            source_lang="Russian",
            target_lang="English",
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            translation_before=translation_before,
            translation_after=current,
            review_before=review_md,
            en_on_main=en_on_main,
            ru_pr_diff=ru_pr_diff,
        )
        verdict = file_merge_verdict(review_md, confirmation_md)
        if verdict != "reject" or extra_after_reject >= max_extra:
            break
        extra_after_reject += 1
        repair_attempted = True
        report = repair_report_for_fixer(
            review_md, translator_confirmation=confirmation_md
        )
        fm_log(
            f"pipeline-v2 QA repair after reject "
            f"{extra_after_reject}/{max_extra} | {ru_path}"
        )
        current, applied, skip, err = _apply_whole_file_repair(
            settings,
            ru_path=ru_path,
            en_path=en_path,
            source_text=source_text,
            current=current,
            review_report=report,
            ru_pr_diff=ru_pr_diff,
            en_on_main=en_on_main,
        )
        repair_applied = repair_applied or applied
        if skip:
            repair_skip_reason = skip
        if err:
            repair_error = err
            break

    outcome = PairQaOutcome(
        ru_path=ru_path,
        en_path=en_path,
        target_path=en_path,
        review_md=review_md,
        repair_attempted=repair_attempted,
        repair_applied=repair_applied,
        repair_skip_reason=repair_skip_reason,
        confirmation_md=confirmation_md,
        repair_error=repair_error,
    )
    return current, outcome


def pipeline_v2_enabled() -> bool:
    import os

    raw = os.environ.get("YDBDOC_PIPELINE", "v2").strip().lower()
    return raw not in ("legacy", "v1", "old")


