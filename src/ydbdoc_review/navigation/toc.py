"""Diplodoc toc*.yaml — parse, diff-scoped merge, validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

_ITEM_SPLIT = re.compile(r"(?m)^- name: ")
_HREF_LINE = re.compile(r"^  href: (.+)$", re.MULTILINE)
# Diplodoc ydb docs often use one-line inline items:
#   - { name: Overview, href: index.md, when: ... }
_INLINE_ITEM = re.compile(
    r"(?m)^\s*- \{\s*name:\s*(.+?)\s*,\s*href:\s*(\S+)",
)
_NAME_LINE = re.compile(r"^(\s*)- name: (.+)$")
_HREF_INDENTED = re.compile(r"^(\s*)href: (.+)$")
_NESTED_ITEMS_LINE = re.compile(r"^(\s*)items:\s*$")


@dataclass
class TocNode:
    name: str
    href: str | None = None
    children: list[TocNode] = field(default_factory=list)
    block: str = ""


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
    nodes, _ = _parse_toc_nodes_at_level(lines, start, list_indent=0)
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


def _serialize_toc_tree(nodes: list[TocNode]) -> str:
    body = "".join(_serialize_toc_node(node, list_indent=0) for node in nodes)
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


def _merge_toc_tree_nodes(
    en_nodes: list[TocNode],
    ru_nodes: list[TocNode],
    *,
    en_by_href: dict[str, TocNode],
    translate_hrefs: set[str],
    translate_name: Callable[[str], str],
) -> list[TocNode]:
    merged: list[TocNode] = []
    for idx, ru_node in enumerate(ru_nodes):
        en_node = en_nodes[idx] if idx < len(en_nodes) else None
        if ru_node.href:
            href = ru_node.href
            if href in en_by_href and href not in translate_hrefs:
                merged.append(en_by_href[href])
            elif href in translate_hrefs:
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

        if not ru_node.children:
            continue

        en_children = en_node.children if en_node and en_node.children else []
        merged_children = _merge_toc_tree_nodes(
            en_children,
            ru_node.children,
            en_by_href=en_by_href,
            translate_hrefs=translate_hrefs,
            translate_name=translate_name,
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
) -> str:
    en_tree = _parse_toc_tree_block(en_main_yaml)
    ru_tree = _parse_toc_tree_block(ru_pr_yaml)
    en_by_href = _index_toc_leaves(en_tree)
    ru_hrefs = _collect_toc_hrefs(ru_tree)
    merged = _merge_toc_tree_nodes(
        en_tree,
        ru_tree,
        en_by_href=en_by_href,
        translate_hrefs=translate_hrefs,
        translate_name=translate_name,
    )
    seen = _collect_toc_hrefs(merged)
    for node in en_tree:
        if node.href and node.href not in seen and node.href not in ru_hrefs:
            merged.append(node)
            seen.add(node.href)
    return _serialize_toc_tree(merged)


def _parse_toc_items_block(text: str) -> list[dict[str, str]]:
    if _has_nested_block_items(text):
        return _flatten_toc_nodes(_parse_toc_tree_block(text))
    parts = _ITEM_SPLIT.split(text)
    items: list[dict[str, str]] = []
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        block = "- name: " + chunk
        m_name = re.match(r"- name: (.+)", block)
        m_href = _HREF_LINE.search(block)
        if not m_name or not m_href:
            continue
        items.append(
            {
                "name": m_name.group(1).strip(),
                "href": m_href.group(1).strip(),
                "block": block.rstrip() + "\n",
            }
        )
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


def toc_translate_scope(ru_base_yaml: str, ru_pr_yaml: str) -> set[str]:
    """``href`` values whose menu ``name`` must be translated for this PR.

    Scope = newly added items or items whose Russian ``name`` changed
    between base and PR head. Unchanged items are **not** in scope.
    """
    base_by_href = {it["href"]: it for it in parse_toc_items(ru_base_yaml)}
    scope: set[str] = set()
    for it in parse_toc_items(ru_pr_yaml):
        href = it["href"]
        prev = base_by_href.get(href)
        if prev is None or prev["name"] != it["name"]:
            scope.add(href)
    return scope


def merge_en_toc_yaml(
    en_main_yaml: str,
    ru_pr_yaml: str,
    *,
    translate_hrefs: set[str],
    translate_name: Callable[[str], str],
) -> str:
    """Build EN toc from RU PR order with strict scope.

    - Existing ``href``: keep EN block unless ``href`` ∈ ``translate_hrefs``
      → translate ``name`` from RU only.
    - New ``href`` in RU PR: add **only** if ``href`` ∈ ``translate_hrefs``.
    - RU removed ``href``: omit from output (mirror RU structure).
    - EN-only ``href`` not in RU PR: append unchanged at end (legacy entries).
    """
    if _has_nested_block_items(en_main_yaml) or _has_nested_block_items(ru_pr_yaml):
        return _merge_en_toc_yaml_nested(
            en_main_yaml,
            ru_pr_yaml,
            translate_hrefs=translate_hrefs,
            translate_name=translate_name,
        )

    line_prefix = _inline_list_line_prefix(en_main_yaml)
    en_by_href = {it["href"]: it for it in parse_toc_items(en_main_yaml)}
    ru_items = parse_toc_items(ru_pr_yaml)
    ru_hrefs = {it["href"] for it in ru_items}
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for rit in ru_items:
        href = rit["href"]
        if href in seen:
            continue
        seen.add(href)
        if href in en_by_href and href not in translate_hrefs:
            merged.append(en_by_href[href])
        elif href in translate_hrefs:
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
        # else: RU-only href outside scope — skip (do not invent EN menu items)

    for it in parse_toc_items(en_main_yaml):
        if it["href"] not in seen and it["href"] not in ru_hrefs:
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
    return re.sub(r"(?m)^- name: .+$", f"- name: {new_name}", block, count=1)


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


def validate_toc_merge(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_hrefs: set[str],
    en_main_yaml: str,
) -> list[TocValidationIssue]:
    """Heuristic checks after merge (Phase E hook)."""
    issues: list[TocValidationIssue] = []
    ru_hrefs = {it["href"] for it in parse_toc_items(ru_pr_yaml)}
    en_hrefs = {it["href"] for it in parse_toc_items(en_merged_yaml)}
    en_main_hrefs = {it["href"] for it in parse_toc_items(en_main_yaml)}

    unexpected = en_hrefs - ru_hrefs - en_main_hrefs
    if unexpected:
        issues.append(
            TocValidationIssue(
                kind="unexpected_href",
                detail=f"EN toc has hrefs not in RU PR and not EN legacy: {sorted(unexpected)}",
            )
        )

    missing_ru = ru_hrefs - en_hrefs
    if missing_ru:
        issues.append(
            TocValidationIssue(
                kind="missing_href",
                detail=f"RU PR hrefs missing from EN toc: {sorted(missing_ru)}",
            )
        )

    if parse_toc_items(ru_pr_yaml) and not parse_toc_items(en_merged_yaml):
        issues.append(
            TocValidationIssue(
                kind="empty_toc",
                detail="EN toc has no items but RU PR does",
            )
        )

    for href in translate_hrefs:
        if href not in en_hrefs:
            issues.append(
                TocValidationIssue(
                    kind="scope_not_applied",
                    detail=f"href {href!r} was in translate scope but missing from EN toc",
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
