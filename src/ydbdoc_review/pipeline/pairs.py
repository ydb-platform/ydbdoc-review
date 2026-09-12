"""RU/EN doc path pairing for ydb/docs mirror layout."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from ydbdoc_review.navigation.paths import is_navigation_yaml

ChangeKind = Literal["added", "modified", "deleted"]


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def is_docs_ru_navigation(path: str, docs_root: str) -> bool:
    """True for changed ``docs/ru/…`` Diplodoc toc/redirect YAML."""
    p = _norm(path)
    root = docs_root.strip("/")
    if not p.startswith(f"{root}/ru/"):
        return False
    return is_navigation_yaml(p)


def is_docs_en_navigation(path: str, docs_root: str) -> bool:
    """True for changed ``docs/en/…`` Diplodoc toc/redirect YAML."""
    p = _norm(path)
    root = docs_root.strip("/")
    if not p.startswith(f"{root}/en/"):
        return False
    return is_navigation_yaml(p)


def is_language_neutral_docs_path(path: str, docs_root: str) -> bool:
    """True for assets outside the ``docs/ru`` / ``docs/en`` mirror trees.

    Examples: ``ydb/docs/_includes/…`` (repo-root snippets), images, binaries.
    Locale-specific fragments live under ``docs/ru/…/_includes/`` and are translated.
    """
    p = _norm(path)
    root = docs_root.strip("/")
    if p.startswith(f"{root}/ru/") or p.startswith(f"{root}/en/"):
        return False
    return p.startswith(f"{root}/")


def is_docs_markdown(path: str, docs_root: str) -> bool:
    """True for mirrored ``docs/ru/…`` or ``docs/en/…`` ``.md`` pages and includes."""
    p = _norm(path)
    if not p.endswith(".md"):
        return False
    if is_language_neutral_docs_path(path, docs_root):
        return False
    root = docs_root.strip("/")
    return p.startswith(f"{root}/ru/") or p.startswith(f"{root}/en/")


def locale_of(path: str, docs_root: str) -> str | None:
    p = _norm(path)
    root = docs_root.strip("/")
    if p.startswith(f"{root}/ru/"):
        return "ru"
    if p.startswith(f"{root}/en/"):
        return "en"
    return None


def counterpart(path: str, docs_root: str) -> str | None:
    """Map ``docs/ru/X`` ↔ ``docs/en/X``."""
    p = _norm(path)
    root = docs_root.strip("/")
    if p.startswith(f"{root}/ru/"):
        rest = p[len(f"{root}/ru/") :]
        return f"{root}/en/{rest}"
    if p.startswith(f"{root}/en/"):
        rest = p[len(f"{root}/en/") :]
        return f"{root}/ru/{rest}"
    return None


@dataclass(frozen=True)
class DocPair:
    """Mirrored RU/EN paths with PR change flags."""

    ru_path: str
    en_path: str
    ru_changed: bool = False
    en_changed: bool = False
    ru_deleted: bool = False
    en_deleted: bool = False


def build_doc_pairs(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> list[DocPair]:
    """Build unique RU/EN pairs from a PR file change list."""
    flags: dict[tuple[str, str], dict[str, bool]] = {}

    for raw_path, kind in changes:
        path = _norm(raw_path)
        if not is_docs_markdown(path, docs_root):
            continue
        locale = locale_of(path, docs_root)
        if locale is None:
            continue
        other = counterpart(path, docs_root)
        if other is None:
            continue

        if locale == "ru":
            ru_path, en_path = path, other
        else:
            ru_path, en_path = other, path

        key = (ru_path, en_path)
        state = flags.setdefault(
            key,
            {
                "ru_changed": False,
                "en_changed": False,
                "ru_deleted": False,
                "en_deleted": False,
            },
        )
        if locale == "ru":
            state["ru_changed"] = True
            if kind == "deleted":
                state["ru_deleted"] = True
        else:
            state["en_changed"] = True
            if kind == "deleted":
                state["en_deleted"] = True

    pairs: list[DocPair] = []
    for (ru_path, en_path), state in sorted(flags.items()):
        pairs.append(
            DocPair(
                ru_path=ru_path,
                en_path=en_path,
                ru_changed=state["ru_changed"],
                en_changed=state["en_changed"],
                ru_deleted=state["ru_deleted"],
                en_deleted=state["en_deleted"],
            )
        )
    return pairs


@dataclass(frozen=True)
class NavigationPair:
    """Mirrored RU/EN navigation YAML paths touched in a PR."""

    ru_path: str
    en_path: str
    ru_changed: bool = False
    en_changed: bool = False
    ru_deleted: bool = False
    supplement_only: bool = False


def build_navigation_pairs(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> list[NavigationPair]:
    """Build navigation YAML pairs from PR file changes (RU and EN sides)."""
    flags: dict[tuple[str, str], dict[str, bool]] = {}

    for raw_path, kind in changes:
        path = _norm(raw_path)
        if is_docs_ru_navigation(path, docs_root):
            en_path = counterpart(path, docs_root)
            if en_path is None:
                continue
            key = (path, en_path)
            state = flags.setdefault(
                key,
                {"ru_changed": False, "en_changed": False, "ru_deleted": False},
            )
            state["ru_changed"] = True
            if kind == "deleted":
                state["ru_deleted"] = True
        elif is_docs_en_navigation(path, docs_root):
            ru_path = counterpart(path, docs_root)
            if ru_path is None:
                continue
            key = (ru_path, path)
            state = flags.setdefault(
                key,
                {"ru_changed": False, "en_changed": False, "ru_deleted": False},
            )
            state["en_changed"] = True

    return [
        NavigationPair(
            ru_path=ru_path,
            en_path=en_path,
            ru_changed=state["ru_changed"],
            en_changed=state["en_changed"],
            ru_deleted=state["ru_deleted"],
        )
        for (ru_path, en_path), state in sorted(flags.items())
    ]


def _merge_navigation_pair_flags(
    flags: dict[tuple[str, str], dict[str, bool]],
    *,
    ru_path: str,
    en_path: str,
    path: str,
    kind: ChangeKind,
    docs_root: str,
) -> None:
    key = (ru_path, en_path)
    state = flags.setdefault(
        key,
        {"ru_changed": False, "en_changed": False, "ru_deleted": False},
    )
    if is_docs_ru_navigation(path, docs_root):
        state["ru_changed"] = True
        if kind == "deleted":
            state["ru_deleted"] = True
    else:
        state["en_changed"] = True


def build_verify_navigation_pairs(
    translation_changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
    source_changes: list[tuple[str, ChangeKind]] | None = None,
) -> list[NavigationPair]:
    """Navigation pairs for ``doc_verify`` on a translation PR.

    Includes EN navigation files changed in the translation PR and RU navigation
    files changed in the source PR (when ``source_changes`` is provided).
    """
    flags: dict[tuple[str, str], dict[str, bool]] = {}

    for raw_path, kind in translation_changes:
        path = _norm(raw_path)
        if is_docs_en_navigation(path, docs_root):
            ru_path = counterpart(path, docs_root)
            if ru_path is None:
                continue
            _merge_navigation_pair_flags(
                flags,
                ru_path=ru_path,
                en_path=path,
                path=path,
                kind=kind,
                docs_root=docs_root,
            )
        elif is_docs_ru_navigation(path, docs_root):
            en_path = counterpart(path, docs_root)
            if en_path is None:
                continue
            _merge_navigation_pair_flags(
                flags,
                ru_path=path,
                en_path=en_path,
                path=path,
                kind=kind,
                docs_root=docs_root,
            )

    if source_changes:
        for raw_path, kind in source_changes:
            path = _norm(raw_path)
            if not is_docs_ru_navigation(path, docs_root):
                continue
            en_path = counterpart(path, docs_root)
            if en_path is None:
                continue
            _merge_navigation_pair_flags(
                flags,
                ru_path=path,
                en_path=en_path,
                path=path,
                kind=kind,
                docs_root=docs_root,
            )

    return [
        NavigationPair(
            ru_path=ru_path,
            en_path=en_path,
            ru_changed=state["ru_changed"],
            en_changed=state["en_changed"],
            ru_deleted=state["ru_deleted"],
        )
        for (ru_path, en_path), state in sorted(flags.items())
    ]


def merge_translation_pr_verify_scope(
    pairs: list[DocPair],
    expected_pairs: list[DocPair],
) -> list[DocPair]:
    """Merge source scope with actual EN state from the translation PR diff."""
    actual_by_en_path = {pair.en_path: pair for pair in pairs}
    merged_expected = [
        replace(
            expected,
            en_changed=expected.en_changed or actual.en_changed,
            en_deleted=expected.en_deleted or actual.en_deleted,
        )
        if (actual := actual_by_en_path.get(expected.en_path)) is not None
        else expected
        for expected in expected_pairs
    ]
    expected_en_paths = {pair.en_path for pair in expected_pairs}
    return [
        *merged_expected,
        *(pair for pair in pairs if pair.en_path not in expected_en_paths),
    ]


def filter_translation_pr_verify_scope(
    pairs: list[DocPair],
    nav_pairs: list[NavigationPair],
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
    allowed_en_paths: frozenset[str] | set[str] | None = None,
    allowed_nav_en_paths: frozenset[str] | set[str] | None = None,
) -> tuple[list[DocPair], list[NavigationPair]]:
    """Narrow ``doc_verify`` to its expected source scope (§6.77 / §6.240).

    Without an explicit source scope, keep only EN present in the PR diff. With
    an explicit source scope, keep every expected target even when its valid
    final bytes produce no diff. ``supplement_only`` navigation remains context.

    When ``allowed_en_paths`` / ``allowed_nav_en_paths`` are set (source-PR
    translation scope), drop tip-ambient EN that drifted into the translation
    branch vs ``main`` but are outside the source RU/EN set. Otherwise verify
    critic-fixes and red-reports unrelated pages (#40385 / #52055).
    """
    root = docs_root.strip("/")
    changed = {_norm(path) for path, _ in changes}
    en_in_diff = {path for path in changed if path.startswith(f"{root}/en/")}
    allowed_en = (
        None if allowed_en_paths is None else {_norm(path) for path in allowed_en_paths}
    )
    allowed_nav = (
        None
        if allowed_nav_en_paths is None
        else {_norm(path) for path in allowed_nav_en_paths}
    )

    scoped_pairs = [
        pair
        for pair in pairs
        if (
            pair.en_path in en_in_diff
            if allowed_en is None
            else pair.en_path in allowed_en
        )
    ]
    scoped_nav = [
        nav
        for nav in nav_pairs
        if (nav.en_path in changed if allowed_nav is None else nav.en_path in allowed_nav)
        and not nav.supplement_only
    ]
    return scoped_pairs, scoped_nav
