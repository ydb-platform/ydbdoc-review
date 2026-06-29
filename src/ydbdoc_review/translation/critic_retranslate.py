"""Re-translate segments after unresolved critic issues."""

from __future__ import annotations

import logging
from collections import defaultdict

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_critic_feedback_repair_messages,
)
from ydbdoc_review.translation.schemas import CriticIssueOut

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2


def retranslate_segment_with_critic_feedback(
    client: YandexLLMClient,
    segment: Segment,
    glossary: Glossary,
    *,
    current_translation: str,
    critic_issues: list[CriticIssueOut],
    file_path: str = "",
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str | None:
    """LLM re-translate for one segment guided by critic issues; return text or None."""
    from ydbdoc_review.translation.translator import (
        parse_translate_response,
        validate_segment_translation,
    )

    if not critic_issues:
        return None

    model_chain = client.model_chain_for_role("translate")
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        for model in model_chain:
            try:
                messages = build_critic_feedback_repair_messages(
                    segment,
                    glossary,
                    current_translation=current_translation,
                    critic_issues=critic_issues,
                    file_path=file_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    version=prompt_version,
                )
                result = client.chat(messages, model=model, role="translate")
                translations = parse_translate_response(
                    result.content, expected_ids={segment.id}
                )
                text = translations[segment.id]
                validate_segment_translation(segment, text)
                logger.info(
                    "Critic-feedback retranslate succeeded for %s (model=%s, attempt=%s)",
                    segment.id,
                    model,
                    attempt,
                )
                return text
            except (LLMParseError, TranslationValidationError) as exc:
                last_exc = exc
                logger.warning(
                    "Critic-feedback retranslate failed for %s (model=%s, attempt=%s): %s",
                    segment.id,
                    model,
                    attempt,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "Critic-feedback retranslate error for %s (model=%s): %s",
                    segment.id,
                    model,
                    exc,
                )
    if last_exc is not None:
        logger.warning(
            "Critic-feedback retranslate gave up for %s: %s",
            segment.id,
            last_exc,
        )
    return None


def retranslate_segments_with_critic_feedback(
    segments: list[Segment],
    segment_ids: set[str],
    translations: dict[str, str],
    issues_by_segment: dict[str, list[CriticIssueOut]],
    client: YandexLLMClient,
    glossary: Glossary,
    *,
    file_path: str,
    source_lang: str,
    target_lang: str,
    prompt_version: str,
    cache: dict[str, str] | None = None,
) -> dict[str, str]:
    """Re-translate selected segments; update cache entries on success."""
    from ydbdoc_review.translation.translator import _cache_key

    by_id = {seg.id: seg for seg in segments}
    updated = dict(translations)

    for seg_id in sorted(segment_ids):
        seg = by_id.get(seg_id)
        if seg is None:
            continue
        issues = issues_by_segment.get(seg_id, [])
        if not issues:
            continue
        current = updated.get(seg_id, seg.text)
        repaired = retranslate_segment_with_critic_feedback(
            client,
            seg,
            glossary,
            current_translation=current,
            critic_issues=issues,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
        )
        if repaired is None:
            continue
        updated[seg_id] = repaired
        if cache is not None:
            cache[_cache_key(seg, target_lang=target_lang)] = repaired

    return updated


def issues_by_segment_id(
    issues: list[CriticIssueOut],
) -> dict[str, list[CriticIssueOut]]:
    """Group critic issues with a ``segment_id``."""
    grouped: dict[str, list[CriticIssueOut]] = defaultdict(list)
    for issue in issues:
        if issue.segment_id:
            grouped[issue.segment_id].append(issue)
    return dict(grouped)
