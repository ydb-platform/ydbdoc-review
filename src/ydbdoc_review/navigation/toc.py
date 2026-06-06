"""Diplodoc toc*.yaml — parse, diff-scoped merge, validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_ITEM_SPLIT = re.compile(r"(?m)^- name: ")
_HREF_LINE = re.compile(r"^  href: (.+)$", re.MULTILINE)
# Diplodoc ydb docs often use one-line inline items:
#   - { name: Overview, href: index.md, when: ... }
_INLINE_ITEM = re.compile(
    r"(?m)^\s*- \{\s*name:\s*(.+?)\s*,\s*href:\s*(\S+)",
)


def _parse_toc_items_block(text: str) -> list[dict[str, str]]:
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
