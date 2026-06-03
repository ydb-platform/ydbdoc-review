"""Per-batch segment translator (JSON I/O + validation)."""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.llm.structured import parse_json_model
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.segmentation.chunker import Batch, chunk_segments
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.repair import repair_segment_translation
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_translate_messages,
)
from ydbdoc_review.translation.schemas import TranslateBatchResponse
from ydbdoc_review.validation.cli_tokens import cli_tokens_preserved
from ydbdoc_review.validation.heuristics import count_fence_markers
from ydbdoc_review.validation.markers import placeholders_match
from ydbdoc_review.validation.placeholder_repair import repair_translation_placeholders
from ydbdoc_review.validation.placeholder_roles import placeholder_roles_valid

logger = logging.getLogger(__name__)

_MAX_BATCH_ATTEMPTS = 3
_PLACEHOLDER_MISMATCH_HINT = "placeholder mismatch"


def parse_translate_response(raw: str, *, expected_ids: set[str]) -> dict[str, str]:
    """Parse and validate translator JSON; return id → translated text."""
    parsed = parse_json_model(raw, TranslateBatchResponse)
    got_ids = {item.id for item in parsed.segments}
    if got_ids != expected_ids:
        missing = expected_ids - got_ids
        extra = got_ids - expected_ids
        parts: list[str] = []
        if missing:
            parts.append(f"missing ids: {sorted(missing)}")
        if extra:
            parts.append(f"extra ids: {sorted(extra)}")
        raise LLMParseError("Segment id mismatch: " + "; ".join(parts))
    return {item.id: item.text for item in parsed.segments}


def validate_segment_translation(source: Segment, translated_text: str) -> None:
    """Structural checks for one segment translation."""
    if not placeholders_match(source.text, translated_text):
        raise TranslationValidationError(
            f"placeholder mismatch for {source.id!r}: "
            f"expected placeholders from source in same order",
            segment_id=source.id,
        )
    if not placeholder_roles_valid(source, translated_text):
        raise TranslationValidationError(
            f"placeholder role mismatch for {source.id!r}: "
            f"⟦V⟧ must not appear in link URLs unless the source does",
            segment_id=source.id,
        )
    if not cli_tokens_preserved(source.text, translated_text):
        raise TranslationValidationError(
            f"CLI/shell token missing in translation for {source.id!r}",
            segment_id=source.id,
        )
    src_fences = count_fence_markers(source.text)
    tgt_fences = count_fence_markers(translated_text)
    if src_fences != tgt_fences:
        raise TranslationValidationError(
            f"fence count mismatch for {source.id!r}: "
            f"source {src_fences} vs translation {tgt_fences}",
            segment_id=source.id,
        )


def validate_batch_translations(
    batch: Batch, translations: dict[str, str]
) -> None:
    """Validate all segments in a batch."""
    for seg in batch.segments:
        if seg.id not in translations:
            raise TranslationValidationError(
                f"missing translation for {seg.id!r}",
                segment_id=seg.id,
            )
        validate_segment_translation(seg, translations[seg.id])


def _cache_key(seg: Segment, *, target_lang: str) -> str:
    payload = json.dumps(
        {"text": seg.text, "path": seg.path, "lang": target_lang},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).digest().hex()


def _apply_placeholder_realignment(
    batch: Batch, translations: dict[str, str]
) -> None:
    """In-place: fix renumbered or exposed atoms before validation."""
    for seg in batch.segments:
        text = translations[seg.id]
        text = repair_translation_placeholders(seg, text)
        translations[seg.id] = text


def _segment_location(seg: Segment) -> str:
    return " › ".join(seg.path) if seg.path else "(начало документа)"


def _translate_batch_once(
    client: YandexLLMClient,
    batch: Batch,
    glossary: Glossary,
    *,
    file_path: str,
    source_lang: str,
    target_lang: str,
    prompt_version: str,
    last_attempt: dict[str, str] | None = None,
) -> dict[str, str]:
    last_exc: LLMParseError | TranslationValidationError | None = None
    model_chain = client.model_chain_for_role("translate")
    primary_model = model_chain[0]
    for attempt in range(1, _MAX_BATCH_ATTEMPTS + 1):
        try:
            messages = build_translate_messages(
                batch,
                glossary,
                file_path=file_path,
                source_lang=source_lang,
                target_lang=target_lang,
                version=prompt_version,
            )
            result = client.chat(messages, model=primary_model)
            expected = {seg.id for seg in batch.segments}
            translations = parse_translate_response(
                result.content, expected_ids=expected
            )
            _apply_placeholder_realignment(batch, translations)
            if last_attempt is not None:
                last_attempt.clear()
                last_attempt.update(translations)
            validate_batch_translations(batch, translations)
            return translations
        except (LLMParseError, TranslationValidationError) as exc:
            last_exc = exc
            if attempt < _MAX_BATCH_ATTEMPTS:
                logger.warning(
                    "Translate batch %s attempt %s/%s failed: %s",
                    batch.index,
                    attempt,
                    _MAX_BATCH_ATTEMPTS,
                    exc,
                )
    if (
        len(model_chain) > 1
        and isinstance(last_exc, TranslationValidationError)
        and _PLACEHOLDER_MISMATCH_HINT in str(last_exc)
    ):
        for fallback_model in model_chain[1:]:
            try:
                logger.warning(
                    "Translate batch %s retry with fallback model %s: %s",
                    batch.index,
                    fallback_model,
                    last_exc,
                )
                messages = build_translate_messages(
                    batch,
                    glossary,
                    file_path=file_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    version=prompt_version,
                )
                result = client.chat(messages, model=fallback_model)
                expected = {seg.id for seg in batch.segments}
                translations = parse_translate_response(
                    result.content, expected_ids=expected
                )
                _apply_placeholder_realignment(batch, translations)
                if last_attempt is not None:
                    last_attempt.clear()
                    last_attempt.update(translations)
                validate_batch_translations(batch, translations)
                return translations
            except (LLMParseError, TranslationValidationError) as exc:
                last_exc = exc
            except Exception as exc:  # noqa: BLE001 - keep original validation error if fallback infra fails
                logger.warning(
                    "Translate batch %s fallback model %s failed: %s",
                    batch.index,
                    fallback_model,
                    exc,
                )
    assert last_exc is not None
    raise last_exc


def _record_manual_action(
    manual_actions: list[ManualAction] | None,
    seg: Segment,
    *,
    message: str,
) -> None:
    if manual_actions is None:
        return
    action = ManualAction(
        segment_id=seg.id,
        location=_segment_location(seg),
        message=message,
    )
    if not any(
        a.segment_id == action.segment_id and a.message == action.message
        for a in manual_actions
    ):
        manual_actions.append(action)


def _recover_or_fallback_segment(
    seg: Segment,
    exc: Exception,
    *,
    client: YandexLLMClient,
    glossary: Glossary,
    file_path: str,
    source_lang: str,
    target_lang: str,
    prompt_version: str,
    failed_attempt: str | None,
    manual_actions: list[ManualAction] | None,
) -> dict[str, str]:
    """Repair-pass, then table fail-soft; otherwise re-raise."""
    if isinstance(exc, TranslationValidationError):
        repaired = repair_segment_translation(
            client,
            seg,
            glossary,
            validation_error=str(exc),
            failed_attempt=failed_attempt,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
        )
        if repaired is not None:
            return {seg.id: repaired}

    if seg.kind in {
        SegmentKind.TABLE_HEADER_CELL,
        SegmentKind.TABLE_BODY_CELL,
    }:
        where = _segment_location(seg)
        message = (
            f"Таблица не переведена автоматически ({where}, `{seg.id}`); "
            "оставлена на русском. Переведите вручную."
        )
        _record_manual_action(manual_actions, seg, message=message)
        logger.warning(
            "Translate kept source table segment %s after validation failure: %s",
            seg.id,
            exc,
        )
        return {seg.id: seg.text}

    raise exc


def translate_batch(
    client: YandexLLMClient,
    batch: Batch,
    glossary: Glossary,
    *,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    manual_actions: list[ManualAction] | None = None,
) -> dict[str, str]:
    """Translate one batch; fall back to per-segment calls on batch failure."""
    last_attempt: dict[str, str] = {}
    try:
        return _translate_batch_once(
            client,
            batch,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
            last_attempt=last_attempt,
        )
    except (LLMParseError, TranslationValidationError) as exc:
        if len(batch.segments) == 1:
            seg = batch.segments[0]
            if isinstance(exc, TranslationValidationError):
                return _recover_or_fallback_segment(
                    seg,
                    exc,
                    client=client,
                    glossary=glossary,
                    file_path=file_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    prompt_version=prompt_version,
                    failed_attempt=last_attempt.get(seg.id),
                    manual_actions=manual_actions,
                )
            raise
        logger.warning(
            "Batch %s failed (%s); retrying %d segments individually",
            batch.index,
            exc,
            len(batch.segments),
        )

    out: dict[str, str] = {}
    for seg in batch.segments:
        single = Batch(index=batch.index, segments=[seg])
        seg_attempt: dict[str, str] = {}
        try:
            out.update(
                _translate_batch_once(
                    client,
                    single,
                    glossary,
                    file_path=file_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    prompt_version=prompt_version,
                    last_attempt=seg_attempt,
                )
            )
        except TranslationValidationError as exc:
            out.update(
                _recover_or_fallback_segment(
                    seg,
                    exc,
                    client=client,
                    glossary=glossary,
                    file_path=file_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    prompt_version=prompt_version,
                    failed_attempt=seg_attempt.get(seg.id),
                    manual_actions=manual_actions,
                )
            )
        except LLMParseError:
            raise
    return out


def translate_segments(
    segments: list[Segment],
    client: YandexLLMClient,
    glossary: Glossary,
    *,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    max_chars: int = 4000,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    cache: dict[str, str] | None = None,
    max_parallel_batches: int = 3,
    manual_actions: list[ManualAction] | None = None,
) -> dict[str, str]:
    """Translate all segments (chunked batches, optional cache, parallel I/O)."""
    if not segments:
        return {}

    translations: dict[str, str] = {}
    pending: list[Segment] = []

    for seg in segments:
        if cache is not None:
            key = _cache_key(seg, target_lang=target_lang)
            cached = cache.get(key)
            if cached is not None:
                validate_segment_translation(seg, cached)
                translations[seg.id] = cached
                continue
        pending.append(seg)

    if not pending:
        return translations

    batches = chunk_segments(pending, max_chars=max_chars)
    if max_parallel_batches < 1:
        raise ValueError("max_parallel_batches must be >= 1")

    def _run_batch(batch: Batch) -> dict[str, str]:
        return translate_batch(
            client,
            batch,
            glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
            manual_actions=manual_actions,
        )

    if max_parallel_batches == 1 or len(batches) == 1:
        batch_results = [_run_batch(b) for b in batches]
    else:
        results_by_index: dict[int, dict[str, str]] = {}
        with ThreadPoolExecutor(max_workers=max_parallel_batches) as pool:
            futures = {pool.submit(_run_batch, b): i for i, b in enumerate(batches)}
            for fut in as_completed(futures):
                results_by_index[futures[fut]] = fut.result()
        batch_results = [results_by_index[i] for i in range(len(batches))]

    for batch, batch_trans in zip(batches, batch_results, strict=True):
        for seg in batch.segments:
            text = batch_trans[seg.id]
            translations[seg.id] = text
            if cache is not None:
                cache[_cache_key(seg, target_lang=target_lang)] = text

    return translations
