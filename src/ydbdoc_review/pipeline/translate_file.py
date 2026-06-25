"""Per-file translation pipeline: parse → translate → critic → render → unified QA."""

from __future__ import annotations

import copy

from ydbdoc_review.config.loader import Config, load_config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.ast_types import Document
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import (
    normalize_target_segments_to_source,
)
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
from ydbdoc_review.validation.placeholder_drift import (
    drop_spurious_placeholder_issues,
    filter_critic_response,
)
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.reporting.locations import build_segment_excerpts, build_segment_line_map
from ydbdoc_review.validation.fence_comments import (
    translate_cyrillic_fence_comments_with_client,
    translate_cyrillic_text_fences_with_client,
)
from ydbdoc_review.validation.prose_cyrillic import (
    translate_cyrillic_prose_with_client,
)
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


def _remap_translations_by_position(
    source_segments: list[Segment],
    target_segments: list[Segment],
    translations: dict[str, str],
) -> dict[str, str]:
    """Re-key a translations dict from source-segment ids to target-segment ids.

    Used in doc_verify mode where critic returns fixes keyed by RU segment id
    but rendering happens on the EN AST. Mapping is by zipped position; the
    caller must ensure segment counts match.
    """
    return {
        tgt.id: translations[src.id]
        for src, tgt in zip(source_segments, target_segments, strict=True)
        if src.id in translations
    }


def _render_with_translations(
    base_doc: Document,
    segments: list[Segment],
    translations: dict[str, str],
    *,
    target_lang: str = "en",
) -> str:
    doc = copy.deepcopy(base_doc)
    reinsert_segments(doc, segments, translations)
    localize_links_in_document(doc, target_lang=target_lang)
    return render_markdown(doc)


def _finalize_en_target(
    text: str,
    normalized_source_text: str,
    *,
    client: YandexLLMClient | None = None,
    glossary: Glossary | None = None,
    file_path: str = "",
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str:
    """Copy fenced bodies from the fence reference (RU in doc_translate, EN in
    doc_verify), translate residual Cyrillic, EN postprocess."""
    text = enforce_source_fenced_blocks(text, normalized_source_text)
    if client is not None and glossary is not None:
        text = translate_cyrillic_fence_comments_with_client(
            text,
            client,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
        )
        text = translate_cyrillic_text_fences_with_client(
            text,
            client,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
        )
        text = translate_cyrillic_prose_with_client(
            text,
            client,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
        )
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

    # Render base: doc_translate uses the source (RU) AST; doc_verify uses the
    # existing target (EN) AST so its fenced code blocks survive untouched.
    render_base_doc: Document = source_doc
    render_base_segments: list[Segment] = segments
    fence_reference_text: str = source_text

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
            translated_text = _finalize_en_target(
                translated_text,
                source_text,
                client=client,
                glossary=glossary,
                file_path=file_path,
                source_lang=src_lang,
                target_lang=tgt_lang,
                prompt_version=version,
            )
    else:
        if existing_target_text is None:
            raise ValueError("existing_target_text is required when enable_translate=False")
        translated_text = existing_target_text
        # doc_verify: derive the render base from the existing EN target so
        # fenced code blocks (mermaid, sql, …) are taken from EN, not RU.
        try:
            target_doc = parse_markdown(existing_target_text)
            target_segments = extract_segments(target_doc)
        except Exception:  # parse failure → fall back to source-based render
            target_doc = source_doc
            target_segments = segments
        if len(target_segments) == len(segments):
            # Renumber EN placeholders to share names with RU for atoms that
            # appear in both. The critic and apply path see one consistent
            # numbering instead of two independent left-to-right schemes, and
            # re-insertion through the EN AST resolves the right atoms.
            target_segments = normalize_target_segments_to_source(
                segments, target_segments
            )
            render_base_doc = target_doc
            render_base_segments = target_segments
            fence_reference_text = existing_target_text

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
        actionable_issues = drop_spurious_placeholder_issues(
            critic_initial.issues, segments, translations
        )
        translations, critic_applied, critic_skipped = apply_critic_fixes(
            translations,
            segments,
            actionable_issues,
            strict_placeholder_order=not enable_translate,
        )
        if actionable_issues:
            render_translations = (
                translations
                if render_base_segments is segments
                else _remap_translations_by_position(
                    segments, render_base_segments, translations
                )
            )
            translated_text = _render_with_translations(
                render_base_doc,
                render_base_segments,
                render_translations,
                target_lang=tgt_lang,
            )
            if tgt_lang.lower() in {"en", "english"}:
                translated_text = _finalize_en_target(
                    translated_text,
                    fence_reference_text,
                    client=client,
                    glossary=glossary,
                    file_path=file_path,
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                    prompt_version=version,
                )
            translations, segment_alignment_error = gate_round_trip(
                segments, translated_text
            )
            if not segment_alignment_error:
                critic_unresolved = run_verify(
                    client,
                    segments=segments,
                    translations=translations,
                    prior_issues=actionable_issues,
                    glossary=glossary,
                    file_path=file_path,
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                    prompt_version=version,
                    max_chars=batch_chars,
                )
                critic_unresolved = filter_critic_response(
                    critic_unresolved,
                    segments,
                    translations,
                    skipped=critic_skipped,
                )
        else:
            critic_unresolved = CriticResponse(verdict="ok", issues=[])

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
        translated_text,
        segments,
        translations,
        placeholder_segments=render_base_segments,
    )
    segment_excerpts = build_segment_excerpts(
        translated_text,
        segments,
        translations,
        segment_lines,
        placeholder_segments=render_base_segments,
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
        segment_excerpts=segment_excerpts,
        segment_alignment_error=segment_alignment_error,
    )
