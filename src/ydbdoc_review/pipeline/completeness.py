"""Ensure translation PR covers every RU artifact from the source PR diff."""

from __future__ import annotations

from pathlib import PurePosixPath

from ydbdoc_review.navigation.paths import is_navigation_yaml
from ydbdoc_review.pipeline.pairs import ChangeKind, counterpart, is_docs_markdown
from ydbdoc_review.pipeline.types import PRTranslationResult


def _norm(path: str) -> str:
    return path.replace("\\", "/")


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
    committed = committed_en_paths(result)
    return sorted(expected - committed)


def gap_label(en_path: str) -> str:
    """Human-readable reason for a completeness gap."""
    name = PurePosixPath(en_path).name.lower()
    if is_navigation_yaml(en_path):
        return f"{en_path} — navigation merge не выполнен"
    return f"{en_path} — не переведён"
