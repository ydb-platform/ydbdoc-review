"""Focused LLM repair pass after translate validation failures."""

from __future__ import annotations

import logging

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_repair_messages,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2


def repair_segment_translation(
    client: YandexLLMClient,
    segment: Segment,
    glossary: Glossary,
    *,
    validation_error: str,
    failed_attempt: str | None = None,
    file_path: str = "",
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str | None:
    """Try to fix one segment after translate validation failed; return text or None."""
    from ydbdoc_review.translation.translator import (
        parse_translate_response,
        validate_segment_translation,
    )

    model_chain = client.model_chain_for_role("translate")
    last_exc: Exception | None = None
    attempt_text = failed_attempt if failed_attempt is not None else ""

    for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
        for model in model_chain:
            try:
                messages = build_repair_messages(
                    segment,
                    glossary,
                    validation_error=validation_error,
                    failed_attempt=attempt_text,
                    file_path=file_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    version=prompt_version,
                )
                result = client.chat(messages, model=model)
                translations = parse_translate_response(
                    result.content, expected_ids={segment.id}
                )
                text = translations[segment.id]
                validate_segment_translation(segment, text)
                logger.info(
                    "Repair pass succeeded for %s (model=%s, attempt=%s)",
                    segment.id,
                    model,
                    attempt,
                )
                return text
            except (LLMParseError, TranslationValidationError) as exc:
                last_exc = exc
                logger.warning(
                    "Repair pass failed for %s (model=%s, attempt=%s): %s",
                    segment.id,
                    model,
                    attempt,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Repair pass error for %s (model=%s): %s",
                    segment.id,
                    model,
                    exc,
                )
    if last_exc is not None:
        logger.warning(
            "Repair pass gave up for %s after %s attempts: %s",
            segment.id,
            _MAX_REPAIR_ATTEMPTS,
            last_exc,
        )
    return None
