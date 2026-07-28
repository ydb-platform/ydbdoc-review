"""Repair EN ``path#fragment`` links that Diplodoc would flag as Title not found.

Covers failure modes from auto-translate (§6.142 / #48047, §6.153 / #48012):

1. Stale autotitle path — RU merge-commit still has ``index.md#sessions`` while
   the heading lives on ``execution_process.md`` (EN baseline / main already
   correct, or RU main overlay missed).
2. Cross-locale explicit anchors — RU ``{#ldap}`` vs EN ``{#ldap-auth-provider}``
   on the twin page; ``force_exact`` copies the RU fragment verbatim.
3. Both RU and EN baseline stale — discover a sibling page that declares the
   fragment via the local toc (or ``index.md`` → ``execution_process.md`` hint).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath

from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.navigation.toc import parse_toc_items
from ydbdoc_review.validation.autotitle_hrefs import _AUTO_LINK
from ydbdoc_review.validation.yfm_anchor import build_heading_anchor_map

DocsReader = Callable[[str], str | None]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _docs_twin_path(path: str) -> str | None:
    if "/docs/en/" in path:
        return path.replace("/docs/en/", "/docs/ru/", 1)
    if "/docs/ru/" in path:
        return path.replace("/docs/ru/", "/docs/en/", 1)
    return None


def _resolve_href_path(page_path: str, href_path: str) -> str | None:
    href_path = href_path.strip()
    if not href_path or href_path.startswith(("http://", "https://", "mailto:")):
        return None
    if not href_path.endswith(".md"):
        return None
    base = PurePosixPath(page_path).parent
    resolved = PurePosixPath(base / href_path)
    parts: list[str] = []
    for part in resolved.parts:
        if part == "..":
            if parts:
                parts.pop()
            continue
        if part in ("", "."):
            continue
        parts.append(part)
    return "/".join(parts)


def fragment_declared_in_markdown(md: str, frag: str) -> bool:
    """True if ``md`` declares ``{#frag}`` (explicit YFM anchor)."""
    if not md or not frag:
        return False
    return f"{{#{frag}}}" in md


def _autotitle_hrefs_by_frag(text: str) -> dict[str, str]:
    by_frag: dict[str, str] = {}
    for href in _AUTO_LINK.findall(text or ""):
        if "#" not in href:
            continue
        path, frag = href.rsplit("#", 1)
        if not frag:
            continue
        if frag in by_frag and by_frag[frag] != href:
            by_frag[frag] = ""
        else:
            by_frag.setdefault(frag, href)
    return by_frag


def _rewrite_href(text: str, old: str, new: str) -> str:
    if old == new or not old or not new:
        return text
    text = text.replace(f"[{{#T}}]({old})", f"[{{#T}}]({new})")
    # Ordinary markdown links with the same target.
    text = text.replace(f"]({old})", f"]({new})")
    return text


def _heading_map_for_targets(
    ru_md: str | None,
    en_md: str | None,
) -> dict[str, str]:
    if not ru_md or not en_md:
        return {}
    return build_heading_anchor_map(parse_markdown(ru_md), parse_markdown(en_md))


def _find_href_declaring_frag_via_toc(
    *,
    en_page_path: str,
    broken_abs_path: str,
    frag: str,
    read_text: DocsReader,
) -> str | None:
    """Search sibling ``href``s from local toc for a page that declares ``{#frag}``.

    Covers the case where RU + EN baseline both still point at a stale path
    (e.g. ``index.md#sessions``) while the heading lives on a sibling
    (``execution_process.md``) listed in the same folder toc (§6.153).
    """
    parent = str(PurePosixPath(broken_abs_path).parent)
    page_parent = str(PurePosixPath(en_page_path).parent)
    candidates: list[str] = []
    for toc_name in ("toc_i.yaml", "toc_p.yaml", "toc.yaml"):
        toc_path = f"{parent}/{toc_name}"
        toc_text = read_text(toc_path)
        if not toc_text:
            continue
        for it in parse_toc_items(toc_text):
            href = it.get("href")
            if href and href.endswith(".md"):
                candidates.append(href)
    # Also try the stem itself replaced by common process/index siblings when
    # toc is missing (still cheap — only paths we can resolve).
    broken_name = PurePosixPath(broken_abs_path).name
    if broken_name == "index.md":
        candidates.extend(
            [
                "execution_process.md",
                "process.md",
                "overview.md",
            ]
        )

    seen: set[str] = set()
    for href in candidates:
        if href in seen:
            continue
        seen.add(href)
        abs_cand = _resolve_href_path(f"{parent}/_dummy.md", href)
        if abs_cand is None or abs_cand == broken_abs_path:
            continue
        md = read_text(abs_cand)
        if not md or not fragment_declared_in_markdown(md, frag):
            continue
        # Prefer a relative href from the linking page when both share a parent.
        try:
            rel = PurePosixPath(abs_cand).relative_to(page_parent).as_posix()
        except ValueError:
            # Fall back to path relative to broken file's directory.
            rel = href if not href.startswith("/") else PurePosixPath(href).name
        return f"{rel}#{frag}"
    return None


def repair_en_fragments(
    en_text: str,
    *,
    en_page_path: str,
    read_text: DocsReader,
    ru_source: str | None = None,
    en_baseline: str | None = None,
) -> str:
    """Fix missing EN ``#fragment`` targets in ``en_text`` for ``en_page_path``.

    ``read_text`` loads repo paths (``ydb/docs/en/...`` / ``ru/...``) from the
    translation worktree / upstream tip.
    """
    if not en_text or not en_page_path:
        return en_text

    out = en_text
    ru_by_frag = _autotitle_hrefs_by_frag(ru_source or "")
    en_base_by_frag = _autotitle_hrefs_by_frag(en_baseline or "")

    # Collect hrefs that carry a fragment (autotitle + markdown).
    candidates: list[str] = []
    for href in _AUTO_LINK.findall(out):
        if "#" in href:
            candidates.append(href)
    for match in _MD_LINK.finditer(out):
        href = match.group(2).strip()
        if "#" in href and ".md" in href.split("#", 1)[0]:
            candidates.append(href)

    seen: set[str] = set()
    for href in candidates:
        if href in seen or "#" not in href:
            continue
        seen.add(href)
        path_part, frag = href.rsplit("#", 1)
        if not frag or not path_part.endswith(".md"):
            continue

        abs_path = _resolve_href_path(en_page_path, path_part)
        if abs_path is None:
            continue

        en_target = read_text(abs_path)
        if en_target is not None and fragment_declared_in_markdown(en_target, frag):
            continue

        # 1) Prefer EN baseline autotitle path when it actually declares the frag.
        base_href = en_base_by_frag.get(frag) or ""
        if base_href and base_href != href and "#" in base_href:
            base_path, base_frag = base_href.rsplit("#", 1)
            if base_frag == frag:
                base_abs = _resolve_href_path(en_page_path, base_path)
                if base_abs:
                    base_md = read_text(base_abs)
                    if base_md and fragment_declared_in_markdown(base_md, frag):
                        # Keep relative shape from baseline when possible.
                        out = _rewrite_href(out, href, base_href)
                        continue

        # 2) Prefer RU source autotitle path (post-overlay) when EN declares frag.
        ru_href = ru_by_frag.get(frag) or ""
        if ru_href and ru_href != href and "#" in ru_href:
            ru_path, ru_frag = ru_href.rsplit("#", 1)
            if ru_frag == frag:
                ru_abs = _resolve_href_path(en_page_path, ru_path)
                # RU path is locale-agnostic in docs (same relative href); resolve
                # as EN page under the EN tree.
                if ru_abs:
                    ru_en_md = read_text(ru_abs)
                    if ru_en_md and fragment_declared_in_markdown(ru_en_md, frag):
                        out = _rewrite_href(out, href, ru_href)
                        continue

        # 3) Same path, different explicit anchors on RU/EN twins (ldap case).
        ru_twin = _docs_twin_path(abs_path)
        ru_md = read_text(ru_twin) if ru_twin else None
        if en_target is None:
            en_target = read_text(abs_path)
        mapping = _heading_map_for_targets(ru_md, en_target)
        new_frag = mapping.get(frag)
        if new_frag and new_frag != frag:
            if en_target and fragment_declared_in_markdown(en_target, new_frag):
                new_href = f"{path_part}#{new_frag}"
                out = _rewrite_href(out, href, new_href)
                continue

        # 4) Both RU and EN baseline stale: find sibling page via local toc (§6.153).
        found = _find_href_declaring_frag_via_toc(
            en_page_path=en_page_path,
            broken_abs_path=abs_path,
            frag=frag,
            read_text=read_text,
        )
        if found:
            out = _rewrite_href(out, href, found)

    return out
