"""Per-file critic: review, apply fixes, verify pass."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ydbdoc_review.llm.client import YandexLLMClient
from ydbdoc_review.llm.errors import LLMParseError
from ydbdoc_review.llm.structured import parse_json_content
from ydbdoc_review.segmentation.chunker import Batch, chunk_segments
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.errors import TranslationValidationError
from ydbdoc_review.translation.glossary import Glossary
from ydbdoc_review.translation.prompts import (
    DEFAULT_PROMPT_VERSION,
    build_critic_batch_messages,
    build_verify_batch_messages,
)
from ydbdoc_review.translation.schemas import CriticIssueOut, CriticResponse, CriticVerdict
from ydbdoc_review.translation.translator import validate_segment_translation

logger = logging.getLogger(__name__)

_MAX_CRITIC_ATTEMPTS = 3
_VERDICT_RANK: dict[CriticVerdict, int] = {"ok": 0, "warnings": 1, "blocked": 2}

# LLMs sometimes invent verdict strings; map to the schema literals before validate.
_VERDICT_ALIASES: dict[str, CriticVerdict] = {
    "ok": "ok",
    "pass": "ok",
    "success": "ok",
    "clean": "ok",
    "warnings": "warnings",
    "warning": "warnings",
    "needs_fix": "warnings",
    "need_fix": "warnings",
    "issues": "warnings",
    "issues_found": "warnings",
    "issue_found": "warnings",
    "fail": "warnings",
    "failed": "warnings",
    "error": "warnings",
    "blocked": "blocked",
    "block": "blocked",
    "reject": "blocked",
    "rejected": "blocked",
}


def normalize_critic_verdict_value(raw: str) -> CriticVerdict | None:
    """Map a free-form LLM verdict string to ``ok`` | ``warnings`` | ``blocked``."""
    key = raw.strip().lower().replace("-", "_").replace(" ", "_")
    return _VERDICT_ALIASES.get(key)


def merge_verdicts(*verdicts: CriticVerdict) -> CriticVerdict:
    """Pick the strictest verdict across critic batches."""
    return max(verdicts, key=lambda v: _VERDICT_RANK[v])


def merge_critic_responses(responses: list[CriticResponse]) -> CriticResponse:
    """Combine batch-level critic/verify responses into one file-level result."""
    if not responses:
        return CriticResponse(verdict="ok", issues=[])
    issues: list[CriticIssueOut] = []
    verdict: CriticVerdict = "ok"
    for response in responses:
        issues.extend(response.issues)
        verdict = merge_verdicts(verdict, response.verdict)
    if any(issue.severity == "blocked" for issue in issues):
        verdict = "blocked"
    elif issues and verdict == "ok":
        verdict = "warnings"
    return CriticResponse(verdict=verdict, issues=issues)


def _fallback_critic_response(*, reason: str) -> CriticResponse:
    """Safe default when critic JSON cannot be parsed after retries."""
    logger.error("Critic skipped (%s); treating as warnings with no issues", reason)
    return CriticResponse(verdict="warnings", issues=[])


def _fetch_critic_response(
    client: YandexLLMClient,
    messages: list,
    *,
    pass_label: str,
    max_tokens: int | None = None,
) -> CriticResponse:
    """Call critic model with parse retries; fallback instead of raising."""
    last_exc: LLMParseError | None = None
    for attempt in range(1, _MAX_CRITIC_ATTEMPTS + 1):
        try:
            result = client.chat(messages, role="critic", max_tokens=max_tokens)
            content = (result.content or "").strip()
            if not content:
                raise LLMParseError("Empty LLM response")
            return parse_critic_response(content)
        except LLMParseError as exc:
            last_exc = exc
            logger.warning(
                "%s parse attempt %s/%s failed: %s",
                pass_label,
                attempt,
                _MAX_CRITIC_ATTEMPTS,
                exc,
            )
    return _fallback_critic_response(reason=str(last_exc or "unknown parse error"))


def parse_critic_response(raw: str) -> CriticResponse:
    """Parse and validate critic / verify JSON (with verdict alias normalization)."""
    data = parse_json_content(raw)
    if isinstance(data, dict):
        verdict_raw = data.get("verdict")
        if isinstance(verdict_raw, str):
            normalized = normalize_critic_verdict_value(verdict_raw)
            if normalized is not None:
                data = {**data, "verdict": normalized}
    try:
        return CriticResponse.model_validate(data)
    except Exception as exc:
        raise LLMParseError(f"JSON schema validation failed: {exc}") from exc


def _segments_by_id(segments: list[Segment]) -> dict[str, Segment]:
    return {seg.id: seg for seg in segments}


def _critic_batches(
    segments: list[Segment],
    *,
    max_chars: int,
) -> list[Batch]:
    return chunk_segments(segments, max_chars=max_chars)


def _batch_segment_ids(batch: Batch) -> set[str]:
    return {seg.id for seg in batch.segments}


def _prior_issues_for_batch(
    prior_issues: list[CriticIssueOut],
    batch: Batch,
    *,
    include_global: bool,
) -> list[dict[str, object]]:
    """Filter prior issues to those relevant to a verify batch."""
    ids = _batch_segment_ids(batch)
    out: list[dict[str, object]] = []
    for issue in prior_issues:
        if issue.segment_id is None:
            if include_global:
                out.append(issue.model_dump())
            continue
        if issue.segment_id in ids:
            out.append(issue.model_dump())
    return out


def _run_critic_batches(
    client: YandexLLMClient,
    *,
    batches: list[Batch],
    translations: dict[str, str],
    glossary: Glossary,
    file_path: str,
    source_lang: str,
    target_lang: str,
    prompt_version: str,
    max_tokens: int | None,
    pass_label: str,
    prior_issues: list[CriticIssueOut] | None = None,
) -> CriticResponse:
    batch_count = len(batches)
    responses: list[CriticResponse] = []
    for batch in batches:
        if prior_issues is None:
            messages = build_critic_batch_messages(
                batch,
                translations,
                glossary,
                file_path=file_path,
                batch_count=batch_count,
                source_lang=source_lang,
                target_lang=target_lang,
                version=prompt_version,
            )
            label = f"{pass_label} batch {batch.index + 1}/{batch_count}"
        else:
            messages = build_verify_batch_messages(
                batch,
                translations,
                _prior_issues_for_batch(
                    prior_issues,
                    batch,
                    include_global=batch.index == 0,
                ),
                glossary,
                file_path=file_path,
                batch_count=batch_count,
                source_lang=source_lang,
                target_lang=target_lang,
                version=prompt_version,
            )
            label = f"{pass_label} batch {batch.index + 1}/{batch_count}"
        responses.append(
            _fetch_critic_response(
                client,
                messages,
                pass_label=label,
                max_tokens=max_tokens,
            )
        )
    return merge_critic_responses(responses)


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
    segments: list[Segment],
    translations: dict[str, str],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    max_chars: int = 4000,
    max_tokens: int | None = None,
    source_text: str = "",
    translated_text: str = "",
) -> CriticResponse:
    """First-pass batched critic review over segment pairs."""
    del source_text, translated_text  # kept for call-site compatibility
    if not segments:
        return CriticResponse(verdict="ok", issues=[])
    batches = _critic_batches(segments, max_chars=max_chars)
    logger.info(
        "Critic %s: %s segments in %s batch(es), max_chars=%s",
        file_path or "<file>",
        len(segments),
        len(batches),
        max_chars,
    )
    return _run_critic_batches(
        client,
        batches=batches,
        translations=translations,
        glossary=glossary,
        file_path=file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        prompt_version=prompt_version,
        max_tokens=max_tokens,
        pass_label="Critic",
    )


def run_verify(
    client: YandexLLMClient,
    *,
    segments: list[Segment],
    translations: dict[str, str],
    prior_issues: list[CriticIssueOut],
    glossary: Glossary,
    file_path: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    max_chars: int = 4000,
    max_tokens: int | None = None,
    source_text: str = "",
    translated_text: str = "",
) -> CriticResponse:
    """Second-pass batched verify after fixes were applied."""
    del source_text, translated_text
    if not segments:
        return CriticResponse(verdict="ok", issues=[])
    batches = _critic_batches(segments, max_chars=max_chars)
    return _run_critic_batches(
        client,
        batches=batches,
        translations=translations,
        glossary=glossary,
        file_path=file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        prompt_version=prompt_version,
        max_tokens=max_tokens,
        pass_label="Verify",
        prior_issues=prior_issues,
    )


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
    max_chars: int = 4000,
    max_tokens: int | None = None,
    run_second_pass: bool = True,
    translated_text_after_fixes: str | None = None,
) -> CriticReviewResult:
    """Run critic, apply safe fixes, optionally re-verify unresolved issues."""
    del source_text, translated_text_after_fixes
    initial = run_critic(
        client,
        segments=segments,
        translations=translations,
        glossary=glossary,
        file_path=file_path,
        source_lang=source_lang,
        target_lang=target_lang,
        prompt_version=prompt_version,
        max_chars=max_chars,
        max_tokens=max_tokens,
        translated_text=translated_text,
    )
    fixed, applied, skipped = apply_critic_fixes(translations, segments, initial.issues)

    unresolved: CriticResponse | None = None
    if run_second_pass and initial.issues:
        unresolved = run_verify(
            client,
            segments=segments,
            translations=fixed,
            prior_issues=initial.issues,
            glossary=glossary,
            file_path=file_path,
            source_lang=source_lang,
            target_lang=target_lang,
            prompt_version=prompt_version,
            max_chars=max_chars,
            max_tokens=max_tokens,
            translated_text=translated_text,
        )

    return CriticReviewResult(
        initial=initial,
        translations=fixed,
        applied=applied,
        skipped=skipped,
        unresolved=unresolved,
    )
