"""Repair EN ``path#fragment`` links that Diplodoc would flag as Title not found.

Covers failure modes from auto-translate (§6.142 / #48047, §6.153 / #48012,
§6.158 / #48223):

1. Stale autotitle path — RU merge-commit still has ``index.md#sessions`` while
   the heading lives on ``execution_process.md`` (EN baseline / main already
   correct, or RU main overlay missed).
2. Cross-locale explicit anchors — RU ``{#ldap}`` vs EN ``{#ldap-auth-provider}``
   on the twin page; ``force_exact`` copies the RU fragment verbatim.
3. Both RU and EN baseline stale — discover a sibling page that declares the
   fragment via the local toc (or ``index.md`` → ``execution_process.md`` hint).
4. Do **not** toc-retarget when the original EN target file already exists
   (§6.158): that produced bare ``topic.md`` / ``create-resource-pool.md``
   under ``en/core/dev/`` and broke ``build-docs`` with unreachable links.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath

from ydbdoc_review.navigation.toc import parse_toc_items
from ydbdoc_review.parsing.ast_types import Heading
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.autotitle_hrefs import _AUTO_LINK
from ydbdoc_review.validation.yfm_anchor import (
    build_heading_anchor_map,
    diplodoc_auto_slug,
    split_heading_anchor_suffix,
)
from ydbdoc_review.validation.yfm_anchor import _iter_headings

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


def _posix_relpath(from_dir: str, to_file: str) -> str:
    """Relative path from directory ``from_dir`` to file ``to_file`` (posix)."""
    start = PurePosixPath(from_dir).parts
    target = PurePosixPath(to_file).parts
    i = 0
    while i < len(start) and i < len(target) and start[i] == target[i]:
        i += 1
    ups = [".."] * (len(start) - i)
    downs = list(target[i:])
    if not ups and not downs:
        return PurePosixPath(to_file).name
    return "/".join([*ups, *downs])


def _heading_declares_frag(heading: Heading, frag: str) -> bool:
    from ydbdoc_review.rendering.markdown_renderer import _render_inline

    plain = _render_inline(heading.children).strip()
    title, explicit = split_heading_anchor_suffix(plain)
    if explicit == frag:
        return True
    # Diplodoc auto-slug when no explicit ``{#…}``.
    if explicit is None and diplodoc_auto_slug(title) == frag:
        return True
    return False


def _page_declares_fragment(md: str, frag: str) -> bool:
    if not md or not frag:
        return False
    if f"{{#{frag}}}" in md:
        return True
    doc = parse_markdown(md)
    return any(_heading_declares_frag(h, frag) for h in _iter_headings(doc.children))


def fragment_declared_in_markdown(
    md: str,
    frag: str,
    *,
    page_path: str | None = None,
    read_text: DocsReader | None = None,
    docs_root: str = "ydb/docs",
) -> bool:
    """True if ``md`` (or a locale ``{% include %}`` it pulls in) declares ``frag``.

    Accepts explicit ``{#frag}`` and Diplodoc auto-slugs from bare headings
    (§6.158 — ``### Parameters`` ⇒ ``#parameters``).
    """
    if _page_declares_fragment(md, frag):
        return True
    if not page_path or read_text is None:
        return False
    for inc in collect_yfm_includes(md):
        resolved = resolve_locale_md_path(
            page_path, inc.path, docs_root=docs_root
        )
        if resolved is None:
            continue
        included = read_text(resolved)
        if included and _page_declares_fragment(included, frag):
            return True
    return False


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
        if not md or not fragment_declared_in_markdown(
            md, frag, page_path=abs_cand, read_text=read_text
        ):
            continue
        # Always compute a path relative to the *linking* page (§6.158).
        # Never fall back to the toc's bare ``href`` — that is relative to the
        # toc folder, not to ``en_page_path``, and yields unreachable links
        # (``en/core/dev/topic.md`` instead of ``…/concepts/datamodel/topic.md``).
        rel = _posix_relpath(page_parent, abs_cand)
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
        if en_target is not None and fragment_declared_in_markdown(
            en_target,
            frag,
            page_path=abs_path,
            read_text=read_text,
        ):
            continue

        # 1) Prefer EN baseline autotitle path when it actually declares the frag.
        base_href = en_base_by_frag.get(frag) or ""
        if base_href and base_href != href and "#" in base_href:
            base_path, base_frag = base_href.rsplit("#", 1)
            if base_frag == frag:
                base_abs = _resolve_href_path(en_page_path, base_path)
                if base_abs:
                    base_md = read_text(base_abs)
                    if base_md and fragment_declared_in_markdown(
                        base_md,
                        frag,
                        page_path=base_abs,
                        read_text=read_text,
                    ):
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
                    if ru_en_md and fragment_declared_in_markdown(
                        ru_en_md,
                        frag,
                        page_path=ru_abs,
                        read_text=read_text,
                    ):
                        out = _rewrite_href(out, href, ru_href)
                        continue

        # 3) Same path, different explicit anchors on RU/EN twins (ldap case).
        ru_twin = _docs_twin_path(abs_path)
        ru_md = read_text(ru_twin) if ru_twin else None
        if en_target is None:
            en_target = read_text(abs_path)
        # Expand include stubs so heading map sees real content.
        en_for_map = en_target
        if en_target is not None:
            for inc in collect_yfm_includes(en_target):
                resolved = resolve_locale_md_path(abs_path, inc.path)
                if resolved is None:
                    continue
                included = read_text(resolved)
                if included:
                    en_for_map = included
                    break
        ru_for_map = ru_md
        if ru_md is not None and ru_twin:
            for inc in collect_yfm_includes(ru_md):
                resolved = resolve_locale_md_path(ru_twin, inc.path)
                if resolved is None:
                    continue
                included = read_text(resolved)
                if included:
                    ru_for_map = included
                    break
        mapping = _heading_map_for_targets(ru_for_map, en_for_map)
        new_frag = mapping.get(frag)
        if new_frag and new_frag != frag:
            if en_for_map and fragment_declared_in_markdown(
                en_for_map,
                new_frag,
                page_path=abs_path,
                read_text=read_text,
            ):
                new_href = f"{path_part}#{new_frag}"
                out = _rewrite_href(out, href, new_href)
                continue

        # 3b) Same page: unique prefix / auto-slug style match on expanded body
        # (RU ``#partitioning`` vs EN ``{#partitioning_row_table}``).
        if en_for_map:
            prefix_hits = _prefix_fragment_candidates(en_for_map, frag)
            if len(prefix_hits) == 1:
                new_href = f"{path_part}#{prefix_hits[0]}"
                out = _rewrite_href(out, href, new_href)
                continue

        # 4) Fragment still missing: find sibling page via local toc (§6.153).
        # Relative href is always computed from the linking page (§6.158) so we
        # never emit a toc-folder basename that is unreachable from ``en_page_path``.
        found = _find_href_declaring_frag_via_toc(
            en_page_path=en_page_path,
            broken_abs_path=abs_path,
            frag=frag,
            read_text=read_text,
        )
        if found:
            out = _rewrite_href(out, href, found)

    return out


def _prefix_fragment_candidates(md: str, frag: str) -> list[str]:
    """Anchors that equal ``frag`` or uniquely extend it (``frag_…``)."""
    if not md or not frag:
        return []
    found: list[str] = []
    for match in re.finditer(r"\{#([^}]+)\}", md):
        anchor = match.group(1)
        if anchor == frag or anchor.startswith(frag + "_"):
            found.append(anchor)
    doc = parse_markdown(md)
    for heading in _iter_headings(doc.children):
        from ydbdoc_review.rendering.markdown_renderer import _render_inline

        plain = _render_inline(heading.children).strip()
        title, explicit = split_heading_anchor_suffix(plain)
        anchor = explicit or diplodoc_auto_slug(title)
        if not anchor:
            continue
        if anchor == frag or anchor.startswith(frag + "_"):
            if anchor not in found:
                found.append(anchor)
    return found
