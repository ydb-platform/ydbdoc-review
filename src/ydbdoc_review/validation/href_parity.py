"""Deterministic RU↔EN link / heading-anchor parity (§6.174 / REQUIREMENTS §8).

Policy: for a translated docs page, internal hrefs must match the source twin
one-to-one. Explicit ASCII ``{#id}`` anchors stay byte-identical; Cyrillic RU
anchors map to a single English id (job dictionary / ``english_yfm_anchor``).
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from ydbdoc_review.validation.autotitle_hrefs import _AUTO_LINK
from ydbdoc_review.validation.link_contract import LinkContractIssue, LinkContractResult
from ydbdoc_review.validation.yfm_anchor import (
    JobAnchorDictionary,
    _heading_plain_text,
    _iter_headings,
    english_yfm_anchor,
    is_ascii_yfm_anchor,
)

DocsTextReader = Callable[[str], str | None]

_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_EXPLICIT_ANCHOR = re.compile(r"\{#([^}]+)\}")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?<!`)`+[^`\n]*`+(?!`)")
_HTML_COMMENT = re.compile(r"<!--[\s\S]*?-->")


def _mask_link_protected_ranges(text: str) -> str:
    chars = list(text)

    def mask(start: int, end: int) -> None:
        for idx in range(start, end):
            if chars[idx] != "\n":
                chars[idx] = " "

    offset = 0
    fence_char = ""
    fence_len = 0
    fence_start = 0
    previous_blank = True
    indented_start: int | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        logical = re.sub(r"^ {0,3}(?:> ?)+", "", stripped)
        if not fence_char:
            opening = _FENCE_OPEN.match(logical)
            if opening:
                marker = opening.group(1)
                fence_char, fence_len, fence_start = marker[0], len(marker), offset
            elif stripped.startswith("    ") and previous_blank:
                indented_start = offset
            elif indented_start is not None and not stripped.startswith("    "):
                mask(indented_start, offset)
                indented_start = None
        elif re.match(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}\s*$", logical):
            mask(fence_start, offset + len(line))
            fence_char = ""
        offset += len(line)
        previous_blank = not stripped.strip()
    if fence_char:
        mask(fence_start, len(text))
    if indented_start is not None:
        mask(indented_start, len(text))

    masked = "".join(chars)
    for pattern in (_INLINE_CODE, _HTML_COMMENT):
        for match in pattern.finditer(masked):
            mask(match.start(), match.end())
        masked = "".join(chars)
    return masked


def _iter_visible_md_link_matches(text: str) -> Iterable[re.Match[str]]:
    masked = _mask_link_protected_ranges(text)
    for match in _MD_LINK.finditer(masked):
        if match.start() > 0 and text[match.start() - 1] == "!":
            continue
        original = _MD_LINK.match(text, match.start())
        if original is not None:
            yield original


def _markdown_destination_span(raw: str) -> tuple[int, int, str] | None:
    """Locate a Markdown destination while leaving wrappers/titles untouched."""
    start = len(raw) - len(raw.lstrip())
    if start == len(raw):
        return None
    if raw[start] == "<":
        end_marker = raw.find(">", start + 1)
        if end_marker < 0:
            return None
        return start + 1, end_marker, raw[start + 1 : end_marker]
    end = start
    while end < len(raw) and not raw[end].isspace():
        end += 1
    return start, end, raw[start:end]


def _en_page_docs_root(en_page_path: str) -> str | None:
    page = en_page_path.replace("\\", "/")
    boundary = "/en/core/"
    if (
        page.count(boundary) != 1
        or not page.endswith(".md")
        or page.startswith("/")
        or posixpath.normpath(page) != page
        or any(part in {"", ".", ".."} for part in page.split("/"))
    ):
        return None
    docs_root, suffix = page.split(boundary, 1)
    if not docs_root or not suffix or docs_root.startswith("/"):
        return None
    return docs_root


def redirected_md_href(
    href: str,
    *,
    en_page_path: str,
    read_text: DocsTextReader,
) -> str | None:
    """Prove a different concrete EN href using the reader's frozen redirects."""
    from ydbdoc_review.navigation.link_deps import canonical_md_dependency_path
    from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown
    from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

    raw = href.strip()
    docs_root = _en_page_docs_root(en_page_path)
    if (
        docs_root is None
        or not raw
        or raw.startswith(("/", "#", "//"))
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw)
    ):
        return None
    before_fragment, fragment_marker, raw_fragment = raw.partition("#")
    raw_path, query_marker, raw_query = before_fragment.partition("?")
    if (
        not raw_path.endswith(".md")
        or not raw_path
        or "\\" in raw_path
        or "%" in raw_path
        or unquote(raw_path) != raw_path
        or any(ord(char) < 32 or ord(char) == 127 for char in raw_path)
    ):
        return None
    page = en_page_path.replace("\\", "/")
    stack = page.split("/")[:-1]
    floor = len(docs_root.split("/")) + 2
    for segment in raw_path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if len(stack) <= floor:
                return None
            stack.pop()
            continue
        if not segment:
            return None
        stack.append(segment)
    guarded_target = "/".join(stack)
    target = resolve_internal_md_href(page, raw_path)
    expected_prefix = f"{docs_root}/en/core/"
    if (
        target is None
        or target != guarded_target
        or not target.startswith(expected_prefix)
        or not target.endswith(".md")
    ):
        return None
    redirects_yaml = read_text(f"{docs_root}/redirects.yaml")
    if redirects_yaml is None:
        return None
    canonical = canonical_md_dependency_path(
        target,
        redirects_yaml=redirects_yaml,
        docs_root=docs_root,
    )
    if canonical is None or canonical == target or not canonical.startswith(expected_prefix):
        return None
    target_text = read_text(canonical)
    if target_text is None:
        return None
    if fragment_marker and raw_fragment:
        fragment = unquote(raw_fragment)
        if not fragment_declared_in_markdown(
            target_text,
            fragment,
            page_path=canonical,
            read_text=read_text,
        ):
            return None
    relative = posixpath.relpath(canonical, posixpath.dirname(page))
    return (
        relative
        + (query_marker + raw_query if query_marker else "")
        + (fragment_marker + raw_fragment if fragment_marker else "")
    )


def _segment_paragraph_links(doc: object, segment: object) -> list[object] | None:
    """Return ordinary internal links owned by one extracted paragraph."""
    from ydbdoc_review.parsing.ast_types import (
        Document,
        InlineEmphasis,
        InlineLink,
        InlineStrong,
        Paragraph,
        YfmIf,
    )
    from ydbdoc_review.segmentation.types import SegmentKind

    if getattr(segment, "kind", None) != SegmentKind.PARAGRAPH:
        return None
    node = doc
    for step in getattr(segment, "ast_path", ()):
        if not isinstance(step, int):
            return None
        if isinstance(node, Document):
            node = node.children[step]
        elif isinstance(node, YfmIf):
            node = node.branches[step]
        elif hasattr(node, "children") and isinstance(node.children, list):
            node = node.children[step]
        else:
            return None
    if not isinstance(node, Paragraph):
        return None

    links: list[object] = []

    def walk(children: list[object]) -> None:
        for child in children:
            if isinstance(child, InlineLink):
                label = "".join(
                    item.content
                    for item in child.children
                    if hasattr(item, "content")
                )
                if label.strip() != "{#T}" and _is_internal_href(child.href):
                    links.append(child)
            elif isinstance(child, (InlineEmphasis, InlineStrong)):
                walk(list(child.children))

    walk(list(node.children))
    return links


def _safe_en_md_target(
    href: str,
    *,
    en_page_path: str,
    docs_root: str,
) -> str | None:
    """Resolve one relative Markdown href without permitting locale/root escape."""
    from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

    raw = href.strip()
    before_fragment = raw.partition("#")[0]
    raw_path = before_fragment.partition("?")[0]
    expected_root = docs_root.strip("/")
    if (
        not expected_root
        or expected_root != docs_root
        or _en_page_docs_root(en_page_path) != expected_root
        or not raw_path
        or not raw_path.endswith(".md")
        or raw.startswith(("/", "#", "//"))
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw)
        or "\\" in raw_path
        or "%" in raw_path
        or unquote(raw_path) != raw_path
        or any(ord(char) < 32 or ord(char) == 127 for char in raw_path)
    ):
        return None
    stack = en_page_path.split("/")[:-1]
    floor = len(expected_root.split("/")) + 2
    for segment in raw_path.split("/"):
        if segment == ".":
            continue
        if segment == "..":
            if len(stack) <= floor:
                return None
            stack.pop()
            continue
        if not segment:
            return None
        stack.append(segment)
    guarded_target = "/".join(stack)
    target = resolve_internal_md_href(en_page_path, raw_path)
    prefix = f"{expected_root}/en/core/"
    if (
        target is None
        or target != guarded_target
        or not target.startswith(prefix)
        or not target.endswith(".md")
        or posixpath.normpath(target) != target
    ):
        return None
    return target


def _eligible_plain_label_spans(text: str, label: str) -> list[tuple[int, int]]:
    """Find exact visible, unlinked label occurrences while preserving offsets."""
    from unicodedata import category

    from ydbdoc_review.validation.en_link_targets import _mask_yfm_include_directives

    def is_word_character(char: str) -> bool:
        return char.isalnum() or char == "_" or category(char).startswith("M")

    masked = _mask_link_protected_ranges(_mask_yfm_include_directives(text))
    chars = list(masked)
    for match in _MD_LINK.finditer(masked):
        for index in range(match.start(), match.end()):
            if chars[index] != "\n":
                chars[index] = " "
    visible = "".join(chars)
    spans: list[tuple[int, int]] = []
    for match in re.finditer(re.escape(label), visible):
        start, end = match.span()
        if start and is_word_character(label[0]) and is_word_character(visible[start - 1]):
            continue
        if (
            end < len(visible)
            and is_word_character(label[-1])
            and is_word_character(visible[end])
        ):
            continue
        spans.append((start, end))
    return spans


def propose_frozen_baseline_link_wrapper_repair(
    target_text: str,
    source_text: str,
    *,
    source_base_text: str,
    target_baseline_text: str,
    missing_href: str,
    en_page_path: str,
    read_baseline: DocsTextReader,
    read_final: DocsTextReader,
    docs_root: str = "ydb/docs",
) -> str | None:
    """Propose one byte-bounded wrapper from immutable source/B/K evidence."""
    try:
        from ydbdoc_review.navigation.link_deps import canonical_md_dependency_path
        from ydbdoc_review.parsing.ast_types import InlineLink, InlineText
        from ydbdoc_review.parsing.markdown_parser import parse_markdown
        from ydbdoc_review.segmentation.extractor import extract_segments
        from ydbdoc_review.segmentation.types import SegmentKind
        from ydbdoc_review.validation.en_link_targets import check_en_page_link_targets
        from ydbdoc_review.validation.fragment_repair import (
            fragment_declared_in_markdown,
        )

        if not all(
            isinstance(value, str) and value
            for value in (
                target_text,
                source_text,
                source_base_text,
                target_baseline_text,
                missing_href,
                en_page_path,
            )
        ):
            return None
        if _en_page_docs_root(en_page_path) != docs_root.strip("/"):
            return None

        texts = (source_base_text, source_text, target_baseline_text, target_text)
        docs = [parse_markdown(text) for text in texts]
        segments = [extract_segments(doc) for doc in docs]
        if not segments[0] or len({len(items) for items in segments}) != 1:
            return None
        for aligned in zip(*segments, strict=True):
            if len({item.kind for item in aligned}) != 1:
                return None

        link_rows: list[list[list[object]]] = []
        for doc, extracted in zip(docs, segments, strict=True):
            owned: list[list[object]] = []
            for segment in extracted:
                links = _segment_paragraph_links(doc, segment)
                owned.append(links if links is not None else [])
            link_rows.append(owned)

        source_matches = [
            (ordinal, slot)
            for ordinal, links in enumerate(link_rows[1])
            for slot, link in enumerate(links)
            if getattr(link, "href", None) == missing_href
        ]
        base_matches = [
            (ordinal, slot)
            for ordinal, links in enumerate(link_rows[0])
            for slot, link in enumerate(links)
            if getattr(link, "href", None) == missing_href
        ]
        if len(source_matches) != 1 or base_matches != source_matches:
            return None
        ordinal, slot = source_matches[0]
        if segments[1][ordinal].kind != SegmentKind.PARAGRAPH:
            return None
        if (
            segments[0][ordinal].text != segments[1][ordinal].text
            or segments[0][ordinal].placeholders != segments[1][ordinal].placeholders
        ):
            return None
        source_links = link_rows[1][ordinal]
        baseline_links = link_rows[2][ordinal]
        if len(source_links) != len(baseline_links) or slot >= len(baseline_links):
            return None
        baseline_link = baseline_links[slot]
        if (
            not isinstance(baseline_link, InlineLink)
            or baseline_link.title is not None
            or len(baseline_link.children) != 1
            or not isinstance(baseline_link.children[0], InlineText)
        ):
            return None
        label = baseline_link.children[0].content
        if not label or not label.strip() or label.strip() == "{#T}":
            return None
        baseline_href = baseline_link.href

        source_target = _safe_en_md_target(
            missing_href,
            en_page_path=en_page_path,
            docs_root=docs_root,
        )
        baseline_target = _safe_en_md_target(
            baseline_href,
            en_page_path=en_page_path,
            docs_root=docs_root,
        )
        redirects = read_baseline(f"{docs_root}/redirects.yaml")
        if source_target is None or baseline_target is None or redirects is None:
            return None
        canonical = canonical_md_dependency_path(
            source_target,
            redirects_yaml=redirects,
            docs_root=docs_root,
        )
        baseline_canonical = canonical_md_dependency_path(
            baseline_target,
            redirects_yaml=redirects,
            docs_root=docs_root,
        )
        if (
            canonical is None
            or canonical == source_target
            or baseline_canonical != canonical
        ):
            return None
        baseline_target_text = read_baseline(canonical)
        final_target_text = read_final(canonical)
        if baseline_target_text is None or final_target_text is None:
            return None
        _path, marker, raw_fragment = baseline_href.partition("#")
        if marker:
            if not raw_fragment:
                return None
            fragment = unquote(raw_fragment)
            if not fragment or not all(
                fragment_declared_in_markdown(
                    content,
                    fragment,
                    page_path=canonical,
                    read_text=reader,
                )
                for content, reader in (
                    (baseline_target_text, read_baseline),
                    (final_target_text, read_final),
                )
            ):
                return None

        current_links = link_rows[3][ordinal]
        if any(
            isinstance(link, InlineLink)
            and link.href == baseline_href
            and len(link.children) == 1
            and isinstance(link.children[0], InlineText)
            and link.children[0].content == label
            for link in current_links
        ):
            return None
        spans = _eligible_plain_label_spans(target_text, label)
        if len(spans) != 1:
            return None
        start, end = spans[0]
        proposal = (
            target_text[:start]
            + "["
            + target_text[start:end]
            + f"]({baseline_href})"
            + target_text[end:]
        )

        proposed_doc = parse_markdown(proposal)
        proposed_segments = extract_segments(proposed_doc)
        if len(proposed_segments) != len(segments[3]) or any(
            before.kind != after.kind
            for before, after in zip(segments[3], proposed_segments, strict=True)
        ):
            return None
        proposed_links = _segment_paragraph_links(
            proposed_doc, proposed_segments[ordinal]
        )
        if proposed_links is None or len(proposed_links) != len(source_links):
            return None
        inserted = proposed_links[slot]
        if (
            not isinstance(inserted, InlineLink)
            or inserted.href != baseline_href
            or len(inserted.children) != 1
            or not isinstance(inserted.children[0], InlineText)
            or inserted.children[0].content != label
        ):
            return None
        expected = target_text[:start] + f"[{label}]({baseline_href})" + target_text[end:]
        if proposal != expected:
            return None
        restored = restore_md_link_hrefs(
            proposal,
            source_text,
            source_ru_base=source_base_text,
            target_baseline=proposal,
        )
        if restored.text != proposal or restored.issues:
            return None
        if check_en_page_link_targets(
            en_page_path,
            proposal,
            read_text=read_final,
            baseline_read_text=read_baseline,
        ):
            return None
        return proposal
    except Exception:
        return None


def retarget_source_owned_redirect_hrefs(
    target_text: str,
    source_text: str,
    *,
    en_page_path: str,
    read_text: DocsTextReader,
) -> str:
    """Retarget only visible source-owned href occurrences; preserve other bytes."""
    from ydbdoc_review.validation.en_link_targets import _mask_yfm_include_directives

    def visible(text: str) -> list[re.Match[str]]:
        return list(_iter_visible_md_link_matches(_mask_yfm_include_directives(text)))

    source_hrefs: Counter[str] = Counter()
    target_hrefs: Counter[str] = Counter()
    source_matches = visible(source_text or "")
    target_matches = visible(target_text or "")
    for match in source_matches:
        span = _markdown_destination_span(match.group(2))
        if span is None:
            continue
        _start, _end, raw_href = span
        source_hrefs[raw_href] += 1
    for match in target_matches:
        span = _markdown_destination_span(match.group(2))
        if span is not None:
            target_hrefs[span[2]] += 1

    proven_by_raw: dict[str, str] = {}
    for raw_href in source_hrefs:
        canonical = redirected_md_href(
            raw_href,
            en_page_path=en_page_path,
            read_text=read_text,
        )
        if canonical is not None:
            proven_by_raw[raw_href] = canonical
    if not proven_by_raw:
        return target_text

    group_old_counts: Counter[str] = Counter()
    for raw_href, canonical in proven_by_raw.items():
        group_old_counts[canonical] += source_hrefs[raw_href]
    group_capacity: dict[str, int] = {}
    raw_capacity: dict[str, int] = {}
    for canonical, old_count in group_old_counts.items():
        source_canonical = source_hrefs[canonical]
        target_canonical = target_hrefs[canonical]
        if target_canonical < source_canonical:
            group_capacity[canonical] = 0
            consumed = old_count
        else:
            consumed = target_canonical - source_canonical
            group_capacity[canonical] = max(0, old_count - consumed)
        for raw_href, raw_canonical in proven_by_raw.items():
            if raw_canonical == canonical:
                raw_capacity[raw_href] = max(0, source_hrefs[raw_href] - consumed)

    replacements: list[tuple[int, int, str]] = []
    for match in target_matches:
        span = _markdown_destination_span(match.group(2))
        if span is None:
            continue
        local_start, local_end, raw_href = span
        canonical = proven_by_raw.get(raw_href)
        if (
            canonical is None
            or raw_capacity.get(raw_href, 0) <= 0
            or group_capacity.get(canonical, 0) <= 0
        ):
            continue
        replacement = redirected_md_href(
            raw_href,
            en_page_path=en_page_path,
            read_text=read_text,
        )
        if replacement is None:
            continue
        raw_capacity[raw_href] -= 1
        group_capacity[canonical] -= 1
        replacements.append(
            (match.start(2) + local_start, match.start(2) + local_end, replacement)
        )
    out = target_text
    for start, end, replacement in reversed(replacements):
        out = out[:start] + replacement + out[end:]
    return out


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


def _localized_content_skeleton(text: str) -> str:
    """Compare skeleton ignoring link targets and fenced-code info strings."""
    masked = _mask_link_protected_ranges(text)
    normalized_lines: list[str] = []
    for line in masked.splitlines():
        logical = re.sub(r"^ {0,3}(?:> ?)+", "", line.rstrip("\r\n"))
        opening = _FENCE_OPEN.match(logical)
        if opening:
            normalized_lines.append(f"{opening.group(1)[:3]}")
        else:
            normalized_lines.append(line)
    skeleton = "\n".join(normalized_lines)
    links = list(_MD_LINK.finditer(skeleton))
    return _link_skeleton(skeleton, links).rstrip()


def is_localized_mirror_delta(base: str | None, current: str | None) -> bool:
    """True when texts differ only in internal hrefs and/or fence info strings."""
    if base is None or current is None or base == current:
        return False
    return _localized_content_skeleton(base) == _localized_content_skeleton(current)


def _sync_fence_openers_from_ru(ru_text: str, en_text: str) -> str:
    """Copy RU fence openers onto EN blocks with identical fenced bodies."""
    from ydbdoc_review.parsing.ast_types import FencedCode
    from ydbdoc_review.validation.fence_integrity import code_blocks_from_text

    ru_blocks = code_blocks_from_text(ru_text)
    en_blocks = code_blocks_from_text(en_text)
    if not ru_blocks or len(ru_blocks) != len(en_blocks):
        return en_text
    if any(ru.content != en.content for ru, en in zip(ru_blocks, en_blocks, strict=True)):
        return en_text

    out = en_text
    for ru_block, en_block in zip(ru_blocks, en_blocks, strict=True):
        if not isinstance(ru_block, FencedCode) or not isinstance(en_block, FencedCode):
            continue
        if ru_block.info.strip() == en_block.info.strip():
            continue
        ru_open = f"{ru_block.fence_char * ru_block.fence_len}{ru_block.info}"
        en_open = f"{en_block.fence_char * en_block.fence_len}{en_block.info}"
        if ru_open == en_open:
            continue
        idx = out.find(en_open)
        if idx == -1:
            return en_text
        out = out[:idx] + ru_open + out[idx + len(en_open) :]
    return out


def prefer_resolvable_en_hrefs(
    proposed: str,
    previous: str,
    *,
    en_page_path: str,
    read_text: DocsTextReader,
) -> str:
    """Keep previous EN hrefs when a proposed path is missing on tip (§6.233).

    Merged-source verify can form an inverted RU mirror delta (tip base →
    stale merge RU) and rewrite tip-correct EN paths to historical ones that
    no longer exist. Prefer the previous EN href whenever its file resolves
    and the proposed one does not.
    """
    from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown
    from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

    if not proposed or not previous or proposed == previous:
        return proposed
    prev_links = list(_iter_visible_md_link_matches(previous))
    prop_links = list(_iter_visible_md_link_matches(proposed))
    if len(prev_links) != len(prop_links):
        return proposed
    replacements: list[tuple[int, int, str]] = []
    for prev, prop in zip(prev_links, prop_links, strict=True):
        prev_href = prev.group(2).strip()
        prop_href = prop.group(2).strip()
        if prev_href == prop_href or not _is_internal_href(prop_href):
            continue
        prop_path = resolve_internal_md_href(en_page_path, prop_href)
        prev_path = resolve_internal_md_href(en_page_path, prev_href)
        if prop_path is None:
            continue
        prop_text = read_text(prop_path)
        prev_text = read_text(prev_path) if prev_path is not None else None
        prop_fragment = unquote(prop_href.partition("#")[2])
        prev_fragment = unquote(prev_href.partition("#")[2])
        if prop_fragment and prop_fragment.isascii() and prop_fragment != prev_fragment:
            continue
        prop_ok = prop_text is not None and (
            not prop_fragment
            or fragment_declared_in_markdown(
                prop_text,
                prop_fragment,
                page_path=prop_path,
                read_text=read_text,
            )
        )
        prev_ok = prev_text is not None and (
            not prev_fragment
            or fragment_declared_in_markdown(
                prev_text,
                prev_fragment,
                page_path=prev_path,
                read_text=read_text,
            )
        )
        if prev_ok and not prop_ok:
            replacements.append((prop.start(), prop.end(), f"[{prop.group(1)}]({prev_href})"))
    out = proposed
    for start, end, replacement in reversed(replacements):
        out = out[:start] + replacement + out[end:]
    return out


def reconcile_final_en_same_fragment_paths(
    ru_base_text: str | None,
    ru_current_text: str | None,
    en_tip_text: str | None,
    en_candidate_text: str | None,
    *,
    ru_page_path: str,
    en_page_path: str,
    read_source_ru: DocsTextReader,
    read_final_en: DocsTextReader,
    link_contract_issues: Iterable[LinkContractIssue] = (),
) -> str:
    """Restore a proven tip-EN path without changing an ASCII fragment.

    This final-tree, four-snapshot reconciliation is deliberately narrower
    than pair-level tip preservation.  It only handles an old, uniquely
    traceable RU occurrence whose current source target is broken while the
    corresponding historical EN target remains resolvable in the final tree.
    Any missing evidence is a no-op so the final EN link gate can block.
    """
    from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown
    from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

    if (
        not ru_base_text
        or not ru_current_text
        or not en_tip_text
        or not en_candidate_text
        or any(link_contract_issues)
    ):
        return en_candidate_text or ""

    def _links(text: str) -> list[tuple[str, str, int, int, int, int]]:
        return [
            (
                match.group(1),
                match.group(2).strip(),
                match.start(),
                match.end(),
                match.start(2),
                match.end(2),
            )
            for match in _iter_visible_md_link_matches(text)
            if _is_internal_href(match.group(2).strip())
        ]

    def _decoded_fragment(href: str) -> str | None:
        _path, marker, fragment = href.partition("#")
        decoded = unquote(fragment)
        if not marker or not decoded or not decoded.isascii():
            return None
        return decoded

    def _key(label: str, href: str) -> tuple[str, str]:
        return (" ".join(label.split()).casefold(), unquote(href))

    def _resolves(page_path: str, href: str, reader: DocsTextReader) -> bool:
        path_part, marker, raw_fragment = href.partition("#")
        fragment = unquote(raw_fragment)
        if not marker or not fragment:
            return False
        target = page_path if not path_part else resolve_internal_md_href(page_path, href)
        if target is None:
            return False
        if "/docs/ru/" in page_path:
            # ``resolve_internal_md_href`` intentionally resolves an EN
            # counterpart for the ordinary translation contract. Here the
            # source-authority proof must instead inspect the immutable RU
            # projection at the equivalent locale path.
            target = target.replace("/docs/en/", "/docs/ru/", 1)
        target_text = reader(target)
        return target_text is not None and fragment_declared_in_markdown(
            target_text,
            fragment,
            page_path=target,
            read_text=reader,
        )

    ru_base_links = _links(ru_base_text)
    ru_current_links = _links(ru_current_text)
    en_tip_links = _links(en_tip_text)
    candidate_links = _links(en_candidate_text)
    if (
        len(candidate_links) != len(ru_current_links)
        or len(ru_base_links) != len(en_tip_links)
    ):
        return en_candidate_text

    base_keys = [_key(label, href) for label, href, *_offsets in ru_base_links]
    current_keys = [_key(label, href) for label, href, *_offsets in ru_current_links]
    base_key_counts = Counter(base_keys)
    current_key_counts = Counter(current_keys)
    base_slot_by_key = {
        key: slot for slot, key in enumerate(base_keys) if base_key_counts[key] == 1
    }
    base_fragment_counts = Counter(
        fragment
        for _label, href, *_offsets in ru_base_links
        if (fragment := _decoded_fragment(href)) is not None
    )
    tip_fragment_counts = Counter(
        fragment
        for _label, href, *_offsets in en_tip_links
        if (fragment := _decoded_fragment(href)) is not None
    )

    replacements: list[tuple[int, int, str]] = []
    for slot, (
        (_candidate_label, candidate_href, start, end, href_start, href_end),
        (_current_label, current_href, *_current_offsets),
    ) in enumerate(zip(candidate_links, ru_current_links, strict=True)):
        # The final candidate must still be source-owned at this exact slot.
        if unquote(candidate_href) != unquote(current_href):
            continue
        fragment = _decoded_fragment(candidate_href)
        if fragment is None:
            continue
        key = current_keys[slot]
        if current_key_counts[key] != 1 or base_key_counts[key] != 1:
            continue
        historical_slot = base_slot_by_key[key]
        baseline_href = en_tip_links[historical_slot][1]
        if (
            _decoded_fragment(current_href) != fragment
            or _decoded_fragment(ru_base_links[historical_slot][1]) != fragment
            or _decoded_fragment(baseline_href) != fragment
            or base_fragment_counts[fragment] != 1
            or tip_fragment_counts[fragment] != 1
        ):
            continue
        # A source-owned target is authority. Only ambient broken RU paths may
        # retain their historical EN owner.
        if _resolves(ru_page_path, current_href, read_source_ru):
            continue
        if _resolves(en_page_path, candidate_href, read_final_en):
            continue
        if not _resolves(en_page_path, baseline_href, read_final_en):
            continue
        baseline_path, separator, _baseline_fragment = baseline_href.partition("#")
        candidate_path, _candidate_separator, raw_candidate_fragment = candidate_href.partition("#")
        if not separator or not raw_candidate_fragment:
            continue
        raw_href = en_candidate_text[href_start:href_end]
        raw_path_offset = (
            raw_href.find(candidate_path)
            if candidate_path
            else len(raw_href) - len(raw_href.lstrip())
        )
        if raw_path_offset < 0:
            continue
        path_start = href_start + raw_path_offset
        replacement = (
            en_candidate_text[start:path_start]
            + baseline_path
            + en_candidate_text[path_start + len(candidate_path) :end]
        )
        replacements.append((start, end, replacement))

    out = en_candidate_text
    for start, end, replacement in reversed(replacements):
        out = out[:start] + replacement + out[end:]
    return out


def overlay_internal_md_hrefs(target: str, preferred: str) -> str:
    """Rewrite internal ``[](…md)`` hrefs in ``target`` from ``preferred`` by label.

    For merged-source verify (§6.233): RU body may still carry historical paths
    while tip main already moved them. When labels uniquely match, prefer tip.
    """
    if not target or not preferred or target == preferred:
        return target
    preferred_by_label: dict[str, str] = {}
    ambiguous: set[str] = set()
    for match in _iter_visible_md_link_matches(preferred):
        label = match.group(1)
        href = match.group(2).strip()
        if not label or label.strip() == "{#T}" or not _is_internal_href(href):
            continue
        key = " ".join(label.split()).casefold()
        if key in preferred_by_label and preferred_by_label[key] != href:
            ambiguous.add(key)
            continue
        preferred_by_label[key] = href
    for key in ambiguous:
        preferred_by_label.pop(key, None)
    if not preferred_by_label:
        return target
    out = target
    for match in reversed(list(_iter_visible_md_link_matches(out))):
        label = match.group(1)
        href = match.group(2).strip()
        if not label or label.strip() == "{#T}" or not _is_internal_href(href):
            continue
        key = " ".join(label.split()).casefold()
        tip_href = preferred_by_label.get(key)
        if tip_href and tip_href != href:
            out = out[: match.start()] + f"[{label}]({tip_href})" + out[match.end() :]
    return out


def apply_localized_mirror_delta(
    source_base: str | None,
    source_current: str,
    target_baseline: str | None,
) -> str | None:
    """Apply href and fence-opener deltas from RU to EN without an LLM."""
    if not source_base or target_baseline is None:
        return None
    if not is_localized_mirror_delta(source_base, source_current):
        return None
    out = target_baseline
    if is_href_only_change(source_base, source_current):
        href_applied = apply_href_only_delta(source_base, source_current, target_baseline)
        if href_applied is None:
            return None
        out = href_applied
    out = _sync_fence_openers_from_ru(source_current, out)
    return out


def _is_internal_href(href: str) -> bool:
    href = href.strip()
    if not href or href.startswith("mailto:") or _HTTP.match(href):
        return False
    # Autotitle / markdown / in-page fragment.
    if href.startswith("#"):
        return True
    path = href.split("#", 1)[0].split("?", 1)[0]
    return path.endswith(".md") or path.endswith(".yaml") or path.endswith(".yml")


def collect_internal_hrefs(text: str) -> list[str]:
    """Internal docs hrefs in document order (autotitle + ``[]()``)."""
    if isinstance(text, LinkContractResult):
        text = text.text
    found: list[str] = []
    for href in _AUTO_LINK.findall(_mask_link_protected_ranges(text or "")):
        if _is_internal_href(href):
            found.append(href.strip())
    for match in _iter_visible_md_link_matches(text or ""):
        label, href = match.group(1), match.group(2).strip()
        if label.strip() == "{#T}":
            continue  # already counted via _AUTO_LINK
        if _is_internal_href(href):
            found.append(href)
    return found


def _localized_en_fragment_pairs_ru_remap(
    source_fragment: str,
    target_fragment: str,
    *,
    target_abs: str | None,
    target_md: str,
    docs_text_reader: DocsTextReader,
) -> bool:
    """True when RU ``source_fragment`` maps to declared EN ``target_fragment`` (§6.235)."""
    from ydbdoc_review.validation.fragment_repair import (
        _remap_fragment_via_ru_en_pages,
        fragment_declared_in_markdown,
    )

    if not source_fragment or not target_fragment:
        return False
    if not fragment_declared_in_markdown(target_md, target_fragment):
        return False
    if fragment_declared_in_markdown(target_md, source_fragment):
        return False
    if not target_abs or "/docs/en/" not in target_abs:
        return False
    ru_abs = target_abs.replace("/docs/en/", "/docs/ru/", 1)
    ru_md = docs_text_reader(ru_abs)
    if not ru_md:
        return False
    mapped = _remap_fragment_via_ru_en_pages(source_fragment, ru_md, target_md)
    return mapped == target_fragment


def collect_explicit_anchors(text: str) -> list[str]:
    """Explicit ``{#id}`` ids (headings and rare inline), excluding ``{#T}``."""
    out: list[str] = []
    for match in _EXPLICIT_ANCHOR.finditer(text or ""):
        anchor = match.group(1).strip()
        if not anchor or anchor.upper() == "T":
            continue
        out.append(anchor)
    return out


def _fragment_mapped_by_dictionary(
    source_fragment: str,
    target_fragment: str,
    dictionary: JobAnchorDictionary | None,
) -> bool:
    """True when job dictionary maps RU ``source_fragment`` → EN ``target_fragment``."""
    if not dictionary or not source_fragment or not target_fragment:
        return False
    if dictionary.get(source_fragment) == target_fragment:
        return True
    for ru_key, en_val in dictionary.as_map().items():
        if en_val == target_fragment and ru_key == source_fragment:
            return True
    return False


def _proven_heading_auto_slug_remap(
    source_fragment: str,
    target_fragment: str,
    target_href: str,
    *,
    en_page_path: str | None,
    docs_text_reader: DocsTextReader | None,
) -> bool:
    """Allow only an aligned implicit RU heading slug -> EN auto-slug remap."""
    if not en_page_path or docs_text_reader is None:
        return False

    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href
    from ydbdoc_review.validation.yfm_anchor import (
        _legacy_transliterated_slug,
        diplodoc_auto_slug,
    )

    target_abs = resolve_internal_md_href(en_page_path, target_href)
    if not target_abs or "/docs/en/" not in target_abs:
        return False
    target_md = docs_text_reader(target_abs)
    ru_md = docs_text_reader(target_abs.replace("/docs/en/", "/docs/ru/", 1))
    if not target_md or not ru_md:
        return False

    ru_heads = list(_iter_headings(parse_markdown(ru_md).children))
    en_heads = list(_iter_headings(parse_markdown(target_md).children))
    if len(ru_heads) != len(en_heads) or any(
        ru_h.level != en_h.level
        for ru_h, en_h in zip(ru_heads, en_heads, strict=True)
    ):
        return False

    matches = 0
    for ru_h, en_h in zip(ru_heads, en_heads, strict=True):
        # Explicit ids are stable contracts. The exception is only for the
        # implicit slugs generated from an aligned pair of heading texts.
        if ru_h.anchor or en_h.anchor:
            continue
        ru_title = _heading_plain_text(ru_h)
        ru_slugs = {
            slug
            for slug in (
                diplodoc_auto_slug(ru_title),
                _legacy_transliterated_slug(ru_title),
            )
            if slug
        }
        en_slug = diplodoc_auto_slug(_heading_plain_text(en_h))
        if source_fragment in ru_slugs and target_fragment == en_slug:
            matches += 1
    return matches == 1


def _exact_ascii_fragment_issues(
    source_hrefs: list[str],
    target_hrefs: list[str],
    *,
    en_page_path: str | None,
    docs_text_reader: DocsTextReader | None,
) -> list[str]:
    """Reject occurrence-paired changes to ASCII fragments in matched link slots.

    ASCII fragment ids are locale-independent identifiers (§8).  This check is
    path-local first and cancels exact occurrences before pairing the remainder,
    so ambient extras do not shift matches. With equal link counts it also checks
    cross-path slots positionally for localized paths and redirects. It deliberately
    precedes tip-baseline grandfathering.
    """
    def _issue(source_href: str, target_href: str) -> str | None:
        _, separator, source_fragment = source_href.partition("#")
        _, _, target_fragment = target_href.partition("#")
        if not separator or not source_fragment or not source_fragment.isascii():
            return None
        if source_fragment == target_fragment:
            return None
        if _proven_heading_auto_slug_remap(
            source_fragment,
            target_fragment,
            target_href,
            en_page_path=en_page_path,
            docs_text_reader=docs_text_reader,
        ):
            return None
        return (
            "href_parity: exact ASCII fragment changed: "
            f"`{source_href}` -> `{target_href}`"
        )

    # Equal document-wide link counts prove link-slot identity by position,
    # including same-path links. This avoids pairing one EN auth_config href
    # with the wrong one of multiple RU auth_config hrefs (#52077).
    if len(source_hrefs) == len(target_hrefs):
        return [
            issue
            for source_href, target_href in zip(source_hrefs, target_hrefs, strict=True)
            if (issue := _issue(source_href, target_href)) is not None
        ]

    source_by_path: dict[str, list[str]] = {}
    target_by_path: dict[str, list[str]] = {}
    for href in source_hrefs:
        source_by_path.setdefault(href.partition("#")[0], []).append(href)
    for href in target_hrefs:
        target_by_path.setdefault(href.partition("#")[0], []).append(href)
    issues: list[str] = []
    for source_path, path_sources in source_by_path.items():
        target_remaining = list(target_by_path.get(source_path, []))
        source_remaining: list[str] = []
        for source_href in path_sources:
            try:
                exact_idx = target_remaining.index(source_href)
            except ValueError:
                source_remaining.append(source_href)
            else:
                target_remaining.pop(exact_idx)
        # With unequal global counts, only a unique unmatched pair on the same
        # path is safe. Multiple leftovers are ambiguous and fall through to
        # the generic missing/extra contract report instead of being mispaired.
        if len(source_remaining) == len(target_remaining) == 1:
            issue = _issue(source_remaining[0], target_remaining[0])
            if issue is not None:
                issues.append(issue)
    return issues


def check_href_parity(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
    ignore_basenames: set[str] | frozenset[str] | None = None,
    en_page_path: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
    docs_text_reader: DocsTextReader | None = None,
    en_baseline_text: str | None = None,
    source_baseline_text: str | None = None,
    dictionary: JobAnchorDictionary | None = None,
) -> list[str]:
    if isinstance(target_text, LinkContractResult):
        target_text = target_text.text
    """Blocking when EN internal href multiset ≠ RU (§6.174)."""
    if source_lang.lower() not in {"ru", "russian"}:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []

    # Markdown renderers may percent-encode Unicode fragments. URL decoding is
    # semantics-preserving and avoids false mismatches such as #50854.
    src_ordered = [unquote(href) for href in collect_internal_hrefs(source_text)]
    tgt_ordered = [unquote(href) for href in collect_internal_hrefs(target_text)]
    src = Counter(src_ordered)
    tgt = Counter(tgt_ordered)
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
        src_ordered = [
            href
            for href in src_ordered
            if PurePosixPath(href.split("#", 1)[0]).name not in ignore_basenames
        ]
        tgt_ordered = [
            href
            for href in tgt_ordered
            if PurePosixPath(href.split("#", 1)[0]).name not in ignore_basenames
        ]

    exact_ascii_issues = (
        _exact_ascii_fragment_issues(
            src_ordered,
            tgt_ordered,
            en_page_path=en_page_path,
            docs_text_reader=docs_text_reader,
        )
        if src != tgt
        else []
    )
    if exact_ascii_issues:
        return exact_ascii_issues

    if en_page_path is not None and docs_text_reader is not None:
        canonical_source = retarget_source_owned_redirect_hrefs(
            source_text,
            source_text,
            en_page_path=en_page_path,
            read_text=docs_text_reader,
        )
        canonical_source_baseline = (
            retarget_source_owned_redirect_hrefs(
                source_baseline_text,
                source_baseline_text,
                en_page_path=en_page_path,
                read_text=docs_text_reader,
            )
            if source_baseline_text is not None
            else None
        )
        if canonical_source != source_text:
            source_text = canonical_source
            src_ordered = [
                unquote(href) for href in collect_internal_hrefs(source_text)
            ]
            src = Counter(src_ordered)
            if ignore_basenames:
                src = _kept(src)
                src_ordered = [
                    href
                    for href in src_ordered
                    if PurePosixPath(href.split("#", 1)[0]).name
                    not in ignore_basenames
                ]
        source_baseline_text = canonical_source_baseline

    # Tip-preserved candidate (§6.228 / P9c): when EN hrefs still match tip and
    # no source baseline is in play (verify/candidate gate), RU tip debt and
    # path renames are ambient. Dual-baseline translate (#50904) still sees
    # newly added RU hrefs when ``source_baseline_text`` is set. Exact ASCII
    # fragment parity above is never grandfathered.
    if en_baseline_text is not None and source_baseline_text is None:
        tip_hrefs = Counter(
            unquote(href) for href in collect_internal_hrefs(en_baseline_text)
        )
        if tgt == tip_hrefs:
            return []

    if src == tgt:
        source_visible = list(_iter_visible_md_link_matches(source_text))
        target_visible = list(_iter_visible_md_link_matches(target_text))
        source_label_counts = Counter(match.group(1) for match in source_visible)
        target_label_counts = Counter(match.group(1) for match in target_visible)
        source_label_map = {
            match.group(1): unquote(match.group(2).strip())
            for match in source_visible
            if match.group(1)
            and match.group(1) != "{#T}"
            and _is_internal_href(match.group(2))
            and source_label_counts[match.group(1)] == 1
            and target_label_counts[match.group(1)] == 1
        }
        target_label_map = {
            match.group(1): unquote(match.group(2).strip())
            for match in target_visible
            if match.group(1)
            and match.group(1) != "{#T}"
            and _is_internal_href(match.group(2))
            and source_label_counts[match.group(1)] == 1
            and target_label_counts[match.group(1)] == 1
        }
        shared_labels = source_label_map.keys() & target_label_map.keys()
        if any(source_label_map[label] != target_label_map[label] for label in shared_labels):
            return ["href_parity: same link label points to a different internal href"]
        duplicate_paths = {
            href.split("#", 1)[0]
            for href in src_ordered
            if sum(item.split("#", 1)[0] == href.split("#", 1)[0] for item in src_ordered) > 1
        }
        for path in duplicate_paths:
            src_path_order = [href for href in src_ordered if href.split("#", 1)[0] == path]
            tgt_path_order = [href for href in tgt_ordered if href.split("#", 1)[0] == path]
            if src_path_order == tgt_path_order:
                continue
            source_pairs = {
                match.group(1): unquote(match.group(2).strip())
                for match in _iter_visible_md_link_matches(source_text)
                if match.group(2).strip().split("#", 1)[0] == path
            }
            target_pairs = {
                match.group(1): unquote(match.group(2).strip())
                for match in _iter_visible_md_link_matches(target_text)
                if match.group(2).strip().split("#", 1)[0] == path
            }
            if source_pairs != target_pairs:
                return ["href_parity: repeated-path links have different order"]
        return []

    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    # Preserve pre-existing RU/EN divergence outside the source PR scope.  A
    # newly added RU href is not grandfathered because it is absent from the
    # source baseline (#45949/#50904).
    if source_baseline_text is not None and en_baseline_text is not None:
        src_base = Counter(unquote(href) for href in collect_internal_hrefs(source_baseline_text))
        en_base = Counter(unquote(href) for href in collect_internal_hrefs(en_baseline_text))
        old_missing = src_base - en_base
        old_extra = en_base - src_base
        current_missing = Counter(missing)
        current_extra = Counter(extra)
        missing = sorted((current_missing - old_missing).elements())
        extra = sorted((current_extra - old_extra).elements())
        if not missing and not extra:
            return []
    # §6.237: grandfather can drop the EN ``extra`` when ``en_baseline_text`` is the
    # live tip EN (verify used ``existing_target_text``) while RU path overlay from
    # tip leaves a new ``missing``. Rebuild position-aligned extras for remap.
    # Pair every occurrence (duplicate hrefs like two ``connect.md#tls``, #40385).
    if missing and not extra and en_page_path:
        rebuilt_extra: list[str] = []
        for source_href in missing:
            for position in (
                pos for pos, value in enumerate(src_ordered) if value == source_href
            ):
                if position >= len(tgt_ordered):
                    continue
                target_href = tgt_ordered[position]
                source_path, _, source_fragment = source_href.partition("#")
                target_path, _, target_fragment = target_href.partition("#")
                if source_fragment and target_fragment and source_fragment != target_fragment:
                    # Same path (P3 fragment remap) or tip slot with different path.
                    rebuilt_extra.append(target_href)
                elif (
                    source_path != target_path
                    and PurePosixPath(source_path).name == PurePosixPath(target_path).name
                ):
                    rebuilt_extra.append(target_href)
        extra = rebuilt_extra
    # Position-aligned tip preserve + P3 fragment remap (§8 / §6.228 / #40385).
    # Use nth occurrence → nth slot so duplicate RU hrefs still pair (#40385).
    if missing and extra and en_page_path:
        from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

        used_extra: set[int] = set()
        kept_missing: list[str] = []
        baseline_ordered = [
            unquote(href) for href in collect_internal_hrefs(en_baseline_text or "")
        ]
        occurrence_seen: Counter[str] = Counter()
        for source_href in missing:
            source_path, _, source_fragment = source_href.partition("#")
            source_positions = [
                pos for pos, value in enumerate(src_ordered) if value == source_href
            ]
            occ = occurrence_seen[source_href]
            occurrence_seen[source_href] += 1
            matched = False
            if occ < len(source_positions):
                position = source_positions[occ]
                if position < len(tgt_ordered):
                    target_href = tgt_ordered[position]
                    target_path, _, target_fragment = target_href.partition("#")
                    for idx, cand in enumerate(extra):
                        if idx in used_extra or cand != target_href:
                            continue
                        baseline_ok = (
                            position < len(baseline_ordered)
                            and baseline_ordered[position] == target_href
                        )
                        # Tip already had this EN href at this slot (path and/or fragment).
                        if baseline_ok:
                            used_extra.add(idx)
                            matched = True
                            break
                        same_path = source_path == target_path
                        dict_ok = _fragment_mapped_by_dictionary(
                            source_fragment, target_fragment, dictionary
                        )
                        if (
                            same_path
                            and source_fragment
                            and target_fragment
                            and source_fragment != target_fragment
                        ):
                            if docs_text_reader is None:
                                # Without a docs reader we cannot prove remap or missing
                                # target; keep the pair as a blocker (heuristics smoke).
                                continue
                            target_abs = resolve_internal_md_href(en_page_path, target_href)
                            target_md = docs_text_reader(target_abs) if target_abs else None
                            if not target_md:
                                # Same missing destination: fragment localization is not a
                                # publish blocker (RU twin debt / P3 remap on dead path).
                                used_extra.add(idx)
                                matched = True
                                break
                            remap_ok = _localized_en_fragment_pairs_ru_remap(
                                source_fragment,
                                target_fragment,
                                target_abs=target_abs,
                                target_md=target_md,
                                docs_text_reader=docs_text_reader,
                            )
                            if dict_ok or remap_ok:
                                used_extra.add(idx)
                                matched = True
                                break
                        elif dict_ok and source_fragment and target_fragment:
                            used_extra.add(idx)
                            matched = True
                            break
            if not matched:
                kept_missing.append(source_href)
        missing = kept_missing
        extra = [href for idx, href in enumerate(extra) if idx not in used_extra]
        if not missing and not extra:
            return []
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
    # After pairing missings, drop leftover tip-ambient EN extras (§6.228).
    if extra and en_baseline_text is not None and source_baseline_text is None:
        en_base = Counter(unquote(href) for href in collect_internal_hrefs(en_baseline_text))
        extra = sorted((Counter(extra) - en_base).elements())
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
    dictionary: JobAnchorDictionary | None = None,
) -> list[str]:
    """Blocking when expected EN explicit ``{#id}`` multisets differ.

    ASCII RU anchors must appear unchanged on EN. Cyrillic RU anchors must map
    to their job-dictionary (or ``english_yfm_anchor``) English counterparts.
    """
    if source_lang.lower() not in {"ru", "russian"}:
        return []
    if target_lang.lower() not in {"en", "english"}:
        return []

    from ydbdoc_review.parsing.markdown_parser import parse_markdown

    ru_doc = parse_markdown(source_text)
    en_doc = parse_markdown(target_text)
    ru_heads = list(_iter_headings(ru_doc.children))
    en_heads = list(_iter_headings(en_doc.children))
    dict_ = dictionary if dictionary is not None else JobAnchorDictionary()

    expected: list[str] = []
    outlines_aligned = len(ru_heads) == len(en_heads) and all(
        src.level == tgt.level for src, tgt in zip(ru_heads, en_heads, strict=True)
    )
    if outlines_aligned:
        for src_h, tgt_h in zip(ru_heads, en_heads, strict=True):
            if not src_h.anchor:
                continue
            if is_ascii_yfm_anchor(src_h.anchor):
                expected.append(src_h.anchor)
            else:
                en_text = _heading_plain_text(tgt_h)
                expected.append(dict_.lookup_or_insert(src_h.anchor, en_text))
    else:
        # Drifted outlines: compare ASCII multiset only; Cyrillic RU must not
        # appear verbatim on EN (REQUIREMENTS §8).
        for anchor in collect_explicit_anchors(source_text):
            if is_ascii_yfm_anchor(anchor):
                expected.append(anchor)
            elif dictionary is not None and dictionary.get(anchor):
                expected.append(dictionary.get(anchor) or anchor)
            else:
                minted = english_yfm_anchor(anchor, "") or anchor
                if is_ascii_yfm_anchor(minted):
                    expected.append(minted)
                else:
                    expected.append(dict_.lookup_or_insert(anchor, ""))

    src = Counter(expected)
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


def check_outbound_fragments(
    en_page_path: str,
    en_text: str,
    *,
    read_text: DocsTextReader | None,
    en_baseline_text: str | None = None,
) -> list[str]:
    """Block newly introduced EN links whose target fragment is undeclared."""
    if not en_page_path or not en_text or read_text is None:
        return []
    baseline_hrefs = set(collect_internal_hrefs(en_baseline_text or ""))
    from ydbdoc_review.validation.fragment_repair import fragment_declared_in_markdown
    from ydbdoc_review.validation.glossary_toc_links import resolve_internal_md_href

    issues: list[str] = []
    for href in collect_internal_hrefs(en_text):
        if href in baseline_hrefs or "#" not in href:
            continue
        path_part, fragment = href.split("#", 1)
        if not fragment:
            continue
        target_path = (
            en_page_path if not path_part else resolve_internal_md_href(en_page_path, href)
        )
        if target_path is None:
            continue
        target_md = en_text if target_path == en_page_path else read_text(target_path)
        if not target_md:
            issues.append(
                f"outbound_fragment: `{href}` points to missing EN target "
                f"`{PurePosixPath(target_path).name}`"
            )
            continue
        if fragment_declared_in_markdown(
            target_md,
            fragment,
            page_path=target_path,
            read_text=read_text,
        ):
            continue
        issues.append(
            f"outbound_fragment: `{href}` points to missing EN anchor "
            f"`{PurePosixPath(target_path).name}#{fragment}`"
        )
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
) -> LinkContractResult:
    """Force EN ``[label](href)`` targets to match RU (§6.174 / #49451).

    1. When non-autotitle internal link **counts** match, rewrite each EN href
       to the RU twin in document order (fixes wrong path e.g.
       ``secondary_index.md#example`` → ``min_max_index.md#example``).
    2. When RU still has underrepresented hrefs, wrap plain
       ``see the section Title.`` phrases with ``[Title](href)`` (glossary
       dropped the ``architecture/metadata-services.md`` links).
    """
    if not translated or not source_ru:
        return LinkContractResult(translated)

    ru_links = _iter_md_links(source_ru)
    if not ru_links:
        return LinkContractResult(translated)

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
                return LinkContractResult("".join(reversed(pieces)))
            return LinkContractResult(out)

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

    contract_issues: list[LinkContractIssue] = []
    for slot_index, href in enumerate(missing_hrefs):

        def _wrap(match: re.Match[str], *, _href: str = href) -> str:
            return f"{match.group(1)}[{match.group(2).strip()}]({_href}){match.group(3)}"

        new_out, n = _SEE_SECTION_PLAIN.subn(_wrap, out, count=1)
        if n:
            out = new_out
            continue
        # Source-owned LinkSlot recovery (#51797). The bounded span is exactly
        # the translated segment with the same ordinal and kind as the EN
        # baseline segment that contained this link slot (SPEC-007).
        if target_baseline is not None:
            baseline_links = _iter_md_links(target_baseline)
            slot = next((i for i, item in enumerate(ru_links) if item[1] == href), None)
            if slot is not None and slot < len(baseline_links):
                label = baseline_links[slot][0]
                restored = _restore_link_in_aligned_segment(
                    out,
                    target_baseline,
                    baseline_label=label,
                    baseline_href=baseline_links[slot][1],
                    current_href=href,
                )
                if restored.ok:
                    out = restored.text
                    continue
                contract_issues.extend(restored.issues)
        # No deterministic label evidence. Final link-contract gate blocks it.
        if not any(issue.href == href for issue in contract_issues):
            contract_issues.append(
                LinkContractIssue(
                    code="missing_link_wrapper",
                    message="no unique translated label in aligned LinkSlot span",
                    slot=slot_index,
                    href=href,
                )
            )
    return LinkContractResult(out, tuple(contract_issues))


def _restore_link_in_aligned_segment(
    translated: str,
    target_baseline: str,
    *,
    baseline_label: str,
    baseline_href: str,
    current_href: str,
) -> LinkContractResult:
    """Restore one LinkSlot only inside its aligned segment ordinal.

    Parse/extract both documents, locate the unique EN-baseline segment that
    owns the baseline link, then use the translated segment with the same list
    ordinal and ``SegmentKind``. Exactly one case-sensitive plain-label match
    inside that span is required. Zero/two matches or alignment drift return
    ``None`` so the final contract emits ``missing_link_wrapper``.
    """
    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.rendering.markdown_renderer import render_markdown
    from ydbdoc_review.segmentation.extractor import extract_segments
    from ydbdoc_review.segmentation.reinsert import reinsert_segments

    baseline_doc = parse_markdown(target_baseline)
    translated_doc = parse_markdown(translated)
    baseline_segments = extract_segments(baseline_doc)
    translated_segments = extract_segments(translated_doc)
    needle = f"[{baseline_label}]("
    baseline_ordinals = [
        index
        for index, segment in enumerate(baseline_segments)
        if needle in segment.text
        and any(
            getattr(protected.node, "href", None) == baseline_href
            for protected in segment.placeholders
        )
    ]
    if len(baseline_ordinals) != 1:
        # Non-unique tip baseline slot is ambient (§6.228 / P9c): cannot
        # deterministically restore, but must not hard-block publish.
        return LinkContractResult(translated)
    ordinal = baseline_ordinals[0]
    if ordinal >= len(translated_segments):
        return LinkContractResult(
            translated,
            (
                LinkContractIssue(
                    "ambiguous_link_slot", "aligned segment ordinal is missing", href=current_href
                ),
            ),
        )
    baseline_segment = baseline_segments[ordinal]
    translated_segment = translated_segments[ordinal]
    if translated_segment.kind != baseline_segment.kind:
        return LinkContractResult(
            translated,
            (
                LinkContractIssue(
                    "ambiguous_link_slot", "aligned segment kind differs", href=current_href
                ),
            ),
        )
    occurrences = list(re.finditer(re.escape(baseline_label), translated_segment.text))
    visible = [
        match
        for match in occurrences
        if not any(
            start <= match.start() < end
            for _label, _href, start, end in _iter_md_links(translated_segment.text)
        )
    ]
    if len(visible) != 1:
        code = "missing_link_wrapper" if not visible else "ambiguous_link_slot"
        return LinkContractResult(
            translated,
            (
                LinkContractIssue(
                    code, f"expected one exact label match, found {len(visible)}", href=current_href
                ),
            ),
        )
    match = visible[0]
    actual = translated_segment.text[match.start() : match.end()]
    replacement = (
        translated_segment.text[: match.start()]
        + f"[{actual}]({current_href})"
        + translated_segment.text[match.end() :]
    )
    reinsert_segments(
        translated_doc,
        translated_segments,
        {translated_segment.id: replacement},
    )
    return LinkContractResult(render_markdown(translated_doc, target_lang="en"))


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
