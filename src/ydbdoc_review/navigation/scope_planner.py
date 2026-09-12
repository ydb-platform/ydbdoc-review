"""Unified navigation scope planner (TOC redesign — §22).

Builds an in-memory view of related sidebars and derives the full set of RU
markdown + navigation YAML files that ``doc_translate`` must produce.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ydbdoc_review.github.provenance import RuAuthority
from ydbdoc_review.navigation.dependency_budget import (
    MarkdownDependencyBudget,
)
from ydbdoc_review.navigation.link_deps import (
    canonical_md_dependency_path,
    direct_md_link_dependencies,
)
from ydbdoc_review.navigation.paths import is_navigation_yaml
from ydbdoc_review.navigation.redirects import (
    redirect_source_repo_md_paths,
)
from ydbdoc_review.navigation.toc import (
    collect_toc_link_targets,
    en_toc_is_absent,
    parse_toc_items,
    resolve_toc_target_path,
    toc_entry_paths,
)
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.pipeline.pairs import (
    ChangeKind,
    DocPair,
    NavigationPair,
    counterpart,
    is_docs_markdown,
)

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
    doc_deleted: frozenset[str] = frozenset()
    doc_en_changed: frozenset[str] = frozenset()
    doc_en_deleted: frozenset[str] = frozenset()
    dependency_budget: MarkdownDependencyBudget = field(
        default_factory=MarkdownDependencyBudget,
        compare=False,
        repr=False,
    )
    # Exact fragment obligations admitted by dependency closure. Ordinary
    # links/includes deliberately do not authorize partial-page translation.
    required_fragment_dependencies: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def link_dep_warnings(self) -> tuple[str, ...]:
        """Compatibility view of all job-global dependency warnings."""
        return self.dependency_budget.warnings

    @property
    def all_ru_paths(self) -> frozenset[str]:
        return self.doc_ru_paths | self.nav_ru_paths

    def required_fragments_for(self, ru_path: str) -> frozenset[str]:
        normalized = _norm(ru_path)
        for path, fragments in self.required_fragment_dependencies:
            if path == normalized:
                return frozenset(fragments)
        return frozenset()


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


def _ru_include_md_targets(ru_md_path: str, ru_text: str, *, docs_root: str) -> set[str]:
    targets: set[str] = set()
    for inc in collect_yfm_includes(ru_text):
        resolved = resolve_locale_md_path(ru_md_path, inc.path, docs_root=docs_root)
        if resolved is not None and resolved.startswith(f"{docs_root.strip('/')}/ru/"):
            targets.add(_norm(resolved))
    return targets


def _internal_ascii_fragment_hrefs(ru_text: str) -> set[str]:
    """Current exact internal ``.md#ASCII`` hrefs."""
    from ydbdoc_review.validation.href_parity import _iter_md_links

    hrefs: set[str] = set()
    for _, href, _, _ in _iter_md_links(ru_text or ""):
        if "#" not in href:
            continue
        path, frag = href.rsplit("#", 1)
        if path.endswith(".md") and frag and frag.isascii():
            hrefs.add(href)
    return hrefs


def _exact_ascii_fragment_owner_dependency(
    ru_page_path: str,
    href: str,
    *,
    read_ru: ReadFn,
    read_en_base: ReadFn,
    docs_root: str,
    redirects_yaml: str | None = None,
) -> str | None:
    """Return the unique direct RU declaration owner missing in EN.

    When tip ``redirects.yaml`` marks the owner as a ``from`` tombstone, follow
    the ``to`` twin so merged-PR scope does not enqueue historical paths (§6.242).
    """
    from ydbdoc_review.validation.fragment_repair import _page_declares_fragment

    if "#" not in href:
        return None
    target_ref, frag = href.rsplit("#", 1)
    if not frag or not frag.isascii():
        return None
    ru_wrapper = resolve_locale_md_path(ru_page_path, target_ref, docs_root=docs_root)
    if ru_wrapper is None or not ru_wrapper.startswith(f"{docs_root.strip('/')}/ru/"):
        return None
    ru_wrapper = canonical_md_dependency_path(
        ru_wrapper,
        redirects_yaml=redirects_yaml,
        docs_root=docs_root,
    )
    if ru_wrapper is None:
        return None
    ru_text = read_ru(ru_wrapper)
    en_wrapper = counterpart(ru_wrapper, docs_root)
    en_text = read_en_base(en_wrapper) if en_wrapper else None
    if ru_text is None or en_text is None:
        return None
    ru_owners: list[tuple[int | None, str]] = []
    if _page_declares_fragment(ru_text, frag):
        ru_owners.append((None, ru_wrapper))
    ru_includes = collect_yfm_includes(ru_text)
    en_includes = collect_yfm_includes(en_text)
    for index, inc in enumerate(ru_includes):
        owner = resolve_locale_md_path(ru_wrapper, inc.path, docs_root=docs_root)
        if owner:
            owner = canonical_md_dependency_path(
                owner,
                redirects_yaml=redirects_yaml,
                docs_root=docs_root,
            )
        owner_text = read_ru(owner) if owner else None
        if owner and owner_text and _page_declares_fragment(owner_text, frag):
            ru_owners.append((index, owner))
    if len(ru_owners) != 1:
        return None
    index, ru_owner = ru_owners[0]
    if index is None:
        en_owner, en_owner_text = en_wrapper, en_text
    else:
        if index >= len(en_includes):
            return None
        en_owner = resolve_locale_md_path(en_wrapper, en_includes[index].path, docs_root=docs_root)
        if en_owner:
            en_owner = canonical_md_dependency_path(
                en_owner,
                redirects_yaml=redirects_yaml,
                docs_root=docs_root,
            )
        en_owner_text = read_en_base(en_owner) if en_owner else None
    if en_owner is None or en_owner_text is None or counterpart(ru_owner, docs_root) != en_owner:
        return None
    if _page_declares_fragment(en_owner_text, frag):
        return None
    return ru_owner


def _discover_ru_tocs(
    *,
    seed_ru_md: set[str],
    seed_ru_nav: set[str],
    read_ru: ReadFn,
    read_ru_base: ReadFn | None,
    diff_paths: set[str],
    docs_root: str,
) -> set[str]:
    """BFS: ancestor sidebars + ``include.path`` child sidebars."""
    todo: set[str] = set(seed_ru_nav)
    for ru_md in seed_ru_md:
        todo.update(_ancestor_ru_tocs(ru_md, docs_root=docs_root))
    seen: set[str] = set()
    queue = [(ru_toc, False) for ru_toc in sorted(todo)]
    while queue:
        ru_toc, forced = queue.pop(0)
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
            if ru_toc in seed_ru_nav and read_ru_base is not None:
                base_text = read_ru_base(ru_toc) or ""
                base_includes = {
                    path
                    for kind, path in collect_toc_link_targets(base_text)
                    if kind == "include"
                }
                if rel not in base_includes:
                    queue.append((child, True))
            elif forced or child in seed_ru_nav or _toc_dir_contains_diff(child, diff_paths):
                queue.append((child, forced))
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
    candidates: set[str],
    read_ru: ReadFn,
    read_en_base: ReadFn,
    docs_root: str,
    redirects_yaml: str,
) -> str | None:
    ru_md = canonical_md_dependency_path(
        _norm(ru_md),
        redirects_yaml=redirects_yaml,
        docs_root=docs_root,
    )
    if ru_md is None or read_ru(ru_md) is None:
        return None
    en_md = counterpart(ru_md, docs_root)
    if en_md is None:
        return None
    if read_en_base(en_md) is not None:
        return None
    candidates.add(ru_md)
    return ru_md


def _pages_from_discovered_toc(
    ru_toc: str,
    ru_toc_text: str,
    *,
    diff_ru_md: set[str],
    diff_ru_nav: set[str],
    candidates: set[str],
    read_ru: ReadFn,
    read_en_base: ReadFn,
    read_ru_base: ReadFn | None,
    docs_root: str,
    redirects_yaml: str,
    include_all_missing: bool = False,
) -> None:
    """Derive markdown scope from one sidebar (§22.5 / §6.72)."""
    if ru_toc in diff_ru_nav:
        for rel in sorted(_new_toc_md_hrefs(ru_toc, ru_toc_text, read_ru_base)):
            ru_md = _norm(resolve_toc_target_path(ru_toc, rel))
            _add_doc_if_en_absent(
                ru_md,
                candidates=candidates,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=docs_root,
                redirects_yaml=redirects_yaml,
            )
        return

    if include_all_missing:
        for rel in sorted(_toc_md_hrefs(ru_toc_text)):
            _add_doc_if_en_absent(
                _norm(resolve_toc_target_path(ru_toc, rel)),
                candidates=candidates,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=docs_root,
                redirects_yaml=redirects_yaml,
            )
        return

    for ru_md in sorted(diff_ru_md):
        basename = ru_md.rsplit("/", 1)[-1]
        if _toc_lists_page(ru_toc, ru_toc_text, basename):
            _add_doc_if_en_absent(
                ru_md,
                candidates=candidates,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=docs_root,
                redirects_yaml=redirects_yaml,
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

    Rules (§22 + REQUIREMENTS §6):
    1. Seed from PR diff (RU ``.md`` + nav yaml).
    2. Discover related ``toc_p`` / ``toc_i`` via ancestors + ``include.path``.
    3. Per discovered sidebar (§22.5): toc in PR diff → **new** ``href``
       entries since base; partial EN sidebar → missing EN mirrors for diff
       pages listed in toc. Cross-section absent-EN full mirror is disabled.
    4. Close locale ``{% include %}`` dependencies for all queued pages.
    5. Queue nav yaml merge when toc is in diff, EN absent, or missing href for
       a changed page; then queue any parent that ``include.path``s a needed
       child while EN lacks that include (§6.116 / #46569).
    6. Close Markdown-link dependencies missing from the frozen EN tree
       (redirect/existing EN skip; dedup; 20-extra budget; §6).
    """
    root = docs_root.strip("/")
    diff_ru_md: set[str] = set()
    deleted_ru_md: set[str] = set()
    diff_en_md: set[str] = set()
    deleted_en_md: set[str] = set()
    diff_ru_nav: set[str] = set()

    for raw_path, kind in changes:
        path = _norm(raw_path)
        if path.startswith(f"{root}/ru/") and is_docs_markdown(path, docs_root):
            if kind == "deleted":
                deleted_ru_md.add(path)
            else:
                diff_ru_md.add(path)
        elif path.startswith(f"{root}/en/") and is_docs_markdown(path, docs_root):
            diff_en_md.add(path)
            if kind == "deleted":
                deleted_en_md.add(path)
        elif kind == "deleted":
            continue
        elif path.startswith(f"{root}/ru/") and is_navigation_yaml(path):
            diff_ru_nav.add(path)

    redirects_yaml = read_en_base(f"{root}/redirects.yaml") or read_ru(
        f"{root}/redirects.yaml"
    ) or ""
    source_roots = diff_ru_md | deleted_ru_md
    budget = MarkdownDependencyBudget(source_roots)

    # Deleted RU pages are translation actions too: their existing EN mirrors
    # must enter the pair pipeline as ``delete_en`` (#50904).
    doc_ru: set[str] = set(source_roots)
    doc_ru.update(
        counterpart(path, docs_root)
        for path in diff_en_md
        if counterpart(path, docs_root) is not None
    )

    tip_tombstone_ru = redirect_source_repo_md_paths(
        redirects_yaml,
        locale="ru",
        docs_root=docs_root,
        candidate_repo_paths=source_roots,
    )
    scanned_live_docs: set[str] = set()
    discovered_tocs: set[str] = set()
    nav_ru: set[str] = set()
    required_fragment_dependencies: dict[str, set[str]] = {}
    new_toc_targets: set[str] = set()
    for ru_toc in diff_ru_nav:
        ru_text = read_ru(ru_toc) or ""
        base_text = read_ru_base(ru_toc) if read_ru_base is not None else None
        base_includes = {
            path
            for kind, path in collect_toc_link_targets(base_text or "")
            if kind == "include"
        }
        for kind, rel in collect_toc_link_targets(ru_text):
            if kind == "include" and rel not in base_includes:
                new_toc_targets.add(_norm(resolve_toc_target_path(ru_toc, rel)))
    first_round = True
    while True:
        live_docs = doc_ru - deleted_ru_md - tip_tombstone_ru
        frontier = sorted(live_docs - scanned_live_docs)
        candidates: set[str] = set()

        discovered_tocs |= _discover_ru_tocs(
            seed_ru_md=live_docs,
            seed_ru_nav=diff_ru_nav,
            read_ru=read_ru,
            read_ru_base=read_ru_base,
            diff_paths=live_docs | diff_ru_nav,
            docs_root=docs_root,
        )
        for ru_toc in sorted(discovered_tocs):
            if _nav_needed(
                ru_toc,
                read_ru=read_ru,
                read_en_base=read_en_base,
                docs_root=docs_root,
                seed_ru_md=live_docs,
                in_diff=ru_toc in diff_ru_nav,
            ):
                nav_ru.add(ru_toc)
        _queue_parents_of_needed_nav(
            discovered_tocs=discovered_tocs,
            nav_ru=nav_ru,
            read_ru=read_ru,
            read_en_base=read_en_base,
            docs_root=docs_root,
        )

        for ru_toc in sorted(discovered_tocs):
            ru_toc_text = read_ru(ru_toc)
            if not ru_toc_text:
                continue
            _pages_from_discovered_toc(
                ru_toc,
                ru_toc_text,
                diff_ru_md=live_docs,
                diff_ru_nav=diff_ru_nav,
                candidates=candidates,
                read_ru=read_ru,
                read_en_base=read_en_base,
                read_ru_base=read_ru_base,
                docs_root=docs_root,
                redirects_yaml=redirects_yaml,
                include_all_missing=ru_toc in new_toc_targets,
            )

        for ru_toc in sorted(nav_ru):
            ru_text = read_ru(ru_toc)
            if not ru_text:
                continue
            toc_dir = _norm(ru_toc).rsplit("/", 1)[0]
            related_directory = any(
                _norm(path).rsplit("/", 1)[0] == toc_dir for path in live_docs
            )
            en_toc = counterpart(ru_toc, docs_root)
            en_text = (read_en_base(en_toc) or "") if en_toc else ""
            if related_directory:
                en_toc_hrefs = _toc_md_hrefs(en_text)
                for rel in sorted(_toc_md_hrefs(ru_text)):
                    ru_md = _norm(resolve_toc_target_path(ru_toc, rel))
                    _add_doc_if_en_absent(
                        ru_md,
                        candidates=candidates,
                        read_ru=read_ru,
                        read_en_base=read_en_base,
                        docs_root=docs_root,
                        redirects_yaml=redirects_yaml,
                    )
                    canonical_ru = canonical_md_dependency_path(
                        ru_md,
                        redirects_yaml=redirects_yaml,
                        docs_root=docs_root,
                    )
                    if canonical_ru is None or rel in en_toc_hrefs:
                        continue
                    if read_ru(canonical_ru) is None:
                        continue
                    en_md = counterpart(canonical_ru, docs_root)
                    if en_md is not None:
                        candidates.add(canonical_ru)

            for item in parse_toc_items(ru_text):
                include_path = item.get("include_path")
                href = item.get("href")
                if not include_path or not href or not href.endswith(".md"):
                    continue
                child = _norm(resolve_toc_target_path(ru_toc, include_path))
                if child not in nav_ru:
                    continue
                child_dir = child.rsplit("/", 1)[0]
                if not any(
                    _norm(path).rsplit("/", 1)[0] == child_dir for path in live_docs
                ):
                    continue
                _add_doc_if_en_absent(
                    _norm(resolve_toc_target_path(ru_toc, href)),
                    candidates=candidates,
                    read_ru=read_ru,
                    read_en_base=read_en_base,
                    docs_root=docs_root,
                    redirects_yaml=redirects_yaml,
                )

        if first_round:
            for source_root in sorted(source_roots & tip_tombstone_ru):
                live = canonical_md_dependency_path(
                    source_root,
                    redirects_yaml=redirects_yaml,
                    docs_root=docs_root,
                )
                if (
                    live is not None
                    and live != source_root
                    and counterpart(live, docs_root) is not None
                    and read_ru(live) is not None
                ):
                    candidates.add(live)

        candidate_fragments: dict[str, set[str]] = {}
        for ru_md in frontier:
            ru_text = read_ru(ru_md)
            scanned_live_docs.add(ru_md)
            if ru_text is None:
                continue
            for target in _ru_include_md_targets(ru_md, ru_text, docs_root=docs_root):
                _add_doc_if_en_absent(
                    target,
                    candidates=candidates,
                    read_ru=read_ru,
                    read_en_base=read_en_base,
                    docs_root=docs_root,
                    redirects_yaml=redirects_yaml,
                )
            for include in collect_yfm_includes(ru_text):
                include_path = resolve_locale_md_path(
                    ru_md, include.path, docs_root=docs_root
                )
                include_text = read_ru(include_path) if include_path else None
                if include_path is None or include_text is None:
                    continue
                for href in _internal_ascii_fragment_hrefs(include_text):
                    owner = _exact_ascii_fragment_owner_dependency(
                        include_path,
                        href,
                        read_ru=read_ru,
                        read_en_base=read_en_base,
                        docs_root=docs_root,
                        redirects_yaml=redirects_yaml,
                    )
                    if owner is not None:
                        candidates.add(owner)
                        fragment = href.rsplit("#", 1)[1]
                        candidate_fragments.setdefault(owner, set()).add(fragment)
            candidates.update(
                direct_md_link_dependencies(
                    ru_md,
                    ru_text,
                    read_ru=read_ru,
                    read_en=read_en_base,
                    redirects_yaml=redirects_yaml,
                    docs_root=docs_root,
                )
            )
            for href in _internal_ascii_fragment_hrefs(ru_text):
                owner = _exact_ascii_fragment_owner_dependency(
                    ru_md,
                    href,
                    read_ru=read_ru,
                    read_en_base=read_en_base,
                    docs_root=docs_root,
                    redirects_yaml=redirects_yaml,
                )
                if owner is not None:
                    candidates.add(owner)
                    fragment = href.rsplit("#", 1)[1]
                    candidate_fragments.setdefault(owner, set()).add(fragment)

        newly_admitted: set[str] = set()
        for target in sorted(candidates):
            if target in doc_ru:
                continue
            if budget.admit(
                target,
                warning_path=counterpart(target, docs_root) or target,
            ):
                doc_ru.add(target)
                newly_admitted.add(target)
                if target in candidate_fragments:
                    required_fragment_dependencies.setdefault(target, set()).update(
                        candidate_fragments[target]
                    )

        first_round = False
        if not newly_admitted:
            break

    # Tip redirect tombstones must not stay as *synthetic* translation targets.
    # Source-diff / deleted tombstones remain so PlanTranslatePairsStep can skip
    # (completeness) or delete_en; extras retarget to the live ``to`` twin (§6.242).
    if redirects_yaml:
        doc_ru = {
            path
            for path in doc_ru
            if path not in tip_tombstone_ru or path in source_roots
        }
        required_fragment_dependencies = {
            path: fragments
            for path, fragments in required_fragment_dependencies.items()
            if path in doc_ru
        }

    nav_from_diff = nav_ru & diff_ru_nav
    doc_from_diff = frozenset(
        diff_ru_md
        | deleted_ru_md
        | {
            counterpart(path, docs_root)
            for path in diff_en_md
            if counterpart(path, docs_root) is not None
        }
    )
    doc_from_main = frozenset(doc_ru - doc_from_diff)
    nav_from_main = frozenset(nav_ru - nav_from_diff)

    return TranslationScopePlan(
        doc_ru_paths=frozenset(doc_ru),
        doc_from_diff=doc_from_diff,
        doc_from_main=doc_from_main,
        doc_deleted=frozenset(deleted_ru_md),
        doc_en_changed=frozenset(diff_en_md),
        doc_en_deleted=frozenset(deleted_en_md),
        nav_ru_paths=frozenset(nav_ru),
        nav_from_diff=frozenset(nav_from_diff),
        nav_from_main=nav_from_main,
        dependency_budget=budget,
        required_fragment_dependencies=tuple(
            (path, tuple(sorted(fragments)))
            for path, fragments in sorted(required_fragment_dependencies.items())
        ),
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
    ru_base_ref: str | None = None,
    authority: RuAuthority | None = None,
) -> tuple[ReadFn, ReadFn, ReadFn]:
    """Build scope readers for ``plan_translation_scope`` in CI.

    ``ru_content_ref`` — optional git ref for RU (merged PR → merge commit, §6.120).
    ``ru_base_ref`` — its pre-merge parent, used to recover the original RU
    delta when translating an old merged PR (§6.210).

    EN baseline is the **translation-branch tip** (``merge_base_with``, usually
    ``origin/main``), not ``merge-base(HEAD, main)``. For merged source PRs HEAD
    is often an ancestor of main, so merge-base == HEAD and EN menus look
    falsely complete (§6.140 / #48018).
    """
    from ydbdoc_review.github.git_ops import (
        merge_base,
        read_text_at_commit,
        resolve_commit_ref,
    )

    if authority is not None:
        en_base_sha = authority.baseline_sha
        ru_content_sha = authority.ru_sha
        ru_base_sha = authority.ru_base_sha
    else:
        en_base_sha = resolve_commit_ref(repo_path, merge_base_with)
        ru_content_sha = resolve_commit_ref(repo_path, ru_content_ref or "HEAD")
        ru_base_sha = (
            resolve_commit_ref(repo_path, ru_base_ref)
            if ru_base_ref is not None
            else merge_base(repo_path, en_base_sha, ru_content_sha)
        )

    def read_ru(path: str) -> str | None:
        return read_text_at_commit(repo_path, ru_content_sha, path)

    def read_en_base(path: str) -> str | None:
        return read_text_at_commit(repo_path, en_base_sha, path)

    def read_ru_base(path: str) -> str | None:
        return read_text_at_commit(repo_path, ru_base_sha, path)

    return read_ru, read_en_base, read_ru_base


def doc_pairs_from_plan(
    plan: TranslationScopePlan,
    *,
    docs_root: str = "ydb/docs",
    skip_en_paths: frozenset[str] | None = None,
    changes: list[tuple[str, ChangeKind]] | None = None,
) -> list[DocPair]:
    """``DocPair`` list for all markdown paths in the scope plan."""
    skip = skip_en_paths or frozenset()
    changed = {
        _norm(path): kind for path, kind in (changes or [])
    }
    pairs: list[DocPair] = []
    for ru_path in sorted(plan.doc_ru_paths):
        en_path = counterpart(ru_path, docs_root)
        if en_path is None or en_path in skip:
            continue
        pairs.append(
            DocPair(
                ru_path=ru_path,
                en_path=en_path,
                ru_changed=(
                    ru_path in changed
                    if changes is not None
                    else True
                ),
                en_changed=en_path in changed,
                ru_deleted=ru_path in plan.doc_deleted,
                en_deleted=changed.get(en_path) == "deleted",
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
    by_key: dict[tuple[str, str], NavigationPair] = {(p.ru_path, p.en_path): p for p in primary}
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
    active_doc_ru_paths: frozenset[str] | set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """``(extra_hrefs, extra_include_paths)`` from scope plan for one sidebar.

    Replaces ``extra_toc_hrefs_from_md_targets`` + ``extra_toc_hrefs_for_pair``
    (§22 J.6): href/include entries are derived from the unified plan, not from
    post-hoc basename intersection after translate.

    ``active_doc_ru_paths`` (§6.165): when set, only those RU markdown paths
    count for href extras. Callers must pass the paths **actually translated**
    (after bilingual skip §6.76). Using the full ``plan.doc_ru_paths`` pulls
    bilingual-skipped pages (e.g. ``quickstart.md``) into toc name retranslation
    and overwrites good EN menu labels (#48411 / #48589).
    """
    doc_paths = plan.doc_ru_paths
    if active_doc_ru_paths is not None:
        doc_paths = frozenset(doc_paths & frozenset(active_doc_ru_paths))
    extra_hrefs: set[str] = set()
    extra_includes: set[str] = set()
    for kind, rel in collect_toc_link_targets(ru_toc_text):
        if kind == "href" and rel.endswith(".md"):
            ru_md = _norm(resolve_toc_target_path(ru_toc, rel))
            if ru_md in doc_paths:
                extra_hrefs.add(rel)
        elif kind == "include" and rel.endswith((".yaml", ".yml")):
            ru_child = _norm(resolve_toc_target_path(ru_toc, rel))
            if ru_child in plan.nav_ru_paths:
                extra_includes.add(rel)
    return extra_hrefs, extra_includes
