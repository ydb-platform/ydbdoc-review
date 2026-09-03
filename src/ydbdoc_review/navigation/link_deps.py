"""Markdown-link dependency queue for ``doc_translate`` (§6 / REQUIREMENTS).

When an internal ``.md`` link from a queued RU page resolves to a path with no
EN file in the frozen EN tree, enqueue the current RU version of that
dependency (same full one-pass translate). Redirect-to-existing-EN and any
existing EN file (even stale) skip enqueue. Dedup cycles/repeats. Initial
source-PR files do not consume the extra budget (default 20).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import unquote

from ydbdoc_review.navigation.redirects import (
    iter_redirect_mappings,
    redirect_public_path_to_repo_md,
)
from ydbdoc_review.pipeline.pairs import counterpart
from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href
from ydbdoc_review.validation.href_parity import collect_internal_hrefs

ReadFn = Callable[[str], str | None]

logger = logging.getLogger(__name__)

MAX_EXTRA_LINK_DEPS = 20

LINK_DEP_LIMIT_WARNING = (
    "link dependency budget exhausted ({limit}): missing EN for {link_path}; "
    "manual action required — translate or add EN mirror manually"
)


@dataclass(frozen=True)
class LinkDependencyResult:
    """Extra RU paths to translate plus yellow limit warnings."""

    queued_ru_paths: frozenset[str]
    warnings: tuple[str, ...]


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def en_repo_path_to_public(en_md: str, *, docs_root: str = "ydb/docs") -> str | None:
    """``ydb/docs/en/core/foo.md`` → ``/foo.md`` (Diplodoc public path)."""
    p = _norm(en_md)
    prefix = f"{docs_root.strip('/')}/en/core"
    if not p.startswith(prefix + "/") and p != prefix:
        return None
    rest = p[len(prefix) :]
    if not rest.startswith("/"):
        rest = "/" + rest
    return rest


def resolve_link_target_en_path(
    from_ru_md: str,
    href: str,
    *,
    docs_root: str = "ydb/docs",
) -> str | None:
    """Resolve an internal MD href to an EN repo ``.md`` path, or ``None``.

    Normalizes ``/ru/`` → ``/en/`` for absolute docs paths, then resolves
    relative hrefs via ``resolve_internal_md_href``.
    """
    raw = unquote((href or "").strip())
    if not raw or raw.startswith(("http://", "https://", "mailto:")):
        return None
    if raw.startswith("#"):
        return None
    path_part = raw.split("#", 1)[0].strip()
    if not path_part or not path_part.endswith(".md"):
        return None

    root = docs_root.strip("/")
    if path_part.startswith("/"):
        p = _norm(path_part)
        if p.startswith("/ru/"):
            p = "/en/" + p[len("/ru/") :]
        if p.startswith(f"/{root}/ru/"):
            p = f"/{root}/en/" + p[len(f"/{root}/ru/") :]
        if p.startswith(f"/{root}/en/"):
            return _norm(p.lstrip("/"))
        if p.startswith("/en/"):
            return f"{root}{p}"
        # Diplodoc public path under core/
        return redirect_public_path_to_repo_md(p, locale="en", docs_root=docs_root)

    return resolve_internal_md_href(from_ru_md, path_part)


def en_present_in_frozen_tree(
    en_md: str,
    *,
    read_en: ReadFn,
    redirect_from_to: dict[str, str],
    docs_root: str = "ydb/docs",
) -> bool:
    """True when EN file exists or redirects to an existing EN path (§6)."""
    en_md = _norm(en_md)
    if read_en(en_md) is not None:
        return True
    public = en_repo_path_to_public(en_md, docs_root=docs_root)
    if public is None:
        return False
    to_public = redirect_from_to.get(public)
    if not to_public:
        return False
    to_en = redirect_public_path_to_repo_md(to_public, locale="en", docs_root=docs_root)
    return read_en(_norm(to_en)) is not None


def collect_md_link_dependencies(
    seed_ru_paths: Iterable[str],
    *,
    read_ru: ReadFn,
    read_en: ReadFn,
    redirects_yaml: str | None = None,
    docs_root: str = "ydb/docs",
    max_extra: int = MAX_EXTRA_LINK_DEPS,
    already_queued: Iterable[str] | None = None,
) -> LinkDependencyResult:
    """BFS Markdown-link deps missing from the frozen EN tree.

    ``seed_ru_paths`` / ``already_queued`` are walked for outgoing links but do
    not consume ``max_extra``. Only newly enqueued link targets count toward
    the budget. After the limit, further missing targets yield warnings and are
    not queued (no infinite recursion).
    """
    root = docs_root.strip("/")
    redirect_from_to = iter_redirect_mappings(redirects_yaml or "")

    known: set[str] = {_norm(p) for p in seed_ru_paths}
    if already_queued is not None:
        known.update(_norm(p) for p in already_queued)

    extras: set[str] = set()
    warnings: list[str] = []
    # Paths whose outgoing links we still need to scan.
    queue = sorted(known)
    scanned: set[str] = set()

    while queue:
        ru_md = queue.pop(0)
        if ru_md in scanned:
            continue
        scanned.add(ru_md)
        ru_text = read_ru(ru_md)
        if not ru_text:
            continue
        for href in collect_internal_hrefs(ru_text):
            en_target = resolve_link_target_en_path(ru_md, href, docs_root=root)
            if en_target is None:
                continue
            if not en_target.startswith(f"{root}/en/") or not en_target.endswith(".md"):
                continue
            if en_present_in_frozen_tree(
                en_target,
                read_en=read_en,
                redirect_from_to=redirect_from_to,
                docs_root=root,
            ):
                continue
            ru_dep = counterpart(en_target, root)
            if ru_dep is None:
                continue
            ru_dep = _norm(ru_dep)
            if ru_dep in known or ru_dep in extras:
                continue
            if read_ru(ru_dep) is None:
                continue
            link_path = en_target
            if len(extras) >= max_extra:
                warnings.append(
                    LINK_DEP_LIMIT_WARNING.format(limit=max_extra, link_path=link_path)
                )
                continue
            extras.add(ru_dep)
            known.add(ru_dep)
            queue.append(ru_dep)

    if warnings:
        logger.warning(
            "Markdown-link dependency budget hit: %s warning(s), %s extras queued",
            len(warnings),
            len(extras),
        )
    return LinkDependencyResult(
        queued_ru_paths=frozenset(extras),
        warnings=tuple(warnings),
    )
