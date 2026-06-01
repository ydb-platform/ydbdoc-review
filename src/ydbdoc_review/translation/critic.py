"""Per-file critic: review, apply fixes, verify pass."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.structured import parse_json_model
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_critic_messages,
    build_verify_messages,
)
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse
from ydbdoc_review.translation.translator import validate_segment_translation

logger = logging.getLogger(__name__)


def parse_critic_response(raw: str) -> CriticResponse:
    """Parse and validate critic / verify JSON."""
    return parse_json_model(raw, CriticResponse)


def _segments_by_id(segments: list[Segment]) -> dict[str, Segment]:
    return {seg.id: seg for seg in segments}


def apply_critic_fixes(
    translations: dict[str, str],
    segments: list[Segment],
    issues: list[CriticIssueOut],
) -> tuple[dict[str, str], list[CriticIssueOut], list[CriticIssueOut]]:
    """Apply ``suggested_text`` fixes that pass structural validation.

    Returns ``(updated_translations, applied_issues, skipped_issues)``.
    """
    by_id = _segments_by_id(segments)
    updated = dict(translations)
    applied: list[CriticIssueOut] = []
    skipped: list[CriticIssueOut] = []

    for issue in issues:
        if issue.suggested_text is None:
            skipped.append(issue)
            continue
        if issue.segment_id is None:
            logger.warning("Critic issue without segment_id cannot be applied: %s", issue.comment)
            skipped.append(issue)
            continue
        seg = by_id.get(issue.segment_id)
        if seg is None:
            logger.warning("Unknown segment_id %r in critic issue", issue.segment_id)
            skipped.append(issue)
            continue
        try:
            validate_segment_translation(seg, issue.suggested_text)
        except TranslationValidationError as exc:
            logger.warning("Skipping critic fix for %s: %s", issue.segment_id, exc)
            skipped.append(issue)
            continue
        updated[issue.segment_id] = issue.suggested_text
        applied.append(issue)

    return updated, applied, skipped


def run_critic(
    client: YandexLLMClient,
    *,
    source_text: str,
    translated_text: str,
    segments: list[Segment],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> CriticResponse:
    """First-pass whole-file critic review."""
    messages = build_critic_messages(
        source_text=source_text,
        translated_text=translated_text,
        segments=segments,
        glossary=glossary,
        file_path=file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        version=prompt_version,
    )
    result = client.chat(messages, role="critic")
    return parse_critic_response(result.content)


def run_verify(
    client: YandexLLMClient,
    *,
    source_text: str,
    translated_text: str,
    segments: list[Segment],
    prior_issues: list[CriticIssueOut],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> CriticResponse:
    """Second-pass verify after fixes were applied."""
    prior_payload = [issue.model_dump() for issue in prior_issues]
    messages = build_verify_messages(
        source_text=source_text,
        translated_text=translated_text,
        segments=segments,
        prior_issues=prior_payload,
        glossary=glossary,
        file_path=file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        version=prompt_version,
    )
    result = client.chat(messages, role="critic")
    return parse_critic_response(result.content)


@dataclass
class CriticReviewResult:
    """Outcome of critic → apply → verify."""

    initial: CriticResponse
    translations: dict[str, str]
    applied: list[CriticIssueOut] = field(default_factory=list)
    skipped: list[CriticIssueOut] = field(default_factory=list)
    unresolved: CriticResponse | None = None


def review_with_critic(
    client: YandexLLMClient,
    *,
    source_text: str,
    translated_text: str,
    segments: list[Segment],
    translations: dict[str, str],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    run_second_pass: bool = True,
    translated_text_after_fixes: str | None = None,
) -> CriticReviewResult:
    """Run critic, apply safe fixes, optionally re-verify unresolved issues.

    Pass ``translated_text_after_fixes`` (re-rendered markdown after reinsert) for
    an accurate verify pass; otherwise verify uses ``translated_text``.
    """
    initial = run_critic(
        client,
        source_text=source_text,
        translated_text=translated_text,
        segments=segments,
        glossary=glossary,
        file_path=file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        prompt_version=prompt_version,
    )
    fixed, applied, skipped = apply_critic_fixes(translations, segments, initial.issues)

    verify_text = translated_text_after_fixes or translated_text
    unresolved: CriticResponse | None = None
    if run_second_pass and initial.issues:
        unresolved = run_verify(
            client,
            source_text=source_text,
            translated_text=verify_text,
            segments=segments,
            prior_issues=initial.issues,
            glossary=glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
        )

    return CriticReviewResult(
        initial=initial,
        translations=fixed,
        applied=applied,
        skipped=skipped,
        unresolved=unresolved,
    )
