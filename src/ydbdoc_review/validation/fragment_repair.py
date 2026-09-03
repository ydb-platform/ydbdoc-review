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
from urllib.parse import unquote

from ydbdoc_review.navigation.redirects import (
    iter_redirect_mappings,
    redirect_public_path_to_repo_md,
)
from ydbdoc_review.navigation.toc import parse_toc_items
from ydbdoc_review.parsing.ast_types import Heading
from ydbdoc_review.parsing.include_paths import collect_yfm_includes, resolve_locale_md_path
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.autotitle_hrefs import _AUTO_LINK
from ydbdoc_review.validation.yfm_anchor import (
    _iter_headings,
    diplodoc_auto_slug,
    split_heading_anchor_suffix,
)

DocsReader = Callable[[str], str | None]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_RAW_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)(\r?\n|$)", re.MULTILINE)


def _append_explicit_anchor_to_heading_index(
    en_md: str, en_heads: list[Heading], index: int, frag: str
) -> str | None:
    raw = list(_RAW_HEADING.finditer(en_md))
    if len(raw) != len(en_heads) or [len(m.group(1)) for m in raw] != [
        h.level for h in en_heads
    ]:
        return None
    match = raw[index]
    body = match.group(2)
    if split_heading_anchor_suffix(body.strip())[1] is not None:
        return None
    replacement = f"{match.group(1)} {body} {{#{frag}}}{match.group(3)}"
    return en_md[: match.start()] + replacement + en_md[match.end() :]


def declare_explicit_fragment_on_include_owner(
    en_md: str, ru_md: str, frag: str
) -> str | None:
    """Append-only declare for include owners when outline-aligned declare fails."""
    if not frag or not frag.isascii():
        return None
    if _page_declares_fragment(en_md, frag):
        return en_md
    from ydbdoc_review.validation.yfm_anchor import (
        _heading_plain_text,
        _legacy_transliterated_slug,
        diplodoc_auto_slug,
    )

    try:
        ru_heads = list(_iter_headings(parse_markdown(ru_md).children))
        en_heads = list(_iter_headings(parse_markdown(en_md).children))
    except Exception:
        return None

    ru_matches: list[int] = []
    for index, heading in enumerate(ru_heads):
        title = _heading_plain_text(heading)
        if frag in {heading.anchor, diplodoc_auto_slug(title), _legacy_transliterated_slug(title)}:
            ru_matches.append(index)
    if len(ru_matches) != 1:
        return None
    ru_title = _heading_plain_text(ru_heads[ru_matches[0]])

    slug_candidates: list[int] = []
    for index, heading in enumerate(en_heads):
        if heading.anchor is not None:
            continue
        title = _heading_plain_text(heading)
        if diplodoc_auto_slug(title) == frag:
            slug_candidates.append(index)
    if len(slug_candidates) == 1:
        return _append_explicit_anchor_to_heading_index(en_md, en_heads, slug_candidates[0], frag)
    if len(slug_candidates) > 1:
        return None

    def _keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ]+", text.casefold())
        return {token for token in tokens if len(token) >= 3}

    ru_keywords = _keywords(ru_title)
    if not ru_keywords:
        return None
    overlap_candidates: list[int] = []
    for index, heading in enumerate(en_heads):
        if heading.anchor is not None:
            continue
        if ru_keywords & _keywords(_heading_plain_text(heading)):
            overlap_candidates.append(index)
    if len(overlap_candidates) != 1:
        return None
    return _append_explicit_anchor_to_heading_index(en_md, en_heads, overlap_candidates[0], frag)


def add_explicit_ascii_fragment_anchor(en_md: str, ru_md: str, frag: str) -> str | None:
    """Append an exact ASCII id only to a fully outline-aligned heading."""
    if not frag or not frag.isascii():
        return en_md
    if _page_declares_fragment(en_md, frag):
        return en_md
    try:
        ru_heads = list(_iter_headings(parse_markdown(ru_md).children))
        en_heads = list(_iter_headings(parse_markdown(en_md).children))
    except Exception:
        return None
    if [h.level for h in ru_heads] != [h.level for h in en_heads]:
        return None
    from ydbdoc_review.validation.yfm_anchor import _heading_plain_text, _legacy_transliterated_slug

    matches: list[int] = []
    for index, heading in enumerate(ru_heads):
        title = _heading_plain_text(heading)
        if frag in {heading.anchor, diplodoc_auto_slug(title), _legacy_transliterated_slug(title)}:
            matches.append(index)
    if len(matches) != 1:
        return None
    index = matches[0]
    if en_heads[index].anchor is not None:
        return None
    return _append_explicit_anchor_to_heading_index(en_md, en_heads, index, frag)


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
    from ydbdoc_review.validation.yfm_anchor import _legacy_transliterated_slug

    plain = _render_inline(heading.children).strip()
    title, explicit = split_heading_anchor_suffix(plain)
    if explicit == frag:
        return True
    # Bare heading: Diplodoc auto-slug or legacy RU translit (§6.239 / R-GL-1).
    if explicit is None and frag in {
        diplodoc_auto_slug(title),
        _legacy_transliterated_slug(title),
    }:
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

    Accepts explicit ``{#frag}``, Diplodoc auto-slugs from bare headings
    (§6.158 — ``### Parameters`` ⇒ ``#parameters``), and
    ``_legacy_transliterated_slug`` matches (same ownership as
    ``add_explicit_ascii_fragment_anchor`` / R-GL-1).
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


def _ru_fragment_for_same_target(
    ru_source: str,
    *,
    ru_page_path: str,
    href_path: str,
) -> str | None:
    """Return the RU source ``#fragment`` for the same relative ``.md`` target."""
    ru_abs = _resolve_href_path(ru_page_path, href_path)
    if ru_abs is None:
        return None

    def _match_href(raw_href: str) -> str | None:
        href = raw_href.strip()
        if "#" not in href:
            return None
        path_part, frag = href.rsplit("#", 1)
        if not frag or not path_part.endswith(".md"):
            return None
        if _resolve_href_path(ru_page_path, path_part) == ru_abs:
            return frag
        return None

    for match in _MD_LINK.finditer(ru_source or ""):
        found = _match_href(match.group(2))
        if found:
            return found
    for href in _AUTO_LINK.findall(ru_source or ""):
        found = _match_href(href)
        if found:
            return found
    return None


def _frag_matches_ru_heading_slug(frag: str, ru_md: str) -> bool:
    """True if ``frag`` is a RU Diplodoc auto-slug or legacy transliteration."""
    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.validation.yfm_anchor import (
        _heading_plain_text,
        _iter_headings,
        _legacy_transliterated_slug,
        diplodoc_auto_slug,
    )

    decoded = unquote(frag)
    for heading in _iter_headings(parse_markdown(ru_md).children):
        ru_text = _heading_plain_text(heading)
        ru_auto = diplodoc_auto_slug(ru_text)
        legacy = _legacy_transliterated_slug(ru_text)
        for slug in (ru_auto, legacy):
            if slug and (decoded == slug or decoded.startswith(f"{slug}-")):
                return True
    return False


def _try_remap_missing_fragment_via_ru_en(
    *,
    frag: str,
    path_part: str,
    en_page_path: str,
    ru_source: str | None,
    en_target: str | None,
    en_abs: str,
    read_text: DocsReader,
    redirect_en_paths: dict[str, str],
    redirect_ru_paths: dict[str, str],
) -> tuple[str, str] | None:
    """Map a missing EN fragment via the paired RU/EN target pages."""
    # TASK-51797 policy: ASCII/transliterated RU fragments are source-owned
    # and must remain byte-identical. The final-tree repair declares that
    # exact id on the paired EN heading. Only Cyrillic ids are localized.
    if not _CYRILLIC.search(unquote(frag)):
        return None
    ru_abs = _resolve_href_path(en_page_path.replace("/docs/en/", "/docs/ru/", 1), path_part)
    if ru_abs is None:
        return None
    ru_target = read_text(ru_abs)
    if not ru_target and ru_abs in redirect_ru_paths:
        ru_abs = redirect_ru_paths[ru_abs]
        ru_target = read_text(ru_abs)
    if not ru_target:
        return None
    effective_en_abs = en_abs
    redirect_en_abs = redirect_en_paths.get(en_abs)
    redirect_en_target = read_text(redirect_en_abs) if redirect_en_abs else None
    if redirect_en_target is not None:
        effective_en_abs = redirect_en_abs
        en_target = redirect_en_target
    if not en_target:
        return None
    ru_page_path = en_page_path.replace("/docs/en/", "/docs/ru/", 1)
    ru_frag = _ru_fragment_for_same_target(
        ru_source or "", ru_page_path=ru_page_path, href_path=path_part
    )
    candidates: list[str] = []
    if ru_frag:
        candidates.append(ru_frag)
    # Only Cyrillic fragments are remapped to a deterministic EN heading id.
    if frag not in candidates and (_CYRILLIC.search(unquote(frag))):
        candidates.append(frag)
    if not candidates:
        return None
    for candidate in candidates:
        new_frag = _remap_fragment_via_ru_en_pages(candidate, ru_target, en_target)
        if (
            new_frag
            and new_frag != frag
            and fragment_declared_in_markdown(
                en_target,
                new_frag,
                page_path=effective_en_abs,
                read_text=read_text,
            )
        ):
            return new_frag, effective_en_abs
    return None


def _remap_fragment_via_ru_en_pages(frag: str, ru_md: str, en_md: str) -> str | None:
    """When a Cyrillic/auto slug is missing on EN, map via paired headings."""
    from urllib.parse import unquote

    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.validation.yfm_anchor import (
        _heading_plain_text,
        _iter_headings,
        _legacy_transliterated_slug,
        build_heading_anchor_map,
        diplodoc_auto_slug,
    )

    decoded = unquote(frag)
    ru_doc = parse_markdown(ru_md)
    en_doc = parse_markdown(en_md)
    anchor_map = build_heading_anchor_map(ru_doc, en_doc)
    if decoded in anchor_map:
        return anchor_map[decoded]
    if frag in anchor_map:
        return anchor_map[frag]

    ru_heads = list(_iter_headings(ru_doc.children))
    en_heads = list(_iter_headings(en_doc.children))
    for src_h, tgt_h in zip(ru_heads, en_heads, strict=False):
        ru_text = _heading_plain_text(src_h)
        ru_auto = diplodoc_auto_slug(ru_text)
        legacy = _legacy_transliterated_slug(ru_text)
        en_anchor = tgt_h.anchor
        en_auto = diplodoc_auto_slug(_heading_plain_text(tgt_h))

        def _en_id() -> str | None:
            if en_anchor and en_anchor.isascii():
                return en_anchor
            return en_auto or None

        if ru_auto and (decoded == ru_auto or decoded.startswith(f"{ru_auto}-")):
            found = _en_id()
            if found:
                return found
        if legacy and (decoded == legacy or decoded.startswith(f"{legacy}-")):
            found = _en_id()
            if found:
                return found
        if src_h.anchor and (decoded == src_h.anchor or decoded.startswith(f"{src_h.anchor}-")):
            if en_anchor:
                return en_anchor
            if en_auto:
                return en_auto
    return None


def prefer_baseline_href_when_fragment_missing(
    en_text: str,
    en_baseline: str | None,
    *,
    en_page_path: str,
    read_text: DocsReader,
) -> str:
    """Restore a valid same-slot EN baseline href over a broken restored href.

    ``restore_md_link_hrefs`` can copy a new RU fragment that does not exist on
    the EN target. Keep the baseline href only when the link occupies the same
    Markdown-link slot (and label), its fragment is declared, and the current
    fragment is not.
    """
    if not en_text or not en_baseline:
        return en_text
    current = list(_MD_LINK.finditer(en_text))
    baseline = list(_MD_LINK.finditer(en_baseline))
    replacements: list[tuple[int, int, str]] = []
    for index, match in enumerate(current):
        if index >= len(baseline):
            break
        base_match = baseline[index]
        label = " ".join(match.group(1).split()).casefold()
        base_label = " ".join(base_match.group(1).split()).casefold()
        if label != base_label:
            continue
        href = match.group(2).strip()
        base_href = base_match.group(2).strip()
        if href == base_href or "#" not in href or "#" not in base_href:
            continue
        path_part, frag = href.rsplit("#", 1)
        base_path, base_frag = base_href.rsplit("#", 1)
        if (
            not frag
            or not base_frag
            or not path_part.endswith(".md")
            or not base_path.endswith(".md")
        ):
            continue
        abs_path = _resolve_href_path(en_page_path, path_part)
        base_abs = _resolve_href_path(en_page_path, base_path)
        if abs_path is None or base_abs is None:
            continue
        target = read_text(abs_path)
        if target and fragment_declared_in_markdown(
            target,
            frag,
            page_path=abs_path,
            read_text=read_text,
        ):
            continue
        base_target = read_text(base_abs)
        if not base_target or not fragment_declared_in_markdown(
            base_target,
            base_frag,
            page_path=base_abs,
            read_text=read_text,
        ):
            continue
        replacements.append(
            (
                match.start(),
                match.end(),
                f"[{match.group(1)}]({base_href})",
            )
        )
    out = en_text
    for start, end, replacement in reversed(replacements):
        out = out[:start] + replacement + out[end:]
    return out


def repair_en_fragments(
    en_text: str,
    *,
    en_page_path: str,
    read_text: DocsReader,
    ru_source: str | None = None,
    en_baseline: str | None = None,
    docs_root: str = "ydb/docs",
) -> str:
    """Fix missing EN ``#fragment`` targets in ``en_text`` for ``en_page_path``.

    ``read_text`` loads repo paths (``ydb/docs/en/...`` / ``ru/...``) from the
    translation worktree / upstream tip.
    """
    if not en_text or not en_page_path:
        return en_text

    out = prefer_baseline_href_when_fragment_missing(
        en_text,
        en_baseline,
        en_page_path=en_page_path,
        read_text=read_text,
    )
    ru_by_frag = _autotitle_hrefs_by_frag(ru_source or "")
    en_base_by_frag = _autotitle_hrefs_by_frag(en_baseline or "")
    redirects_yaml = read_text(f"{docs_root.strip('/')}/redirects.yaml") or ""
    redirect_mappings = iter_redirect_mappings(redirects_yaml)
    redirect_en_paths = {
        redirect_public_path_to_repo_md(
            src, locale="en", docs_root=docs_root
        ): redirect_public_path_to_repo_md(dst, locale="en", docs_root=docs_root)
        for src, dst in redirect_mappings.items()
    }
    redirect_ru_paths = {
        redirect_public_path_to_repo_md(
            src, locale="ru", docs_root=docs_root
        ): redirect_public_path_to_repo_md(dst, locale="ru", docs_root=docs_root)
        for src, dst in redirect_mappings.items()
    }

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

        # 0) Remap missing fragments via the paired RU/EN target page. Covers
        # Cyrillic auto-slugs and LLM-invented ASCII slugs (#40385 system-view).
        remapped = _try_remap_missing_fragment_via_ru_en(
            frag=frag,
            path_part=path_part,
            en_page_path=en_page_path,
            ru_source=ru_source,
            en_target=en_target,
            en_abs=abs_path,
            read_text=read_text,
            redirect_en_paths=redirect_en_paths,
            redirect_ru_paths=redirect_ru_paths,
        )
        if remapped:
            new_frag, effective_en_abs = remapped
            new_path = path_part
            if effective_en_abs != abs_path:
                new_path = _posix_relpath(
                    str(PurePosixPath(en_page_path).parent),
                    effective_en_abs,
                )
            out = _rewrite_href(out, href, f"{new_path}#{new_frag}")
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

        # 3) Same fragment on a sibling page — only when the original target is
        # missing, or is a stale hub ``index.md`` (§6.153 sessions). If the
        # linked file exists but lacks the frag, leave it: §6.174 / §6.158 —
        # do not invent another path or EN-only fragment id.
        if en_target is None or PurePosixPath(abs_path).name == "index.md":
            found = _find_href_declaring_frag_via_toc(
                en_page_path=en_page_path,
                broken_abs_path=abs_path,
                frag=frag,
                read_text=read_text,
            )
            if found:
                out = _rewrite_href(out, href, found)

    return out
