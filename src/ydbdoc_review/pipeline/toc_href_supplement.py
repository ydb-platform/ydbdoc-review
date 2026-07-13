"""Add RU/EN pairs for sidebar ``href`` targets missing on EN main (§6.89)."""

from __future__ import annotations

import logging

from ydbdoc_review.github.git_ops import read_text, read_text_at_ref
from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.pipeline.pairs import ChangeKind, DocPair, NavigationPair, counterpart

logger = logging.getLogger(__name__)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _read_ru_toc(repo_path: str, ru_toc: str) -> str:
    text = read_text(repo_path, ru_toc)
    if text is not None:
        return text
    head = read_text_at_ref(repo_path, "HEAD", ru_toc)
    return head or ""


def _en_md_on_base(
    repo_path: str, merge_base_with: str, en_md: str
) -> bool:
    return read_text_at_ref(repo_path, merge_base_with, en_md) is not None


def _ru_md_exists(repo_path: str, ru_md: str) -> bool:
    if read_text(repo_path, ru_md) is not None:
        return True
    return read_text_at_ref(repo_path, "HEAD", ru_md) is not None


def _ru_tocs_to_scan(
    nav_pairs: list[NavigationPair],
    *,
    repo_path: str,
) -> list[str]:
    """RU toc yaml files to scan, following ``include.path`` to child sidebars."""
    queue = [_norm(p.ru_path) for p in nav_pairs]
    seen: set[str] = set()
    out: list[str] = []

    while queue:
        ru_toc = queue.pop(0)
        if ru_toc in seen:
            continue
        seen.add(ru_toc)
        text = _read_ru_toc(repo_path, ru_toc)
        if not text.strip():
            continue
        out.append(ru_toc)
        for kind, rel in collect_toc_link_targets(text):
            if kind != "include" or not rel.endswith((".yaml", ".yml")):
                continue
            ru_child = _norm(resolve_toc_target_path(ru_toc, rel))
            if ru_child not in seen:
                queue.append(ru_child)
    return out


def supplement_toc_href_pairs(
    pairs: list[DocPair],
    nav_pairs: list[NavigationPair],
    *,
    repo_path: str,
    merge_base_with: str,
    docs_root: str = "ydb/docs",
) -> tuple[list[DocPair], list[tuple[str, ChangeKind]]]:
    """Translate RU pages listed in queued navigation sidebars when EN is absent.

    When EN toc is mirrored from RU (§6.85) or child sidebars are supplemented
    (§6.84), href targets must exist as EN ``.md`` files — same rule as locale
    ``{% include %}`` supplementation (§6.80).
    """
    if not nav_pairs:
        return pairs, []

    by_ru = {pair.ru_path: pair for pair in pairs}
    extra_changes: list[tuple[str, ChangeKind]] = []

    for ru_toc in _ru_tocs_to_scan(nav_pairs, repo_path=repo_path):
        ru_toc_text = _read_ru_toc(repo_path, ru_toc)
        if not ru_toc_text:
            continue
        for kind, rel in collect_toc_link_targets(ru_toc_text):
            if kind != "href" or not rel.endswith(".md"):
                continue
            ru_md = _norm(resolve_toc_target_path(ru_toc, rel))
            en_md = counterpart(ru_md, docs_root)
            if en_md is None or ru_md in by_ru:
                continue
            if not _ru_md_exists(repo_path, ru_md):
                continue
            if _en_md_on_base(repo_path, merge_base_with, en_md):
                continue

            by_ru[ru_md] = DocPair(
                ru_path=ru_md,
                en_path=en_md,
                ru_changed=True,
            )
            kind_change: ChangeKind = (
                "added"
                if read_text_at_ref(repo_path, merge_base_with, ru_md) is None
                else "modified"
            )
            extra_changes.append((ru_md, kind_change))
            logger.info(
                "Supplement toc href pair from %s → %s (%s)",
                ru_toc,
                ru_md,
                kind_change,
            )

    return sorted(by_ru.values(), key=lambda p: p.ru_path), extra_changes
