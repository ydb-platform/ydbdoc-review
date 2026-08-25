"""Per-file critic: review, apply fixes, verify pass."""

from __future__ import annotations

import logging
import re
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
from ydbdoc_review.validation.markers import extract_placeholders

logger = logging.getLogger(__name__)

_MISSING_CONTENT_ISSUE = re.compile(
    r"missing|omit|omits|drops?\s+the|не\s+перевед|пропущ",
    re.IGNORECASE,
)
_TRUNCATED_SUGGESTION = re.compile(r"(?:…|\.\.\.)$")

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
    """Fail closed when critic JSON cannot be parsed after retries."""
    logger.error("Critic failed (%s); blocking verification", reason)
    return CriticResponse(
        verdict="blocked",
        issues=[
            CriticIssueOut(
                severity="blocked",
                category="critic_execution_failed",
                comment=f"Critic execution failed: {reason}",
            )
        ],
    )


def _fetch_critic_response(
    client: YandexLLMClient,
    messages: list,
    *,
    pass_label: str,
    max_tokens: int | None = None,
) -> CriticResponse:
    """Call critic with JSON repair and a model fallback before failing closed."""
    last_exc: LLMParseError | None = None
    original_messages = list(messages)
    retry_messages = original_messages
    model_chain = client.model_chain_for_role("critic")
    for attempt in range(1, _MAX_CRITIC_ATTEMPTS + 1):
        content = ""
        # First retry asks the primary model to repair its malformed response.
        # The final retry uses the configured fallback with the original prompt,
        # avoiding a deterministic loop on the same model and payload.
        model = model_chain[0]
        if attempt == _MAX_CRITIC_ATTEMPTS and len(model_chain) > 1:
            model = model_chain[1]
            retry_messages = original_messages
        try:
            result = client.chat(
                retry_messages,
                model=model,
                max_tokens=max_tokens,
            )
            content = (result.content or "").strip()
            if not content:
                raise LLMParseError("Empty LLM response")
            return parse_critic_response(content)
        except LLMParseError as exc:
            last_exc = exc
            preview = content[:200]
            logger.warning(
                "%s parse attempt %s/%s failed: %s; model=%s "
                "response_chars=%s response_preview=%r",
                pass_label,
                attempt,
                _MAX_CRITIC_ATTEMPTS,
                exc,
                model,
                len(content),
                preview,
            )
            if attempt == 1 and content:
                retry_messages = [
                    *original_messages,
                    {
                        "role": "assistant",
                        "content": content,
                    },
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON. Return the same "
                            "critic result as one valid JSON object matching the requested "
                            "schema. Return JSON only, without Markdown fences or prose."
                        ),
                    },
                ]
    return _fallback_critic_response(reason=str(last_exc or "unknown parse error"))


def parse_critic_response(raw: str) -> CriticResponse:
    """Parse and validate critic / verify JSON (with verdict alias normalization)."""
    data = parse_json_content(raw)
    if isinstance(data, dict):
        verdict_raw = data.get("verdict")
        normalized_verdict: CriticVerdict | None = None
        if isinstance(verdict_raw, str):
            normalized_verdict = normalize_critic_verdict_value(verdict_raw)
            if normalized_verdict is not None:
                data = {**data, "verdict": normalized_verdict}
        issues = data.get("issues")
        if isinstance(issues, list):
            default_severity = (
                "blocked" if normalized_verdict == "blocked" else "warning"
            )
            normalized_issues: list[object] = []
            for issue in issues:
                if not isinstance(issue, dict):
                    normalized_issues.append(issue)
                    continue
                # Some critic models emit a bare rewrite with no diagnosis.
                # It is not an actionable issue and often repeats the current
                # translation verbatim, so treating it as blocked creates a
                # false failure and an endless fixup loop.
                if (
                    issue.get("suggested_text")
                    and not issue.get("severity")
                    and not issue.get("category")
                    and not issue.get("comment")
                    and not issue.get("description")
                ):
                    continue
                normalized_issue = dict(issue)
                normalized_issue.setdefault("severity", default_severity)
                normalized_issue.setdefault("category", "translation_quality")
                if not normalized_issue.get("comment"):
                    normalized_issue["comment"] = (
                        normalized_issue.get("description")
                        or normalized_issue.get("suggested_text")
                        or "Critic reported a translation issue."
                    )
                normalized_issues.append(normalized_issue)
            data = {**data, "issues": normalized_issues}
            if issues and not normalized_issues:
                data["verdict"] = "ok"
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


def _critic_fix_would_regress(
    current: str,
    suggested: str,
    issue: CriticIssueOut,
) -> str | None:
    """Return a skip reason when auto-apply would likely remove good translation."""
    if not current.strip() or not suggested.strip():
        return None
    haystack = f"{issue.category} {issue.comment}"
    if _MISSING_CONTENT_ISSUE.search(haystack) and len(suggested) < len(current):
        return "missing-content fix is shorter than current translation"
    if _TRUNCATED_SUGGESTION.search(suggested.rstrip()):
        return "truncated suggested_text"
    return None


_CODE_ONLY_LINK = re.compile(r"\[(⟦C\d+⟧)\]\((⟦U\d+⟧)\)")


def _drop_impossible_code_link_issues(
    response: CriticResponse,
    translations: dict[str, str],
    segments: list[Segment],
) -> CriticResponse:
    """Ignore link advice that contradicts a source-preserved code-only label."""
    kept: list[CriticIssueOut] = []
    source_by_id = _segments_by_id(segments)
    for issue in response.issues:
        current = translations.get(issue.segment_id or "", "")
        source = source_by_id.get(issue.segment_id or "")
        suggested = issue.suggested_text or ""
        link_matches = list(
            _CODE_ONLY_LINK.finditer(source.text if source is not None else "")
        )
        haystack = f"{issue.category} {issue.comment}"
        impossible = re.search(r"link|anchor", haystack, re.IGNORECASE) and any(
            re.search(
                rf"\[[^\]]*{re.escape(match.group(1))}[^\]]*\]"
                rf"\({re.escape(match.group(2))}\)",
                current,
            )
            and match.group(2) in suggested
            and match.group(1) not in suggested
            for match in link_matches
        )
        if impossible:
            logger.warning(
                "Ignoring critic issue for %s: suggestion removes a source-preserved "
                "code-only link label",
                issue.segment_id,
            )
            continue
        kept.append(issue)
    if len(kept) == len(response.issues):
        return response
    return CriticResponse(verdict="ok" if not kept else response.verdict, issues=kept)


def apply_critic_fixes(
    translations: dict[str, str],
    segments: list[Segment],
    issues: list[CriticIssueOut],
    *,
    strict_placeholder_order: bool = False,
) -> tuple[dict[str, str], list[CriticIssueOut], list[CriticIssueOut]]:
    """Apply ``suggested_text`` fixes that pass structural validation.

    When ``strict_placeholder_order`` is True, reject suggestions whose
    placeholder **set** differs from the current translation (renumber / add /
    drop). Same ids in a different order are allowed after §6.55 align (§6.133).

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
        current = updated.get(issue.segment_id, seg.text)
        regress = _critic_fix_would_regress(current, issue.suggested_text, issue)
        if regress:
            logger.warning(
                "Skipping critic fix for %s: %s (%s)",
                issue.segment_id,
                regress,
                issue.comment[:120],
            )
            skipped.append(issue)
            continue
        if strict_placeholder_order:
            current_ph = extract_placeholders(current)
            suggested_ph = extract_placeholders(issue.suggested_text)
            # Same placeholder ids, different order is safe after §6.55 align
            # (ids name the same atoms). Reject only renumber / add / drop (§6.133).
            if sorted(current_ph) != sorted(suggested_ph):
                logger.warning(
                    "Skipping critic fix for %s: placeholder set change in doc_verify "
                    "(current=%s, suggested=%s) would mis-render EN atoms",
                    issue.segment_id,
                    current_ph,
                    suggested_ph,
                )
                skipped.append(issue)
                continue
            # same multiset, possibly reordered — apply
        try:
            validate_segment_translation(seg, issue.suggested_text)
        except TranslationValidationError as exc:
            logger.warning("Skipping critic fix for %s: %s", issue.segment_id, exc)
            skipped.append(issue)
            continue
        from ydbdoc_review.validation.heuristics import check_cyrillic_in_en

        if check_cyrillic_in_en(issue.suggested_text, target_lang="en"):
            logger.warning(
                "Skipping critic fix for %s: suggested_text introduces Cyrillic in EN",
                issue.segment_id,
            )
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
    initial = _drop_impossible_code_link_issues(initial, translations, segments)
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
        unresolved = _drop_impossible_code_link_issues(unresolved, fixed, segments)

    return CriticReviewResult(
        initial=initial,
        translations=fixed,
        applied=applied,
        skipped=skipped,
        unresolved=unresolved,
    )
