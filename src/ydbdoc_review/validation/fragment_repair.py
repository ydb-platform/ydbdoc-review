"""Repair EN ``path#fragment`` when the **path** is wrong but the frag is shared.

§6.174: do **not** remap RU fragments onto EN-only ids (``#ldap`` must stay
``#ldap``). Cross-locale id drift is caught by ``href_parity`` /
``anchor_parity`` / ``inbound_fragment`` instead.

Still covers (§6.142 / #48047, §6.153 / #48012, §6.158 / #48223):

1. Stale autotitle path — ``index.md#sessions`` while the heading lives on
   ``execution_process.md`` (same fragment id on both locales).
2. Both RU and EN baseline stale — discover a sibling via local toc.
3. Do **not** toc-retarget when the original EN target file already exists
   (§6.158): bare basenames under the wrong folder break ``build-docs``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath

from ydbdoc_review.parsing.ast_types import Heading
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.yfm_anchor import (
    _iter_headings,
    diplodoc_auto_slug,
    split_heading_anchor_suffix,
)

DocsReader = Callable[[str], str | None]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")


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
        resolved = resolve_locale_md_path(page_path, inc.path, docs_root=docs_root)
        if resolved is None:
            continue
        included = read_text(resolved)
        if included and _page_declares_fragment(included, frag):
            return True
    return False
