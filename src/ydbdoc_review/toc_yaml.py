"""Merge Diplodoc-style toc*.yaml: keep EN labels from main, add only new PR entries."""

from __future__ import annotations

import re
from typing import Callable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ydbdoc_review.config import Settings


_ITEM_HEAD = re.compile(r"^- name: (.+)$", re.MULTILINE)
_HREF_LINE = re.compile(r"^  href: (.+)$", re.MULTILINE)


def _parse_toc_items(yaml_text: str) -> list[dict[str, str]]:
    """Return list of {name, href, block} preserving each item's full YAML block."""
    text = yaml_text.replace("\r\n", "\n")
    if not text.strip():
        return []
    parts = re.split(r"(?m)^- name: ", text)
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


def _replace_item_name(block: str, new_name: str) -> str:
    return re.sub(
        r"(?m)^- name: .+$",
        f"- name: {new_name}",
        block,
        count=1,
    )


def _serialize_toc(items: list[dict[str, str]]) -> str:
    body = "".join(it["block"] for it in items)
    if not body.endswith("\n"):
        body += "\n"
    return "items:\n" + body


def merge_en_toc_yaml(
    en_main_yaml: str,
    ru_pr_yaml: str,
    *,
    new_hrefs: set[str],
    translate_name: Callable[[str], str],
) -> str:
    """
    Build EN toc using RU PR order. Existing hrefs keep EN-main blocks; only hrefs in
    `new_hrefs` are taken from RU and get translated titles. Other RU-only hrefs (e.g.
    VS Code not translated in this run) are skipped.
    """
    en_by_href = {it["href"]: it for it in _parse_toc_items(en_main_yaml)}
    ru_items = _parse_toc_items(ru_pr_yaml)
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for rit in ru_items:
        href = rit["href"]
        if href in seen:
            continue
        seen.add(href)
        if href in en_by_href:
            merged.append(en_by_href[href])
        elif href in new_hrefs:
            en_name = translate_name(rit["name"]).strip()
            merged.append(
                {
                    "name": en_name,
                    "href": href,
                    "block": _replace_item_name(rit["block"], en_name),
                }
            )

    for it in _parse_toc_items(en_main_yaml):
        if it["href"] not in seen:
            merged.append(it)

    return _serialize_toc(merged)


def translate_toc_title(settings: "Settings", ru_title: str) -> str:
    from ydbdoc_review.llm import translate_markdown

    raw = translate_markdown(
        settings,
        source_lang="Russian",
        target_lang="English",
        source_path="toc-entry",
        source_text=ru_title,
    )
    line = raw.strip().splitlines()[0] if raw.strip() else ru_title
    return line.strip().strip('"').strip("'")

