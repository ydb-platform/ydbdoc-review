"""Unified navigation scope planner (TOC redesign — §22).

Builds an in-memory view of related sidebars and derives the full set of RU
markdown + navigation YAML files that ``doc_translate`` must produce.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from ydbdoc_review.navigation.paths import is_navigation_yaml
from ydbdoc_review.navigation.toc import (
    collect_toc_link_targets,
    en_toc_is_absent,
    resolve_toc_target_path,
    toc_entry_paths,
)
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.pairs import ChangeKind, DocPair, NavigationPair, counterpart, is_docs_markdown

ReadFn = Callable[[str], str | None]

logger = logging.getLogger(__name__)

_TOC_FILENAMES = ("toc_p.yaml", "toc_i.yaml")


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _toc_dir_contains_diff(ru_toc: str, diff_paths: set[str]) -> bool:
    """True when a changed file lives in the sidebar's directory subtree."""
    toc_dir = _norm(ru_toc).rsplit("/", 1)[0] + "/"
    return any(_norm(p).startswith(toc_dir) for p in diff_paths)


@dataclass(frozen=True)
class TranslationScopePlan:
    """Everything ``doc_translate`` should touch for one source PR."""

    doc_ru_paths: frozenset[str]
    doc_from_diff: frozenset[str]
    doc_from_main: frozenset[str]
    nav_ru_paths: frozenset[str]
    nav_from_diff: frozenset[str]
    nav_from_main: frozenset[str]

    @property
    def all_ru_paths(self) -> frozenset[str]:
        return self.doc_ru_paths | self.nav_ru_paths


def _ancestor_ru_tocs(ru_md_path: str, *, docs_root: str) -> list[str]:
    """Sidebar yaml paths in ancestor directories of a RU page."""
    from pathlib import PurePosixPath

    root = docs_root.strip("/")
    ru_root = PurePosixPath(root) / "ru"
    dir_path = PurePosixPath(_norm(ru_md_path)).parent
    out: list[str] = []
    seen: set[str] = set()
    while dir_path >= ru_root:
        for name in _TOC_FILENAMES:
            ru_toc = _norm(str(dir_path / name))
            if ru_toc not in seen:
                out.append(ru_toc)
                seen.add(ru_toc)
        if dir_path == ru_root:
            break
        dir_path = dir_path.parent
    return out


def _ru_include_md_targets(
    ru_md_path: str, ru_text: str, *, docs_root: str
) -> set[str]:
    targets: set[str] = set()
    for inc in collect_yfm_includes(ru_text):
        resolved = resolve_locale_md_path(ru_md_path, inc.path, docs_root=docs_root)
        if resolved is not None and resolved.startswith(f"{docs_root.strip('/')}/ru/"):
            targets.add(_norm(resolved))
    return targets


def _discover_ru_tocs(
    *,
    seed_ru_md: set[str],
    seed_ru_nav: set[str],
    read_ru: ReadFn,
    diff_paths: set[str],
) -> set[str]:
    """BFS: ancestor sidebars + ``include.path`` child sidebars."""
    todo: set[str] = set(seed_ru_nav)
    for ru_md in seed_ru_md:
        todo.update(_ancestor_ru_tocs(ru_md, docs_root="ydb/docs"))
    seen: set[str] = set()
    queue = sorted(todo)
    while queue:
        ru_toc = queue.pop(0)
        if ru_toc in seen:
            continue
        seen.add(ru_toc)
        text = read_ru(ru_toc)
        if not text or not text.strip():
            continue
        for kind, rel in collect_toc_link_targets(text):
            if kind != "include" or not rel.endswith((".yaml", ".yml")):
                continue
            child = _norm(resolve_toc_target_path(ru_toc, rel))
            if child in seen:
                continue
            if child in seed_ru_nav or _toc_dir_contains_diff(child, diff_paths):
                queue.append(child)
    return seen


def _toc_lists_page(ru_toc: str, ru_toc_text: str, basename: str) -> bool:
    for kind, rel in collect_toc_link_targets(ru_toc_text):
        if kind == "href" and rel.endswith(".md"):
            from pathlib import PurePosixPath

            if PurePosixPath(rel).name == basename:
                return True
    return False


def _toc_md_hrefs(ru_toc_text: str) -> set[str]:
    return {
        rel
        for kind, rel in collect_toc_link_targets(ru_toc_text)
        if kind == "href" and rel.endswith(".md")
    }


def _new_toc_md_hrefs(
    ru_toc: str,
    ru_toc_text: str,
    read_ru_base: ReadFn | None,
) -> set[str]:
    """Relative ``href`` paths added in RU toc since merge-base (PR head vs base)."""
    head_hrefs = _toc_md_hrefs(ru_toc_text)
    if read_ru_base is None:
        return set()
    base_text = read_ru_base(ru_toc)
    if not base_text:
        return head_hrefs
    return head_hrefs - _toc_md_hrefs(base_text)


def _add_doc_if_en_absent(
    ru_md: str,
    *,
    doc_ru: set[str],
    read_ru: ReadFn,
    read_en_base: ReadFn,
    docs_root: str,
) -> None:
    if ru_md in doc_ru:
        return
    if not read_ru(ru_md):
        return
    en_md = counterpart(ru_md, docs_root)
    if en_md is None:
        return
    if read_en_base(en_md) is None:
        doc_ru.add(ru_md)


def _pages_from_discovered_toc(
    ru_toc: str,
    ru_toc_text: str,
    *,
    diff_ru_md: set[str],
    diff_ru_nav: set[str],
    doc_ru: set[str],
    read_ru: ReadFn,
    read_en_base: ReadFn,
    read_ru_base: ReadFn | None,
    docs_root: str,
) -> None:
    """Derive markdown scope from one sidebar (§22.5 / §6.72)."""
    if ru_toc in diff_ru_nav:
        for rel in _new_toc_md_hrefs(ru_toc, ru_toc_text, read_ru_base):
            ru_md = _norm(resolve_toc_target_path(ru_toc, rel))
            _add_doc_if_en_absent(
                ru_md,
                doc_ru=doc_ru,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=docs_root,
            )
        return

    for ru_md in diff_ru_md:
        basename = ru_md.rsplit("/", 1)[-1]
        if _toc_lists_page(ru_toc, ru_toc_text, basename):
            _add_doc_if_en_absent(
                ru_md,
                doc_ru=doc_ru,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=docs_root,
            )


def _nav_needed(
    ru_toc: str,
    *,
    read_ru: ReadFn,
    read_en_base: ReadFn,
    docs_root: str,
    seed_ru_md: set[str],
    in_diff: bool,
) -> bool:
    if in_diff:
        return True
    en_toc = counterpart(ru_toc, docs_root)
    if en_toc is None:
        return False
    ru_text = read_ru(ru_toc)
    if not ru_text:
        return False
    en_text = read_en_base(en_toc) or ""
    if en_toc_is_absent(en_text):
        return True
    for ru_md in seed_ru_md:
        basename = ru_md.rsplit("/", 1)[-1]
        if _toc_lists_page(ru_toc, ru_text, basename):
            en_lists = _toc_lists_page(en_toc, en_text, basename) if en_text else False
            if not en_lists:
                return True
    return False


def _en_has_include(en_toc_text: str, rel: str) -> bool:
    """True when EN sidebar already lists ``include.path: rel`` (exact match)."""
    if not en_toc_text:
        return False
    _, includes = toc_entry_paths(en_toc_text)
    return rel in includes


def _queue_parents_of_needed_nav(
    *,
    discovered_tocs: set[str],
    nav_ru: set[str],
    read_ru: ReadFn,
    read_en_base: ReadFn,
    docs_root: str,
) -> None:
    """Queue parent sidebars that ``include.path`` a child already in ``nav_ru``.

    §6.116 / #46569: child ``toc_*.yaml`` can be merged while the parent still
    points at a legacy flat ``href`` and never gains ``include.path``. Basename
    checks in ``_nav_needed`` miss parents that only list ``section/index.md``.
    """
    changed = True
    while changed:
        changed = False
        for ru_toc in sorted(discovered_tocs):
            if ru_toc in nav_ru:
                continue
            ru_text = read_ru(ru_toc)
            if not ru_text:
                continue
            en_toc = counterpart(ru_toc, docs_root)
            en_text = (read_en_base(en_toc) or "") if en_toc else ""
            for kind, rel in collect_toc_link_targets(ru_text):
                if kind != "include" or not rel.endswith((".yaml", ".yml")):
                    continue
                child = _norm(resolve_toc_target_path(ru_toc, rel))
                if child not in nav_ru:
                    continue
                if _en_has_include(en_text, rel):
                    continue
                nav_ru.add(ru_toc)
                changed = True
                break


def plan_translation_scope(
    changes: list[tuple[str, ChangeKind]],
    *,
    read_ru: ReadFn,
    read_en_base: ReadFn,
    read_ru_base: ReadFn | None = None,
    docs_root: str = "ydb/docs",
) -> TranslationScopePlan:
    """Plan markdown + navigation scope from a source PR change list.

    Rules (§22):
    1. Seed from PR diff (RU ``.md`` + nav yaml).
    2. Discover related ``toc_p`` / ``toc_i`` via ancestors + ``include.path``.
    3. Per discovered sidebar (§22.5): toc in PR diff → **new** ``href``
       entries since base; partial EN sidebar → missing EN mirrors for diff
       pages listed in toc. Cross-section absent-EN full mirror is disabled.
    4. Close locale ``{% include %}`` dependencies for all queued pages.
    5. Queue nav yaml merge when toc is in diff, EN absent, or missing href for
       a changed page; then queue any parent that ``include.path``s a needed
       child while EN lacks that include (§6.116 / #46569).
    """
    root = docs_root.strip("/")
    diff_ru_md: set[str] = set()
    diff_ru_nav: set[str] = set()

    for raw_path, kind in changes:
        if kind == "deleted":
            continue
        path = _norm(raw_path)
        if path.startswith(f"{root}/ru/") and is_docs_markdown(path, docs_root):
            diff_ru_md.add(path)
        elif path.startswith(f"{root}/ru/") and is_navigation_yaml(path):
            diff_ru_nav.add(path)

    discovered_tocs = _discover_ru_tocs(
        seed_ru_md=diff_ru_md,
        seed_ru_nav=diff_ru_nav,
        read_ru=read_ru,
        diff_paths=diff_ru_md | diff_ru_nav,
    )

    doc_ru: set[str] = set(diff_ru_md)

    for ru_toc in sorted(discovered_tocs):
        ru_toc_text = read_ru(ru_toc)
        if not ru_toc_text:
            continue
        _pages_from_discovered_toc(
            ru_toc,
            ru_toc_text,
            diff_ru_md=diff_ru_md,
            diff_ru_nav=diff_ru_nav,
            doc_ru=doc_ru,
            read_ru=read_ru,
            read_en_base=read_en_base,
            read_ru_base=read_ru_base,
            docs_root=docs_root,
        )

    queue = sorted(doc_ru)
    while queue:
        ru_md = queue.pop(0)
        ru_text = read_ru(ru_md)
        if not ru_text:
            continue
        for target in sorted(
            _ru_include_md_targets(ru_md, ru_text, docs_root=docs_root)
        ):
            if target in doc_ru:
                continue
            en_md = counterpart(target, docs_root)
            if en_md is None:
                continue
            if read_en_base(en_md) is None and read_ru(target):
                doc_ru.add(target)
                queue.append(target)

    doc_from_diff = frozenset(diff_ru_md)
    doc_from_main = frozenset(doc_ru - diff_ru_md)

    nav_ru: set[str] = set()
    nav_from_diff: set[str] = set()
    for ru_toc in discovered_tocs:
        if _nav_needed(
            ru_toc,
            read_ru=read_ru,
            read_en_base=read_en_base,
            docs_root=docs_root,
            seed_ru_md=diff_ru_md,
            in_diff=ru_toc in diff_ru_nav,
        ):
            nav_ru.add(ru_toc)
            if ru_toc in diff_ru_nav:
                nav_from_diff.add(ru_toc)

    _queue_parents_of_needed_nav(
        discovered_tocs=discovered_tocs,
        nav_ru=nav_ru,
        read_ru=read_ru,
        read_en_base=read_en_base,
        docs_root=docs_root,
    )

    nav_from_main = frozenset(nav_ru - nav_from_diff)

    return TranslationScopePlan(
        doc_ru_paths=frozenset(doc_ru),
        doc_from_diff=doc_from_diff,
        doc_from_main=doc_from_main,
        nav_ru_paths=frozenset(nav_ru),
        nav_from_diff=frozenset(nav_from_diff),
        nav_from_main=nav_from_main,
    )


def changes_from_manifest(
    pr_diff_ru: list[str],
    *,
    default_kind: ChangeKind = "modified",
) -> list[tuple[str, ChangeKind]]:
    return [(path, default_kind) for path in pr_diff_ru]


def make_repo_scope_readers(
    repo_path: str,
    merge_base_with: str,
    *,
    ru_content_ref: str | None = None,
) -> tuple[ReadFn, ReadFn, ReadFn]:
    """Build scope readers for ``plan_translation_scope`` in CI.

    ``ru_content_ref`` — optional git ref for RU (merged PR → merge commit, §6.120).
    """
    from ydbdoc_review.github.git_ops import merge_base, read_text, read_text_at_ref

    mb = "HEAD"
    try:
        mb = merge_base(repo_path, merge_base_with, "HEAD")
    except RuntimeError:
        logger.debug(
            "merge-base %s..HEAD unavailable; using HEAD for EN baseline reads",
            merge_base_with,
        )

    def read_ru(path: str) -> str | None:
        if ru_content_ref:
            text = read_text_at_ref(repo_path, ru_content_ref, path)
            if text is not None:
                return text
        text = read_text(repo_path, path)
        if text is not None:
            return text
        return read_text_at_ref(repo_path, "HEAD", path)

    def read_en_base(path: str) -> str | None:
        text = read_text_at_ref(repo_path, mb, path)
        if text is not None:
            return text
        return read_text_at_ref(repo_path, merge_base_with, path)

    def read_ru_base(path: str) -> str | None:
        text = read_text_at_ref(repo_path, mb, path)
        if text is not None:
            return text
        return read_text_at_ref(repo_path, merge_base_with, path)

    return read_ru, read_en_base, read_ru_base


def doc_pairs_from_plan(
    plan: TranslationScopePlan,
    *,
    docs_root: str = "ydb/docs",
    skip_en_paths: frozenset[str] | None = None,
) -> list[DocPair]:
    """``DocPair`` list for all markdown paths in the scope plan."""
    skip = skip_en_paths or frozenset()
    pairs: list[DocPair] = []
    for ru_path in sorted(plan.doc_ru_paths):
        en_path = counterpart(ru_path, docs_root)
        if en_path is None or en_path in skip:
            continue
        pairs.append(
            DocPair(
                ru_path=ru_path,
                en_path=en_path,
                ru_changed=True,
            )
        )
    return pairs


def navigation_pairs_from_plan(
    plan: TranslationScopePlan,
    *,
    docs_root: str = "ydb/docs",
) -> list[NavigationPair]:
    """``NavigationPair`` list for sidebar yaml paths in the scope plan."""
    pairs: list[NavigationPair] = []
    for ru_path in sorted(plan.nav_ru_paths):
        en_path = counterpart(ru_path, docs_root)
        if en_path is None:
            continue
        pairs.append(
            NavigationPair(
                ru_path=ru_path,
                en_path=en_path,
                ru_changed=True,
                supplement_only=ru_path in plan.nav_from_main,
            )
        )
    return pairs


def merge_navigation_pair_lists(
    primary: list[NavigationPair],
    extra: list[NavigationPair],
) -> list[NavigationPair]:
    """Union nav pairs; ``extra`` wins on ``ru_changed`` / clears ``supplement_only``."""
    by_key: dict[tuple[str, str], NavigationPair] = {
        (p.ru_path, p.en_path): p for p in primary
    }
    for pair in extra:
        key = (pair.ru_path, pair.en_path)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = pair
            continue
        by_key[key] = NavigationPair(
            ru_path=pair.ru_path,
            en_path=pair.en_path,
            ru_changed=prev.ru_changed or pair.ru_changed,
            en_changed=prev.en_changed or pair.en_changed,
            ru_deleted=prev.ru_deleted or pair.ru_deleted,
            supplement_only=prev.supplement_only and pair.supplement_only,
        )
    return sorted(by_key.values(), key=lambda p: (p.ru_path, p.en_path))


def synthetic_changes_from_plan(
    plan: TranslationScopePlan,
    *,
    kind: ChangeKind = "added",
) -> list[tuple[str, ChangeKind]]:
    """Synthetic RU change entries for paths discovered outside the PR diff."""
    out: list[tuple[str, ChangeKind]] = []
    for path in sorted(plan.doc_from_main | plan.nav_from_main):
        out.append((path, kind))
    return out


def planned_toc_extras_for_pair(
    plan: TranslationScopePlan,
    ru_toc: str,
    ru_toc_text: str,
    *,
    docs_root: str = "ydb/docs",
) -> tuple[set[str], set[str]]:
    """``(extra_hrefs, extra_include_paths)`` from scope plan for one sidebar.

    Replaces ``extra_toc_hrefs_from_md_targets`` + ``extra_toc_hrefs_for_pair``
    (§22 J.6): href/include entries are derived from the unified plan, not from
    post-hoc basename intersection after translate.
    """
    extra_hrefs: set[str] = set()
    extra_includes: set[str] = set()
    for kind, rel in collect_toc_link_targets(ru_toc_text):
        if kind == "href" and rel.endswith(".md"):
            ru_md = _norm(resolve_toc_target_path(ru_toc, rel))
            if ru_md in plan.doc_ru_paths:
                extra_hrefs.add(rel)
        elif kind == "include" and rel.endswith((".yaml", ".yml")):
            ru_child = _norm(resolve_toc_target_path(ru_toc, rel))
            if ru_child in plan.nav_ru_paths:
                extra_includes.add(rel)
    return extra_hrefs, extra_includes
