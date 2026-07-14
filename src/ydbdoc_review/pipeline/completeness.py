"""Ensure translation PR covers every RU artifact from the source PR diff."""

from __future__ import annotations

from ydbdoc_review.navigation.paths import is_navigation_yaml
from ydbdoc_review.pipeline.pairs import ChangeKind, counterpart, is_docs_markdown
from ydbdoc_review.pipeline.types import PRTranslationResult


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
    """EN paths written or planned with output text in this run."""
    paths: set[str] = set()
    for run in result.pair_results:
        if run.deleted or run.skipped or run.error:
            continue
        if run.target_text is not None:
            paths.add(run.plan.target_path)
    for nav in result.navigation_results:
        if nav.error:
            continue
        if nav.target_text is not None:
            paths.add(nav.en_path)
    return paths


def completeness_gaps(
    changes: list[tuple[str, ChangeKind]],
    result: PRTranslationResult,
    *,
    docs_root: str = "ydb/docs",
) -> list[str]:
    """Sorted EN mirror paths missing from the translation run."""
    expected = expected_en_mirrors(changes, docs_root=docs_root)
    expected -= bilingual_en_mirrors(changes, docs_root=docs_root)
    expected = {
        path
        for path in expected
        if not is_misresolved_shared_include_mirror(path, docs_root=docs_root)
    }
    committed = committed_en_paths(result)
    return sorted(expected - committed)


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
