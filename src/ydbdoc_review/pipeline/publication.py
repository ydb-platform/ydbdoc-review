"""Conservative publication policy for assembled translation candidates."""

from __future__ import annotations

from ydbdoc_review.pipeline.types import PRTranslationResult, PublicationImpact
from ydbdoc_review.validation.fence_integrity import check_absolute_paths_in_fences
from ydbdoc_review.validation.heuristics import (
    check_broken_inline_code_markup,
    check_fence_parity,
    check_heading_parity,
    check_list_tab_parity,
    check_unrestored_placeholders,
    check_unrestored_yfmvar_placeholders,
)
from ydbdoc_review.validation.include_targets import check_include_parity
from ydbdoc_review.validation.ru_source_bugs import (
    check_required_anchor_lines,
    normalize_ru_source_for_translation,
)


def _has_direct_structural_failure(
    source_text: str,
    target_text: str,
    *,
    source_lang: str,
    source_file: str,
) -> bool:
    """Re-run typed structural validators instead of parsing heuristic messages."""
    normalized_source = normalize_ru_source_for_translation(source_text)
    return any(
        (
            check_fence_parity(
                normalized_source,
                target_text,
                source_lang=source_lang,
            ),
            check_absolute_paths_in_fences(normalized_source, target_text),
            check_required_anchor_lines(source_text, target_text),
            check_heading_parity(normalized_source, target_text),
            check_list_tab_parity(normalized_source, target_text),
            check_include_parity(
                source_text,
                target_text,
                source_file=source_file,
            ),
        )
    )


def _is_incomplete(result: PRTranslationResult) -> bool:
    if result.completeness_gaps:
        return True
    for run in result.pair_results:
        if run.error:
            return True
        if (
            run.plan.action not in {"skip", "delete_en"}
            and not run.skipped
            and not run.deleted
            and run.target_text is None
        ):
            return True
        file_result = run.file_result
        if file_result is None:
            continue
        if file_result.manual_actions:
            return True
        if any(
            str(warning).startswith("translate_soft_keep:")
            for warning in file_result.heuristic_warnings
        ):
            return True
    return any(nav.error for nav in result.navigation_results)


def _is_unsafe(result: PRTranslationResult) -> bool:
    for run in result.pair_results:
        if run.validation_issues:
            return True
        file_result = run.file_result
        if file_result is None:
            continue
        if file_result.segment_alignment_error:
            return True
        if file_result.link_contract_issues:
            return True
        if run.source_text is not None and run.target_text is not None:
            source_lang = run.plan.source_lang.lower()
            target_lang = run.plan.target_lang.lower()
            if (
                source_lang in {"ru", "russian"}
                and target_lang in {"en", "english"}
                and _has_direct_structural_failure(
                    run.source_text,
                    run.target_text,
                    source_lang=source_lang,
                    source_file=run.plan.source_path,
                )
            ):
                return True
        if run.target_text and run.plan.target_lang.lower() in {"en", "english"}:
            if check_unrestored_placeholders(run.target_text, target_lang="en"):
                return True
            if check_unrestored_yfmvar_placeholders(run.target_text, target_lang="en"):
                return True
            if check_broken_inline_code_markup(run.target_text, target_lang="en"):
                return True
    return any(
        nav.verdict == "blocked" and bool(nav.warnings)
        for nav in result.navigation_results
    )


def evaluate_publication_impact(result: PRTranslationResult) -> PublicationImpact:
    """Return the typed publication decision; every WITHHOLD outranks publish."""
    if _is_incomplete(result):
        return PublicationImpact.WITHHOLD_INCOMPLETE
    if _is_unsafe(result):
        return PublicationImpact.WITHHOLD_UNSAFE
    if result.final_tree_blockers:
        return PublicationImpact.PUBLISH_RED
    return PublicationImpact.PUBLISH_NORMAL


def refresh_publication_impact(result: PRTranslationResult) -> PublicationImpact:
    """Evaluate and persist the decision on the PR-level result."""
    result.publication_impact = evaluate_publication_impact(result)
    return result.publication_impact
