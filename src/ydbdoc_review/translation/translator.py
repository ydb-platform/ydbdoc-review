"""Per-batch segment translator (JSON I/O + validation)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError, LLMRetryExhaustedError
from ydbdoc_review.llm.retry import (
    is_eliza_model_unavailable,
    is_model_unavailable,
    is_rate_limit_error,
)
from ydbdoc_review.llm.structured import parse_json_model
from ydbdoc_review.segmentation.chunker import Batch, chunk_segments
from ydbdoc_review.segmentation.types import Segment, SegmentKind
from ydbdoc_review.shutdown import check_shutdown
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.manual import ManualAction
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_translate_messages,
)
from ydbdoc_review.translation.repair import repair_segment_translation
from ydbdoc_review.translation.schemas import TranslateBatchResponse
from ydbdoc_review.validation.cli_tokens import cli_tokens_preserved
from ydbdoc_review.validation.heuristics import (
    check_cyrillic_in_en,
    count_fence_markers,
)
from ydbdoc_review.validation.markers import (
    is_placeholder_only_text,
    placeholders_match,
)
from ydbdoc_review.validation.placeholder_repair import repair_translation_placeholders
from ydbdoc_review.validation.placeholder_roles import placeholder_roles_valid

logger = logging.getLogger(__name__)

_MAX_BATCH_ATTEMPTS = 3
_PLACEHOLDER_MISMATCH_HINT = "placeholder mismatch"
_MONOLITH_BUDGET_MESSAGE = "segment exceeds safe translate output budget"


def _is_length_resplit_failure(
    exc: LLMParseError,
    *,
    content: str,
) -> bool:
    """True when empty/truncated JSON likely came from output length limits."""
    msg = str(exc)
    if "Segment id mismatch" in msg:
        return False
    if "JSON schema validation failed" in msg:
        return False
    if not content.strip():
        return True
    if "finish_reason=length" in msg:
        return True
    if "Invalid JSON in LLM response" in msg and not content.strip():
        return True
    return False


def _is_timeout_exhausted(exc: BaseException) -> bool:
    """True when the model chain entry failed on transport/timeout budget."""
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg or "connection" in msg


_MODEL_REFUSAL_MARKERS = (
    "я не могу обсуждать",
    "не могу помочь с этой темой",  # noqa: RUF001
    "i can't discuss",
    "i cannot discuss",
    "unable to discuss this",
    "content policy",
)


def _is_model_refusal(text: str) -> bool:
    normalized = (text or "").strip().casefold()
    return bool(normalized) and any(
        marker in normalized for marker in _MODEL_REFUSAL_MARKERS
    )


def _fallback_cause(exc: BaseException, *, content: str = "") -> str | None:
    """Return a stable report reason when the next model must be tried."""
    if isinstance(exc, LLMRetryExhaustedError):
        if is_model_unavailable(exc) or is_eliza_model_unavailable(exc):
            return "model unavailable after retries"
        if is_rate_limit_error(exc) or _is_timeout_exhausted(exc):
            return "model unavailable after retries"
        return None
    if isinstance(exc, LLMParseError):
        if _is_model_refusal(content):
            return "model refusal"
        if not content.strip():
            return "empty model response"
        if "Segment id mismatch" in str(exc):
            return "incomplete model response"
        return "unreadable model response"
    if isinstance(exc, TranslationValidationError) and _PLACEHOLDER_MISMATCH_HINT in str(exc):
        return "placeholder validation failure"
    return None


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


def validate_segment_translation(
    source: Segment,
    translated_text: str,
    *,
    target_lang: str = "en",
) -> None:
    """Structural checks for one segment translation."""
    cyrillic_issues = check_cyrillic_in_en(
        translated_text, target_lang=target_lang
    )
    if cyrillic_issues:
        raise TranslationValidationError(
            f"Cyrillic remains in EN translation for {source.id!r}: "
            f"{cyrillic_issues[0]}",
            segment_id=source.id,
        )
    if not placeholders_match(source.text, translated_text):
        raise TranslationValidationError(
            f"placeholder mismatch for {source.id!r}: "
            f"expected placeholders from source in same order",
            segment_id=source.id,
        )
    if is_placeholder_only_text(source.text) and not is_placeholder_only_text(
        translated_text
    ):
        raise TranslationValidationError(
            f"placeholder-only segment {source.id!r} must stay marker-only "
            f"(no prose around ⟦…⟧); got elaboration",
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
    batch: Batch,
    translations: dict[str, str],
    *,
    target_lang: str = "en",
) -> None:
    """Validate all segments in a batch."""
    for seg in batch.segments:
        if seg.id not in translations:
            raise TranslationValidationError(
                f"missing translation for {seg.id!r}",
                segment_id=seg.id,
            )
        validate_segment_translation(
            seg, translations[seg.id], target_lang=target_lang
        )


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
    return " › ".join(seg.path) if seg.path else "(начало документа)"  # noqa: RUF001


def _translate_batch_with_model(
    client: YandexLLMClient,
    batch: Batch,
    glossary: Glossary,
    *,
    file_path: str,
    source_lang: str,
    target_lang: str,
    prompt_version: str,
    model: str,
    max_attempts: int,
    last_attempt: dict[str, str] | None = None,
    allow_resplit: bool = True,
    last_raw_content: list[str] | None = None,
) -> dict[str, str]:
    last_exc: LLMParseError | TranslationValidationError | None = None
    last_content = ""
    for attempt in range(1, max_attempts + 1):
        try:
            messages = build_translate_messages(
                batch,
                glossary,
                file_path=file_path,
                source_lang=source_lang,
                target_lang=target_lang,
                version=prompt_version,
            )
            result = client.chat(messages, model=model, role="translate")
            last_content = result.content or ""
            if last_raw_content is not None:
                last_raw_content[:] = [last_content]
            expected = {seg.id for seg in batch.segments}
            translations = parse_translate_response(
                result.content, expected_ids=expected
            )
            _apply_placeholder_realignment(batch, translations)
            if last_attempt is not None:
                last_attempt.clear()
                last_attempt.update(translations)
            validate_batch_translations(
                batch, translations, target_lang=target_lang
            )
            return translations
        except (LLMParseError, TranslationValidationError) as exc:
            last_exc = exc
            if (
                allow_resplit
                and isinstance(exc, LLMParseError)
                and len(batch.segments) > 1
                and _is_length_resplit_failure(exc, content=last_content)
            ):
                mid = max(1, len(batch.segments) // 2)
                split_batches = [
                    Batch(index=batch.index, segments=batch.segments[:mid]),
                    Batch(index=batch.index, segments=batch.segments[mid:]),
                ]
                logger.warning(
                    "Translate batch %s length failure; retrying as %s + %s segment halves",
                    batch.index,
                    mid,
                    len(batch.segments) - mid,
                )
                merged: dict[str, str] = {}
                for half in split_batches:
                    merged.update(
                        _translate_batch_with_model(
                            client,
                            half,
                            glossary,
                            file_path=file_path,
                            source_lang=source_lang,
                            target_lang=target_lang,
                            prompt_version=prompt_version,
                            model=model,
                            max_attempts=max_attempts,
                            last_attempt=last_attempt,
                            allow_resplit=False,
                            last_raw_content=last_raw_content,
                        )
                    )
                return merged
            if attempt < max_attempts:
                logger.warning(
                    "Translate batch %s attempt %s/%s failed: %s",
                    batch.index,
                    attempt,
                    max_attempts,
                    exc,
                )
    assert last_exc is not None
    raise last_exc


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
    allow_resplit: bool = True,
    last_raw_content: list[str] | None = None,
    fallback_reasons: list[str] | None = None,
) -> dict[str, str]:
    model_chain = client.model_chain_for_role("translate")
    last_validation_exc: LLMParseError | TranslationValidationError | None = None
    last_infra_exc: LLMRetryExhaustedError | None = None
    raw_holder = last_raw_content if last_raw_content is not None else []

    for model_idx, model in enumerate(model_chain):
        max_attempts = _MAX_BATCH_ATTEMPTS if model_idx == 0 else 1
        try:
            return _translate_batch_with_model(
                client,
                batch,
                glossary,
                file_path=file_path,
                source_lang=source_lang,
                target_lang=target_lang,
                prompt_version=prompt_version,
                model=model,
                max_attempts=max_attempts,
                last_attempt=last_attempt,
                allow_resplit=allow_resplit,
                last_raw_content=raw_holder,
            )
        except LLMRetryExhaustedError as exc:
            last_infra_exc = exc
            # Advance on rate-limit or transport timeout — same slug already
            # burned its retry budget (§6.230 / #40385 monitoring_config).
            if model_idx + 1 < len(model_chain) and (
                is_rate_limit_error(exc)
                or _is_timeout_exhausted(exc)
                or is_model_unavailable(exc)
                or is_eliza_model_unavailable(exc)
            ):
                logger.warning(
                    "Translate batch %s model %s exhausted, trying fallback %s: %s",
                    batch.index,
                    model,
                    model_chain[model_idx + 1],
                    exc,
                )
                if fallback_reasons is not None:
                    fallback_reasons.append(
                        f"{model} -> {model_chain[model_idx + 1]}: "
                        f"{_fallback_cause(exc) or 'model failure'}"
                    )
                continue
            raise
        except (LLMParseError, TranslationValidationError) as exc:
            last_validation_exc = exc
            cause = _fallback_cause(
                exc, content=raw_holder[0] if raw_holder else ""
            )
            if (
                model_idx + 1 < len(model_chain)
                and cause is not None
            ):
                logger.warning(
                    "Translate batch %s retry with fallback model %s: %s",
                    batch.index,
                    model_chain[model_idx + 1],
                    exc,
                )
                if fallback_reasons is not None:
                    fallback_reasons.append(
                        f"{model} -> {model_chain[model_idx + 1]}: {cause}"
                    )
                continue
            raise

    if last_validation_exc is not None:
        raise last_validation_exc
    if last_infra_exc is not None:
        raise last_infra_exc
    raise RuntimeError("translate batch finished without result or error")


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
    fallback_reasons: list[str] | None = None,
    allow_resplit: bool = True,
) -> dict[str, str]:
    """Translate one batch; fall back to per-segment calls on batch failure."""
    last_attempt: dict[str, str] = {}
    last_raw_content: list[str] = []
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
            allow_resplit=allow_resplit,
            last_raw_content=last_raw_content,
            fallback_reasons=fallback_reasons,
        )
    except (LLMParseError, TranslationValidationError) as exc:
        if len(batch.segments) == 1:
            seg = batch.segments[0]
            raw_content = last_raw_content[0] if last_raw_content else ""
            if isinstance(exc, LLMParseError) and _is_length_resplit_failure(
                exc, content=raw_content
            ):
                _record_manual_action(
                    manual_actions,
                    seg,
                    message=_MONOLITH_BUDGET_MESSAGE,
                )
                raise TranslationValidationError(
                    _MONOLITH_BUDGET_MESSAGE,
                    segment_id=seg.id,
                ) from exc
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
                    fallback_reasons=fallback_reasons,
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
    max_output_chars: int = 6000,
    expansion_ratio: float = 1.35,
    json_overhead: int = 512,
    segment_max_chars: int = 1200,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    cache: dict[str, str] | None = None,
    max_parallel_batches: int = 3,
    manual_actions: list[ManualAction] | None = None,
    fallback_reasons: list[str] | None = None,
    on_validated_segment: Callable[[Segment, str], None] | None = None,
    load_validated_segment: Callable[[Segment], str | None] | None = None,
) -> dict[str, str]:
    """Translate all segments (chunked batches, optional cache, parallel I/O)."""
    if not segments:
        return {}

    translations: dict[str, str] = {}
    pending: list[Segment] = []

    for seg in segments:
        # Config-table keys etc.: copy markers as-is — never send to LLM (§6.172).
        if is_placeholder_only_text(seg.text):
            translations[seg.id] = seg.text.strip()
            continue
        if load_validated_segment is not None:
            try:
                resumed = load_validated_segment(seg)
            except Exception as exc:
                logger.warning(
                    "Translation resume load failed for %s: %s",
                    seg.id,
                    exc,
                )
                resumed = None
            if resumed is not None:
                try:
                    validate_segment_translation(
                        seg,
                        resumed,
                        target_lang=target_lang,
                    )
                except TranslationValidationError as exc:
                    logger.warning(
                        "Translation resume for %s rejected by current validation: %s",
                        seg.id,
                        exc,
                    )
                else:
                    translations[seg.id] = resumed
                    if on_validated_segment is not None:
                        on_validated_segment(seg, resumed)
                    continue
        elif cache is not None:
            key = _cache_key(seg, target_lang=target_lang)
            cached = cache.get(key)
            if cached is not None:
                validate_segment_translation(
                    seg, cached, target_lang=target_lang
                )
                translations[seg.id] = cached
                if on_validated_segment is not None:
                    on_validated_segment(seg, cached)
                continue
        pending.append(seg)

    if not pending:
        return translations

    batches = chunk_segments(
        pending,
        max_chars=max_chars,
        max_output_chars=max_output_chars,
        expansion_ratio=expansion_ratio,
        json_overhead=json_overhead,
        segment_max_chars=segment_max_chars,
    )
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
            fallback_reasons=fallback_reasons,
        )

    def _accept_completed_batch(
        batch: Batch,
        batch_translations: dict[str, str],
    ) -> None:
        """Persist/cache only segments that pass the existing validator."""
        for seg in batch.segments:
            text = batch_translations[seg.id]
            try:
                validate_segment_translation(seg, text, target_lang=target_lang)
            except TranslationValidationError:
                # Recovery may deliberately return the source together with a
                # blocking manual action. It remains diagnostic output only.
                continue
            if cache is not None:
                cache[_cache_key(seg, target_lang=target_lang)] = text
            if on_validated_segment is not None:
                on_validated_segment(seg, text)

    if max_parallel_batches == 1 or len(batches) == 1:
        batch_results = []
        for b in batches:
            check_shutdown()
            batch_result = _run_batch(b)
            _accept_completed_batch(b, batch_result)
            batch_results.append(batch_result)
    else:
        results_by_index: dict[int, dict[str, str]] = {}
        with ThreadPoolExecutor(max_workers=max_parallel_batches) as pool:
            futures = {pool.submit(_run_batch, b): i for i, b in enumerate(batches)}
            try:
                for fut in as_completed(futures):
                    batch_index = futures[fut]
                    batch_result = fut.result()
                    _accept_completed_batch(batches[batch_index], batch_result)
                    results_by_index[batch_index] = batch_result
            except KeyboardInterrupt:
                for fut in futures:
                    fut.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                raise
        batch_results = [results_by_index[i] for i in range(len(batches))]

    for batch, batch_trans in zip(batches, batch_results, strict=True):
        for seg in batch.segments:
            text = batch_trans[seg.id]
            translations[seg.id] = text

    return translations
