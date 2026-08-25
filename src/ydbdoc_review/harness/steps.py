"""Harness pipeline steps — one responsibility per stage."""

from __future__ import annotations

import logging
from typing import Protocol

from ydbdoc_review.harness.context import HarnessContext
from ydbdoc_review.harness.critic_verdict import compute_critic_verdict
from ydbdoc_review.harness.render import (
    finalize_en_target,
    remap_translations_by_position,
    render_with_translations,
)
from ydbdoc_review.harness.state import FileRunState
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.qa import (
    compose_file_verdict,
    gate_round_trip,
    partial_align_translations_from_target,
)
from ydbdoc_review.reporting.locations import (
    build_segment_excerpts,
    build_segment_line_map,
    build_segment_source_excerpts,
)
from ydbdoc_review.segmentation.extractor import extract_segments
from ydbdoc_review.segmentation.placeholder_align import normalize_target_segments_to_source
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
from ydbdoc_review.translation.differential import (
    DifferentialTranslationConfig,
    low_magnitude_patch_has_anchors,
    patch_en_with_added_translations,
    patch_en_with_source_added_autotitle_lines,
    prepare_differential_seed,
    slim_pending_for_low_magnitude_patch,
)
from ydbdoc_review.translation.file_profiles import is_glossary_file
from ydbdoc_review.translation.schemas import CriticResponse
from ydbdoc_review.translation.translator import translate_segments
from ydbdoc_review.validation.heuristics import (
    _classify_heuristic,
    check_fence_parity,
    check_list_tab_parity,
    run_file_heuristics_classified,
)
from ydbdoc_review.validation.include_targets import repair_missing_includes
from ydbdoc_review.validation.markdown_layout import repair_generated_markdown_layout
from ydbdoc_review.validation.placeholder_drift import (
    drop_spurious_placeholder_issues,
    filter_critic_response,
)
from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation
from ydbdoc_review.validation.structural_repair import repair_en_structure_from_ru

logger = logging.getLogger(__name__)


def _en_structure_safe_for_low_magnitude_patch(ru_text: str, en_text: str) -> bool:
    """Refuse EN splice when fence/tab-container counts already diverge (§6.193).

    Low-magnitude patch keeps the existing EN tree. If EN is missing SDK language
    panes (RU 6 fences vs EN 2), splicing paragraphs cannot restore them — force
    full reconstruct from RU instead (#37673 / #50684).
    """
    if check_fence_parity(ru_text, en_text):
        return False
    if check_list_tab_parity(ru_text, en_text):
        return False
    # Pane count: same number of ``{% list tabs %}`` can still hide missing
    # ``- Go`` / ``- Rust`` children.
    import re

    from ydbdoc_review.parsing.ast_types import YfmIf, YfmTab
    from ydbdoc_review.segmentation.extractor import (
        DEFAULT_TAB_TITLE_WHITELIST,
        extract_segments,
    )
    from ydbdoc_review.validation.homoglyphs import normalize_confusable_cyrillic

    def pane_titles(text: str) -> list[str]:
        doc = parse_markdown(text)
        titles: list[str] = []

        def walk(blocks: list) -> None:
            for block in blocks:
                if isinstance(block, YfmTab):
                    titles.append("".join(getattr(node, "content", "") for node in block.title))
                if isinstance(block, YfmIf):
                    for branch in block.branches:
                        walk(branch.children)
                    continue
                children = getattr(block, "children", None)
                if children:
                    walk(children)

        walk(doc.children)
        return titles

    def language_key(title: str) -> str | None:
        raw = title.strip()
        normalized = normalize_confusable_cyrillic(raw).lower()
        if normalized in DEFAULT_TAB_TITLE_WHITELIST:
            return normalized
        match = re.match(
            r"^(.+?)\s*\((?:alternative|альтернативный)\)$",
            raw,
            flags=re.IGNORECASE,
        )
        if match:
            base = normalize_confusable_cyrillic(match.group(1).strip()).lower()
            if base in DEFAULT_TAB_TITLE_WHITELIST:
                return f"{base} (alternative)"
        return None

    ru_titles = pane_titles(ru_text)
    en_titles = pane_titles(en_text)
    if len(ru_titles) != len(en_titles):
        return False
    if len(extract_segments(parse_markdown(ru_text))) != len(
        extract_segments(parse_markdown(en_text))
    ):
        return False
    # Technical language pane names are structure, not prose. A legacy EN
    # ``With#`` opposite RU ``С#`` means the old target is unsafe to splice.
    for ru_title, en_title in zip(ru_titles, en_titles, strict=True):
        ru_key = language_key(ru_title)
        if ru_key is not None and language_key(en_title) != ru_key:
            return False
    return True


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
            out_warnings=state.finalize_warnings,
            en_toc_reachable=ctx.en_toc_reachable,
            # Verify uses EN as the fence-body authority, but layout repair must
            # still restore the raw RU marker/container structure (#50741).
            layout_source_text=state.source_text,
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
            out_warnings=state.finalize_warnings,
            en_toc_reachable=ctx.en_toc_reachable,
            layout_source_text=state.source_text,
            protected_source_text=state.source_text,
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
        cfg = ctx.config.translation
        if state.existing_target_text and state.base_source_text:
            deterministic_index_patch = patch_en_with_source_added_autotitle_lines(
                state.base_source_text,
                state.source_text,
                state.existing_target_text,
            )
            if deterministic_index_patch is not None:
                state.translations = {}
                state.translated_text = deterministic_index_patch
                state.stopped_early = True
                state.differential_meta = {
                    "mode": "differential",
                    "reason": "source_added_autotitle_lines",
                    "deterministic_autotitle_patch": True,
                }
                logger.info("Deterministic autotitle-list insertion: preserve existing EN bytes")
                return
        diff_cfg = DifferentialTranslationConfig.from_env_and_defaults(
            enabled=cfg.differential_enabled,
            stale_days_threshold=cfg.differential_stale_days,
            change_magnitude_threshold=cfg.differential_change_magnitude,
            min_en_file_ratio=cfg.differential_min_en_ratio,
        )
        strategy, seeded, pending = prepare_differential_seed(
            pr_segments=state.segments,
            ru_pr_text=state.source_text,
            en_current_text=state.existing_target_text,
            ru_base_text=state.base_source_text,
            config=diff_cfg,
        )
        patch_analysis = None
        semantic_noop = False
        if (
            strategy.mode == "differential"
            and state.existing_target_text
            and state.base_source_text
        ):
            if not _en_structure_safe_for_low_magnitude_patch(
                state.source_text, state.existing_target_text
            ):
                logger.info(
                    "Skip low-magnitude EN patch: fence/tab structure diverges "
                    "from RU — full reconstruct (§6.193)"
                )
            else:
                slim = slim_pending_for_low_magnitude_patch(
                    pending,
                    ru_base_text=state.base_source_text,
                    ru_pr_text=state.source_text,
                )
                if slim is not None:
                    slim_pending, slim_analysis = slim
                    change_ids = (
                        slim_analysis.added_segment_ids | slim_analysis.modified_segment_ids
                    )
                    if not change_ids and not slim_analysis.removed_blocks:
                        semantic_noop = True
                        pending = []
                        logger.info("Semantic no-op RU diff: preserve existing EN exactly (§6.217)")
                    patch_has_anchors = low_magnitude_patch_has_anchors(
                        state.segments, slim_analysis
                    )
                    if patch_has_anchors and not semantic_noop:
                        pending, patch_analysis = slim_pending, slim_analysis
                        logger.info(
                            "Low-magnitude patch: LLM %d added/modified segment(s) "
                            "(magnitude=%.2f); splice into existing EN (no reconstruct)",
                            len(pending),
                            patch_analysis.change_magnitude,
                        )
                    elif change_ids:
                        logger.info(
                            "Skip low-magnitude EN patch: changed segment has no "
                            "explicit heading anchor — full reconstruct (§6.213)"
                        )
        state.differential_meta = {
            "mode": strategy.mode,
            "reason": strategy.reason,
            "seeded": len(seeded),
            "pending": len(pending),
            "low_magnitude_patch": patch_analysis is not None,
            "semantic_noop": semantic_noop,
            **strategy.config,
        }
        if patch_analysis is not None:
            state.differential_meta["change_magnitude"] = patch_analysis.change_magnitude
        if strategy.mode == "skip":
            state.translations = {}
            state.translated_text = state.existing_target_text or state.source_text
            state.stopped_early = True
            return
        if semantic_noop and state.existing_target_text:
            state.translations = {}
            state.translated_text = state.existing_target_text
            state.stopped_early = True
            return

        # Low-magnitude: never reconstruct from RU. Keep EN and splice only
        # added/modified translations (pending may be empty → unchanged EN).
        if patch_analysis is not None and state.existing_target_text:
            state.translations = {}
            to_llm = list(pending)
            if not to_llm:
                change_ids = patch_analysis.added_segment_ids | patch_analysis.modified_segment_ids
                to_llm = [s for s in state.segments if s.id in change_ids]
            if to_llm:
                state.translations = translate_segments(
                    to_llm,
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
            state.translated_text = patch_en_with_added_translations(
                state.existing_target_text,
                pr_segments=state.segments,
                translations=state.translations,
                added_segment_ids=patch_analysis.added_segment_ids,
                modified_segment_ids=patch_analysis.modified_segment_ids,
            )
            if ctx.target_lang.lower() in {"en", "english"}:
                _apply_en_structural_repair(state, ctx)
                state.translated_text = finalize_en_target(
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
                )
            return

        state.translations = dict(seeded)
        if pending:
            new_trans = translate_segments(
                pending,
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
            state.translations.update(new_trans)
        elif not state.translations:
            # Full path with empty pending should not happen; safety net.
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


# Full verify realign retranslates every RU segment. Glossary-scale pages
# (400+) hang CI on Eliza timeouts (#49578 / #45667). Cap and leave 🔴.
_VERIFY_REALIGN_MAX_SEGMENTS = 80
_PARTIAL_VERIFY_REALIGN_MAX_PENDING = 80


def _apply_en_structural_repair(state: FileRunState, ctx: HarnessContext) -> None:
    if ctx.target_lang.lower() not in {"en", "english"}:
        return
    if not state.source_text or not state.translated_text:
        return
    repaired = repair_en_structure_from_ru(state.translated_text, state.source_text)
    if repaired != state.translated_text:
        state.translated_text = repaired
        state.finalize_warnings.append(
            "structural_repair: restored heading anchors / signature blocks from RU"
        )


def _try_partial_verify_realign(state: FileRunState, ctx: HarnessContext) -> bool:
    """Translate only RU segments missing from EN (§6.191 / #49957)."""
    assert state.source_doc is not None
    seeded = partial_align_translations_from_target(
        state.segments,
        state.translated_text,
        require_trustworthy=False,
    )
    pending = [seg for seg in state.segments if seg.id not in seeded]
    if not pending or len(pending) > _PARTIAL_VERIFY_REALIGN_MAX_PENDING:
        return False
    logger.info(
        "partial verify realign for %s: translate %d gap segment(s)",
        state.file_path,
        len(pending),
    )
    new_trans = translate_segments(
        pending,
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
    state.translations = {**seeded, **new_trans}
    state.render_base_doc = state.source_doc
    state.render_base_segments = state.segments
    state.fence_reference_text = state.source_text
    _render_translated_from_source(state, ctx)
    state.finalize_warnings.append(
        f"verify_realign_partial: translated {len(pending)} gap segment(s) from RU"
    )
    return True


class RoundTripStep:
    name = "round_trip"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
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
        if not state.segment_alignment_error or state.mode != "verify":
            return
        # Structural RU/EN mismatch (YFM↔GFM rows, condensed sections, …):
        # rebuild EN from RU so critic/heuristics can finish (§6.147).
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
        if is_glossary_file(state.file_path):
            logger.info("Glossary verify: skip structural alignment gate (§6.186)")
            state.finalize_warnings.append(
                "glossary_verify_alignment_skipped: structural RU/EN "
                "segment drift ignored on glossary hub"
            )
            state.segment_alignment_error = None
            return
        if _try_partial_verify_realign(state, ctx):
            state.translations, state.segment_alignment_error = gate_round_trip(
                state.segments, state.translated_text
            )
            if not state.segment_alignment_error:
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
        state.finalize_warnings.append(
            "verify_realign: rebuilt EN from RU due to segment alignment mismatch"
        )
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
        state.render_base_doc = state.source_doc
        state.render_base_segments = state.segments
        state.fence_reference_text = state.source_text
        _render_translated_from_source(state, ctx)
        state.translations, state.segment_alignment_error = gate_round_trip(
            state.segments, state.translated_text
        )


class CriticLoopStep:
    name = "critic_loop"

    def run(self, state: FileRunState, ctx: HarnessContext) -> None:
        if not ctx.enable_critic or state.segment_alignment_error:
            return
        if state.mode == "verify" and is_glossary_file(state.file_path):
            logger.info("Glossary verify: skip critic_loop (§6.188)")
            state.finalize_warnings.append(
                "glossary_verify_critic_skipped: hub page; heuristics only on verify"
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
        if state.stopped_early:
            return
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
        state.translated_text = finalize_en_target(
            state.translated_text,
            fence_ref,
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
            en_baseline_text=state.existing_target_text,
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
