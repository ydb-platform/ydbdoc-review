"""Harness pipeline steps — one responsibility per stage."""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.critic_verdict import compute_critic_verdict
from ydbdoc_review.harness.render import (
    finalize_en_target_result as finalize_en_target,
)
from ydbdoc_review.harness.render import (
    remap_translations_by_position,
    render_with_translations,
)
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.ops.translation_checkpoint import (
    load_verified_unit,
    translation_unit_key_for_segment,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import (
    compose_file_verdict,
    gate_round_trip,
)
from ydbdoc_review.reporting.locations import (
    build_segment_excerpts,
    build_segment_line_map,
    build_segment_source_excerpts,
)
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import normalize_target_segments_to_source
from ydbdoc_review.segmentation.types import Segment
from ydbdoc_review.translation.coverage import assemble_coverage
from ydbdoc_review.translation.critic import (
    apply_critic_fixes,
    run_verify,
)
from ydbdoc_review.translation.critic import (
    run_critic as run_critic_pass,
)
from ydbdoc_review.translation.critic_retranslate import (
    issues_by_segment_id,
    retranslate_segments_with_critic_feedback,
)
from ydbdoc_review.translation.file_profiles import is_glossary_file
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.translation.translator import translate_segments
from ydbdoc_review.validation.heuristics import (
    _classify_heuristic,
    run_file_heuristics_classified,
)
from ydbdoc_review.validation.href_parity import check_href_parity, collect_internal_hrefs
from ydbdoc_review.validation.include_targets import repair_missing_includes
from ydbdoc_review.validation.link_contract import coerce_link_contract
from ydbdoc_review.validation.markdown_layout import repair_generated_markdown_layout
from ydbdoc_review.validation.placeholder_drift import (
    drop_spurious_placeholder_issues,
    filter_critic_response,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation
from ydbdoc_review.validation.structural_repair import repair_en_structure_from_ru

logger = logging.getLogger(__name__)


class HarnessStep(Protocol):
    name: str

    def run(self, state: FileRunState, ctx: HarnessContext) -> None: ...


def normalize_source_text(raw: str, *, source_lang: str) -> str:
    if source_lang.lower() in {"ru", "russian"}:
        return normalize_ru_source_for_translation(raw)
    return raw


def _render_translated_from_source(state: FileRunState, ctx: HarnessContext) -> None:
    assert state.source_doc is not None
    state.translated_text = render_with_translations(
        state.source_doc,
        state.segments,
        state.translations,
        target_lang=ctx.target_lang,
        job_anchor_dictionary=ctx.job_anchor_dictionary,
    )
    if ctx.target_lang.lower() in {"en", "english"}:
        contract = coerce_link_contract(
            finalize_en_target(
                state.translated_text,
                state.source_text,
                client=ctx.client,
                glossary=ctx.glossary,
                file_path=state.file_path,
                source_lang=ctx.source_lang,
                target_lang=ctx.target_lang,
                prompt_version=ctx.prompt_version,
                out_warnings=state.finalize_warnings,
                en_toc_reachable=ctx.en_toc_reachable,
                # Verify uses EN as the fence-body authority, but layout repair must
                # still restore the raw RU marker/container structure (#50741).
                layout_source_text=state.source_text,
                source_base_text=state.base_source_text,
                target_baseline_text=state.base_target_text or state.existing_target_text,
                docs_text_reader=ctx.docs_text_reader,
            )
        )
        state.translated_text = contract.text
        state.link_contract_issues = list(
            dict.fromkeys([*state.link_contract_issues, *contract.issues])
        )
        state.translated_text = repair_missing_includes(
            state.source_text,
            state.translated_text,
            source_file=state.file_path,
            docs_root=ctx.config.paths.docs_root,
            docs_text_reader=ctx.docs_text_reader,
            out_warnings=state.finalize_warnings,
        )


def _unresolved_retry_segment_ids(state: FileRunState) -> set[str]:
    if state.critic_unresolved is None:
        return set()
    return {issue.segment_id for issue in state.critic_unresolved.issues if issue.segment_id}


def _needs_critic_feedback_retranslate(state: FileRunState) -> bool:
    if state.segment_alignment_error:
        return False
    return bool(_unresolved_retry_segment_ids(state))


def run_critic_loop(state: FileRunState, ctx: HarnessContext) -> None:
    """Critic → apply fixes → re-render → verify (mutates ``state``)."""
    state.critic_initial = run_critic_pass(
        ctx.client,
        segments=state.segments,
        translations=state.translations,
        glossary=ctx.glossary,
        file_path=state.file_path,
        source_lang=ctx.source_lang,
        target_lang=ctx.target_lang,
        prompt_version=ctx.prompt_version,
        max_chars=ctx.batch_chars,
    )
    if any(issue.category == "critic_model_refusal" for issue in state.critic_initial.issues):
        state.finalize_warnings.append(
            "critic_model_refusal: model declined review; heuristics only on verify"
        )
        state.critic_unresolved = CriticResponse(verdict="ok", issues=[])
        return
    if any(issue.category == "critic_execution_failed" for issue in state.critic_initial.issues):
        state.critic_unresolved = state.critic_initial
        return
    actionable_issues = drop_spurious_placeholder_issues(
        state.critic_initial.issues,
        state.segments,
        state.translations,
        source_text=state.raw_source_text,
        source_file=state.file_path,
        en_toc_reachable=ctx.en_toc_reachable,
    )
    state.translations, state.critic_applied, state.critic_skipped = apply_critic_fixes(
        state.translations,
        state.segments,
        actionable_issues,
        strict_placeholder_order=(state.mode == "verify"),
    )
    if not actionable_issues:
        state.critic_unresolved = CriticResponse(verdict="ok", issues=[])
        return

    assert state.render_base_doc is not None
    render_translations = (
        state.translations
        if state.render_base_segments is state.segments
        else remap_translations_by_position(
            state.segments, state.render_base_segments, state.translations
        )
    )
    state.translated_text = render_with_translations(
        state.render_base_doc,
        state.render_base_segments,
        render_translations,
        target_lang=ctx.target_lang,
        job_anchor_dictionary=ctx.job_anchor_dictionary,
    )
    if ctx.target_lang.lower() in {"en", "english"}:
        contract = coerce_link_contract(
            finalize_en_target(
                state.translated_text,
                state.fence_reference_text,
                client=ctx.client,
                glossary=ctx.glossary,
                file_path=state.file_path,
                source_lang=ctx.source_lang,
                target_lang=ctx.target_lang,
                prompt_version=ctx.prompt_version,
                out_warnings=state.finalize_warnings,
                en_toc_reachable=ctx.en_toc_reachable,
                layout_source_text=state.source_text,
                protected_source_text=state.source_text,
                source_base_text=state.base_source_text,
                target_baseline_text=state.base_target_text or state.existing_target_text,
            )
        )
        state.translated_text = contract.text
        state.link_contract_issues = list(
            dict.fromkeys([*state.link_contract_issues, *contract.issues])
        )
    state.translations, state.segment_alignment_error = gate_round_trip(
        state.segments, state.translated_text
    )
    if state.segment_alignment_error:
        return
    state.critic_unresolved = run_verify(
        ctx.client,
        segments=state.segments,
        translations=state.translations,
        prior_issues=actionable_issues,
        glossary=ctx.glossary,
        file_path=state.file_path,
        source_lang=ctx.source_lang,
        target_lang=ctx.target_lang,
        prompt_version=ctx.prompt_version,
        max_chars=ctx.batch_chars,
    )
    state.critic_unresolved = filter_critic_response(
        state.critic_unresolved,
        state.segments,
        state.translations,
        source_text=state.raw_source_text,
        source_file=state.file_path,
        en_toc_reachable=ctx.en_toc_reachable,
    )
    if state.mode != "verify" or not state.critic_unresolved.issues:
        return

    second_actionable = drop_spurious_placeholder_issues(
        state.critic_unresolved.issues,
        state.segments,
        state.translations,
        source_text=state.raw_source_text,
        source_file=state.file_path,
        en_toc_reachable=ctx.en_toc_reachable,
    )
    second_translations, second_applied, second_skipped = apply_critic_fixes(
        state.translations,
        state.segments,
        second_actionable,
        strict_placeholder_order=True,
    )
    state.critic_applied.extend(second_applied)
    state.critic_skipped.extend(second_skipped)
    if not second_applied:
        return

    state.translations = second_translations
    render_translations = (
        state.translations
        if state.render_base_segments is state.segments
        else remap_translations_by_position(
            state.segments, state.render_base_segments, state.translations
        )
    )
    state.translated_text = render_with_translations(
        state.render_base_doc,
        state.render_base_segments,
        render_translations,
        target_lang=ctx.target_lang,
        job_anchor_dictionary=ctx.job_anchor_dictionary,
    )
    if ctx.target_lang.lower() in {"en", "english"}:
        contract = coerce_link_contract(
            finalize_en_target(
                state.translated_text,
                state.fence_reference_text,
                client=ctx.client,
                glossary=ctx.glossary,
                file_path=state.file_path,
                source_lang=ctx.source_lang,
                target_lang=ctx.target_lang,
                prompt_version=ctx.prompt_version,
                out_warnings=state.finalize_warnings,
                en_toc_reachable=ctx.en_toc_reachable,
                layout_source_text=state.source_text,
                protected_source_text=state.source_text,
                source_base_text=state.base_source_text,
                target_baseline_text=state.base_target_text or state.existing_target_text,
            )
        )
        state.translated_text = contract.text
        state.link_contract_issues = list(
            dict.fromkeys([*state.link_contract_issues, *contract.issues])
        )
    state.translations, state.segment_alignment_error = gate_round_trip(
        state.segments, state.translated_text
    )
    if state.segment_alignment_error:
        return
    state.critic_unresolved = run_verify(
        ctx.client,
        segments=state.segments,
        translations=state.translations,
        prior_issues=second_actionable,
        glossary=ctx.glossary,
        file_path=state.file_path,
        source_lang=ctx.source_lang,
        target_lang=ctx.target_lang,
        prompt_version=ctx.prompt_version,
        max_chars=ctx.batch_chars,
    )
    state.critic_unresolved = filter_critic_response(
        state.critic_unresolved,
        state.segments,
        state.translations,
        source_text=state.raw_source_text,
        source_file=state.file_path,
        en_toc_reachable=ctx.en_toc_reachable,
    )


class ParseStep:
    name = "parse"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        state.source_text = normalize_source_text(
            state.raw_source_text, source_lang=ctx.source_lang
        )
        state.source_doc = parse_markdown(state.source_text)
        state.segments = extract_segments(state.source_doc)
        state.segment_locations = {
            seg.id: " › ".join(seg.path) if seg.path else "(начало документа)"  # noqa: RUF001
            for seg in state.segments
        }
        state.render_base_doc = state.source_doc
        state.render_base_segments = state.segments
        state.fence_reference_text = state.source_text
        if state.coverage_plan is not None:
            if state.mode == "translate":
                if state.coverage_plan.source_path != state.file_path:
                    raise ValueError("coverage plan source path mismatch")
                actual_source_hash = hashlib.sha256(
                    state.raw_source_text.encode("utf-8")
                ).hexdigest()
                if actual_source_hash != state.coverage_plan.source_hash:
                    raise ValueError("coverage plan source hash mismatch")
                actual_en_hash = (
                    hashlib.sha256(state.existing_target_text.encode("utf-8")).hexdigest()
                    if state.existing_target_text is not None
                    else None
                )
                if actual_en_hash != state.coverage_plan.en_hash:
                    raise ValueError("coverage plan EN hash mismatch")
            actions = [unit.action for unit in state.coverage_plan.units]
            fallback_reasons = (
                tuple(dict.fromkeys(unit.reason for unit in state.coverage_plan.units))
                if state.coverage_plan.mode == "full"
                else ()
            )
            state.differential_meta = {
                "mode": state.coverage_plan.mode,
                "reason": (
                    "proof-based coverage plan"
                    if state.coverage_plan.mode == "units"
                    else (
                        fallback_reasons[0]
                        if fallback_reasons
                        else "full protected-source materialization"
                    )
                ),
                "seeded": actions.count("reuse_verified"),
                "pending": actions.count("translate_required"),
                "protected": actions.count("materialize_protected"),
                "low_magnitude_patch": False,
                "semantic_noop": False,
                "enabled": state.coverage_plan.mode == "units",
                "fallback_reasons": fallback_reasons,
            }
        if not state.segments:
            state.stopped_early = True
            protected_targets = (
                [unit.target for unit in state.coverage_plan.units]
                if state.mode == "translate"
                and state.coverage_plan is not None
                and all(
                    unit.action == "materialize_protected" and unit.target is not None
                    for unit in state.coverage_plan.units
                )
                else []
            )
            if protected_targets:
                state.translated_text = "".join(
                    target for target in protected_targets if target is not None
                )
            elif state.mode == "verify" and state.existing_target_text is not None:
                state.translated_text = state.existing_target_text
            else:
                state.translated_text = state.existing_target_text or state.source_text
            return


class TranslateStep:
    name = "translate"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if state.mode != "translate" or not state.segments:
            return
        assert state.source_doc is not None
        def _retain_validated_segment(segment: Segment, target: str) -> None:
            if ctx.checkpoint is None:
                return
            source = segment.text.encode("utf-8")
            unit_key = translation_unit_key_for_segment(
                segment,
                source_path=state.file_path,
                target_locale=ctx.target_lang,
            )
            ctx.checkpoint.save_unit(
                unit_key,
                source,
                target.encode("utf-8"),
                validated=True,
            )

        def _load_validated_segment(segment: Segment) -> str | None:
            if ctx.checkpoint is None or ctx.resume_parent_run_id is None:
                return None
            source = segment.text.encode("utf-8")
            unit_key = translation_unit_key_for_segment(
                segment,
                source_path=state.file_path,
                target_locale=ctx.target_lang,
            )
            loaded = load_verified_unit(
                ctx.checkpoint.store,
                ctx.resume_parent_run_id,
                ctx.checkpoint.identity,
                unit_key,
                source,
            )
            if loaded is None:
                return None
            try:
                return loaded.target.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning(
                    "Translation resume target is not UTF-8 for %s: %s",
                    segment.id,
                    exc,
                )
                return None

        coverage_plan = state.coverage_plan
        if coverage_plan is not None and coverage_plan.mode == "units":
            if coverage_plan.source_path != state.file_path:
                raise ValueError("coverage plan source path mismatch")
            actual_source_hash = hashlib.sha256(
                state.raw_source_text.encode("utf-8")
            ).hexdigest()
            if actual_source_hash != coverage_plan.source_hash:
                raise ValueError("coverage plan source hash mismatch")

            translated_units: dict[str, str] = {}
            translated_segment_values: dict[str, str] = {}
            for unit in coverage_plan.units:
                if unit.action != "translate_required":
                    continue
                unit_doc = parse_markdown(unit.source)
                unit_segments = extract_segments(unit_doc)
                if not unit_segments:
                    raise ValueError(
                        f"translate_required coverage unit has no prose: {unit.key}"
                    )
                unit_translations = translate_segments(
                    unit_segments,
                    ctx.client,
                    ctx.glossary,
                    file_path=state.file_path,
                    source_lang=ctx.source_lang,
                    target_lang=ctx.target_lang,
                    max_chars=ctx.batch_chars,
                    max_output_chars=ctx.batch_max_output_chars,
                    expansion_ratio=ctx.batch_output_expansion_ratio,
                    json_overhead=ctx.batch_json_overhead_chars,
                    segment_max_chars=ctx.segment_max_source_chars,
                    prompt_version=ctx.prompt_version,
                    cache=ctx.cache,
                    max_parallel_batches=ctx.parallel,
                    manual_actions=state.manual_actions,
                    fallback_reasons=state.fallback_reasons,
                    on_validated_segment=_retain_validated_segment,
                    load_validated_segment=(
                        _load_validated_segment
                        if ctx.resume_parent_run_id is not None
                        else None
                    ),
                )
                unit_state = FileRunState(
                    mode="translate",
                    file_path=state.file_path,
                    raw_source_text=unit.source,
                    source_text=unit.source,
                    existing_target_text=None,
                    base_target_text=None,
                    base_source_text=None,
                    source_doc=unit_doc,
                    segments=unit_segments,
                    translations=unit_translations,
                    render_base_doc=unit_doc,
                    render_base_segments=unit_segments,
                    fence_reference_text=unit.source,
                )
                _render_translated_from_source(unit_state, ctx)
                translated_units[unit.key] = unit_state.translated_text
                translated_segment_values.update(unit_translations)
                state.finalize_warnings.extend(unit_state.finalize_warnings)
                state.link_contract_issues.extend(unit_state.link_contract_issues)

            state.translations = translated_segment_values
            state.translated_text = assemble_coverage(
                coverage_plan,
                existing_en=state.existing_target_text,
                translated_units=translated_units,
            )
            actions = [unit.action for unit in coverage_plan.units]
            state.differential_meta = {
                "mode": "units",
                "reason": "proof-based coverage plan",
                "seeded": actions.count("reuse_verified"),
                "pending": actions.count("translate_required"),
                "protected": actions.count("materialize_protected"),
                "low_magnitude_patch": False,
                "semantic_noop": False,
                "enabled": True,
                "fallback_reasons": tuple(state.fallback_reasons),
            }
            if ctx.target_lang.lower() in {"en", "english"}:
                _apply_en_structural_repair(state, ctx)
            return

        # None is the exact legacy full path. A full coverage plan records why
        # proof-based execution fell back, but does not seed or splice old EN.
        fallback_reasons = (
            tuple(dict.fromkeys(unit.reason for unit in coverage_plan.units))
            if coverage_plan is not None
            else ()
        )
        state.differential_meta = {
            "mode": "full",
            "reason": (
                fallback_reasons[0]
                if fallback_reasons
                else "REQUIREMENTS §5/§13: differential seed/splice disabled on translate"
            ),
            "seeded": 0,
            "pending": len(state.segments),
            "protected": 0,
            "low_magnitude_patch": False,
            "semantic_noop": False,
            "enabled": False,
            "fallback_reasons": tuple(
                dict.fromkeys((*fallback_reasons, *state.fallback_reasons))
            ),
        }

        state.translations = translate_segments(
            state.segments,
            ctx.client,
            ctx.glossary,
            file_path=state.file_path,
            source_lang=ctx.source_lang,
            target_lang=ctx.target_lang,
            max_chars=ctx.batch_chars,
            max_output_chars=ctx.batch_max_output_chars,
            expansion_ratio=ctx.batch_output_expansion_ratio,
            json_overhead=ctx.batch_json_overhead_chars,
            segment_max_chars=ctx.segment_max_source_chars,
            prompt_version=ctx.prompt_version,
            cache=ctx.cache,
            max_parallel_batches=ctx.parallel,
            manual_actions=state.manual_actions,
            fallback_reasons=state.fallback_reasons,
            on_validated_segment=_retain_validated_segment,
            load_validated_segment=(
                _load_validated_segment if ctx.resume_parent_run_id is not None else None
            ),
        )
        _render_translated_from_source(state, ctx)
        if ctx.target_lang.lower() in {"en", "english"}:
            _apply_en_structural_repair(state, ctx)


class LoadTargetStep:
    name = "load_target"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if state.mode != "verify":
            return
        if state.existing_target_text is None:
            raise ValueError("existing_target_text is required for verify mode")
        state.translated_text = state.existing_target_text
        assert state.source_doc is not None
        try:
            target_doc = parse_markdown(state.existing_target_text)
            target_segments = extract_segments(target_doc)
        except Exception:
            target_doc = state.source_doc
            target_segments = state.segments
        if len(target_segments) == len(state.segments):
            target_segments = normalize_target_segments_to_source(state.segments, target_segments)
            state.render_base_doc = target_doc
            state.render_base_segments = target_segments
            state.fence_reference_text = state.existing_target_text


# Structural mismatches remain diagnostic blockers in verify. The size threshold
# only selects the more useful diagnostic for unusually large pages.
_VERIFY_REALIGN_MAX_SEGMENTS = 80


def _apply_en_structural_repair(state: FileRunState, ctx: HarnessContext) -> None:
    if ctx.target_lang.lower() not in {"en", "english"}:
        return
    if not state.source_text or not state.translated_text:
        return
    repaired = repair_en_structure_from_ru(
        state.translated_text,
        state.source_text,
        dictionary=ctx.job_anchor_dictionary,
    )
    if repaired != state.translated_text:
        state.translated_text = repaired
        state.finalize_warnings.append(
            "structural_repair: restored heading anchors / signature blocks from RU"
        )


def _try_partial_verify_realign(state: FileRunState, ctx: HarnessContext) -> bool:
    """Compatibility hook: F-108 keeps verify realignment disabled."""
    del state, ctx
    return False


class RoundTripStep:
    name = "round_trip"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if state.mode == "verify" and is_glossary_file(state.file_path):
            # The glossary is a historical hub whose RU/EN rows are not expected
            # to align segment-for-segment. Verify its exact EN bytes with the
            # deterministic checks below, without an in-memory structural repair
            # or a hidden translation pass.
            state.translations, state.segment_alignment_error = gate_round_trip(
                state.segments, state.translated_text
            )
            if state.segment_alignment_error:
                state.finalize_warnings.append(
                    "glossary_verify_alignment_skipped: structural RU/EN "
                    "segment drift is outside read-only coverage"
                )
                state.segment_alignment_error = None
            return
        target_before_structural_repair = state.translated_text
        if state.mode == "verify" and ctx.target_lang.lower() in {"en", "english"}:
            _apply_en_structural_repair(state, ctx)
            # Repair renderer-added legacy markers before parsing/alignment.
            # This must run *after* AST structural repair, which can itself add
            # synthetic closers for malformed legacy nesting (#50741).
            state.translated_text = repair_generated_markdown_layout(
                state.source_text, state.translated_text
            )
        state.translations, state.segment_alignment_error = gate_round_trip(
            state.segments, state.translated_text
        )
        if (
            state.segment_alignment_error
            and state.mode == "verify"
            and state.translated_text != target_before_structural_repair
        ):
            _, original_alignment_error = gate_round_trip(
                state.segments, target_before_structural_repair
            )
            if original_alignment_error:
                state.segment_alignment_error = original_alignment_error
        if not state.segments:
            return
        if not state.segment_alignment_error or state.mode != "verify":
            return
        # Structural RU/EN mismatch (YFM↔GFM rows, condensed sections, …):
        # keep EN unchanged and report the first divergent element.
        logger.info(
            "verify realign for %s: %s",
            state.file_path,
            state.segment_alignment_error,
        )
        if not ctx.allow_verify_realign:
            logger.info(
                "verify realign disabled for diagnostic critic-only run: %s",
                state.file_path,
            )
            return
        if (
            state.translated_text
            and collect_internal_hrefs(state.source_text)
            and not check_href_parity(state.source_text, state.translated_text)
        ):
            logger.info(
                "verify realign skipped: RU/EN href parity OK for %s",
                state.file_path,
            )
            state.segment_alignment_error = None
            return
        if is_glossary_file(state.file_path):
            logger.info("Glossary verify: skip structural alignment gate (§6.186)")
            state.finalize_warnings.append(
                "glossary_verify_alignment_skipped: structural RU/EN "
                "segment drift ignored on glossary hub"
            )
            state.segment_alignment_error = None
            return
        if len(state.segments) > _VERIFY_REALIGN_MAX_SEGMENTS:
            logger.warning(
                "verify realign skipped for %s (%d segments > %d); "
                "keep existing EN and report alignment mismatch (§6.185)",
                state.file_path,
                len(state.segments),
                _VERIFY_REALIGN_MAX_SEGMENTS,
            )
            state.finalize_warnings.append(
                "verify_realign_skipped: too many segments for full retranslate; "
                "alignment mismatch left as blocker"
            )
            return
        logger.warning(
            "verify realign left unresolved for %s; keeping existing target",
            state.file_path,
        )
        state.finalize_warnings.append(
            "verify_realign_skipped: full translation is not allowed in doc_verify; "
            "alignment mismatch left as blocker"
        )


class CriticLoopStep:
    name = "critic_loop"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if not state.segments or not ctx.enable_critic or state.segment_alignment_error:
            return
        if state.mode == "translate" and state.differential_meta:
            current_usage = ctx.client.usage_tracker.records[ctx.usage_record_start :]
            if not any(record.success for record in current_usage):
                logger.info(
                    "Translate QA: skip model critic for zero-prose result %s",
                    state.file_path,
                )
                return
        if state.mode == "verify" and is_glossary_file(state.file_path):
            logger.info("Glossary verify: skip critic_loop (§6.188)")
            state.finalize_warnings.append(
                "glossary_verify_critic_skipped: coverage is informational; semantic critic, "
                "text rewrite, and realign are disabled; fix findings through "
                "doc_translate or doc_continue"
            )
            return
        run_critic_loop(state, ctx)


class FinalizeEnStep:
    """Post-critic EN finalize: fence/prose Cyrillic translate (§6.136).

    On ``doc_verify``, critic often reports fence-comment issues via heuristics
    only (``Fixed segments: 0``). Without this step those Cyrillic ``--`` / ``//``
    comments stay in the EN file and get committed unchanged.
    """

    name = "finalize_en"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        # Deterministic href/code protections must still run after critic leaves
        # a stale alignment error; otherwise the report sees critic-mutated bytes.
        if ctx.target_lang.lower() not in {"en", "english"}:
            return
        if not state.translated_text:
            return
        if state.mode == "verify" and is_glossary_file(state.file_path):
            logger.info("Glossary verify: skip finalize_en (§6.187)")
            state.finalize_warnings.append(
                "glossary_verify_finalize_skipped: hub page; keep EN as-is on verify"
            )
            return
        # Prefer EN self-reference on verify so enforce_source does not copy RU
        # fence bodies over the target (LoadTargetStep sets fence_reference_text).
        fence_ref = state.fence_reference_text or state.translated_text
        before = state.translated_text
        contract = coerce_link_contract(
            finalize_en_target(
                state.translated_text,
                fence_ref,
                client=ctx.client if state.segments else None,
                glossary=ctx.glossary,
                file_path=state.file_path,
                source_lang=ctx.source_lang,
                target_lang=ctx.target_lang,
                prompt_version=ctx.prompt_version,
                out_warnings=state.finalize_warnings,
                en_toc_reachable=ctx.en_toc_reachable,
                layout_source_text=state.source_text,
                protected_source_text=state.source_text,
                source_base_text=state.base_source_text,
                target_baseline_text=state.base_target_text or state.existing_target_text,
                docs_text_reader=ctx.docs_text_reader,
            )
        )
        state.translated_text = contract.text
        state.link_contract_issues = list(
            dict.fromkeys([*state.link_contract_issues, *contract.issues])
        )
        # RU→EN include parity repair (§6.148): must use RU source, not fence_ref.
        state.translated_text = repair_missing_includes(
            state.source_text,
            state.translated_text,
            source_file=state.file_path,
            docs_root=ctx.config.paths.docs_root,
            docs_text_reader=ctx.docs_text_reader,
            out_warnings=state.finalize_warnings,
        )
        # A subsequent post-critic finalize must preserve this finalized fence
        # body instead of restoring the stale pre-finalize verify input.
        state.fence_reference_text = state.translated_text
        if state.translated_text == before or not state.segments:
            return
        state.translations, align_err = gate_round_trip(state.segments, state.translated_text)
        if align_err:
            # Fence/include-only edits should not fail the whole verify; keep text.
            state.finalize_warnings.append(f"finalize_en_round_trip: {align_err}")


class CriticFeedbackRetryStep:
    """Re-translate segments with unresolved critic issues (translate mode only)."""

    name = "critic_feedback_retry"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if state.mode != "translate" or not state.segments or not ctx.enable_critic:
            return
        if ctx.critic_feedback_retries < 1:
            return

        while state.translate_retry_count < ctx.critic_feedback_retries:
            if not _needs_critic_feedback_retranslate(state):
                break
            assert state.critic_unresolved is not None
            segment_ids = _unresolved_retry_segment_ids(state)
            if not segment_ids:
                break

            grouped = issues_by_segment_id(state.critic_unresolved.issues)
            state.translations = retranslate_segments_with_critic_feedback(
                state.segments,
                segment_ids,
                state.translations,
                grouped,
                ctx.client,
                ctx.glossary,
                file_path=state.file_path,
                source_lang=ctx.source_lang,
                target_lang=ctx.target_lang,
                prompt_version=ctx.prompt_version,
                cache=ctx.cache,
            )
            state.render_base_doc = state.source_doc
            state.render_base_segments = state.segments
            state.fence_reference_text = state.source_text
            _render_translated_from_source(state, ctx)
            state.translations, state.segment_alignment_error = gate_round_trip(
                state.segments, state.translated_text
            )
            if state.segment_alignment_error:
                break

            state.critic_applied = []
            state.critic_skipped = []
            run_critic_loop(state, ctx)
            state.translate_retry_count += 1


class HeuristicsStep:
    name = "heuristics"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        from ydbdoc_review.validation.heuristics import stripped_link_basenames_from_warnings

        state.heuristics = run_file_heuristics_classified(
            state.raw_source_text,
            state.translated_text,
            normalized_source_text=state.source_text,
            source_lang=ctx.source_lang,
            target_lang=ctx.target_lang,
            source_file=state.file_path,
            en_toc_reachable=ctx.en_toc_reachable,
            ignore_link_basenames=stripped_link_basenames_from_warnings(state.finalize_warnings),
            docs_text_reader=ctx.docs_text_reader,
            docs_repo_path=ctx.docs_repo_path,
            en_baseline_text=state.base_target_text or state.existing_target_text,
            source_baseline_text=state.base_source_text,
            glossary=ctx.glossary,
        )
        for message in state.finalize_warnings:
            bucket = _classify_heuristic(message)
            getattr(state.heuristics, bucket).append(message)


class VerdictStep:
    name = "verdict"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        del ctx
        state.critic_verdict = compute_critic_verdict(
            initial=state.critic_initial,
            unresolved=state.critic_unresolved,
        )
        assert state.heuristics is not None
        state.verdict = compose_file_verdict(
            critic_verdict=state.critic_verdict,
            alignment_error=state.segment_alignment_error,
            heuristics=state.heuristics,
            manual_actions=bool(state.manual_actions),
        )


class ReportArtifactsStep:
    name = "report_artifacts"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        del ctx
        if state.stopped_early:
            return
        state.segment_lines = build_segment_line_map(
            state.translated_text,
            state.segments,
            state.translations,
            placeholder_segments=state.render_base_segments,
        )
        state.segment_excerpts = build_segment_excerpts(
            state.translated_text,
            state.segments,
            state.translations,
            state.segment_lines,
            placeholder_segments=state.render_base_segments,
        )
        state.segment_source_excerpts = build_segment_source_excerpts(
            state.segments,
        )
