"""Conservative publication policy for assembled translation candidates."""

from __future__ import annotations

from dataclasses import dataclass

from ydbdoc_review.pipeline.types import PRTranslationResult, PublicationImpact
from ydbdoc_review.validation.fence_integrity import check_absolute_paths_in_fences
from ydbdoc_review.validation.final_language import check_final_en_language
from ydbdoc_review.validation.heuristics import (
    check_broken_inline_code_markup,
    check_fence_parity,
    check_heading_parity,
    check_list_tab_parity,
    check_unrestored_yfmvar_placeholders,
    run_file_heuristics_classified,
)
from ydbdoc_review.validation.include_targets import check_include_parity
from ydbdoc_review.validation.ru_source_bugs import (
    check_required_anchor_lines,
    normalize_ru_source_for_translation,
)

_REPAIRABLE_FINAL_TREE_CODES = frozenset(
    {"en_link_target", "translation_soft_keep"}
)


def _is_unrestored_marker(message: str) -> bool:
    """Return whether a heuristic is the publish-with-RED marker leak gate."""
    return message.startswith("unrestored_placeholder:")


def _has_unrestored_marker(result: PRTranslationResult) -> bool:
    """Marker leakage is publishable, but must keep the result visibly RED."""
    for run in result.pair_results:
        file_result = run.file_result
        if file_result is not None and any(
            _is_unrestored_marker(message)
            for message in file_result.heuristic_blocking
        ):
            return True
    return False


def _has_critic_execution_failure(result: PRTranslationResult) -> bool:
    """Technical critic failure keeps the candidate publishable as RED."""
    for run in result.pair_results:
        critic = (
            run.file_result.critic_unresolved
            if run.file_result is not None
            else None
        )
        if critic is not None and any(
            issue.category == "critic_execution_failed" for issue in critic.issues
        ):
            return True
    return False


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
    full_classified_set: bool = False,
) -> bool:
    """Re-run canonical heuristics; soft-keeps require the complete set."""
    normalized_source = normalize_ru_source_for_translation(source_text)
    if full_classified_set:
        return bool(
            run_file_heuristics_classified(
                source_text,
                target_text,
                normalized_source_text=normalized_source,
                source_lang=source_lang,
                target_lang="en",
                source_file=source_file,
            ).blocking
        )
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
    if result.completeness_gaps or result.publication_failure:
        return True
    # Dependency scope warnings are deliberately yellow in ordinary reports,
    # but an unresolved dependency with an explicit manual action means the
    # candidate is incomplete and must not be reported GREEN.
    if any("manual action required" in warning for warning in result.yellow_warnings):
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
        if not run.deleted and run.target_text is not None and check_final_en_language(
            run.target_text, target_lang=run.plan.target_lang
        ):
            return True
        file_result = run.file_result
        if file_result is None:
            continue
        if file_result.segment_alignment_error:
            return True
        if file_result.link_contract_issues:
            return True
        critics = (
            file_result.critic_initial,
            file_result.critic_unresolved,
        )
        if any(
            issue.category == "critic_model_refusal"
            for critic_response in critics
            if critic_response is not None
            for issue in critic_response.issues
        ):
            return True
        critic = (
            file_result.critic_unresolved
            if file_result.critic_unresolved is not None
            else file_result.critic_initial
        )
        if critic is not None and (
            critic.verdict == "blocked"
            or any(issue.severity == "blocked" for issue in critic.issues)
        ) and not (
            critic.issues
            and all(
                issue.category == "critic_execution_failed"
                for issue in critic.issues
            )
        ):
            return True
        repairable_messages = repairable_messages_by_path.get(
            run.plan.target_path.replace("\\", "/"), set()
        )
        if any(
            message not in repairable_messages
            and not _is_unrestored_marker(message)
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
                    full_classified_set=run.soft_keep_reason is not None,
                )
            ):
                return True
        if run.target_text and run.plan.target_lang.lower() in {"en", "english"}:
            if check_unrestored_yfmvar_placeholders(run.target_text, target_lang="en"):
                return True
            if check_broken_inline_code_markup(run.target_text, target_lang="en"):
                return True
    # Navigation QA findings are addressable RED. Invalid YAML, residual
    # Cyrillic and evidence for different candidate bytes remain unsafe.
    return any(
        (
            nav.verdict == "blocked"
            and any(w.split(":", 1)[0] == "invalid_yaml" for w in nav.warnings)
        )
        or any(
            message.startswith(("en_language:", "report_checkout_mismatch:"))
            for message in [*nav.warnings, *nav.heuristic_blocking]
        )
        or (nav.target_text is not None and bool(check_final_en_language(nav.target_text)))
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
    repairable = repairable or _has_unrestored_marker(result)
    repairable = repairable or _has_critic_execution_failure(result)
    repairable = repairable or any(
        navigation.warnings or navigation.heuristic_blocking
        for navigation in result.navigation_results
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
