"""Preserve Diplodoc ``[{#T}](href)`` targets across locale translates."""

from __future__ import annotations

import re

_AUTO_LINK = re.compile(r"\[\{#T\}\]\(([^)]+)\)")
_BARE_T = re.compile(r"(?<!\[)\{#T\}")


def _doc_href_stem(href: str) -> str:
    path = href.strip().split("#", 1)[0]
    if path.endswith("/index.md"):
        return path[: -len("/index.md")]
    if path.endswith(".md"):
        return path[: -len(".md")]
    return path


def overlay_autotitle_fragment_hrefs(target: str, preferred: str) -> str:
    """Rewrite ``[{#T}](…#frag)`` in ``target`` using ``preferred`` when fragments match.

    Used for merged source PRs (§6.120 / §6.128): RU body comes from the merge
    commit, but unique ``#fragment`` targets may have moved on ``main`` after
    merge (e.g. Sessions ``index.md#sessions`` → ``execution_process.md#sessions``).
    Preferring HEAD/main hrefs for those fragments avoids YFM010 on EN.
    """
    if not target or not preferred:
        return target

    by_frag: dict[str, str] = {}
    for href in _AUTO_LINK.findall(preferred):
        if "#" not in href:
            continue
        frag = href.rsplit("#", 1)[-1]
        if not frag:
            continue
        if frag in by_frag and by_frag[frag] != href:
            by_frag[frag] = ""  # ambiguous
        else:
            by_frag.setdefault(frag, href)

    out = target
    for href in _AUTO_LINK.findall(target):
        if "#" not in href:
            continue
        frag = href.rsplit("#", 1)[-1]
        preferred_href = by_frag.get(frag) or ""
        if not preferred_href or preferred_href == href:
            continue
        out = out.replace(f"[{{#T}}]({href})", f"[{{#T}}]({preferred_href})", 1)
    return out


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

    RU→EN (``force_exact=True``):
    1. Re-attach bare ``{#T}`` left by ``strip_unreachable`` (#47108) using RU
       hrefs that are missing from EN.
    2. When full ``[{#T}](…)`` counts match, force each href to the RU twin.
    """
    if not source_base or not translated:
        return translated

    if force_exact:
        return _restore_autotitle_force_exact(translated, source_base)

    tr_paths = _AUTO_LINK.findall(translated)
    base_paths = _AUTO_LINK.findall(source_base)
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


def _restore_autotitle_force_exact(translated: str, source_base: str) -> str:
    base_hrefs = _AUTO_LINK.findall(source_base)
    if not base_hrefs:
        return translated

    present = set(_AUTO_LINK.findall(translated))
    unused = [href for href in base_hrefs if href not in present]

    def _replace_bare(_match: re.Match[str]) -> str:
        nonlocal unused
        if not unused:
            return "{#T}"
        href = unused.pop(0)
        return f"[{{#T}}]({href})"

    out = _BARE_T.sub(_replace_bare, translated)

    tr_paths = _AUTO_LINK.findall(out)
    if len(tr_paths) == len(base_hrefs) and tr_paths:
        for tr_href, base_href in zip(tr_paths, base_hrefs):
            if tr_href == base_href:
                continue
            out = out.replace(f"[{{#T}}]({tr_href})", f"[{{#T}}]({base_href})", 1)
        return out

    # Counts differ (e.g. after strip_unreachable): still force unique
    # ``#fragment`` twins so critic cannot leave ``index.md#sessions`` when RU
    # has ``execution_process.md#sessions`` (#47104).
    by_frag: dict[str, str] = {}
    for href in base_hrefs:
        if "#" not in href:
            continue
        frag = href.rsplit("#", 1)[-1]
        if not frag:
            continue
        if frag in by_frag and by_frag[frag] != href:
            by_frag[frag] = ""  # ambiguous — skip
        else:
            by_frag.setdefault(frag, href)
    for tr_href in _AUTO_LINK.findall(out):
        if "#" not in tr_href:
            continue
        frag = tr_href.rsplit("#", 1)[-1]
        base_href = by_frag.get(frag) or ""
        if not base_href or base_href == tr_href:
            continue
        out = out.replace(f"[{{#T}}]({tr_href})", f"[{{#T}}]({base_href})", 1)
    return out
