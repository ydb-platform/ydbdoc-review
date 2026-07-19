"""EN toc reachability and internal link stripping (YFM003 variant A)."""

from __future__ import annotations

import re
from collections import deque
from pathlib import PurePosixPath
from typing import Callable

from ydbdoc_review.navigation.toc import collect_toc_link_targets, resolve_toc_target_path
from ydbdoc_review.parsing.ast_types import (
    BlockQuote,
    BulletList,
    Heading,
    InlineEmphasis,
    InlineLink,
    InlineNode,
    InlineStrong,
    ListItem,
    OrderedList,
    Paragraph,
    Table,
    TermDefinition,
    YfmCut,
    YfmIf,
    YfmNote,
    YfmTab,
    YfmTabs,
)
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.rendering.markdown_renderer import render_markdown

ReadText = Callable[[str], str | None]

_DEFAULT_EN_ROOT_TOC = "ydb/docs/en/core/toc_p.yaml"
_DOCS_ROOT = "ydb/docs"


def normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def en_mirror_path(file_path: str, *, docs_root: str = _DOCS_ROOT) -> str:
    """Map a RU doc path to its EN mirror for relative link resolution."""
    normalized = normalize_repo_path(file_path)
    ru_prefix = f"{docs_root}/ru/"
    en_prefix = f"{docs_root}/en/"
    if normalized.startswith(ru_prefix):
        return en_prefix + normalized[len(ru_prefix) :]
    return normalized


def ru_toc_to_en_path(ru_toc_path: str, *, docs_root: str = _DOCS_ROOT) -> str | None:
    """Map ``…/ru/…/toc*.yaml`` to the EN mirror path."""
    normalized = normalize_repo_path(ru_toc_path)
    prefix = f"{docs_root}/ru/"
    if not normalized.startswith(prefix):
        return None
    return f"{docs_root}/en/{normalized[len(prefix) :]}"


def resolve_internal_md_href(from_file: str, href: str) -> str | None:
    """Return normalized EN repo path for a relative ``.md`` href, or ``None``."""
    if not href or href.startswith(("http://", "https://", "mailto:")):
        return None
    if href.startswith("#"):
        return None
    path_part = href.split("#", 1)[0].strip()
    if not path_part or not path_part.endswith(".md"):
        return None
    from_en = en_mirror_path(from_file)
    base = PurePosixPath(from_en).parent
    parts: list[str] = []
    for part in (base / path_part).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return normalize_repo_path("/".join(parts))


_MD_LINK_HREF = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def md_link_basenames_outside_reachable(
    text: str,
    *,
    file_path: str,
    reachable: frozenset[str],
) -> set[str]:
    """Basenames of internal ``.md`` links whose EN targets are outside toc reachability.

    Used by ``md_link_parity`` / critic filters so intentional strip (§6.107) does
    not fail QA for missing EN mirrors of those links.
    """
    ignore: set[str] = set()
    for match in _MD_LINK_HREF.finditer(text):
        href = match.group(1).strip()
        path_part = href.split("#", 1)[0].strip()
        if not path_part.endswith(".md"):
            continue
        target = resolve_internal_md_href(file_path, href)
        if target is not None and target not in reachable:
            ignore.add(PurePosixPath(path_part).name)
    return ignore


def _read_toc_yaml(
    read_text: ReadText,
    en_toc_path: str,
    *,
    allow_ru_fallback: bool,
) -> str | None:
    """Read EN toc yaml; optional RU mirror only for pending PR nav files."""
    text = read_text(en_toc_path)
    if text is not None:
        return text
    if not allow_ru_fallback:
        return None
    ru_path = en_toc_path.replace("/en/", "/ru/", 1)
    return read_text(ru_path)


def collect_en_toc_reachable_md(
    read_text: ReadText,
    *,
    root_toc: str = _DEFAULT_EN_ROOT_TOC,
    extra_md_paths: set[str] | frozenset[str] = frozenset(),
    extra_toc_paths: set[str] | frozenset[str] = frozenset(),
    seed_extra_md: bool = True,
) -> frozenset[str]:
    """Collect EN ``.md`` paths reachable from the **EN-only** sidebar toc graph.

    When ``seed_extra_md`` is True (default, strip path), pending translated pages
    are treated as already reachable. When False (orphan-page QA), only pages
    actually listed via toc ``href`` (and existing / pending on disk) count.
    """
    pending_tocs = frozenset(normalize_repo_path(p) for p in extra_toc_paths)
    pending_md = frozenset(normalize_repo_path(p) for p in extra_md_paths)
    reachable: set[str] = set(pending_md) if seed_extra_md else set()
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
        yaml_text = _read_toc_yaml(
            read_text,
            toc_path,
            allow_ru_fallback=toc_path in pending_tocs,
        )
        if not yaml_text:
            continue
        for kind, rel in collect_toc_link_targets(yaml_text):
            resolved = normalize_repo_path(resolve_toc_target_path(toc_path, rel))
            if kind == "href" and resolved.endswith(".md"):
                # Diplodoc YFM003: href must exist on disk in EN checkout
                # (or be a pending translate target for this PR).
                if read_text(resolved) is not None or resolved in pending_md:
                    reachable.add(resolved)
            elif kind == "include" and resolved not in seen_tocs:
                toc_queue.append(resolved)
    return frozenset(reachable)


def build_en_toc_reachable_from_repo(
    repo_path: str,
    *,
    docs_root: str = _DOCS_ROOT,
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
        extra_md_paths={normalize_repo_path(p) for p in pending_en_md},
        extra_toc_paths={normalize_repo_path(p) for p in pending_en_tocs},
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


def _walk_table_cells(table: Table, walk_inline) -> None:
    for row in (table.header, *table.rows):
        for cell in row.cells:
            walk_inline(cell.children)


def _walk_blocks(blocks, *, from_file: str, reachable: frozenset[str]) -> None:
    for block in blocks:
        if isinstance(block, (Paragraph, Heading)):
            _walk_inline(block.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, TermDefinition):
            _walk_inline(block.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, (BulletList, OrderedList)):
            for item in block.children:
                if isinstance(item, ListItem):
                    _walk_blocks(item.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, BlockQuote):
            _walk_blocks(block.children, from_file=from_file, reachable=reachable)
        elif isinstance(block, Table):
            _walk_table_cells(
                block,
                lambda nodes: _walk_inline(
                    nodes, from_file=from_file, reachable=reachable
                ),
            )
        elif isinstance(block, YfmIf):
            for branch in block.branches:
                _walk_blocks(
                    branch.children, from_file=from_file, reachable=reachable
                )
        elif isinstance(block, YfmTabs):
            for tab in block.children:
                if isinstance(tab, YfmTab):
                    _walk_inline(
                        tab.title, from_file=from_file, reachable=reachable
                    )
                    _walk_blocks(
                        tab.children, from_file=from_file, reachable=reachable
                    )
        elif isinstance(block, (YfmNote, YfmCut)):
            _walk_blocks(block.children, from_file=from_file, reachable=reachable)


def strip_unreachable_internal_links(
    text: str,
    *,
    file_path: str,
    reachable: frozenset[str],
    target_lang: str = "en",
    out_stripped: list[str] | None = None,
) -> str:
    """Drop internal ``.md`` links whose targets are outside the EN toc graph."""
    doc = parse_markdown(text)
    from_en = en_mirror_path(file_path)
    stripped: list[str] = []

    def _walk_inline_count(
        nodes: list[InlineNode],
        *,
        from_file: str,
    ) -> None:
        i = 0
        while i < len(nodes):
            node = nodes[i]
            if isinstance(node, InlineLink):
                target = resolve_internal_md_href(from_file, node.href)
                if target is not None and target not in reachable:
                    stripped.append(node.href)
                    nodes[i : i + 1] = list(node.children)
                    continue
                _walk_inline_count(node.children, from_file=from_file)
            elif isinstance(node, (InlineEmphasis, InlineStrong)):
                _walk_inline_count(node.children, from_file=from_file)
            i += 1

    def _walk_blocks_count(blocks) -> None:
        for block in blocks:
            if isinstance(block, (Paragraph, Heading)):
                _walk_inline_count(block.children, from_file=from_en)
            elif isinstance(block, TermDefinition):
                _walk_inline_count(block.children, from_file=from_en)
            elif isinstance(block, (BulletList, OrderedList)):
                for item in block.children:
                    if isinstance(item, ListItem):
                        _walk_blocks_count(item.children)
            elif isinstance(block, BlockQuote):
                _walk_blocks_count(block.children)
            elif isinstance(block, Table):
                _walk_table_cells(
                    block,
                    lambda nodes: _walk_inline_count(nodes, from_file=from_en),
                )
            elif isinstance(block, YfmIf):
                for branch in block.branches:
                    _walk_blocks_count(branch.children)
            elif isinstance(block, YfmTabs):
                for tab in block.children:
                    if isinstance(tab, YfmTab):
                        _walk_inline_count(tab.title, from_file=from_en)
                        _walk_blocks_count(tab.children)
            elif isinstance(block, (YfmNote, YfmCut)):
                _walk_blocks_count(block.children)

    _walk_blocks_count(doc.children)
    if out_stripped is not None:
        out_stripped.extend(stripped)
    return render_markdown(doc, target_lang=target_lang)


# Backward-compatible alias
strip_unreachable_glossary_links = strip_unreachable_internal_links
