"""Path globs that ``doc_translate`` / nav merge must not touch (§6.167)."""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from pathlib import PurePosixPath

from ydbdoc_review.pipeline.pairs import ChangeKind


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def matches_translate_skip(path: str, globs: Sequence[str]) -> bool:
    """True when ``path`` (repo or toc-relative) matches a skip glob.

    Toc ``include.path`` / ``href`` values like ``public-materials/toc_p.yaml``
    are matched as well as full ``ydb/docs/ru/core/public-materials/...``.
    """
    if not globs:
        return False
    p = _norm(path)
    candidates = [p]
    parts = p.split("/")
    for i in range(1, len(parts)):
        candidates.append("/".join(parts[i:]))
    for cand in candidates:
        pp = PurePosixPath(cand)
        for raw in globs:
            g = raw.replace("\\", "/").strip()
            if not g:
                continue
            if pp.match(g):
                return True
            # ``public-materials/*`` style (single star = one segment or rest)
            if fnmatch.fnmatch(cand, g):
                return True
            if g.startswith("**/") and fnmatch.fnmatch(cand, g[3:]):
                return True
            if g.endswith("/**") and (
                cand == g[:-3]
                or cand.startswith(g[:-2])
                or fnmatch.fnmatch(cand, g)
            ):
                return True
    return False


def filter_translate_changes(
    changes: list[tuple[str, ChangeKind]],
    globs: Sequence[str],
) -> list[tuple[str, ChangeKind]]:
    """Drop PR file changes under ``translate_skip_globs``."""
    if not globs:
        return changes
    return [(p, k) for p, k in changes if not matches_translate_skip(p, globs)]


def filter_path_set(
    paths: frozenset[str] | set[str],
    globs: Sequence[str],
) -> frozenset[str]:
    if not globs:
        return frozenset(paths)
    return frozenset(p for p in paths if not matches_translate_skip(p, globs))


def toc_entry_is_skipped(item: dict[str, str], globs: Sequence[str]) -> bool:
    """True when a parsed toc item's href or include_path is under a skip glob."""
    if not globs:
        return False
    href = item.get("href") or ""
    include = item.get("include_path") or ""
    return bool(
        (href and matches_translate_skip(href, globs))
        or (include and matches_translate_skip(include, globs))
    )
