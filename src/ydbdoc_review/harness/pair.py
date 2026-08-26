"""Execute one pair plan via per-file FileHarness."""

from __future__ import annotations

import logging
from dataclasses import is_dataclass, replace

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.profiles import TRANSLATE_WITH_QA_PROFILE, VERIFY_PROFILE
from ydbdoc_review.harness.runner import FileHarness
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.llm.errors import LLMError
from ydbdoc_review.pipeline.analyze import PairContent, PairPlan, PairProvenance
from ydbdoc_review.pipeline.qa import compose_file_verdict
from ydbdoc_review.pipeline.types import PairRunResult
from ydbdoc_review.translation.differential import (
    autotitle_delta_satisfied_in_en,
)
from ydbdoc_review.translation.errors import TranslationError
from ydbdoc_review.validation.autotitle_hrefs import restore_autotitle_hrefs
from ydbdoc_review.validation.fragment_repair import repair_en_fragments
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.href_parity import (
    apply_href_only_delta,
    apply_localized_mirror_delta,
    check_href_parity,
    insert_missing_autotitle_list_items,
    is_href_only_change,
    restore_md_link_hrefs,
)
from ydbdoc_review.validation.markdown_layout import repair_generated_markdown_layout
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation
from ydbdoc_review.validation.structural_delta import (
    HistoricalDeltaStatus,
    historical_operations_survive,
    structural_delta_satisfied,
)
from ydbdoc_review.validation.structural_repair import repair_en_structure_from_ru

logger = logging.getLogger(__name__)


def _try_deterministic_en_preserve(
    content: PairContent,
    plan: PairPlan,
    source_text: str,
    existing_target: str | None,
    ctx: HarnessContext,
    *,
    historical_merged_provenance: bool = False,
) -> tuple[str, str] | None:
    """Return preserved/patched EN when the RU merge delta is already satisfied."""
    if existing_target is None or plan.target_path != content.pair.en_path:
        return None
    ru_base = content.ru_base_text
    historical_head = content.ru_text or source_text
    if not ru_base:
        return None
    if ru_base == historical_head:
        if content.pair.previous_ru_path is not None:
            return existing_target, HistoricalDeltaStatus.ALREADY_TRANSLATED.value
        return None
    if not historical_merged_provenance:
        return None

    authoritative_current = bool(
        content.current_ru_text is not None
        and source_text == content.current_ru_text
        and source_text != historical_head
    )
    survival = historical_operations_survive(
        ru_base, historical_head, content.current_ru_text
    )
    if not survival.survives:
        logger.info(
            "Historical delta for %s is no longer present in current RU; "
            "preserving current EN byte-for-byte (%s)",
            plan.target_path,
            survival.reason,
        )
        return existing_target, HistoricalDeltaStatus.SUPERSEDED.value

    structural = structural_delta_satisfied(
        ru_base,
        historical_head,
        existing_target,
        current_source=content.current_ru_text,
    )
    if structural.satisfied:
        logger.info(
            "Historical structural delta already satisfied for %s; "
            "preserving EN byte-for-byte (%s; additions=%d, removals=%d)",
            plan.target_path,
            structural.reason,
            len(structural.additions),
            len(structural.removals),
        )
        return existing_target, structural.status.value
    if structural.fail_closed:
        if authoritative_current or plan.action == "translate_to_en":
            logger.info(
                "Unsafe historical structural patch for %s; falling through "
                "to translation from the authoritative source",
                plan.target_path,
            )
            return None
        raise TranslationError(
            "historical structural delta cannot be applied safely without "
            f"overwriting later target structure: {structural.reason}"
        )

    href_only = is_href_only_change(ru_base, historical_head)
    localized = apply_localized_mirror_delta(
        ru_base,
        historical_head,
        existing_target,
        en_page_path=plan.target_path,
        docs_text_reader=ctx.docs_text_reader,
    )
    if localized is not None:
        if ctx.docs_text_reader is not None:
            localized = repair_en_fragments(
                localized,
                en_page_path=plan.target_path,
                read_text=ctx.docs_text_reader,
                ru_source=historical_head,
                en_baseline=content.en_base_text or existing_target,
            )
        if href_only:
            parity_errors = check_href_parity(
                historical_head,
                localized,
                en_page_path=plan.target_path,
                docs_text_reader=ctx.docs_text_reader,
            )
            if parity_errors:
                raise TranslationError(
                    "localized href patch did not satisfy exact RU/EN parity: "
                    + "; ".join(parity_errors)
                )
        else:
            patched = structural_delta_satisfied(
                ru_base,
                historical_head,
                localized,
                current_source=content.current_ru_text,
            )
            if not patched.satisfied:
                raise TranslationError(
                    "localized structural patch did not satisfy surviving "
                    f"historical operations: {patched.reason}"
                )
        logger.info(
            "Deterministic localized mirror delta for %s; bypassing LLM and repairs",
            plan.target_path,
        )
        return localized, HistoricalDeltaStatus.TRANSLATED_NOW.value

    if autotitle_delta_satisfied_in_en(ru_base, historical_head, existing_target):
        logger.info(
            "RU autotitle delta already satisfied in EN for %s; preserving bytes",
            plan.target_path,
        )
        return existing_target, HistoricalDeltaStatus.ALREADY_TRANSLATED.value

    parity_target = existing_target
    if ctx.docs_text_reader is not None:
        parity_target = repair_en_fragments(
            parity_target,
            en_page_path=plan.target_path,
            read_text=ctx.docs_text_reader,
            ru_source=historical_head,
            en_baseline=content.en_base_text or existing_target,
        )
    if is_href_only_change(ru_base, historical_head) and not check_href_parity(
        historical_head,
        parity_target,
        en_page_path=plan.target_path,
        docs_text_reader=ctx.docs_text_reader,
    ):
        logger.info(
            "RU/EN href parity OK for %s despite structural drift; preserving EN",
            plan.target_path,
        )
        return parity_target, HistoricalDeltaStatus.ALREADY_TRANSLATED.value

    if structural.status is HistoricalDeltaStatus.MISSING_CURRENT_DELTA:
        if authoritative_current:
            logger.info(
                "Missing historical operation for %s will be translated from "
                "authoritative current RU",
                plan.target_path,
            )
            return None
        raise TranslationError(
            "surviving historical operation is not translated and has no safe localized patch: "
            f"{structural.reason}"
        )

    return None


def _read_source_text(content: PairContent, plan: PairPlan) -> str | None:
    if plan.authoritative_source_text is not None:
        return plan.authoritative_source_text
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
    *,
    historical_merged_provenance: bool = False,
) -> PairRunResult:
    """Run one pair plan; delegates to ``FileHarness`` for translate/verify."""
    if plan.action == "skip":
        disposition = (
            "delete_already_satisfied"
            if plan.pair.ru_deleted
            else "superseded_absent"
            if plan.provenance is PairProvenance.SUPERSEDED_ABSENT
            else "existing_satisfied"
        )
        return PairRunResult(plan=plan, skipped=True, historical_disposition=disposition)

    if plan.action == "blocked":
        return PairRunResult(
            plan=plan,
            error=plan.summary or "pair disposition blocked",
            historical_disposition="blocked",
        )

    if plan.action in {"delete_en", "delete_target"}:
        return PairRunResult(plan=plan, deleted=True, target_text=None)

    source_text = _read_source_text(content, plan)
    if source_text is None:
        return PairRunResult(
            plan=plan,
            error=f"Missing source text for {plan.source_path!r}",
        )

    existing_target = _read_target_text(content, plan)
    if plan.provenance is PairProvenance.CURRENT_RU_MISSING_EN:
        existing_target = None
        content = replace(content, ru_base_text=None, en_base_text=None, en_text=None)
    historical_replay = historical_merged_provenance and content.historical_replay
    if historical_replay and plan.action in {"translate_to_en", "critic_only"}:
        try:
            preserved = _try_deterministic_en_preserve(
                content,
                plan,
                source_text,
                existing_target,
                ctx,
                historical_merged_provenance=historical_merged_provenance,
            )
        except TranslationError as exc:
            logger.error("Refusing destructive historical rewrite: %s", exc)
            return PairRunResult(
                plan=plan,
                target_text=existing_target,
                source_text=source_text,
                error=str(exc),
            )
        if preserved is not None:
            if isinstance(preserved, tuple):
                preserved_text, disposition = preserved
            else:  # Backward-compatible for custom/mocked preserve hooks.
                preserved_text = preserved
                disposition = HistoricalDeltaStatus.ALREADY_TRANSLATED.value
            return PairRunResult(
                plan=plan,
                target_text=preserved_text,
                source_text=source_text,
                skipped=disposition
                in {
                    HistoricalDeltaStatus.ALREADY_TRANSLATED.value,
                    HistoricalDeltaStatus.SUPERSEDED.value,
                },
                historical_disposition=disposition,
            )
        if (
            historical_replay
            and content.current_ru_text is not None
            and source_text == content.current_ru_text
            and content.ru_text != source_text
        ):
            # Historical snapshots prove operation ownership only.  Any
            # fallback translates current RU without seeding from old blobs.
            content = replace(content, ru_base_text=None, en_base_text=None)
    if plan.action == "critic_only" and is_href_only_change(content.en_base_text, existing_target):
        href_only_target = existing_target
        if ctx.docs_text_reader is not None and existing_target is not None:
            href_only_target = repair_en_fragments(
                existing_target,
                en_page_path=plan.target_path,
                read_text=ctx.docs_text_reader,
                ru_source=source_text,
                en_baseline=content.en_base_text or existing_target,
            )
        logger.info(
            "Deterministic href-only target %s; critic is read-only/bypassed",
            plan.target_path,
        )
        return PairRunResult(
            plan=plan,
            target_text=href_only_target,
            source_text=source_text,
        )
    if historical_replay and plan.action == "translate_to_en" and existing_target is not None:
        deterministic = apply_href_only_delta(
            content.ru_base_text,
            source_text,
            content.en_base_text or existing_target,
            en_page_path=plan.target_path,
            docs_text_reader=ctx.docs_text_reader,
        )
        if deterministic is not None:
            if ctx.docs_text_reader is not None:
                deterministic = repair_en_fragments(
                    deterministic,
                    en_page_path=plan.target_path,
                    read_text=ctx.docs_text_reader,
                    ru_source=source_text,
                    en_baseline=content.en_base_text or existing_target,
                )
            return PairRunResult(
                plan=plan,
                target_text=deterministic,
                source_text=source_text,
                historical_disposition=HistoricalDeltaStatus.TRANSLATED_NOW.value,
            )
    enable_translate = plan.action in ("translate_to_en", "translate_to_ru")
    enable_critic = True
    profile = TRANSLATE_WITH_QA_PROFILE if enable_translate else VERIFY_PROFILE

    base_source: str | None = None
    base_target: str | None = None
    if historical_replay or plan.action == "critic_only":
        if plan.action in {"translate_to_en", "critic_only"} and (
            plan.target_lang.lower() in {"en", "english"}
            or plan.target_path == content.pair.en_path
        ):
            base_source = content.ru_base_text
            base_target = content.en_base_text
        elif plan.action == "translate_to_ru":
            base_source = content.en_base_text
            base_target = content.ru_base_text

    state = FileRunState(
        mode=profile.name,  # type: ignore[arg-type]
        file_path=plan.source_path,
        raw_source_text=source_text,
        source_text=source_text,
        existing_target_text=(
            existing_target if historical_replay or not enable_translate else None
        ),
        base_target_text=base_target,
        base_source_text=base_source,
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
        en_toc_reachable=(
            ctx.en_toc_reachable if historical_replay or not enable_translate else None
        ),
        docs_text_reader=(
            ctx.docs_text_reader if historical_replay or not enable_translate else None
        ),
        docs_repo_path=(
            ctx.docs_repo_path if historical_replay or not enable_translate else None
        ),
    )

    try:
        file_result = FileHarness(profile).run(state, harness_ctx)
    except (LLMError, TranslationError, ValueError) as exc:
        logger.exception("Failed to process %s", plan.target_path)
        return PairRunResult(plan=plan, error=str(exc))

    differential_meta = getattr(file_result, "differential_meta", {})
    semantic_noop = False
    deterministic_autotitle_patch = (
        isinstance(differential_meta, dict)
        and differential_meta.get("deterministic_autotitle_patch") is True
    )
    target_text = existing_target if semantic_noop else file_result.final_text
    is_en_target = plan.action in ("translate_to_en", "critic_only") and (
        plan.target_lang.lower() in {"en", "english"}
        or plan.target_path == content.pair.en_path
    )
    before_pair_repairs: str | None = None
    if target_text and source_text and not semantic_noop:
        if plan.action == "translate_to_ru":
            target_text = restore_autotitle_hrefs(target_text, content.ru_text)
        elif is_en_target:
            before_pair_repairs = target_text
            # Also on doc_verify critic_only: critic can reintroduce stale
            # hrefs (Sessions → index.md#sessions, #47104 after 05:32 fixup).
            # Always sync ``[{#T}](href)`` from RU, including after deterministic
            # autotitle-list insertion (#50904: backup-and-recovery/index.md).
            target_text = restore_autotitle_hrefs(
                target_text,
                source_text,
                force_exact=True,
                en_page_path=plan.target_path,
                en_toc_reachable=ctx.en_toc_reachable,
            )
        if (
            not deterministic_autotitle_patch
            and is_en_target
            and not enable_translate
        ):
            target_text = insert_missing_autotitle_list_items(
                target_text,
                source_text,
                en_page_path=plan.target_path,
                en_toc_reachable=ctx.en_toc_reachable,
            )
            target_text = restore_md_link_hrefs(
                target_text,
                source_text,
                source_ru_base=None if enable_translate else content.ru_base_text,
                target_baseline=(
                    None
                    if enable_translate
                    else content.en_text or content.en_base_text
                ),
                en_page_path=plan.target_path,
                docs_text_reader=ctx.docs_text_reader,
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
                    ru_source=source_text,
                    en_baseline=content.en_text or content.en_base_text,
                )
            target_text = repair_en_structure_from_ru(target_text, source_text)
            # Pair-level structural repair reparses legacy YFM after the file
            # harness and can reintroduce synthetic fence closers. Raw RU layout
            # must remain the last structural authority before QA/commit (#50741).
            target_text = repair_generated_markdown_layout(
                normalize_ru_source_for_translation(source_text), target_text
            )
        elif enable_translate and is_en_target:
            # Source-controlled structure is safe to restore. Do not consult
            # current EN, TOC reachability, or repository-wide link guesses.
            target_text = repair_en_structure_from_ru(target_text, source_text)
            target_text = repair_generated_markdown_layout(
                normalize_ru_source_for_translation(source_text), target_text
            )
        if (
            is_en_target
            and before_pair_repairs is not None
            and target_text != before_pair_repairs
            and is_dataclass(file_result)
        ):
            # Restore runs after harness heuristics — refresh QA so the report
            # matches committed text (#49451), including deterministic autotitle
            # href sync (#50904).
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
                source_baseline_text=content.ru_base_text,
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

    if plan.action == "critic_only" and existing_target is not None:
        # Also cover an empty (but valid) RU document, which skips the repair
        # branch above. The report byte guard must remain unconditional.
        target_text = existing_target
        if ctx.docs_text_reader is not None:
            target_text = repair_en_fragments(
                target_text,
                en_page_path=plan.target_path,
                read_text=ctx.docs_text_reader,
                ru_source=content.ru_text,
                en_baseline=content.en_text or content.en_base_text,
            )

    return PairRunResult(
        plan=plan,
        target_text=target_text,
        file_result=file_result,
        source_text=source_text,
    )
