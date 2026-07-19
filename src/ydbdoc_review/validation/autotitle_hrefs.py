"""Preserve Diplodoc ``[{#T}](href)`` targets across locale translates."""

from __future__ import annotations

import re

_AUTO_LINK = re.compile(r"\[\{#T\}\]\(([^)]+)\)")


def _doc_href_stem(href: str) -> str:
    path = href.strip().split("#", 1)[0]
    if path.endswith("/index.md"):
        return path[: -len("/index.md")]
    if path.endswith(".md"):
        return path[: -len(".md")]
    return path


def restore_autotitle_hrefs(
    translated: str,
    source_base: str | None,
    *,
    force_exact: bool = False,
) -> str:
    """Copy ``[{#T}](href)`` targets from ``source_base`` onto ``translated``.

    EN→RU (default): rewrite when paths denote the same doc (``page.md`` vs
    ``page/index.md``); Diplodoc toc validation fails if EN→RU copies the EN
    path literally.

    RU→EN (``force_exact=True``): always take the source href when ``{#T}``
    counts match — autotitle destinations must stay in lockstep with RU, and
    the model sometimes emits a stale sibling path (e.g. ``index.md#sessions``
    instead of ``execution_process.md#sessions``, #47100 / YFM010).
    """
    if not source_base or not translated:
        return translated

    tr_paths = _AUTO_LINK.findall(translated)
    base_paths = _AUTO_LINK.findall(source_base)
    if len(tr_paths) != len(base_paths) or not tr_paths:
        return translated

    out = translated
    for tr_href, base_href in zip(tr_paths, base_paths):
        if tr_href == base_href:
            continue
        if not force_exact and _doc_href_stem(tr_href) != _doc_href_stem(base_href):
            continue
        out = out.replace(f"[{{#T}}]({tr_href})", f"[{{#T}}]({base_href})", 1)
    return out
