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
from ydbdoc_review.translation.glossary import Glossary, load_glossary
from ydbdoc_review.translation.prompts import DEFAULT_PROMPT_VERSION
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.translation.translator import translate_segments
from ydbdoc_review.pipeline.types import FileTranslationResult, FileVerdict


def _render_with_translations(
    source_doc: Document,
    segments: list[Segment],
    translations: dict[str, str],
) -> str:
    doc = copy.deepcopy(source_doc)
    reinsert_segments(doc, segments, translations)
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

    if not segments:
        return FileTranslationResult.from_usage(
            tracker=client.usage_tracker,
            file_path=file_path,
            final_text=source_text,
            segments_count=0,
            verdict="ok",
            prompt_version=version,
        )

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

    critic_initial: CriticResponse | None = None
    critic_applied = []
    critic_skipped = []
    critic_unresolved: CriticResponse | None = None

    if enable_critic:
        critic_initial = run_critic_pass(
            client,
            source_text=source_text,
            translated_text=translated_text,
            segments=segments,
            glossary=glossary,
            file_path=file_path,
            source_lang=src_lang,
            target_lang=tgt_lang,
            prompt_version=version,
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
                source_text=source_text,
                translated_text=text_after_fixes,
                segments=segments,
                prior_issues=critic_initial.issues,
                glossary=glossary,
                file_path=file_path,
                source_lang=src_lang,
                target_lang=tgt_lang,
                prompt_version=version,
            )
            translated_text = text_after_fixes

    verdict = _compute_verdict(
        initial=critic_initial,
        unresolved=critic_unresolved,
    )

    # Phase E: deterministic heuristics hook (empty until implemented).
    heuristic_warnings: list[str] = []

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
    )
