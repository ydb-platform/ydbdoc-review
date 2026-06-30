"""Resolve locale ``{% include %}`` paths in mirrored ``docs/ru|en`` trees."""

from __future__ import annotations

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
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.pipeline.pairs import (
    is_docs_markdown,
    is_language_neutral_docs_path,
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
    """Parse markdown and return all ``{% include %}`` directives."""
    doc = parse_markdown(text)
    return iter_yfm_includes_in_blocks(doc.children)
