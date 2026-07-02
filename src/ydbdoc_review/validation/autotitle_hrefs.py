"""Preserve RU autotitle link targets when EN→RU re-translate would break YFM paths."""

from __future__ import annotations

import re

_AUTO_LINK = re.compile(r"\[\{#T\}\]\(([^)]+)\)")


def _doc_href_stem(href: str) -> str:
    path = href.strip()
    if path.endswith("/index.md"):
        return path[: -len("/index.md")]
    if path.endswith(".md"):
        return path[: -len(".md")]
    return path


def restore_autotitle_hrefs(translated: str, ru_base: str | None) -> str:
    """Copy ``[{#T}](href)`` targets from ``ru_base`` when paths denote the same doc.

    EN mirrors often use ``page.md`` where RU uses ``page/index.md``; Diplodoc
    toc validation fails if EN→RU translation copies the EN path literally.
    """
    if not ru_base or not translated:
        return translated

    tr_paths = _AUTO_LINK.findall(translated)
    base_paths = _AUTO_LINK.findall(ru_base)
    if len(tr_paths) != len(base_paths) or not tr_paths:
        return translated

    out = translated
    for tr_href, base_href in zip(tr_paths, base_paths):
        if tr_href == base_href:
            continue
        if _doc_href_stem(tr_href) != _doc_href_stem(base_href):
            continue
        out = out.replace(f"[{{#T}}]({tr_href})", f"[{{#T}}]({base_href})", 1)
    return out
