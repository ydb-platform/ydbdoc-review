"""Diplodoc toc*.yaml — parse, diff-scoped merge, validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_ITEM_SPLIT = re.compile(r"(?m)^- name: ")
_HREF_LINE = re.compile(r"^  href: (.+)$", re.MULTILINE)


def parse_toc_items(yaml_text: str) -> list[dict[str, str]]:
    """Return ``[{name, href, block}, ...]`` preserving each item's YAML block."""
    text = yaml_text.replace("\r\n", "\n")
    if not text.strip():
        return []
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
            merged.append(
                {
                    "name": en_name,
                    "href": href,
                    "block": _replace_item_name(rit["block"], en_name),
                }
            )
        # else: RU-only href outside scope — skip (do not invent EN menu items)

    for it in parse_toc_items(en_main_yaml):
        if it["href"] not in seen and it["href"] not in ru_hrefs:
            merged.append(it)

    return _serialize_toc(merged)


def _replace_item_name(block: str, new_name: str) -> str:
    return re.sub(r"(?m)^- name: .+$", f"- name: {new_name}", block, count=1)


def _serialize_toc(items: list[dict[str, str]]) -> str:
    body = "".join(it["block"] for it in items)
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

    for href in translate_hrefs:
        if href not in en_hrefs:
            issues.append(
                TocValidationIssue(
                    kind="scope_not_applied",
                    detail=f"href {href!r} was in translate scope but missing from EN toc",
                )
            )

    return issues
