"""Ensure translation PR covers every RU artifact from the source PR diff."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum

from ydbdoc_review.navigation.paths import is_navigation_yaml
from ydbdoc_review.pipeline.pairs import (
    ChangeKind,
    DocPair,
    NavigationPair,
    build_doc_pairs,
    counterpart,
    is_docs_markdown,
)
from ydbdoc_review.pipeline.types import PRTranslationResult
from ydbdoc_review.validation.href_parity import (
    check_href_parity,
    collect_internal_hrefs,
    is_href_only_change,
)


class CompletenessState(StrEnum):
    EXISTING_SATISFIED = "existing_satisfied"
    ADD_REQUIRED = "add_required"
    ADDED = "added"
    UPDATE_REQUIRED = "update_required"
    UPDATED = "updated"
    DELETE_REQUIRED = "delete_required"
    DELETED = "deleted"
    DELETE_ALREADY_SATISFIED = "delete_already_satisfied"
    SUPERSEDED_ABSENT = "superseded_absent"
    BLOCKED = "blocked"


def evaluate_completeness_states(
    result: PRTranslationResult,
    *,
    blocked_paths: set[str] | None = None,
) -> dict[str, CompletenessState]:
    blocked_paths = blocked_paths or set()
    states: dict[str, CompletenessState] = {}
    for run in result.pair_results:
        path = run.plan.target_path
        disposition = run.historical_disposition
        move_incomplete = (
            run.plan.pair.previous_en_path is not None
            and run.target_text is not None
            and run.plan.pair.previous_en_path not in run.additional_delete_paths
        )
        if path in blocked_paths or run.error or move_incomplete:
            states[path] = CompletenessState.BLOCKED
        elif run.deleted:
            states[path] = CompletenessState.DELETED
        elif disposition == "delete_already_satisfied":
            states[path] = CompletenessState.DELETE_ALREADY_SATISFIED
        elif disposition in {"superseded", "superseded_absent"}:
            states[path] = CompletenessState.SUPERSEDED_ABSENT
        elif disposition in {"already_translated", "existing_satisfied"}:
            states[path] = CompletenessState.EXISTING_SATISFIED
        elif run.target_text is not None:
            states[path] = (
                CompletenessState.ADDED
                if run.plan.provenance.value == "current_ru_missing_en"
                else CompletenessState.UPDATED
            )
        else:
            states[path] = CompletenessState.BLOCKED
    return states


def completeness_state_gaps(states: dict[str, CompletenessState]) -> list[str]:
    return sorted(path for path, state in states.items() if state is CompletenessState.BLOCKED)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def is_misresolved_shared_include_mirror(
    en_path: str,
    *,
    docs_root: str = "ydb/docs",
) -> bool:
    """True when ``en_path`` is a false RU↔EN mirror of ``docs/_includes/…``.

    Recipe pages reference shared snippets as ``../../../_includes/go/…`` which
    resolves to nonexistent ``docs/{ru,en}/_includes/…`` instead of language-
    neutral ``docs/_includes/…`` (PR #43997).
    """
    ru_path = counterpart(en_path, docs_root)
    if ru_path is None:
        return False
    root = docs_root.strip("/")
    ru_norm = _norm(ru_path)
    return ru_norm.startswith(f"{root}/ru/_includes/") or ru_norm.startswith(
        f"{root}/en/_includes/"
    )


def bilingual_en_mirrors(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> set[str]:
    """EN paths where both RU and EN mirrors changed in the source PR (§6.76)."""
    ru_touched: set[str] = set()
    en_touched: set[str] = set()
    root = docs_root.strip("/")

    for raw_path, kind in changes:
        if kind == "deleted":
            continue
        path = _norm(raw_path)
        if path.startswith(f"{root}/ru/"):
            if not is_docs_markdown(path, docs_root) and not is_navigation_yaml(path):
                continue
            en_path = counterpart(path, docs_root)
            if en_path is not None:
                ru_touched.add(en_path)
        elif path.startswith(f"{root}/en/"):
            if not is_docs_markdown(path, docs_root) and not is_navigation_yaml(path):
                continue
            en_touched.add(path)
    return ru_touched & en_touched


def expected_en_mirrors(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> set[str]:
    """EN paths that ``doc_translate`` should produce for this source PR."""
    expected: set[str] = set()
    root = docs_root.strip("/")

    for raw_path, kind in changes:
        if kind == "deleted":
            continue
        path = _norm(raw_path)
        if not path.startswith(f"{root}/ru/"):
            continue
        if not is_docs_markdown(path, docs_root) and not is_navigation_yaml(path):
            continue
        en_path = counterpart(path, docs_root)
        if en_path is not None:
            expected.add(en_path)
    return expected


def committed_en_paths(result: PRTranslationResult) -> set[str]:
    """EN paths written or intentionally satisfied in this run.

    Navigation merge may return ``target_text=None`` with ``verdict=ok`` when the
    merged EN equals the upstream baseline (§6.141 no-op). That still satisfies
    the completeness gate: there is nothing to push for that file (§6.144).
    """
    paths: set[str] = set()
    for run in result.pair_results:
        if run.error:
            continue
        if run.deleted:
            paths.add(run.plan.target_path)
            continue
        paths.update(run.additional_delete_paths)
        if run.skipped and run.historical_disposition in {
            "already_translated",
            "existing_satisfied",
            "superseded",
            "superseded_absent",
            "delete_already_satisfied",
        }:
            paths.add(run.plan.target_path)
            continue
        if run.target_text is not None:
            paths.add(run.plan.target_path)
    for nav in result.navigation_results:
        if nav.error:
            continue
        if nav.target_text is not None or nav.verdict == "ok":
            paths.add(nav.en_path)
    return paths


def completeness_gaps(
    changes: list[tuple[str, ChangeKind]],
    result: PRTranslationResult,
    *,
    docs_root: str = "ydb/docs",
    bidirectional: bool = False,
    logical_pairs: list[DocPair] | None = None,
) -> list[str]:
    """Sorted opposite-locale targets missing from the translation run."""
    expected: set[str]
    if logical_pairs is not None:
        expected = set()
        for pair in logical_pairs:
            if pair.ru_changed and pair.en_changed:
                continue
            if pair.ru_changed:
                expected.add(pair.en_path)
            elif pair.en_changed:
                expected.add(pair.ru_path)
    elif bidirectional:
        expected = set()
        for pair in build_doc_pairs(changes, docs_root=docs_root):
            if pair.ru_changed and pair.en_changed:
                continue
            if pair.ru_changed:
                expected.add(pair.en_path)
            elif pair.en_changed:
                expected.add(pair.ru_path)
    else:
        expected = expected_en_mirrors(changes, docs_root=docs_root)
        expected -= bilingual_en_mirrors(changes, docs_root=docs_root)
    expected = {
        path
        for path in expected
        if not is_misresolved_shared_include_mirror(path, docs_root=docs_root)
    }
    committed = committed_en_paths(result)
    return sorted(expected - committed)


def translation_pr_scope_gaps(
    expected_pairs: list[DocPair],
    expected_nav_pairs: list[NavigationPair],
    translation_changes: list[tuple[str, ChangeKind]],
    *,
    already_satisfied: frozenset[str] | None = None,
) -> list[str]:
    """Expected source-scope EN paths absent from a translation PR diff.

    This is deliberately independent of the critic result: a critic cannot
    approve a file it was never given.  ``supplement_only`` navigation files are
    context for merging and are not required in the resulting commit.
    """
    changed = {_norm(path) for path, _ in translation_changes}
    expected = {pair.en_path for pair in expected_pairs}
    expected.update(nav.en_path for nav in expected_nav_pairs if not nav.supplement_only)
    expected -= already_satisfied or frozenset()
    return sorted(expected - changed)


def href_only_source_noop_satisfied(
    source_base: str | None,
    source_head: str | None,
    current_ru: str | None,
    current_en: str | None,
) -> bool:
    """Whether a historical RU snapshot is already superseded/satisfied in main.

    A later RU move can supersede the source PR before its translation runs.
    In that case forcing historical content into EN can restore deleted sections
    or create unreachable links (#50976). A superseded snapshot is out of scope;
    a still-current href-only edit is covered only when RU/EN links match.
    """
    if source_head is None or current_ru is None or current_en is None:
        return False
    source_was_href_only = is_href_only_change(source_base, source_head)
    if not source_was_href_only:
        return False
    source_was_superseded = source_head != current_ru
    if source_was_superseded:
        base_hrefs = Counter(collect_internal_hrefs(source_base or ""))
        head_hrefs = Counter(collect_internal_hrefs(source_head))
        introduced = head_hrefs - base_hrefs
        current_hrefs = Counter(collect_internal_hrefs(current_ru))
        if introduced and not (introduced & current_hrefs):
            # The exact operation is superseded, but current RU/EN still must
            # form a valid pair before the source path is considered complete.
            return not check_href_parity(current_ru, current_en)
        return not check_href_parity(current_ru, current_en)
    return not check_href_parity(current_ru, current_en)


def gap_label(en_path: str, *, docs_root: str = "ydb/docs") -> str:
    """Human-readable reason for a completeness gap."""
    if is_misresolved_shared_include_mirror(en_path, docs_root=docs_root):
        return (
            f"{en_path} — ложное EN-зеркало общего snippet "
            f"`{docs_root}/_includes/…` (не переводится; путь include в recipe)"
        )
    if is_navigation_yaml(en_path):
        return f"{en_path} — navigation merge не выполнен"
    return f"{en_path} — не переведён"
