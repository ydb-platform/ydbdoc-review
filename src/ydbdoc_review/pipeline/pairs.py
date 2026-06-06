"""RU/EN doc path pairing for ydb/docs mirror layout."""

from __future__ import annotations

from dataclasses import dataclass
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


def is_docs_markdown(path: str, docs_root: str) -> bool:
    """True for ``docs/ru/…`` or ``docs/en/…`` ``.md`` files (not ``_includes``)."""
    p = _norm(path)
    if not p.endswith(".md"):
        return False
    if "/_includes/" in p:
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
    """Mirrored RU/EN navigation YAML paths touched in the source PR."""

    ru_path: str
    en_path: str
    ru_changed: bool = False
    ru_deleted: bool = False


def build_navigation_pairs(
    changes: list[tuple[str, ChangeKind]],
    *,
    docs_root: str = "ydb/docs",
) -> list[NavigationPair]:
    """Build navigation YAML pairs from PR file changes (RU side only)."""
    flags: dict[tuple[str, str], dict[str, bool]] = {}

    for raw_path, kind in changes:
        path = _norm(raw_path)
        if not is_docs_ru_navigation(path, docs_root):
            continue
        en_path = counterpart(path, docs_root)
        if en_path is None:
            continue
        key = (path, en_path)
        state = flags.setdefault(
            key,
            {"ru_changed": False, "ru_deleted": False},
        )
        state["ru_changed"] = True
        if kind == "deleted":
            state["ru_deleted"] = True

    return [
        NavigationPair(
            ru_path=ru_path,
            en_path=en_path,
            ru_changed=state["ru_changed"],
            ru_deleted=state["ru_deleted"],
        )
        for (ru_path, en_path), state in sorted(flags.items())
    ]
