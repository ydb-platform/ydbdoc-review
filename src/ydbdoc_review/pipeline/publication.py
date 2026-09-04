"""Conservative publication policy for assembled translation candidates."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.pipeline.types import PRTranslationResult, PublicationImpact
from ydbdoc_review.validation.fence_integrity import check_absolute_paths_in_fences
from ydbdoc_review.validation.heuristics import (
    check_broken_inline_code_markup,
    check_cyrillic_in_en,
    check_cyrillic_in_en_all_fences,
    check_fence_parity,
    check_heading_parity,
    check_list_tab_parity,
    check_unrestored_placeholders,
    check_unrestored_yfmvar_placeholders,
)
from ydbdoc_review.validation.href_parity import check_href_parity
from ydbdoc_review.validation.include_targets import check_include_parity
from ydbdoc_review.validation.ru_source_bugs import (
    check_required_anchor_lines,
    normalize_ru_source_for_translation,
)

_REPAIRABLE_FINAL_TREE_CODES = frozenset(
    {"en_link_target", "translation_soft_keep"}
)


@dataclass(frozen=True)
class ClassifiedPublicationBlockers:
    """Typed publication view of every merge-blocking result signal."""

    incomplete: bool = False
    unsafe: bool = False
    repairable_final_tree: bool = False

    @property
    def any(self) -> bool:
        return self.incomplete or self.unsafe or self.repairable_final_tree


def _has_direct_structural_failure(
    source_text: str,
    target_text: str,
    *,
    source_lang: str,
    source_file: str,
    retained_soft_keep: bool = False,
) -> bool:
    """Re-run typed structural validators instead of parsing heuristic messages."""
    normalized_source = normalize_ru_source_for_translation(source_text)
    structural_failure = any(
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
    if structural_failure or not retained_soft_keep:
        return structural_failure
    # A retained old EN target did not pass the current translation harness, so
    # close the two remaining typed integrity gaps here. Normal translated runs
    # keep relying on their ordinary file-result evidence.
    return bool(
        check_href_parity(normalized_source, target_text)
        or check_cyrillic_in_en(target_text, target_lang="en")
        or check_cyrillic_in_en_all_fences(target_text, target_lang="en")
    )


def _is_incomplete(result: PRTranslationResult) -> bool:
    if result.completeness_gaps or result.publication_failure:
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
    return any(nav.error for nav in result.navigation_results)


def _is_unsafe(result: PRTranslationResult) -> bool:
    repairable_messages_by_path: dict[str, set[str]] = {}
    unsupported_final_tree_blocker = False
    for blocker in result.final_tree_blockers:
        if blocker.code not in _REPAIRABLE_FINAL_TREE_CODES:
            unsupported_final_tree_blocker = True
            continue
        repairable_messages_by_path.setdefault(
            blocker.path.replace("\\", "/"), set()
        ).add(blocker.message)
    if unsupported_final_tree_blocker:
        return True
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
        critic = (
            file_result.critic_unresolved
            if file_result.critic_unresolved is not None
            else file_result.critic_initial
        )
        if critic is not None and (
            critic.verdict == "blocked"
            or any(issue.severity == "blocked" for issue in critic.issues)
        ):
            return True
        repairable_messages = repairable_messages_by_path.get(
            run.plan.target_path.replace("\\", "/"), set()
        )
        if any(
            message not in repairable_messages
            for message in file_result.heuristic_blocking
        ):
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
                    retained_soft_keep=run.soft_keep_reason is not None,
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


def classify_publication_blockers(
    result: PRTranslationResult,
) -> ClassifiedPublicationBlockers:
    """Classify all blocker evidence with an explicit repairable allowlist."""
    incomplete = _is_incomplete(result)
    unsafe = _is_unsafe(result)
    repairable = bool(result.final_tree_blockers) and all(
        blocker.code in _REPAIRABLE_FINAL_TREE_CODES
        for blocker in result.final_tree_blockers
    )
    return ClassifiedPublicationBlockers(
        incomplete=incomplete,
        unsafe=unsafe,
        repairable_final_tree=repairable,
    )


def evaluate_publication_impact(result: PRTranslationResult) -> PublicationImpact:
    """Return the typed publication decision; every WITHHOLD outranks publish."""
    blockers = classify_publication_blockers(result)
    if blockers.incomplete:
        return PublicationImpact.WITHHOLD_INCOMPLETE
    if blockers.unsafe:
        return PublicationImpact.WITHHOLD_UNSAFE
    if blockers.repairable_final_tree:
        return PublicationImpact.PUBLISH_RED
    return PublicationImpact.PUBLISH_NORMAL


def refresh_publication_impact(result: PRTranslationResult) -> PublicationImpact:
    """Evaluate and persist the decision on the PR-level result."""
    result.publication_impact = evaluate_publication_impact(result)
    return result.publication_impact
