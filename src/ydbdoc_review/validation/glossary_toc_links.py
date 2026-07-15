"""EN toc reachability and glossary link stripping (YFM003 variant A)."""

from __future__ import annotations

from collections import deque
from pathlib import PurePosixPath
from typing import Callable

from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.parsing.ast_types import (
    BlockQuote,
    BulletList,
    Document,
    InlineEmphasis,
    InlineLink,
    InlineNode,
    InlineStrong,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    TableCell,
    TableRow,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTabs,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown

ReadText = Callable[[str], str | None]

_DEFAULT_EN_ROOT_TOC = "ydb/docs/en/core/toc_p.yaml"


def normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def ru_toc_to_en_path(ru_toc_path: str, *, docs_root: str = "ydb/docs") -> str | None:
    """Map ``…/ru/…/toc*.yaml`` to the EN mirror path."""
    normalized = normalize_repo_path(ru_toc_path)
    prefix = f"{docs_root}/ru/"
    if not normalized.startswith(prefix):
        return None
    return f"{docs_root}/en/{normalized[len(prefix) :]}"


def resolve_internal_md_href(from_file: str, href: str) -> str | None:
    """Return normalized repo path for a relative ``.md`` href, or ``None``."""
    if not href or href.startswith(("http://", "https://", "mailto:")):
        return None
    if href.startswith("#"):
        return None
    path_part = href.split("#", 1)[0].strip()
    if not path_part or not path_part.endswith(".md"):
        return None
    base = PurePosixPath(normalize_repo_path(from_file)).parent
    parts: list[str] = []
    for part in (base / path_part).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return normalize_repo_path("/".join(parts))


def _read_toc_yaml(read_text: ReadText, en_toc_path: str) -> str | None:
    text = read_text(en_toc_path)
    if text is not None:
        return text
    ru_path = en_toc_path.replace("/en/", "/ru/", 1)
    return read_text(ru_path)


def collect_en_toc_reachable_md(
    read_text: ReadText,
    *,
    root_toc: str = _DEFAULT_EN_ROOT_TOC,
    extra_md_paths: set[str] | frozenset[str] = frozenset(),
    extra_toc_paths: set[str] | frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Collect EN ``.md`` paths reachable from the sidebar toc graph."""
    reachable: set[str] = {normalize_repo_path(p) for p in extra_md_paths}
    toc_queue: deque[str] = deque()
    seen_tocs: set[str] = set()

    for toc in (root_toc, *extra_toc_paths):
        normalized = normalize_repo_path(toc)
        if normalized not in seen_tocs:
            toc_queue.append(normalized)

    while toc_queue:
        toc_path = toc_queue.popleft()
        if toc_path in seen_tocs:
            continue
        seen_tocs.add(toc_path)
        yaml_text = _read_toc_yaml(read_text, toc_path)
        if not yaml_text:
            continue
        for kind, rel in collect_toc_link_targets(yaml_text):
            resolved = normalize_repo_path(resolve_toc_target_path(toc_path, rel))
            if kind == "href" and resolved.endswith(".md"):
                reachable.add(resolved)
            elif kind == "include" and resolved not in seen_tocs:
                toc_queue.append(resolved)
    return frozenset(reachable)


def build_en_toc_reachable_from_repo(
    repo_path: str,
    *,
    docs_root: str = "ydb/docs",
    pending_en_md: set[str] | frozenset[str] = frozenset(),
    pending_en_tocs: set[str] | frozenset[str] = frozenset(),
    read_text: ReadText | None = None,
) -> frozenset[str]:
    """Build reachability set from a local checkout."""
    if read_text is None:
        from ydbdoc_review.github.git_ops import read_text as _read

        def read_text(path: str) -> str | None:
            return _read(repo_path, path)

    root_toc = f"{docs_root}/en/core/toc_p.yaml"
    return collect_en_toc_reachable_md(
        read_text,
        root_toc=root_toc,
        extra_md_paths={
            normalize_repo_path(p) for p in pending_en_md
        },
        extra_toc_paths={
            normalize_repo_path(p) for p in pending_en_tocs
        },
    )


def _walk_inline(
    nodes: list[InlineNode],
    *,
    from_file: str,
    reachable: frozenset[str],
) -> None:
    i = 0
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, InlineLink):
            target = resolve_internal_md_href(from_file, node.href)
            if target is not None and target not in reachable:
                nodes[i : i + 1] = list(node.children)
                continue
            _walk_inline(node.children, from_file=from_file, reachable=reachable)
        elif isinstance(node, (InlineEmphasis, InlineStrong)):
            _walk_inline(node.children, from_file=from_file, reachable=reachable)
        i += 1


def _walk_blocks(blocks, *, from_file: str, reachable: frozenset[str]) -> None:
    for block in blocks:
        if isinstance(block, Paragraph):
            _walk_inline(block.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    _walk_blocks(item.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, BlockQuote):
            _walk_blocks(block.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, Table):
            for row in block.children:
                if isinstance(row, TableRow):
                    for cell in row.children:
                        if isinstance(cell, TableCell):
                            _walk_inline(cell.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, (YfmNote, YfmCut, YfmIf, YfmTabs)):
            _walk_blocks(block.children, from_file=from_file, reachable=reachable)


def strip_unreachable_glossary_links(
    text: str,
    *,
    file_path: str,
    reachable: frozenset[str],
    target_lang: str = "en",
) -> str:
    """Drop internal ``.md`` links whose targets are outside the EN toc graph."""
    if not reachable:
        return text
    doc = parse_markdown(text)
    _walk_blocks(doc.children, from_file=file_path, reachable=reachable)
    return render_markdown(doc, target_lang=target_lang)
