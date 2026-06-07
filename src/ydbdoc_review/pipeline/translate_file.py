"""Per-file translation pipeline: parse → translate → critic → render → unified QA."""

from __future__ import annotations

import copy

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.ast_types import Document
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.reinsert import reinsert_segments
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.critic import (
    apply_critic_fixes,
    run_critic as run_critic_pass,
    run_verify,
)
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import Glossary, load_glossary
from ydbdoc_review.translation.prompts import DEFAULT_PROMPT_VERSION
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.translation.translator import translate_segments
from ydbdoc_review.pipeline.types import FileTranslationResult, FileVerdict
from ydbdoc_review.pipeline.qa import compose_file_verdict, gate_round_trip
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.reporting.locations import build_segment_line_map
from ydbdoc_review.validation.fence_integrity import enforce_source_fenced_blocks
from ydbdoc_review.validation.homoglyphs import postprocess_en_target_markdown
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation
from ydbdoc_review.validation.link_locale import (
    localize_links_in_document,
    localize_links_in_text,
)


def _normalize_source_text(raw: str, *, source_lang: str) -> str:
    if source_lang.lower() in {"ru", "russian"}:
        return normalize_ru_source_for_translation(raw)
    return raw


def _render_with_translations(
    source_doc: Document,
    segments: list[Segment],
    translations: dict[str, str],
    *,
    target_lang: str = "en",
) -> str:
    doc = copy.deepcopy(source_doc)
    reinsert_segments(doc, segments, translations)
    localize_links_in_document(doc, target_lang=target_lang)
    return render_markdown(doc)


def _finalize_en_target(text: str, normalized_source_text: str) -> str:
    """Copy fenced bodies from normalized RU, then EN postprocess (homoglyphs, <строка>)."""
    text = enforce_source_fenced_blocks(text, normalized_source_text)
    text = localize_links_in_text(text, target_lang="en")
    return postprocess_en_target_markdown(text)


def _compute_critic_verdict(
    *,
    initial: CriticResponse | None,
    unresolved: CriticResponse | None,
) -> FileVerdict:
    if unresolved is None:
        if initial is None:
            return "ok"
        if not initial.issues:
            if initial.verdict == "blocked":
                return "blocked"
            return "ok"
        if initial.verdict == "blocked":
            return "blocked"
        return "warnings"
    if unresolved.verdict == "blocked":
        return "blocked"
    if unresolved.issues:
        if any(i.severity == "blocked" for i in unresolved.issues):
            return "blocked"
        return "warnings"
    return "ok"


def translate_file(
    source_text: str,
    client: YandexLLMClient,
    glossary: Glossary | None = None,
    *,
    file_path: str = "",
    config: Config | None = None,
    source_lang: str | None = None,
    target_lang: str | None = None,
    max_chars: int | None = None,
    prompt_version: str | None = None,
    cache: dict[str, str] | None = None,
    max_parallel_batches: int | None = None,
    enable_critic: bool = True,
    enable_translate: bool = True,
    existing_target_text: str | None = None,
) -> FileTranslationResult:
    """Run the per-file pipeline; QA after render is identical for translate and verify."""
    cfg = config or load_config()
    glossary = glossary or load_glossary()
    src_lang = source_lang or cfg.translation.source_lang
    tgt_lang = target_lang or cfg.translation.target_lang
    batch_chars = max_chars or cfg.translation.segments_per_batch_chars
    version = prompt_version or cfg.prompts.version
    parallel = max_parallel_batches or cfg.llm.concurrency.batches_per_file

    usage_record_start = len(client.usage_tracker.records)

    raw_source_text = source_text
    source_text = _normalize_source_text(source_text, source_lang=src_lang)

    source_doc = parse_markdown(source_text)
    segments = extract_segments(source_doc)
    segment_locations = {
        seg.id: " › ".join(seg.path) if seg.path else "(начало документа)"
        for seg in segments
    }

    if not segments:
        return FileTranslationResult.from_usage(
            tracker=client.usage_tracker,
            record_start=usage_record_start,
            file_path=file_path,
            final_text=existing_target_text or source_text,
            segments_count=0,
            verdict="ok",
            prompt_version=version,
        )

    manual_actions: list[ManualAction] = []
    segment_alignment_error: str | None = None
    translations: dict[str, str] = {}

    if enable_translate:
        # Full render from source AST — never merge or patch existing target text.
        translations = translate_segments(
            segments,
            client,
            glossary,
            file_path=file_path,
            source_lang=src_lang,
            target_lang=tgt_lang,
            max_chars=batch_chars,
            prompt_version=version,
            cache=cache,
            max_parallel_batches=parallel,
            manual_actions=manual_actions,
        )
        translated_text = _render_with_translations(
            source_doc, segments, translations, target_lang=tgt_lang
        )
        if tgt_lang.lower() in {"en", "english"}:
            translated_text = _finalize_en_target(translated_text, source_text)
    else:
        if existing_target_text is None:
            raise ValueError("existing_target_text is required when enable_translate=False")
        translated_text = existing_target_text

    translations, segment_alignment_error = gate_round_trip(segments, translated_text)

    critic_initial: CriticResponse | None = None
    critic_applied = []
    critic_skipped = []
    critic_unresolved: CriticResponse | None = None

    if enable_critic and not segment_alignment_error:
        critic_initial = run_critic_pass(
            client,
            segments=segments,
            translations=translations,
            glossary=glossary,
            file_path=file_path,
            source_lang=src_lang,
            target_lang=tgt_lang,
            prompt_version=version,
            max_chars=batch_chars,
        )
        translations, critic_applied, critic_skipped = apply_critic_fixes(
            translations, segments, critic_initial.issues
        )
        if critic_initial.issues:
            translated_text = _render_with_translations(
                source_doc, segments, translations, target_lang=tgt_lang
            )
            if tgt_lang.lower() in {"en", "english"}:
                translated_text = _finalize_en_target(translated_text, source_text)
            translations, segment_alignment_error = gate_round_trip(
                segments, translated_text
            )
            if not segment_alignment_error:
                critic_unresolved = run_verify(
                    client,
                    segments=segments,
                    translations=translations,
                    prior_issues=critic_initial.issues,
                    glossary=glossary,
                    file_path=file_path,
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                    prompt_version=version,
                    max_chars=batch_chars,
                )

    critic_verdict = _compute_critic_verdict(
        initial=critic_initial,
        unresolved=critic_unresolved,
    )

    heuristics = run_file_heuristics_classified(
        raw_source_text,
        translated_text,
        normalized_source_text=source_text,
        source_lang=src_lang,
        target_lang=tgt_lang,
    )

    verdict = compose_file_verdict(
        critic_verdict=critic_verdict,
        alignment_error=segment_alignment_error,
        heuristics=heuristics,
        manual_actions=bool(manual_actions),
    )

    segment_lines = build_segment_line_map(
        translated_text, segments, translations
    )

    return FileTranslationResult.from_usage(
        tracker=client.usage_tracker,
        record_start=usage_record_start,
        file_path=file_path,
        final_text=translated_text,
        segments_count=len(segments),
        verdict=verdict,
        prompt_version=version,
        critic_initial=critic_initial,
        critic_applied=critic_applied,
        critic_skipped=critic_skipped,
        critic_unresolved=critic_unresolved,
        heuristic_blocking=heuristics.blocking,
        heuristic_warnings=heuristics.warnings,
        heuristic_info=heuristics.info,
        manual_actions=manual_actions,
        segment_locations=segment_locations,
        segment_lines=segment_lines,
        segment_alignment_error=segment_alignment_error,
    )
