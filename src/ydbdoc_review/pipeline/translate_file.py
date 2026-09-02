"""Per-file translation pipeline — delegates to ``harness`` (translate / verify profiles)."""

from __future__ import annotations

from ydbdoc_review.config.loader import Config
from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import compose_file_verdict, gate_round_trip
from ydbdoc_review.pipeline.types import FileTranslationResult
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.model_policy import TranslationJobManifest
from ydbdoc_review.translation.one_pass import translate_ru_to_en_once
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


def _compute_critic_verdict(
    *, initial: CriticResponse | None, unresolved: CriticResponse | None
) -> str:
    """Map read-only critic reports to the common file verdict."""
    active = unresolved or initial
    if active is None or not active.issues:
        return "ok"
    return "blocked" if active.verdict == "blocked" else "warnings"


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
    enable_critic: bool = False,
    enable_translate: bool = True,
    existing_target_text: str | None = None,
    base_source_text: str | None = None,
    manifest: TranslationJobManifest | None = None,
) -> FileTranslationResult:
    """Translate once from RU, or return an existing EN target read-only for QA."""
    if not enable_translate:
        target = existing_target_text or ""
        normalized = normalize_ru_source_for_translation(source_text)
        segments = extract_segments(parse_markdown(normalized))
        _translations, alignment_error = gate_round_trip(segments, target)
        heuristics = run_file_heuristics_classified(
            source_text,
            target,
            normalized_source_text=normalized,
            source_file=file_path,
        )
        return FileTranslationResult(
            file_path=file_path,
            final_text=target,
            segments_count=len(segments),
            verdict=compose_file_verdict(
                critic_verdict="ok",
                alignment_error=alignment_error,
                heuristics=heuristics,
                manual_actions=False,
            ),
            prompt_version=prompt_version or "one-pass-v1",
            heuristic_blocking=heuristics.blocking,
            heuristic_warnings=heuristics.warnings,
            heuristic_info=heuristics.info,
            segment_alignment_error=alignment_error,
        )
    if manifest is None:
        raise ValueError("translation manifest is required before model use")
    result = translate_ru_to_en_once(
        source_text,
        client,
        file_path=file_path,
        manifest=manifest,
    )
    return FileTranslationResult(
        file_path=file_path,
        final_text=result.text,
        segments_count=result.prose_count,
        verdict="ok",
        prompt_version=prompt_version or "one-pass-v1",
    )
