"""Diplodoc toc*.yaml — parse, diff-scoped merge, validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_ITEM_SPLIT = re.compile(r"(?m)^- name: ")
_HREF_LINE = re.compile(r"^  href: (.+)$", re.MULTILINE)
_INCLUDE_PATH = re.compile(r"^\s+path: (.+)$", re.MULTILINE)
_INCLUDE_PATH_INLINE = re.compile(
    r"include:\s*\{[^}]*\bpath:\s*([^,}\s]+)",
    re.MULTILINE,
)
_INCLUDE_ONLY_ITEM = re.compile(
    r"(?m)^\s*-\s+include:\s*\{[^}]*\bpath:\s*([^,}\s]+)",
)
# Diplodoc ydb docs often use one-line inline items:
#   - { name: Overview, href: index.md, when: ... }
_INLINE_ITEM = re.compile(
    r"(?m)^\s*- \{\s*name:\s*(.+?)\s*,\s*href:\s*(\S+)",
)
_NAME_LINE = re.compile(r"^(\s*)- name: (.+)$")
_HREF_INDENTED = re.compile(r"^(\s*)href: (.+)$")
_NESTED_ITEMS_LINE = re.compile(r"^(\s*)items:\s*$")


@dataclass(frozen=True)
class TocTranslateScope:
    """Sidebar entries whose Russian ``name`` must be translated for this PR."""

    hrefs: frozenset[str]
    include_paths: frozenset[str]

    def with_extra_hrefs(self, extra: set[str]) -> TocTranslateScope:
        return TocTranslateScope(self.hrefs | frozenset(extra), self.include_paths)

    def with_extra_include_paths(self, extra: set[str]) -> TocTranslateScope:
        return TocTranslateScope(self.hrefs, self.include_paths | frozenset(extra))


@dataclass
class TocNode:
    name: str
    href: str | None = None
    include_path: str | None = None
    children: list[TocNode] = field(default_factory=list)
    block: str = ""


def _attach_include_path(node: TocNode) -> None:
    """Set ``include_path`` from block body when entry is an ``include:`` link."""
    if node.children:
        return
    for pattern in (_INCLUDE_PATH, _INCLUDE_PATH_INLINE):
        match = pattern.search(node.block)
        if match:
            node.include_path = match.group(1).strip().strip("'\"")
            return


def iter_toc_include_paths(yaml_text: str) -> list[str]:
    """All ``include.path`` values in toc YAML (block, inline, include-only items)."""
    return _iter_toc_include_paths(yaml_text)


def toc_entry_paths(yaml_text: str) -> tuple[set[str], set[str]]:
    """Return ``(hrefs, include_paths)`` referenced by a toc file."""
    hrefs = {it["href"] for it in parse_toc_items(yaml_text) if it.get("href")}
    includes = set(iter_toc_include_paths(yaml_text))
    return hrefs, includes


def en_toc_is_absent(en_main_yaml: str) -> bool:
    """True when EN sidebar yaml is missing or has no navigable entries."""
    text = en_main_yaml.replace("\r\n", "\n").strip()
    if not text:
        return True
    hrefs, includes = toc_entry_paths(en_main_yaml)
    return not hrefs and not includes


def _iter_toc_include_paths(yaml_text: str) -> list[str]:
    """All ``include.path`` values in toc YAML (block, inline, include-only items)."""
    text = yaml_text.replace("\r\n", "\n")
    paths: list[str] = []
    seen: set[str] = set()
    for pattern in (_INCLUDE_PATH, _INCLUDE_PATH_INLINE, _INCLUDE_ONLY_ITEM):
        for match in pattern.finditer(text):
            path = match.group(1).strip().strip("'\"")
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _top_level_list_indent(lines: list[str], start: int) -> int:
    """Indent of the first ``- name:`` after root ``items:`` (0 or 2 spaces)."""
    for i in range(start, len(lines)):
        match = _NAME_LINE.match(lines[i])
        if match:
            return len(match.group(1))
    return 0


def _has_nested_block_items(yaml_text: str) -> bool:
    """True when block-format toc has ``items:`` under a ``- name:`` entry."""
    text = yaml_text.replace("\r\n", "\n")
    if _parse_toc_items_inline(text):
        return False
    for line in text.splitlines():
        if line.startswith("items:"):
            continue
        m = _NESTED_ITEMS_LINE.match(line)
        if m and len(m.group(1)) >= 2:
            return True
    return False


def _parse_toc_tree_block(yaml_text: str) -> list[TocNode]:
    text = yaml_text.replace("\r\n", "\n")
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.strip() == "items:":
            start = i + 1
            break
    list_indent = _top_level_list_indent(lines, start)
    nodes, _ = _parse_toc_nodes_at_level(lines, start, list_indent=list_indent)
    return nodes


def _parse_toc_nodes_at_level(
    lines: list[str],
    start: int,
    *,
    list_indent: int,
) -> tuple[list[TocNode], int]:
    nodes: list[TocNode] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m_name = _NAME_LINE.match(line)
        if not m_name:
            break
        item_indent = len(m_name.group(1))
        if item_indent < list_indent:
            break
        if item_indent > list_indent:
            break

        node = TocNode(name=m_name.group(2).strip())
        block_lines = [line]
        i += 1
        while i < len(lines):
            inner = lines[i]
            if not inner.strip():
                block_lines.append(inner)
                i += 1
                continue

            m_sibling = _NAME_LINE.match(inner)
            if m_sibling and len(m_sibling.group(1)) == item_indent:
                break
            if m_sibling and len(m_sibling.group(1)) < item_indent:
                break

            m_href = _HREF_INDENTED.match(inner)
            m_items = _NESTED_ITEMS_LINE.match(inner)
            if m_href and len(m_href.group(1)) == item_indent + 2:
                node.href = m_href.group(2).strip()
                block_lines.append(inner)
                i += 1
                continue
            if m_items and len(m_items.group(1)) == item_indent + 2:
                block_lines.append(inner)
                i += 1
                children, i = _parse_toc_nodes_at_level(
                    lines,
                    i,
                    list_indent=item_indent + 2,
                )
                node.children = children
                continue

            block_lines.append(inner)
            i += 1

        node.block = "\n".join(block_lines).rstrip() + "\n"
        _attach_include_path(node)
        nodes.append(node)
    return nodes, i


def _flatten_toc_nodes(nodes: list[TocNode]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for node in nodes:
        if node.href:
            items.append(
                {
                    "name": node.name,
                    "href": node.href,
                    "block": node.block,
                }
            )
        elif node.include_path:
            items.append(
                {
                    "name": node.name,
                    "include_path": node.include_path,
                    "block": node.block,
                }
            )
        if node.children:
            items.extend(_flatten_toc_nodes(node.children))
    return items


def _leaf_block(name: str, href: str, *, list_indent: int) -> str:
    pad = " " * list_indent
    child_pad = " " * (list_indent + 2)
    return (
        f"{pad}- name: {name}\n"
        f"{child_pad}href: {href}\n"
    )


def _serialize_toc_tree(nodes: list[TocNode], *, list_indent: int = 0) -> str:
    body = "".join(_serialize_toc_node(node, list_indent=list_indent) for node in nodes)
    if not body.endswith("\n"):
        body += "\n"
    return "items:\n" + body


def _serialize_toc_node(node: TocNode, *, list_indent: int) -> str:
    if node.children:
        pad = " " * list_indent
        child_pad = " " * (list_indent + 2)
        lines = [f"{pad}- name: {node.name}", f"{child_pad}items:"]
        for child in node.children:
            lines.append(_serialize_toc_node(child, list_indent=list_indent + 2).rstrip("\n"))
        return "\n".join(lines) + "\n"
    if node.block and node.block.strip():
        return node.block
    if node.href:
        return _leaf_block(node.name, node.href, list_indent=list_indent)
    return ""


def _index_toc_leaves(nodes: list[TocNode]) -> dict[str, TocNode]:
    by_href: dict[str, TocNode] = {}
    for node in nodes:
        if node.href:
            by_href[node.href] = node
        if node.children:
            by_href.update(_index_toc_leaves(node.children))
    return by_href


def _collect_toc_hrefs(nodes: list[TocNode]) -> set[str]:
    hrefs: set[str] = set()
    for node in nodes:
        if node.href:
            hrefs.add(node.href)
        if node.children:
            hrefs.update(_collect_toc_hrefs(node.children))
    return hrefs


def _index_toc_includes(nodes: list[TocNode]) -> dict[str, TocNode]:
    by_path: dict[str, TocNode] = {}
    for node in nodes:
        if node.include_path:
            by_path[node.include_path] = node
        if node.children:
            by_path.update(_index_toc_includes(node.children))
    return by_path


def _collect_toc_include_paths(nodes: list[TocNode]) -> set[str]:
    paths: set[str] = set()
    for node in nodes:
        if node.include_path:
            paths.add(node.include_path)
        if node.children:
            paths.update(_collect_toc_include_paths(node.children))
    return paths


def _walk_toc_nodes(nodes: list[TocNode]):
    for node in nodes:
        yield node
        if node.children:
            yield from _walk_toc_nodes(node.children)


def _merge_toc_tree_nodes(
    en_nodes: list[TocNode],
    ru_nodes: list[TocNode],
    *,
    en_by_href: dict[str, TocNode],
    en_by_include: dict[str, TocNode],
    translate_hrefs: set[str],
    translate_include_paths: set[str],
    translate_name: Callable[[str], str],
    ru_base_hrefs: set[str] | None = None,
    ru_base_include_paths: set[str] | None = None,
    restrict_gap_fill_to_scope: bool = False,
) -> list[TocNode]:
    merged: list[TocNode] = []
    base_hrefs = ru_base_hrefs or set()
    base_includes = ru_base_include_paths or set()
    for idx, ru_node in enumerate(ru_nodes):
        en_node = en_nodes[idx] if idx < len(en_nodes) else None
        if ru_node.href:
            href = ru_node.href
            if href in en_by_href and href not in translate_hrefs:
                merged.append(en_by_href[href])
            elif href in translate_hrefs or (
                not restrict_gap_fill_to_scope
                and href not in en_by_href
                and href in base_hrefs
            ):
                en_name = translate_name(ru_node.name).strip()
                list_indent = 0
                if en_node and en_node.block:
                    m = _NAME_LINE.match(en_node.block.splitlines()[0])
                    if m:
                        list_indent = len(m.group(1))
                elif ru_node.block:
                    m = _NAME_LINE.match(ru_node.block.splitlines()[0])
                    if m:
                        list_indent = len(m.group(1))
                merged.append(
                    TocNode(
                        name=en_name,
                        href=href,
                        block=_leaf_block(en_name, href, list_indent=list_indent),
                    )
                )
            continue

        if ru_node.include_path:
            path = ru_node.include_path
            if path in en_by_include and path not in translate_include_paths:
                merged.append(en_by_include[path])
            elif path in translate_include_paths or (
                not restrict_gap_fill_to_scope
                and path not in en_by_include
                and path in base_includes
            ):
                en_name = translate_name(ru_node.name).strip()
                block = _replace_item_name(ru_node.block, en_name)
                merged.append(
                    TocNode(name=en_name, include_path=path, block=block)
                )
            continue

        if not ru_node.children:
            continue

        en_children = en_node.children if en_node and en_node.children else []
        merged_children = _merge_toc_tree_nodes(
            en_children,
            ru_node.children,
            en_by_href=en_by_href,
            en_by_include=en_by_include,
            translate_hrefs=translate_hrefs,
            translate_include_paths=translate_include_paths,
            translate_name=translate_name,
            ru_base_hrefs=base_hrefs,
            ru_base_include_paths=base_includes,
            restrict_gap_fill_to_scope=restrict_gap_fill_to_scope,
        )
        if not merged_children:
            continue
        parent_name = en_node.name if en_node else translate_name(ru_node.name).strip()
        merged.append(TocNode(name=parent_name, children=merged_children))

    return merged


def _merge_en_toc_yaml_nested(
    en_main_yaml: str,
    ru_pr_yaml: str,
    *,
    translate_hrefs: set[str],
    translate_name: Callable[[str], str],
    ru_base_hrefs: set[str] | None = None,
    translate_include_paths: set[str] | None = None,
    ru_base_include_paths: set[str] | None = None,
    restrict_gap_fill_to_scope: bool = False,
) -> str:
    include_scope = translate_include_paths or set()
    base_includes = ru_base_include_paths or set()
    en_text = en_main_yaml.replace("\r\n", "\n")
    en_lines = en_text.splitlines()
    items_start = 0
    for i, line in enumerate(en_lines):
        if line.strip() == "items:":
            items_start = i + 1
            break
    list_indent = _top_level_list_indent(en_lines, items_start)
    en_tree = _parse_toc_tree_block(en_main_yaml)
    ru_tree = _parse_toc_tree_block(ru_pr_yaml)
    en_by_href = _index_toc_leaves(en_tree)
    en_by_include = _index_toc_includes(en_tree)
    ru_hrefs = _collect_toc_hrefs(ru_tree)
    ru_includes = _collect_toc_include_paths(ru_tree)
    merged = _merge_toc_tree_nodes(
        en_tree,
        ru_tree,
        en_by_href=en_by_href,
        en_by_include=en_by_include,
        translate_hrefs=translate_hrefs,
        translate_include_paths=include_scope,
        translate_name=translate_name,
        ru_base_hrefs=ru_base_hrefs,
        ru_base_include_paths=base_includes,
        restrict_gap_fill_to_scope=restrict_gap_fill_to_scope,
    )
    seen_hrefs = _collect_toc_hrefs(merged)
    seen_includes = _collect_toc_include_paths(merged)
    base_hrefs = ru_base_hrefs or set()
    for node in _walk_toc_nodes(en_tree):
        if node.href and node.href not in seen_hrefs and node.href not in ru_hrefs:
            if node.href in base_hrefs:
                continue
            merged.append(node)
            seen_hrefs.add(node.href)
        if (
            node.include_path
            and node.include_path not in seen_includes
            and node.include_path not in ru_includes
        ):
            if node.include_path in base_includes:
                continue
            merged.append(node)
            seen_includes.add(node.include_path)
    return _serialize_toc_tree(merged, list_indent=list_indent)


def _parse_toc_items_block(text: str) -> list[dict[str, str]]:
    if _has_nested_block_items(text):
        return _flatten_toc_nodes(_parse_toc_tree_block(text))
    lines = text.replace("\r\n", "\n").split("\n")
    items: list[dict[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        name_match = re.match(r"^(\s*)- name:\s*(.+)", line)
        if name_match:
            block_lines = [line]
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if re.match(r"^\s*-\s+(name:|include:)", nxt):
                    break
                block_lines.append(nxt)
                i += 1
            block = "\n".join(block_lines).rstrip() + "\n"
            m_href = _HREF_LINE.search(block)
            m_include = _INCLUDE_PATH.search(block)
            name = name_match.group(2).strip()
            if m_href:
                items.append(
                    {
                        "name": name,
                        "href": m_href.group(1).strip(),
                        "block": block,
                    }
                )
            elif m_include:
                items.append(
                    {
                        "name": name,
                        "include_path": m_include.group(1).strip(),
                        "block": block,
                    }
                )
            continue
        include_match = _INCLUDE_ONLY_ITEM.match(line)
        if include_match:
            path = include_match.group(1).strip().strip("'\"")
            items.append(
                {
                    "include_path": path,
                    "block": line.rstrip() + "\n",
                }
            )
        i += 1
    return items


def _parse_toc_items_inline(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for m in _INLINE_ITEM.finditer(text):
        line_start, _ = _inline_item_line_bounds(text, m)
        line_end = text.find("\n", m.start())
        if line_end == -1:
            line_end = len(text)
        block = text[line_start:line_end].rstrip() + "\n"
        items.append(
            {
                "name": m.group(1).strip(),
                "href": m.group(2).strip().rstrip(","),
                "block": block,
            }
        )
    return items


def _inline_item_line_bounds(text: str, match: re.Match[str]) -> tuple[int, int]:
    """Return ``(line_start, dash_index)`` for an inline toc item match."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.start())
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    dash_index = line_start + line.index("-")
    return line_start, dash_index


def _inline_list_line_prefix(yaml_text: str) -> str | None:
    """Leading whitespace before ``-`` on the first inline toc item line."""
    text = yaml_text.replace("\r\n", "\n")
    m = _INLINE_ITEM.search(text)
    if not m:
        return None
    line_start, dash_index = _inline_item_line_bounds(text, m)
    return text[line_start:dash_index]


def _normalize_inline_block(block: str, line_prefix: str) -> str:
    """Apply EN-main list-entry indentation to one inline ``- { ... }`` line."""
    stripped = block.strip()
    if not stripped.startswith("- {"):
        return block if block.endswith("\n") else block + "\n"
    return line_prefix + stripped + "\n"


def _inline_line_prefixes(yaml_text: str) -> set[str]:
    """Distinct leading whitespace prefixes before ``-`` on inline toc lines."""
    text = yaml_text.replace("\r\n", "\n")
    prefixes: set[str] = set()
    for m in _INLINE_ITEM.finditer(text):
        line_start, dash_index = _inline_item_line_bounds(text, m)
        prefixes.add(text[line_start:dash_index])
    return prefixes


def parse_toc_items(yaml_text: str) -> list[dict[str, str]]:
    """Return ``[{name, href, block}, ...]`` preserving each item's YAML block."""
    text = yaml_text.replace("\r\n", "\n")
    if not text.strip():
        return []
    inline = _parse_toc_items_inline(text)
    if inline:
        return inline
    return _parse_toc_items_block(text)


def collect_toc_link_targets(yaml_text: str) -> list[tuple[str, str]]:
    """Return ordered ``(kind, path)`` pairs for sidebar ``href`` and ``include.path``."""
    text = yaml_text.replace("\r\n", "\n")
    if not text.strip():
        return []
    if _has_nested_block_items(text):
        items = _flatten_toc_nodes(_parse_toc_tree_block(text))
    else:
        items = parse_toc_items(text)
    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        href = item.get("href")
        if href and not href.startswith(("http://", "https://", "mailto:")):
            key = ("href", href)
            if key not in seen:
                seen.add(key)
                targets.append(key)
    for path in _iter_toc_include_paths(text):
        key = ("include", path)
        if key not in seen:
            seen.add(key)
            targets.append(key)
    return targets


def resolve_toc_target_path(en_toc_path: str, rel_path: str) -> str:
    """Resolve toc ``href`` / ``include.path`` relative to the EN yaml file."""
    base = PurePosixPath(en_toc_path).parent
    parts: list[str] = []
    for part in (base / rel_path).parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts)


def toc_translate_scope(ru_base_yaml: str, ru_pr_yaml: str) -> TocTranslateScope:
    """``href`` / ``include.path`` values whose menu ``name`` must be translated.

    Scope = newly added items or items whose Russian ``name`` changed
    between base and PR head. Unchanged items are **not** in scope.
    """
    base_by_href = {it["href"]: it for it in parse_toc_items(ru_base_yaml) if it.get("href")}
    base_by_include = {
        it["include_path"]: it
        for it in parse_toc_items(ru_base_yaml)
        if it.get("include_path")
    }
    hrefs: set[str] = set()
    include_paths: set[str] = set()
    for it in parse_toc_items(ru_pr_yaml):
        href = it.get("href")
        if href:
            prev = base_by_href.get(href)
            if prev is None or prev["name"] != it["name"]:
                hrefs.add(href)
            continue
        include_path = it.get("include_path")
        if include_path:
            prev = base_by_include.get(include_path)
            if prev is None or prev["name"] != it["name"]:
                include_paths.add(include_path)
    return TocTranslateScope(frozenset(hrefs), frozenset(include_paths))


def merge_en_toc_yaml(
    en_main_yaml: str,
    ru_pr_yaml: str,
    *,
    translate_hrefs: set[str],
    translate_name: Callable[[str], str],
    ru_base_hrefs: set[str] | None = None,
    translate_include_paths: set[str] | None = None,
    ru_base_include_paths: set[str] | None = None,
    restrict_gap_fill_to_scope: bool = False,
) -> str:
    """Build EN toc from RU PR order with strict scope.

    - Existing ``href``: keep EN block unless ``href`` ∈ ``translate_hrefs``
      → translate ``name`` from RU only.
    - New ``href`` in RU PR: add **only** if ``href`` ∈ ``translate_hrefs``.
    - RU ``href`` in merge-base but missing from EN main: add with translated
      ``name`` (§6.59 — closes EN nav gaps like ``debug-logs-otel.md``),
      unless ``restrict_gap_fill_to_scope`` (§6.72 parent toc supplement).
    - ``include.path`` sidebar links: same scope rules via
      ``translate_include_paths`` / ``ru_base_include_paths``.
    - RU removed ``href``: omit from output (mirror RU structure).
    - EN-only ``href`` not in RU PR: append unchanged at end (legacy entries).
    """
    include_scope = translate_include_paths or set()
    base_includes = ru_base_include_paths or set()
    if _has_nested_block_items(en_main_yaml) or _has_nested_block_items(ru_pr_yaml):
        return _merge_en_toc_yaml_nested(
            en_main_yaml,
            ru_pr_yaml,
            translate_hrefs=translate_hrefs,
            translate_name=translate_name,
            ru_base_hrefs=ru_base_hrefs,
            translate_include_paths=include_scope,
            ru_base_include_paths=base_includes,
            restrict_gap_fill_to_scope=restrict_gap_fill_to_scope,
        )

    line_prefix = _inline_list_line_prefix(en_main_yaml)
    en_items = parse_toc_items(en_main_yaml)
    en_by_href = {it["href"]: it for it in en_items if it.get("href")}
    en_by_include = {
        it["include_path"]: it for it in en_items if it.get("include_path")
    }
    ru_items = parse_toc_items(ru_pr_yaml)
    ru_hrefs = {it["href"] for it in ru_items if it.get("href")}
    ru_includes = {it["include_path"] for it in ru_items if it.get("include_path")}
    base_hrefs = ru_base_hrefs or set()
    merged: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()
    seen_includes: set[str] = set()

    for rit in ru_items:
        href = rit.get("href")
        if href:
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            if href in en_by_href and href not in translate_hrefs:
                merged.append(en_by_href[href])
            elif href in translate_hrefs or (
                not restrict_gap_fill_to_scope
                and href not in en_by_href
                and href in base_hrefs
            ):
                en_name = translate_name(rit["name"]).strip()
                block = _replace_item_name(rit["block"], en_name)
                if line_prefix is not None:
                    block = _normalize_inline_block(block, line_prefix)
                merged.append(
                    {
                        "name": en_name,
                        "href": href,
                        "block": block,
                    }
                )
            continue

        include_path = rit.get("include_path")
        if include_path:
            if include_path in seen_includes:
                continue
            seen_includes.add(include_path)
            if include_path in en_by_include and include_path not in include_scope:
                merged.append(en_by_include[include_path])
            elif include_path in include_scope or (
                not restrict_gap_fill_to_scope
                and include_path not in en_by_include
                and include_path in base_includes
            ):
                block = rit["block"]
                if rit.get("name"):
                    en_name = translate_name(rit["name"]).strip()
                    block = _replace_item_name(block, en_name)
                    merged.append(
                        {
                            "name": en_name,
                            "include_path": include_path,
                            "block": block,
                        }
                    )
                else:
                    merged.append(
                        {
                            "include_path": include_path,
                            "block": block,
                        }
                    )

    for it in en_items:
        href = it.get("href")
        if href and href not in seen_hrefs and href not in ru_hrefs:
            if href in base_hrefs:
                continue
            merged.append(it)
        include_path = it.get("include_path")
        if (
            include_path
            and include_path not in seen_includes
            and include_path not in ru_includes
        ):
            if include_path in base_includes:
                continue
            merged.append(it)

    return _serialize_toc(merged, line_prefix=line_prefix)


def _replace_item_name(block: str, new_name: str) -> str:
    if re.match(r"^\s*- \{", block):
        return re.sub(
            r"(\{\s*name:\s*)(.+?)(\s*,\s*href:)",
            rf"\1{new_name}\3",
            block,
            count=1,
        )
    return re.sub(r"(?m)^(\s*)- name: .+$", rf"\1- name: {new_name}", block, count=1)


def _serialize_toc(
    items: list[dict[str, str]],
    *,
    line_prefix: str | None = None,
) -> str:
    blocks: list[str] = []
    for it in items:
        block = it["block"]
        if line_prefix is not None and re.match(r"^\s*- \{", block):
            block = _normalize_inline_block(block, line_prefix)
        blocks.append(block)
    body = "".join(blocks)
    if not body.endswith("\n"):
        body += "\n"
    return "items:\n" + body


@dataclass(frozen=True)
class TocValidationIssue:
    kind: str
    detail: str


def _toc_entry_labels(items: list[dict[str, str]]) -> set[str]:
    """Stable ids for toc entries (``href`` leaf or ``include.path`` link)."""
    labels: set[str] = set()
    for it in items:
        href = it.get("href")
        if href:
            labels.add(f"href:{href}")
        include_path = it.get("include_path")
        if include_path:
            labels.add(f"include:{include_path}")
    return labels


def _en_covers_ru_href(
    ru_item: dict[str, str],
    en_items: list[dict[str, str]],
    *,
    en_main_hrefs: set[str],
) -> bool:
    """True when EN toc mirrors a scoped RU ``href`` (exact or legacy alias).

    Legacy alias: same Diplodoc ``name``, different ``href`` basename, and the
    EN ``href`` already exists on EN main (e.g. RU ``hive_config.md`` vs EN
    ``hive.md`` — §6.74 / #44942).
    """
    href = ru_item.get("href")
    if not href:
        return True
    en_hrefs = {it["href"] for it in en_items if it.get("href")}
    if href in en_hrefs:
        return True
    name = ru_item.get("name")
    if not name:
        return False
    for en_it in en_items:
        en_href = en_it.get("href")
        if en_it.get("name") == name and en_href and en_href in en_main_hrefs:
            return True
    return False


def validate_toc_merge(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_hrefs: set[str],
    en_main_yaml: str,
    translate_include_paths: set[str] | None = None,
) -> list[TocValidationIssue]:
    """Heuristic checks after merge (Phase E hook)."""
    issues: list[TocValidationIssue] = []
    ru_items = parse_toc_items(ru_pr_yaml)
    en_items = parse_toc_items(en_merged_yaml)
    ru_labels = _toc_entry_labels(ru_items)
    en_labels = _toc_entry_labels(en_items)
    en_main_items = parse_toc_items(en_main_yaml)
    en_main_hrefs = {it["href"] for it in en_main_items if it.get("href")}
    en_main_includes = {
        it["include_path"] for it in en_main_items if it.get("include_path")
    }

    en_main_labels = _toc_entry_labels(en_main_items)

    if (
        len(en_main_labels) >= 3
        and len(en_labels) < max(1, len(en_main_labels) // 2)
    ):
        issues.append(
            TocValidationIssue(
                kind="collapsed_toc",
                detail=(
                    f"EN merged toc has {len(en_labels)} entries vs "
                    f"{len(en_main_labels)} in EN main — possible navigation regression"
                ),
            )
        )

    en_merged_hrefs = {it["href"] for it in en_items if it.get("href")}
    en_merged_includes = {
        it["include_path"] for it in en_items if it.get("include_path")
    }
    ru_hrefs = {it["href"] for it in ru_items if it.get("href")}
    ru_includes = {it["include_path"] for it in ru_items if it.get("include_path")}

    unexpected_hrefs = en_merged_hrefs - ru_hrefs - en_main_hrefs
    unexpected_includes = en_merged_includes - ru_includes - en_main_includes
    unexpected = sorted(
        f"href:{href}" for href in unexpected_hrefs
    ) + sorted(f"include:{path}" for path in unexpected_includes)
    if unexpected:
        issues.append(
            TocValidationIssue(
                kind="unexpected_href",
                detail=f"EN toc has hrefs not in RU PR and not EN legacy: {unexpected}",
            )
        )

    if ru_items and not en_items:
        issues.append(
            TocValidationIssue(
                kind="empty_toc",
                detail="EN toc has no items but RU PR does",
            )
        )

    include_scope = translate_include_paths or set()
    ru_by_href = {it["href"]: it for it in ru_items if it.get("href")}
    for href in translate_hrefs:
        ru_item = ru_by_href.get(href)
        if ru_item is None:
            if f"href:{href}" not in en_labels:
                issues.append(
                    TocValidationIssue(
                        kind="scope_not_applied",
                        detail=(
                            f"href {href!r} was in translate scope but missing "
                            "from EN toc"
                        ),
                    )
                )
            continue
        if not _en_covers_ru_href(
            ru_item, en_items, en_main_hrefs=en_main_hrefs
        ):
            issues.append(
                TocValidationIssue(
                    kind="scope_not_applied",
                    detail=(
                        f"href {href!r} was in translate scope but missing from EN toc"
                    ),
                )
            )
    for include_path in include_scope:
        if f"include:{include_path}" not in en_labels:
            issues.append(
                TocValidationIssue(
                    kind="scope_not_applied",
                    detail=(
                        f"include.path {include_path!r} was in translate scope "
                        "but missing from EN toc"
                    ),
                )
            )

    prefixes = _inline_line_prefixes(en_merged_yaml)
    if len(prefixes) > 1:
        issues.append(
            TocValidationIssue(
                kind="inconsistent_indent",
                detail=(
                    "inline toc list entries use mixed indentation "
                    f"(prefixes: {sorted(prefixes)!r})"
                ),
            )
        )

    return issues
