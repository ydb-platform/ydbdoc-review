"""Resolve locale ``{% include %}`` paths in mirrored ``docs/ru|en`` trees."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ydbdoc_review.parsing.ast_types import (
    BlockNode,
    BlockQuote,
    BulletList,
    OrderedList,
    YfmCut,
    YfmIf,
    YfmInclude,
    YfmNote,
    YfmTabs,
)
from ydbdoc_review.pipeline.pairs import (
    is_docs_markdown,
    is_language_neutral_docs_path,
)

# Same single-line pattern as ``yfm_plugins/includes.py`` (block rule).
_YFM_INCLUDE_LINE_RE = re.compile(
    r"^\{%\s*include\s+(?:(notitle)\s+)?\[([^\]]*)\]\(([^)]+)\)\s*%\}\s*$"
)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def _posix_join(base_dir: str, rel: str) -> str:
    parts = _norm(base_dir).split("/")
    for segment in _norm(rel).split("/"):
        if segment == "..":
            if parts:
                parts.pop()
        elif segment and segment != ".":
            parts.append(segment)
    return "/".join(parts)


def _locale_root_shared_include_resolved(resolved: str, *, docs_root: str) -> bool:
    """True when ``docs/{ru|en}/_includes/…`` is a mis-resolved repo-root snippet.

    Recipe pages often reference shared SDK snippets as
    ``../../../_includes/go/foo.md`` (three ``..`` from ``…/ydb-sdk/``), which
    lands on ``docs/ru/_includes/…`` instead of language-neutral
    ``docs/_includes/…``. Those files are not mirrored RU↔EN.
    """
    root = docs_root.strip("/")
    p = _norm(resolved)
    for locale in ("ru", "en"):
        if p.startswith(f"{root}/{locale}/_includes/"):
            return True
    return False


def resolve_locale_md_path(
    base_md_path: str,
    include_ref: str,
    *,
    docs_root: str = "ydb/docs",
) -> str | None:
    """Map an include ``path`` relative to ``base_md_path`` to a locale mirror ``.md``."""
    ref = include_ref.strip().split("#", 1)[0].strip()
    if not ref or not ref.endswith(".md"):
        return None

    root = docs_root.strip("/")
    base = _norm(base_md_path)

    if ref.startswith("/"):
        resolved = _norm(ref.lstrip("/"))
    else:
        resolved = _posix_join(str(PurePosixPath(base).parent), ref)

    if is_language_neutral_docs_path(resolved, docs_root):
        return None
    if _locale_root_shared_include_resolved(resolved, docs_root=docs_root):
        return None
    if not is_docs_markdown(resolved, docs_root):
        return None
    if not resolved.startswith(f"{root}/ru/") and not resolved.startswith(
        f"{root}/en/"
    ):
        return None
    return resolved


def iter_yfm_includes_in_blocks(blocks: list[BlockNode]) -> list[YfmInclude]:
    """Collect ``YfmInclude`` nodes from a block subtree (document order)."""
    out: list[YfmInclude] = []

    def walk(nodes: list[BlockNode]) -> None:
        for block in nodes:
            if isinstance(block, YfmInclude):
                out.append(block)
            elif isinstance(block, (BlockQuote, YfmCut, YfmNote)):
                walk(block.children)
            elif isinstance(block, YfmIf):
                for branch in block.branches:
                    walk(branch.children)
            elif isinstance(block, YfmTabs):
                for tab in block.children:
                    walk(tab.children)
            elif isinstance(block, (BulletList, OrderedList)):
                for item in block.children:
                    walk(item.children)

    walk(blocks)
    return out


def collect_yfm_includes(text: str) -> list[YfmInclude]:
    """Return all ``{% include %}`` directives (line scan, no full AST).

    Include directives are single-line YFM blocks. Line scan avoids parser
    failures on include fragments that are bare bullet lists (e.g.
    ``export-additional-params.md``) where mdit emits spurious ``front_matter``
    tokens inside nested list items.
    """
    out: list[YfmInclude] = []
    for line in text.splitlines():
        m = _YFM_INCLUDE_LINE_RE.match(line.strip())
        if not m:
            continue
        out.append(
            YfmInclude(
                text=m.group(2),
                path=m.group(3),
                notitle=bool(m.group(1)),
            )
        )
    return out
