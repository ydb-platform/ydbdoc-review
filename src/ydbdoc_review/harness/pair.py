"""Execute one pair plan via per-file FileHarness."""

from __future__ import annotations

import logging
from dataclasses import is_dataclass, replace

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.profiles import TRANSLATE_PROFILE, VERIFY_PROFILE
from ydbdoc_review.harness.runner import FileHarness
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.llm.errors import LLMError
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan
from ydbdoc_review.pipeline.qa import compose_file_verdict
from ydbdoc_review.pipeline.types import PairRunResult
from ydbdoc_review.translation.errors import TranslationError
from ydbdoc_review.validation.autotitle_hrefs import restore_autotitle_hrefs
from ydbdoc_review.validation.fragment_repair import repair_en_fragments
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.href_parity import (
    apply_href_only_delta,
    insert_missing_autotitle_list_items,
    is_href_only_change,
    restore_md_link_hrefs,
)
from ydbdoc_review.validation.markdown_layout import repair_generated_markdown_layout
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation
from ydbdoc_review.validation.structural_repair import repair_en_structure_from_ru

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
    if plan.action == "critic_only" and is_href_only_change(content.en_base_text, existing_target):
        logger.info(
            "Deterministic href-only target %s; critic is read-only/bypassed",
            plan.target_path,
        )
        return PairRunResult(
            plan=plan,
            target_text=existing_target,
            source_text=source_text,
        )
    if plan.action == "translate_to_en" and existing_target is not None:
        deterministic = apply_href_only_delta(
            content.ru_base_text,
            source_text,
            content.en_base_text or existing_target,
        )
        if deterministic is not None:
            logger.info(
                "Deterministic href-only translation for %s; bypassing LLM and repairs",
                plan.target_path,
            )
            return PairRunResult(
                plan=plan,
                target_text=deterministic,
                source_text=source_text,
            )
    enable_translate = plan.action in ("translate_to_en", "translate_to_ru")
    enable_critic = plan.action != "skip"
    profile = TRANSLATE_PROFILE if enable_translate else VERIFY_PROFILE

    # §6.132: pass existing EN + base RU into translate so differential can seed.
    base_source: str | None = None
    if enable_translate and plan.action == "translate_to_en":
        base_source = content.ru_base_text
    elif enable_translate and plan.action == "translate_to_ru":
        base_source = content.en_base_text

    state = FileRunState(
        mode=profile.name,  # type: ignore[arg-type]
        file_path=plan.source_path,
        raw_source_text=source_text,
        source_text=source_text,
        existing_target_text=existing_target,
        base_source_text=base_source if enable_translate else None,
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
        docs_text_reader=ctx.docs_text_reader,
        docs_repo_path=ctx.docs_repo_path,
    )

    try:
        file_result = FileHarness(profile).run(state, harness_ctx)
    except (LLMError, TranslationError, ValueError) as exc:
        # Keep existing EN so §6.80 completeness does not abort the whole PR
        # when one large file times out (glossary on #45667).
        if (
            plan.action == "translate_to_en"
            and existing_target
            and plan.target_path == content.pair.en_path
        ):
            logger.warning(
                "Translate failed for %s; keeping existing EN (%s)",
                plan.target_path,
                exc,
            )
            return PairRunResult(
                plan=plan,
                target_text=existing_target,
                source_text=source_text,
                error=None,
            )
        logger.exception("Failed to process %s", plan.target_path)
        return PairRunResult(plan=plan, error=str(exc))

    differential_meta = getattr(file_result, "differential_meta", {})
    semantic_noop = (
        plan.action == "translate_to_en"
        and existing_target is not None
        and isinstance(differential_meta, dict)
        and differential_meta.get("semantic_noop") is True
    )
    target_text = existing_target if semantic_noop else file_result.final_text
    if target_text and content.ru_text and not semantic_noop:
        if plan.action == "translate_to_ru":
            target_text = restore_autotitle_hrefs(target_text, content.ru_text)
        elif plan.action in ("translate_to_en", "critic_only") and (
            plan.target_lang.lower() in {"en", "english"}
            or plan.target_path == content.pair.en_path
        ):
            before_restore = target_text
            # Also on doc_verify critic_only: critic can reintroduce stale
            # hrefs (Sessions → index.md#sessions, #47104 after 05:32 fixup).
            target_text = restore_autotitle_hrefs(
                target_text,
                content.ru_text,
                force_exact=True,
                en_page_path=plan.target_path,
                en_toc_reachable=ctx.en_toc_reachable,
            )
            target_text = insert_missing_autotitle_list_items(
                target_text,
                content.ru_text,
                en_page_path=plan.target_path,
                en_toc_reachable=ctx.en_toc_reachable,
            )
            target_text = restore_md_link_hrefs(
                target_text,
                content.ru_text,
                source_ru_base=content.ru_base_text,
                target_baseline=content.en_text or content.en_base_text,
            )
            # Critic may reintroduce RU-only hrefs; strip again after restore.
            if ctx.en_toc_reachable is not None:
                from ydbdoc_review.validation.glossary_toc_links import (
                    strip_unreachable_internal_links,
                )

                target_text = strip_unreachable_internal_links(
                    target_text,
                    file_path=plan.target_path,
                    reachable=ctx.en_toc_reachable,
                    target_lang=plan.target_lang,
                )
            # §6.142: retarget missing EN fragments (stale path / ldap≠ldap-auth).
            if ctx.docs_text_reader is not None:
                target_text = repair_en_fragments(
                    target_text,
                    en_page_path=plan.target_path,
                    read_text=ctx.docs_text_reader,
                    ru_source=content.ru_text,
                    en_baseline=content.en_text or content.en_base_text,
                )
            target_text = repair_en_structure_from_ru(target_text, content.ru_text)
            # Pair-level structural repair reparses legacy YFM after the file
            # harness and can reintroduce synthetic fence closers. Raw RU layout
            # must remain the last structural authority before QA/commit (#50741).
            target_text = repair_generated_markdown_layout(
                normalize_ru_source_for_translation(source_text), target_text
            )
            # Verification must describe the immutable checkout, never a
            # repaired in-memory candidate. Keep the critic findings, then
            # recompute deterministic QA below against the exact PR bytes.
            if plan.action == "critic_only" and existing_target is not None:
                target_text = existing_target
            # Restore runs after harness heuristics — refresh QA so the report
            # matches committed text (#49451).
            if target_text != before_restore and is_dataclass(file_result):
                from ydbdoc_review.harness.critic_verdict import compute_critic_verdict

                norm = (
                    normalize_ru_source_for_translation(source_text)
                    if plan.source_lang.lower() in {"ru", "russian"}
                    else source_text
                )
                classified = run_file_heuristics_classified(
                    source_text,
                    target_text,
                    normalized_source_text=norm,
                    source_lang=plan.source_lang,
                    target_lang=plan.target_lang,
                    source_file=plan.source_path,
                    en_toc_reachable=ctx.en_toc_reachable,
                    docs_text_reader=ctx.docs_text_reader,
                    docs_repo_path=ctx.docs_repo_path,
                    en_baseline_text=content.en_text or content.en_base_text,
                )
                critic_verdict = compute_critic_verdict(
                    initial=file_result.critic_initial,
                    unresolved=file_result.critic_unresolved,
                )
                verdict = compose_file_verdict(
                    critic_verdict=critic_verdict,
                    alignment_error=file_result.segment_alignment_error,
                    heuristics=classified,
                    manual_actions=bool(file_result.manual_actions),
                )
                file_result = replace(
                    file_result,
                    final_text=target_text,
                    heuristic_blocking=list(classified.blocking),
                    heuristic_warnings=list(classified.warnings),
                    heuristic_info=list(classified.info),
                    verdict=verdict,
                )

    return PairRunResult(
        plan=plan,
        target_text=target_text,
        file_result=file_result,
        source_text=source_text,
    )
