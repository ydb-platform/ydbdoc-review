"""Per-file translation pipeline: parse → translate → critic → render."""

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
from ydbdoc_review.validation.heuristics import bump_verdict_for_heuristics, run_file_heuristics
from ydbdoc_review.validation.link_locale import localize_links_in_document


def _align_translations(
    source_segments: list[Segment],
    target_text: str,
) -> dict[str, str]:
    """Map source segment ids → target segment texts (same structure required)."""
    target_segments = extract_segments(parse_markdown(target_text))
    if len(target_segments) != len(source_segments):
        raise TranslationValidationError(
            f"segment count mismatch: source {len(source_segments)} vs "
            f"target {len(target_segments)}"
        )
    return {
        src.id: tgt.text for src, tgt in zip(source_segments, target_segments, strict=True)
    }


def _render_with_translations(
    source_doc: Document,
    segments: list[Segment],
    translations: dict[str, str],
) -> str:
    doc = copy.deepcopy(source_doc)
    reinsert_segments(doc, segments, translations)
    localize_links_in_document(doc)
    return render_markdown(doc)


def _compute_verdict(
    *,
    initial: CriticResponse | None,
    unresolved: CriticResponse | None,
) -> FileVerdict:
    if unresolved is None:
        if initial is None:
            return "ok"
        if not initial.issues:
            if initial.verdict in ("blocked", "warnings"):
                return initial.verdict
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
    """Run the full per-file translation pipeline on markdown ``source_text``."""
    cfg = config or load_config()
    glossary = glossary or load_glossary()
    src_lang = source_lang or cfg.translation.source_lang
    tgt_lang = target_lang or cfg.translation.target_lang
    batch_chars = max_chars or cfg.translation.segments_per_batch_chars
    version = prompt_version or cfg.prompts.version
    parallel = max_parallel_batches or cfg.llm.concurrency.batches_per_file

    source_doc = parse_markdown(source_text)
    segments = extract_segments(source_doc)
    segment_locations = {
        seg.id: " › ".join(seg.path) if seg.path else "(начало документа)"
        for seg in segments
    }

    if not segments:
        return FileTranslationResult.from_usage(
            tracker=client.usage_tracker,
            file_path=file_path,
            final_text=existing_target_text or source_text,
            segments_count=0,
            verdict="ok",
            prompt_version=version,
        )

    if enable_translate:
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
        )
        translated_text = _render_with_translations(source_doc, segments, translations)
    else:
        if existing_target_text is None:
            raise ValueError("existing_target_text is required when enable_translate=False")
        try:
            translations = _align_translations(segments, existing_target_text)
        except TranslationValidationError:
            translations = {seg.id: seg.text for seg in segments}
        translated_text = existing_target_text

    critic_initial: CriticResponse | None = None
    critic_applied = []
    critic_skipped = []
    critic_unresolved: CriticResponse | None = None

    if enable_critic:
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
            text_after_fixes = _render_with_translations(
                source_doc, segments, translations
            )
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
            translated_text = text_after_fixes

    verdict = _compute_verdict(
        initial=critic_initial,
        unresolved=critic_unresolved,
    )

    heuristic_warnings = run_file_heuristics(
        source_text,
        translated_text,
        source_lang=src_lang,
        target_lang=tgt_lang,
    )
    verdict = bump_verdict_for_heuristics(verdict, heuristic_warnings)

    return FileTranslationResult.from_usage(
        tracker=client.usage_tracker,
        file_path=file_path,
        final_text=translated_text,
        segments_count=len(segments),
        verdict=verdict,
        prompt_version=version,
        critic_initial=critic_initial,
        critic_applied=critic_applied,
        critic_skipped=critic_skipped,
        critic_unresolved=critic_unresolved,
        heuristic_warnings=heuristic_warnings,
        segment_locations=segment_locations,
    )
