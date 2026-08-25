"""Deterministic RU↔EN link / heading-anchor parity (§6.174).

Policy: for a translated docs page, internal hrefs and explicit ``{#id}``
anchors must match the source twin one-to-one. No EN-only fragment remaps
(``#ldap`` must stay ``#ldap``, not become ``#ldap-auth-provider``).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from ydbdoc_review.validation.autotitle_hrefs import _AUTO_LINK

DocsTextReader = Callable[[str], str | None]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_EXPLICIT_ANCHOR = re.compile(r"\{#([^}]+)\}")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)


def _link_skeleton(text: str, links: list[re.Match[str]]) -> str:
    out: list[str] = []
    cursor = 0
    for match in links:
        out.append(text[cursor : match.start()])
        out.append(f"[{match.group(1)}](<href>)")
        cursor = match.end()
    out.append(text[cursor:])
    return "".join(out)


def is_href_only_change(base: str | None, current: str | None) -> bool:
    """True when two Markdown texts differ only in link destinations."""
    if base is None or current is None or base == current:
        return False
    base_links = list(_MD_LINK.finditer(base))
    current_links = list(_MD_LINK.finditer(current))
    return (
        bool(base_links)
        and len(base_links) == len(current_links)
        and _link_skeleton(base, base_links).rstrip()
        == _link_skeleton(current, current_links).rstrip()
    )


def _is_internal_href(href: str) -> bool:
    href = href.strip()
    if not href or href.startswith("mailto:") or _HTTP.match(href):
        return False
    # Autotitle / markdown / in-page fragment.
    if href.startswith("#"):
        return True
    path = href.split("#", 1)[0]
    return path.endswith(".md") or path.endswith(".yaml") or path.endswith(".yml")


def collect_internal_hrefs(text: str) -> list[str]:
    """Internal docs hrefs in document order (autotitle + ``[]()``)."""
    found: list[str] = []
    for href in _AUTO_LINK.findall(text or ""):
        if _is_internal_href(href):
            found.append(href.strip())
    for match in _MD_LINK.finditer(text or ""):
        label, href = match.group(1), match.group(2).strip()
        if label.strip() == "{#T}":
            continue  # already counted via _AUTO_LINK
        if _is_internal_href(href):
            found.append(href)
    return found


def collect_explicit_anchors(text: str) -> list[str]:
    """Explicit ``{#id}`` ids (headings and rare inline), excluding ``{#T}``."""
    out: list[str] = []
    for match in _EXPLICIT_ANCHOR.finditer(text or ""):
        anchor = match.group(1).strip()
        if not anchor or anchor.upper() == "T":
            continue
        out.append(anchor)
    return out


def restore_unique_same_path_fragments(target_text: str, source_text: str) -> str:
    """Restore source fragments only for unique links with an unchanged path."""
    source_by_path: dict[str, list[str]] = {}
    for match in _MD_LINK.finditer(source_text or ""):
        href = match.group(2).strip()
        if "#" not in href:
            continue
        path, fragment = href.rsplit("#", 1)
        if path.endswith(".md") and fragment:
            source_by_path.setdefault(path, []).append(fragment)

    target_links = list(_MD_LINK.finditer(target_text or ""))
    target_by_path: dict[str, list[re.Match[str]]] = {}
    for match in target_links:
        href = match.group(2).strip()
        if "#" not in href:
            continue
        path, fragment = href.rsplit("#", 1)
        if path.endswith(".md") and fragment:
            target_by_path.setdefault(path, []).append(match)

    replacements: list[tuple[re.Match[str], str]] = []
    for path, source_fragments in source_by_path.items():
        targets = target_by_path.get(path, [])
        if len(source_fragments) == len(targets) == 1:
            replacements.append((targets[0], f"{path}#{source_fragments[0]}"))

    out = target_text
    for match, href in sorted(replacements, key=lambda item: item[0].start(), reverse=True):
        out = out[: match.start(2)] + href + out[match.end(2) :]
    return out


def check_href_parity(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
    ignore_basenames: set[str] | frozenset[str] | None = None,
    en_page_path: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
) -> list[str]:
    """Blocking when EN internal href multiset ≠ RU (§6.174)."""
    if source_lang.lower() not in {"ru", "russian"}:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []

    # Markdown renderers may percent-encode Unicode fragments. URL decoding is
    # semantics-preserving and avoids false mismatches such as #50854.
    src = Counter(unquote(href) for href in collect_internal_hrefs(source_text))
    tgt = Counter(unquote(href) for href in collect_internal_hrefs(target_text))
    if ignore_basenames:

        def _kept(counter: Counter[str]) -> Counter[str]:
            out: Counter[str] = Counter()
            for href, n in counter.items():
                base = PurePosixPath(href.split("#", 1)[0]).name
                if base in ignore_basenames:
                    continue
                out[href] = n
            return out

        src = _kept(src)
        tgt = _kept(tgt)

    if src == tgt:
        return []

    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    # A section can be moved independently in RU and EN. Accept a localized EN
    # destination when the RU path is unreachable in the EN toc, the EN path is
    # reachable, and both links keep the same basename and fragment (#50976).
    if missing and extra and en_page_path and en_toc_reachable is not None:
        from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

        paired_missing: set[int] = set()
        paired_extra: set[int] = set()
        for missing_idx, source_href in enumerate(missing):
            source_target = resolve_internal_md_href(en_page_path, source_href)
            if source_target is not None and source_target in en_toc_reachable:
                continue
            source_path, _, source_fragment = source_href.partition("#")
            for extra_idx, target_href in enumerate(extra):
                if extra_idx in paired_extra:
                    continue
                target_path, _, target_fragment = target_href.partition("#")
                if source_fragment != target_fragment:
                    continue
                if PurePosixPath(source_path).name != PurePosixPath(target_path).name:
                    continue
                target = resolve_internal_md_href(en_page_path, target_href)
                if target is not None and target in en_toc_reachable:
                    paired_missing.add(missing_idx)
                    paired_extra.add(extra_idx)
                    break
        missing = [href for idx, href in enumerate(missing) if idx not in paired_missing]
        extra = [href for idx, href in enumerate(extra) if idx not in paired_extra]

    # EN may link to toc-reachable pages that the source-PR RU snapshot lacks
    # (post-merge main drift, #49451 self-heal). Those extras are not blockers.
    if extra and en_page_path and en_toc_reachable is not None:
        from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

        kept_extra: list[str] = []
        for href in extra:
            target = resolve_internal_md_href(en_page_path, href)
            if target is not None and target in en_toc_reachable:
                continue
            kept_extra.append(href)
        extra = kept_extra
    if not missing and not extra:
        return []
    parts: list[str] = []
    if missing:
        preview = ", ".join(f"`{h}`" for h in missing[:6])
        more = f", … (+{len(missing) - 6})" if len(missing) > 6 else ""
        parts.append(f"missing in EN: {preview}{more}")
    if extra:
        preview = ", ".join(f"`{h}`" for h in extra[:6])
        more = f", … (+{len(extra) - 6})" if len(extra) > 6 else ""
        parts.append(f"extra in EN: {preview}{more}")
    return [f"href_parity: RU/EN internal links differ — {'; '.join(parts)}"]


def check_heading_anchor_parity(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
) -> list[str]:
    """Blocking when explicit ``{#id}`` multisets differ (RU vs EN)."""
    if source_lang.lower() not in {"ru", "russian"}:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []

    src = Counter(collect_explicit_anchors(source_text))
    tgt = Counter(collect_explicit_anchors(target_text))
    if src == tgt:
        return []

    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    parts: list[str] = []
    if missing:
        preview = ", ".join(f"`{{#{a}}}`" for a in missing[:8])
        more = f", … (+{len(missing) - 8})" if len(missing) > 8 else ""
        parts.append(f"missing in EN: {preview}{more}")
    if extra:
        preview = ", ".join(f"`{{#{a}}}`" for a in extra[:8])
        more = f", … (+{len(extra) - 8})" if len(extra) > 8 else ""
        parts.append(f"extra in EN: {preview}{more}")
    return [f"anchor_parity: RU/EN explicit {{#id}} differ — {'; '.join(parts)}"]


def _href_targets_page(
    href: str,
    en_page_path: str,
    *,
    from_path: str,
) -> str | None:
    """Return fragment if ``href`` from ``from_path`` resolves to ``en_page_path``."""
    href = href.strip()
    if "#" not in href:
        return None
    path_part, frag = href.rsplit("#", 1)
    if not frag:
        return None
    if path_part in {"", "."}:
        # in-page — not inbound from another file
        return None
    from ydbdoc_review.validation.glossary_toc_links import (
        normalize_repo_path,
        resolve_internal_md_href,
    )

    resolved = resolve_internal_md_href(from_path, href)
    if resolved is None:
        return None
    if normalize_repo_path(resolved) == normalize_repo_path(en_page_path):
        return frag
    return None


def iter_en_markdown_paths(repo_path: str, *, docs_root: str = "ydb/docs") -> list[str]:
    root = Path(repo_path) / docs_root / "en"
    if not root.is_dir():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(repo_path).as_posix()
        out.append(rel)
    return out


def check_inbound_fragments(
    en_page_path: str,
    en_text: str,
    *,
    repo_path: str | None = None,
    read_text: DocsTextReader | None = None,
    en_paths: Iterable[str] | None = None,
    docs_root: str = "ydb/docs",
    ru_text: str | None = None,
    en_baseline_text: str | None = None,
) -> list[str]:
    """Blocking when other EN pages link to missing ``#frag`` on this page.

    Catches the #48792 hole: ``authentication.md`` anchors become ``{#ldap}``
    while ``create-resource-pool-classifier.md`` still has ``#ldap-auth-provider``.

    Skips ambient EN typos that neither the RU twin nor the pre-translate EN
    baseline ever declared (e.g. ``#tablets`` vs ``{#tablet}``, #49451).
    """
    if not en_page_path or not en_text:
        return []
    if en_paths is None:
        if not repo_path:
            return []
        en_paths = iter_en_markdown_paths(repo_path, docs_root=docs_root)

    declared = set(collect_explicit_anchors(en_text))
    ru_declared = set(collect_explicit_anchors(ru_text)) if ru_text is not None else None
    baseline_declared = (
        set(collect_explicit_anchors(en_baseline_text)) if en_baseline_text is not None else None
    )
    removed_from_baseline = baseline_declared - declared if baseline_declared is not None else None
    # Also treat Diplodoc auto-slugs as declared via fragment_repair helper.
    from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown

    issues: list[str] = []
    seen: set[str] = set()
    for other_path in en_paths:
        if other_path == en_page_path:
            continue
        if read_text is not None:
            other_text = read_text(other_path)
        elif repo_path:
            p = Path(repo_path) / other_path
            other_text = p.read_text(encoding="utf-8") if p.is_file() else None
        else:
            other_text = None
        if not other_text:
            continue
        for href in collect_internal_hrefs(other_text):
            frag = _href_targets_page(href, en_page_path, from_path=other_path)
            if not frag:
                continue
            # Translation QA only: RU still has the id, or we dropped an EN id.
            if ru_declared is not None or removed_from_baseline is not None:
                ru_needs = bool(ru_declared and frag in ru_declared)
                we_removed = bool(
                    removed_from_baseline is not None and frag in removed_from_baseline
                )
                if not ru_needs and not we_removed:
                    continue
            key = f"{other_path}::{href}"
            if key in seen:
                continue
            seen.add(key)
            ok = frag in declared or fragment_declared_in_markdown(
                en_text, frag, page_path=en_page_path, read_text=read_text
            )
            if ok:
                continue
            issues.append(
                f"inbound_fragment: `{other_path}` links to "
                f"`{PurePosixPath(en_page_path).name}#{frag}` but that anchor "
                f"is missing on the translated page"
            )
            if len(issues) >= 12:
                issues.append("inbound_fragment: … further inbound misses truncated")
                return issues
    return issues


_SEE_SECTION_PLAIN = re.compile(
    r"(see the section )([^.\n\[\]]+)(\.)",
    re.IGNORECASE,
)


def _iter_md_links(text: str) -> list[tuple[str, str, int, int]]:
    """``(label, href, start, end)`` for non-autotitle internal ``[]()`` links."""
    out: list[tuple[str, str, int, int]] = []
    for match in _MD_LINK.finditer(text or ""):
        label, href = match.group(1), match.group(2).strip()
        if label.strip() == "{#T}":
            continue
        if not _is_internal_href(href):
            continue
        out.append((label, href, match.start(), match.end()))
    return out


def apply_href_only_delta(
    source_base: str | None,
    source_current: str,
    target_baseline: str | None,
) -> str | None:
    """Apply a pure RU Markdown-link target delta to EN without an LLM.

    Returns ``None`` when the source edit contains anything except href
    replacements or when an old target cannot be matched unambiguously.
    """
    if not source_base or target_baseline is None:
        return None
    base_links = list(_MD_LINK.finditer(source_base))
    current_links = list(_MD_LINK.finditer(source_current))
    if len(base_links) != len(current_links) or not base_links:
        return None

    if not is_href_only_change(source_base, source_current):
        return None
    changes = [
        (before.group(2).strip(), after.group(2).strip())
        for before, after in zip(base_links, current_links, strict=True)
        if before.group(2).strip() != after.group(2).strip()
    ]
    if not changes:
        return None

    out = target_baseline
    for old_href, new_href in changes:
        target_links = list(_MD_LINK.finditer(out))
        old_matches = [m for m in target_links if m.group(2).strip() == old_href]
        if len(old_matches) == 1:
            match = old_matches[0]
            out = out[: match.start()] + f"[{match.group(1)}]({new_href})" + out[match.end() :]
            continue
        if not old_matches and any(m.group(2).strip() == new_href for m in target_links):
            continue  # Proven accepted no-op: EN main already has the target.
        return None
    return out


def restore_md_link_hrefs(
    translated: str,
    source_ru: str,
    *,
    source_ru_base: str | None = None,
    target_baseline: str | None = None,
) -> str:
    """Force EN ``[label](href)`` targets to match RU (§6.174 / #49451).

    1. When non-autotitle internal link **counts** match, rewrite each EN href
       to the RU twin in document order (fixes wrong path e.g.
       ``secondary_index.md#example`` → ``min_max_index.md#example``).
    2. When RU still has underrepresented hrefs, wrap plain
       ``see the section Title.`` phrases with ``[Title](href)`` (glossary
       dropped the ``architecture/metadata-services.md`` links).
    """
    if not translated or not source_ru:
        return translated

    ru_links = _iter_md_links(source_ru)
    if not ru_links:
        return translated

    out = translated
    en_links = _iter_md_links(out)

    # A translated page is not required to have historically identical hrefs
    # to its RU twin.  For an incremental source change, mirror only the RU
    # href positions that actually changed.  Rewriting every EN link by
    # position corrupted unrelated links in #45949.
    if source_ru_base is not None and target_baseline is not None:
        ru_base_links = _iter_md_links(source_ru_base)
        baseline_links = _iter_md_links(target_baseline)
        if (
            len(ru_base_links) == len(ru_links)
            and len(baseline_links) == len(ru_links)
            and len(en_links) == len(ru_links)
        ):
            replacements: dict[int, str] = {}
            for idx, (before, after) in enumerate(zip(ru_base_links, ru_links, strict=True)):
                if before[1] != after[1]:
                    replacements[idx] = after[1]
            if replacements:
                pieces: list[str] = []
                cursor = len(out)
                for idx in reversed(range(len(en_links))):
                    label, href, start, end = en_links[idx]
                    pieces.append(out[end:cursor])
                    pieces.append(f"[{label}]({replacements.get(idx, href)})")
                    cursor = start
                pieces.append(out[:cursor])
                return "".join(reversed(pieces))
            return out

    ru_href_counts = Counter(href for _label, href, _s, _e in ru_links)
    en_href_counts = Counter(href for _label, href, _s, _e in en_links)

    if len(en_links) == len(ru_links) and en_links and en_href_counts != ru_href_counts:
        # Rebuild from the end so offsets stay valid.
        pieces: list[str] = []
        cursor = len(out)
        for (elabel, _ehref, start, end), (_rlabel, rhref, _rs, _re) in zip(
            reversed(en_links), reversed(ru_links), strict=True
        ):
            pieces.append(out[end:cursor])
            pieces.append(f"[{elabel}]({rhref})")
            cursor = start
        pieces.append(out[:cursor])
        out = "".join(reversed(pieces))

    # Reinject dropped links (counts still differ or plain-text leftovers).
    present = Counter(href for _label, href, _s, _e in _iter_md_links(out))
    needed = ru_href_counts
    missing_hrefs: list[str] = []
    for href, n in needed.items():
        for _ in range(max(0, n - present.get(href, 0))):
            missing_hrefs.append(href)

    for href in missing_hrefs:

        def _wrap(match: re.Match[str], *, _href: str = href) -> str:
            return f"{match.group(1)}[{match.group(2).strip()}]({_href}){match.group(3)}"

        new_out, n = _SEE_SECTION_PLAIN.subn(_wrap, out, count=1)
        if n:
            out = new_out
            continue
        # Fallback: no plain "see the section" — leave for critic / continue.
    return out


def insert_missing_autotitle_list_items(
    translated: str,
    source_ru: str,
    *,
    en_page_path: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
) -> str:
    """Insert missing ``[{#T}](href)`` bullet lines from RU into EN (#49451).

    When RU and EN are sibling bullet lists and EN omitted a path that RU
    still lists (e.g. critic dropped ``static-group-self-heal.md`` while
    adding ``state-storage-reconfiguration.md``), splice the missing
    ``- [{#T}](…)`` line after the previous shared neighbor.

    Skips hrefs whose EN targets are outside the toc graph (no EN page yet)
    so restore does not fight ``strip_unreachable`` / reintroduce 🔴
    ``href_parity`` on the next verify.
    """
    if not translated or not source_ru:
        return translated

    ru_hrefs = [h for h in _AUTO_LINK.findall(source_ru) if _is_internal_href(h)]
    en_hrefs = [h for h in _AUTO_LINK.findall(translated) if _is_internal_href(h)]
    if not ru_hrefs:
        return translated

    skip: set[str] = set()
    if en_toc_reachable is not None and en_page_path:
        from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

        for href in ru_hrefs:
            target = resolve_internal_md_href(en_page_path, href)
            if target is not None and target not in en_toc_reachable:
                skip.add(href)

    missing = [h for h in ru_hrefs if h not in en_hrefs and h not in skip]
    if not missing:
        return translated

    out = translated
    for href in missing:
        if f"[{{#T}}]({href})" in out:
            continue
        # Find previous RU neighbor that exists in EN; insert after that line.
        try:
            idx = ru_hrefs.index(href)
        except ValueError:
            continue
        prev = next(
            (
                ru_hrefs[i]
                for i in range(idx - 1, -1, -1)
                if ru_hrefs[i] in en_hrefs or f"[{{#T}}]({ru_hrefs[i]})" in out
            ),
            None,
        )
        line = f"- [{{#T}}]({href})"
        if prev:
            needle = f"[{{#T}}]({prev})"
            pos = out.find(needle)
            if pos < 0:
                continue
            # End of the line containing needle.
            eol = out.find("\n", pos)
            if eol < 0:
                out = out + "\n" + line + "\n"
            else:
                out = out[:eol] + "\n" + line + out[eol:]
        else:
            # No previous neighbor — insert before first EN autotitle bullet.
            m = re.search(r"(?m)^(\s*-\s*\[\{#T\}\]\([^)]+\))", out)
            if not m:
                continue
            out = out[: m.start()] + line + "\n" + out[m.start() :]
        en_hrefs.append(href)
    return out
