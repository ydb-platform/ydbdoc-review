"""Harness pipeline steps — one responsibility per stage."""

from __future__ import annotations

from typing import Protocol

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.critic_verdict import compute_critic_verdict
from ydbdoc_review.harness.render import finalize_en_target, render_with_translations
from ydbdoc_review.harness.render import remap_translations_by_position
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import compose_file_verdict, gate_round_trip
from ydbdoc_review.reporting.locations import build_segment_excerpts, build_segment_line_map
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import normalize_target_segments_to_source
from ydbdoc_review.translation.critic import (
    apply_critic_fixes,
    run_critic as run_critic_pass,
    run_verify,
)
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.translation.critic_retranslate import (
    issues_by_segment_id,
    retranslate_segments_with_critic_feedback,
)
from ydbdoc_review.translation.translator import translate_segments
from ydbdoc_review.validation.heuristics import run_file_heuristics_classified
from ydbdoc_review.validation.placeholder_drift import (
    drop_spurious_placeholder_issues,
    filter_critic_response,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation


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
    )
    if ctx.target_lang.lower() in {"en", "english"}:
        state.translated_text = finalize_en_target(
            state.translated_text,
            state.source_text,
            client=ctx.client,
            glossary=ctx.glossary,
            file_path=state.file_path,
            source_lang=ctx.source_lang,
            target_lang=ctx.target_lang,
            prompt_version=ctx.prompt_version,
        )


def _unresolved_retry_segment_ids(state: FileRunState) -> set[str]:
    if state.critic_unresolved is None:
        return set()
    return {
        issue.segment_id
        for issue in state.critic_unresolved.issues
        if issue.segment_id
    }


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
    actionable_issues = drop_spurious_placeholder_issues(
        state.critic_initial.issues, state.segments, state.translations
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
    )
    if ctx.target_lang.lower() in {"en", "english"}:
        state.translated_text = finalize_en_target(
            state.translated_text,
            state.fence_reference_text,
            client=ctx.client,
            glossary=ctx.glossary,
            file_path=state.file_path,
            source_lang=ctx.source_lang,
            target_lang=ctx.target_lang,
            prompt_version=ctx.prompt_version,
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
        skipped=state.critic_skipped,
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
            seg.id: " › ".join(seg.path) if seg.path else "(начало документа)"
            for seg in state.segments
        }
        if not state.segments:
            state.stopped_early = True
            state.translated_text = state.existing_target_text or state.source_text
            return
        state.render_base_doc = state.source_doc
        state.render_base_segments = state.segments
        state.fence_reference_text = state.source_text


class TranslateStep:
    name = "translate"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if state.mode != "translate":
            return
        assert state.source_doc is not None
        state.translations = translate_segments(
            state.segments,
            ctx.client,
            ctx.glossary,
            file_path=state.file_path,
            source_lang=ctx.source_lang,
            target_lang=ctx.target_lang,
            max_chars=ctx.batch_chars,
            prompt_version=ctx.prompt_version,
            cache=ctx.cache,
            max_parallel_batches=ctx.parallel,
            manual_actions=state.manual_actions,
        )
        _render_translated_from_source(state, ctx)


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
            target_segments = normalize_target_segments_to_source(
                state.segments, target_segments
            )
            state.render_base_doc = target_doc
            state.render_base_segments = target_segments
            state.fence_reference_text = state.existing_target_text


class RoundTripStep:
    name = "round_trip"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        del ctx
        state.translations, state.segment_alignment_error = gate_round_trip(
            state.segments, state.translated_text
        )


class CriticLoopStep:
    name = "critic_loop"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if not ctx.enable_critic or state.segment_alignment_error:
            return
        run_critic_loop(state, ctx)


class CriticFeedbackRetryStep:
    """Re-translate segments with unresolved critic issues (translate mode only)."""

    name = "critic_feedback_retry"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if state.mode != "translate" or not ctx.enable_critic:
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
        state.heuristics = run_file_heuristics_classified(
            state.raw_source_text,
            state.translated_text,
            normalized_source_text=state.source_text,
            source_lang=ctx.source_lang,
            target_lang=ctx.target_lang,
        )


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
