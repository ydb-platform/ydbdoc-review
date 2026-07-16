"""Execute one pair plan via per-file FileHarness."""

from __future__ import annotations

import logging

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.profiles import TRANSLATE_PROFILE, VERIFY_PROFILE
from ydbdoc_review.harness.runner import FileHarness
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.llm.errors import LLMError
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.types import PairRunResult
from ydbdoc_review.translation.errors import TranslationError
from ydbdoc_review.validation.autotitle_hrefs import restore_autotitle_hrefs

logger = logging.getLogger(__name__)


def _read_source_text(content: PairContent, plan: PairPlan) -> str | None:
    if plan.source_path == content.pair.ru_path:
        return content.ru_text
    return content.en_text


def _read_target_text(content: PairContent, plan: PairPlan) -> str | None:
    if plan.target_path == content.pair.en_path:
        return content.en_text
    return content.ru_text


def run_pair_plan(
    content: PairContent,
    plan: PairPlan,
    ctx: HarnessContext,
    cache: dict[str, str],
) -> PairRunResult:
    """Run one pair plan; delegates to ``FileHarness`` for translate/verify."""
    if plan.action == "skip":
        return PairRunResult(plan=plan, skipped=True)

    if plan.action == "delete_en":
        return PairRunResult(plan=plan, deleted=True, target_text=None)

    source_text = _read_source_text(content, plan)
    if source_text is None:
        return PairRunResult(
            plan=plan,
            error=f"Missing source text for {plan.source_path!r}",
        )

    existing_target = _read_target_text(content, plan)
    enable_translate = plan.action in ("translate_to_en", "translate_to_ru")
    enable_critic = plan.action != "skip"
    profile = TRANSLATE_PROFILE if enable_translate else VERIFY_PROFILE

    state = FileRunState(
        mode=profile.name,  # type: ignore[arg-type]
        file_path=plan.source_path,
        raw_source_text=source_text,
        source_text=source_text,
        existing_target_text=existing_target if not enable_translate else None,
    )
    harness_ctx = HarnessContext.from_options(
        ctx.client,
        glossary=ctx.glossary,
        config=ctx.config,
        source_lang=plan.source_lang,
        target_lang=plan.target_lang,
        cache=cache,
        enable_critic=enable_critic,
        usage_record_start=len(ctx.client.usage_tracker.records),
        en_toc_reachable=ctx.en_toc_reachable,
    )

    try:
        file_result = FileHarness(profile).run(state, harness_ctx)
    except (LLMError, TranslationError, ValueError) as exc:
        logger.exception("Failed to process %s", plan.target_path)
        return PairRunResult(plan=plan, error=str(exc))

    target_text = file_result.final_text
    if plan.action == "translate_to_ru" and target_text and content.ru_text:
        target_text = restore_autotitle_hrefs(target_text, content.ru_text)

    return PairRunResult(
        plan=plan,
        target_text=target_text,
        file_result=file_result,
    )
