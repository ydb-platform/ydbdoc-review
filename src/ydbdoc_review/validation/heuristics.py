"""Deterministic post-translation heuristics (Phase E)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

from ydbdoc_review.navigation.paths import navigation_yaml_kind
from ydbdoc_review.navigation.redirects import (
    RedirectValidationIssue,
    validate_redirect_merge,
)
from ydbdoc_review.navigation.toc import TocValidationIssue, validate_toc_merge
from ydbdoc_review.parsing.ast_types import FencedCode
from ydbdoc_review.parsing.markdown_parser import parse_markdown
from ydbdoc_review.validation.ru_source_bugs import normalize_legacy_markdown_structure

_CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")
_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_TABS = re.compile(r"\{%\s*list\s+tabs\b")
_PLACEHOLDER = re.compile(r"⟦[^⟧]+⟧")
# Any leftover protect markers (literal or URL-encoded). Letter+digit is the
# normal form (C/L/I/H/V/T/U/S); broad ⟦…⟧ still blocks odd survivors (§6.164).
_UNRESTORED_PLACEHOLDER = re.compile(r"⟦[^⟧]+⟧")
_UNRESTORED_PLACEHOLDER_ENCODED = re.compile(r"%E2%9F%A6[^%]*%E2%9F%A7", re.IGNORECASE)
# Parser URL stand-ins from link_with_variable; must be restored to ``{{ var }}``
# before publish (#47995 / #48812 left ``yfmvar-0-yfmvarend`` in EN hrefs).
_UNRESTORED_YFMVAR = re.compile(r"yfmvar-\d+-yfmvarend", re.IGNORECASE)
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# Path split by a mid-bold backtick that only wraps the extension:
# ``**/home/…/id_ed25519`.pub`**`` (#49040 / #48968). Do NOT match legitimate
# ``**Box `workflow`**`` / ``**`flag`**`` (§6.177 / #49059 false positive).
_BROKEN_BOLD_INLINE_CODE = re.compile(r"\*\*[^*`\n]*/[^*`\n]+`\.[A-Za-z0-9]+`\*\*")
# Dropped inline code left an empty parenthetical: ``( extension)`` vs
# ``(расширение `.pub`)``.
_EMPTY_EXTENSION_PAREN = re.compile(r"\(\s+extension\s*\)", re.IGNORECASE)

_LENGTH_RATIO_MIN = 0.55
_LENGTH_RATIO_MAX = 1.85
_LENGTH_RATIO_BORDERLINE_MIN = 0.45
_LENGTH_RATIO_BORDERLINE_MAX = 2.2


def _strip_fenced_blocks(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    in_fence = False
    fence_char = ""
    for line in lines:
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
            elif marker[0] == fence_char:
                in_fence = False
            continue
        if not in_fence:
            out.append(line)
    return "".join(out)


def _plain_text_length(text: str) -> int:
    body = _strip_fenced_blocks(text)
    body = _PLACEHOLDER.sub("", body)
    return len(re.sub(r"\s+", "", body))


def check_length_ratio(
    source_text: str,
    target_text: str,
    *,
    source_lang: str,
    target_lang: str,
) -> list[str]:
    """RU↔EN length ratio on prose-like content (fences stripped)."""
    # Raw legacy RU may contain info-bearing closers or a missing closer.  The
    # regex fence stripper then counts code as prose on only one side and emits
    # a false short-translation warning.  Compare the same buildable structure
    # used by translation/layout validation.
    source_text = normalize_legacy_markdown_structure(source_text)
    target_text = normalize_legacy_markdown_structure(target_text)
    src_len = _plain_text_length(source_text)
    tgt_len = _plain_text_length(target_text)
    if src_len < 40 or tgt_len < 40:
        return []
    ratio = tgt_len / src_len if src_len else 0.0
    if _LENGTH_RATIO_MIN <= ratio <= _LENGTH_RATIO_MAX:
        return []
    label = f"{source_lang}→{target_lang}"
    if _LENGTH_RATIO_BORDERLINE_MIN <= ratio < _LENGTH_RATIO_MIN:
        return [f"length_ratio: {label} ratio {ratio:.2f} (short vs source, borderline)"]
    if _LENGTH_RATIO_MAX < ratio <= _LENGTH_RATIO_BORDERLINE_MAX:
        return [f"length_ratio: {label} ratio {ratio:.2f} (long vs source, borderline)"]
    return [f"length_ratio: {label} ratio {ratio:.2f} outside sane bounds"]


def check_unrestored_placeholders(target_text: str, *, target_lang: str) -> list[str]:
    """Protect markers left in the final EN page (literal or percent-encoded)."""
    if target_lang.lower() not in {"en", "english"}:
        return []
    found: list[str] = []
    for match in _UNRESTORED_PLACEHOLDER.finditer(target_text):
        found.append(match.group(0))
    for match in _UNRESTORED_PLACEHOLDER_ENCODED.finditer(target_text):
        found.append(match.group(0))
    if not found:
        return []
    # Stable unique preview (order preserved).
    seen: set[str] = set()
    unique: list[str] = []
    for token in found:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    preview = ", ".join(f"`{t}`" for t in unique[:8])
    extra = f", … (+{len(unique) - 8})" if len(unique) > 8 else ""
    return [
        f"unrestored_placeholder: {len(found)} leftover protect marker(s) in EN: {preview}{extra}"
    ]


def check_unrestored_yfmvar_placeholders(target_text: str, *, target_lang: str) -> list[str]:
    """``yfmvar-N-yfmvarend`` left in EN (link_with_variable failed to restore).

    These are parse-time stand-ins for ``{{ var }}`` inside markdown hrefs.
    If they survive into the published page, links are broken (§6.173 / #48812).
    """
    if target_lang.lower() not in {"en", "english"}:
        return []
    found = _UNRESTORED_YFMVAR.findall(target_text)
    if not found:
        return []
    seen: set[str] = set()
    unique: list[str] = []
    for token in found:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(token)
    preview = ", ".join(f"`{t}`" for t in unique[:8])
    extra = f", … (+{len(unique) - 8})" if len(unique) > 8 else ""
    return [
        f"unrestored_yfmvar: {len(found)} leftover yfmvar placeholder(s) in EN: {preview}{extra}"
    ]


def check_broken_inline_code_markup(target_text: str, *, target_lang: str) -> list[str]:
    """Mangled path+``.ext`` bold/backtick or empty ``( extension)`` (§6.176–§6.177).

    Catches ``**/…/id_ed25519`.pub`**`` after the model drops `` `.pub` `` from a
    parenthetical. Does not flag legitimate ``**Box `workflow`**`` (#49059).
    """
    if target_lang.lower() not in {"en", "english"}:
        return []
    body = _strip_fenced_blocks(target_text)
    issues: list[str] = []
    for match in _BROKEN_BOLD_INLINE_CODE.finditer(body):
        preview = match.group(0).replace("\n", " ").strip()[:80]
        issues.append(f"broken_inline_code: bold+backtick nesting «{preview}»")
    for match in _EMPTY_EXTENSION_PAREN.finditer(body):
        preview = match.group(0)
        issues.append(f"broken_inline_code: empty extension paren «{preview}»")
    if not issues:
        return []
    out = issues[:8]
    if len(issues) > 8:
        out.append(f"broken_inline_code: … (+{len(issues) - 8} more)")
    return out


def check_cyrillic_in_en(target_text: str, *, target_lang: str) -> list[str]:
    """Cyrillic letters in English target outside verbatim code fences."""
    if target_lang.lower() != "en":
        return []
    body = _strip_fenced_blocks(target_text)
    # Explicit YFM anchors are stable identifiers copied byte-for-byte from RU
    # (§6.192). Cyrillic inside ``{#id}`` is therefore not untranslated prose.
    body = re.sub(r"\{#[^}\s]+\}", "", body)
    # #50976: protected certificate Subject notation, copied byte-for-byte.
    body = body.replace("`Имя=Значение,...@<domain>`", "")
    matches = list(_CYRILLIC.finditer(body))
    if not matches:
        return []
    warnings: list[str] = []
    seen_snippets: set[str] = set()
    for match in matches[:12]:
        start = max(0, match.start() - 25)
        end = min(len(body), match.end() + 25)
        snippet = body[start:end].replace("\n", " ").strip()
        if snippet in seen_snippets:
            continue
        seen_snippets.add(snippet)
        line = body.count("\n", 0, match.start()) + 1
        warnings.append(f"Кириллица в EN-тексте (строка ~{line}): «{snippet}»")
    if len(matches) > 12:
        warnings.append(
            f"… и ещё {len(matches) - 12} вхождений кириллицы (всего {len(matches)} символов)"
        )
    return warnings


def check_cyrillic_in_en_all_fences(target_text: str, *, target_lang: str) -> list[str]:
    """Cyrillic inside **any** fenced code block in EN (yaml/yql/go/text/…).

    Prose Cyrillic is covered by ``check_cyrillic_in_en`` (fences stripped).
    Comment-only / ``text``-fence helpers still auto-translate, but residual
    Cyrillic in YAML angle-brackets (``<SID по умолчанию>``, #48595) was
    invisible to those helpers — this check is the hard merge gate (§6.164).
    """
    if target_lang.lower() not in {"en", "english"}:
        return []
    from ydbdoc_review.parsing.ast_types import FencedCode
    from ydbdoc_review.parsing.markdown_parser import parse_markdown
    from ydbdoc_review.validation.fence_integrity import collect_code_blocks

    found: list[str] = []
    blocks = collect_code_blocks(parse_markdown(target_text))
    for block_index, block in enumerate(blocks, start=1):
        if not isinstance(block, FencedCode):
            continue
        lang = (block.info or "").strip().split()[0].lower() if block.info else ""
        for line_index, line in enumerate(block.content.splitlines()):
            if not _CYRILLIC.search(line):
                continue
            preview = line.strip().replace("\n", " ")[:120]
            lang_hint = f" `{lang}`" if lang else ""
            found.append(
                "cyrillic_in_code_fence: "
                f"block {block_index}{lang_hint} line {line_index}: «{preview}»"
            )
    if not found:
        return []
    out = found[:12]
    if len(found) > 12:
        out.append(
            f"cyrillic_in_code_fence: … и ещё {len(found) - 12} строк с кириллицей в code fence"
        )
    return out


def _md_link_basenames(text: str) -> set[str]:
    out: set[str] = set()
    for match in _MD_LINK.finditer(text):
        href = match.group(1).strip().split("#", 1)[0]
        if href.endswith(".md"):
            out.add(PurePosixPath(href).name)
    return out


def check_md_link_parity(
    source_text: str,
    target_text: str,
    *,
    source_lang: str,
    target_lang: str,
    source_file: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
    ignore_basenames: set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Blocking when EN is missing ``.md`` links present in RU (§6.59 index/toc gaps).

    Links whose EN targets sit outside the EN toc graph (intentionally stripped
    in finalize, §6.107 / §6.114) are excluded from the comparison.
    ``ignore_basenames`` covers hrefs already removed by strip in this run
    (§6.156) even when verify-time reachability would no longer exclude them.
    """
    if source_lang.lower() not in {"ru", "russian"} or target_lang.lower() != "en":
        return []
    missing = _md_link_basenames(source_text) - _md_link_basenames(target_text)
    # Same-file self-links (RU often links to its own basename) are not EN gaps.
    if source_file:
        missing.discard(PurePosixPath(source_file).name)
    if ignore_basenames:
        missing -= set(ignore_basenames)
    if missing and en_toc_reachable is not None and source_file:
        from ydbdoc_review.validation.glossary_toc_links import (
            md_link_basenames_outside_reachable,
        )

        ignore = md_link_basenames_outside_reachable(
            source_text,
            file_path=source_file,
            reachable=en_toc_reachable,
        )
        missing -= ignore
    missing_sorted = sorted(missing)
    if not missing_sorted:
        return []
    preview = ", ".join(missing_sorted[:6])
    if len(missing_sorted) > 6:
        preview += f", … (+{len(missing_sorted) - 6})"
    return [f"md_link_parity: EN missing RU links: {preview}"]


def count_fence_markers(text: str) -> int:
    """Opening fence markers inside one segment's text (paragraph/table cells)."""
    return len(_FENCE_OPEN.findall(text))


def _count_fenced_code_blocks(text: str) -> int:
    """Fenced code blocks in a full markdown file (AST), not ``` lines inside blocks."""
    doc = parse_markdown(text)
    count = 0

    def walk(blocks: list) -> None:
        nonlocal count
        for block in blocks:
            if isinstance(block, FencedCode):
                count += 1
            children = getattr(block, "children", None)
            if children:
                walk(children)

    walk(doc.children)
    return count


def check_fence_parity(source_text: str, target_text: str, *, source_lang: str = "ru") -> list[str]:
    from ydbdoc_review.validation.fence_integrity import (
        fence_marker_tokens,
        fence_structure_is_round_trip_stable,
    )

    if not fence_structure_is_round_trip_stable(source_text, lang=source_lang):
        # Translated surrounding list prose can change how our permissive parser
        # groups malformed legacy fences. Exact raw markers plus Diplodoc build
        # are the reliable contract in this narrow case (#50741).
        source_markers = fence_marker_tokens(source_text)
        target_markers = fence_marker_tokens(target_text)
        if source_markers == target_markers:
            return []
        return [
            "fence_parity: unstable source marker sequence differs "
            f"(source {len(source_markers)} vs target {len(target_markers)})"
        ]
    src = _count_fenced_code_blocks(source_text)
    tgt = _count_fenced_code_blocks(target_text)
    if src == tgt:
        return []
    return [f"fence_parity: source {src} fenced blocks vs target {tgt}"]


def check_heading_parity(source_text: str, target_text: str) -> list[str]:
    """Compare heading counts via AST (incl. ``{% if %}`` bodies, §6.156).

    Line-regex ``^#{1,6}`` misses headings indented inside YFM conditionals
    (RU ``  ## ROLLUP`` vs EN ``## ROLLUP`` → false 24 vs 25).
    """
    src = _count_headings_ast(source_text)
    tgt = _count_headings_ast(target_text)
    if src == tgt:
        return []
    return [f"heading_parity: source {src} headings vs target {tgt}"]


def _count_headings_ast(text: str) -> int:
    from ydbdoc_review.parsing.ast_types import Heading, YfmIf

    doc = parse_markdown(text)
    count = 0

    def walk(blocks: list) -> None:
        nonlocal count
        for block in blocks:
            if isinstance(block, Heading):
                count += 1
            if isinstance(block, YfmIf):
                for branch in block.branches:
                    walk(branch.children)
                continue
            children = getattr(block, "children", None)
            if children:
                walk(children)

    walk(doc.children)
    return count


def check_list_tab_parity(source_text: str, target_text: str) -> list[str]:
    src = len(_LIST_TABS.findall(source_text))
    tgt = len(_LIST_TABS.findall(target_text))
    if src == tgt:
        return []
    return [f"list_tab_parity: source {src} tab blocks vs target {tgt}"]


@dataclass
class ClassifiedHeuristics:
    """Heuristic findings split by merge impact."""

    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def all_non_info(self) -> list[str]:
        return [*self.blocking, *self.warnings]


def _classify_heuristic(message: str) -> Literal["blocking", "warnings", "info"]:
    if message.startswith("ru_source"):
        return "info"
    if message.startswith("verify_realign:"):
        # Informational: EN was rebuilt from RU so critic could run (§6.147).
        return "info"
    if message.startswith("verify_realign_partial:"):
        # Same family as verify_realign — gap fill already applied (§6.192 / #37673).
        return "info"
    if message.startswith("verify_realign_skipped:"):
        return "info"
    if message.startswith("glossary_verify_alignment_skipped:"):
        return "info"
    if message.startswith("glossary_verify_finalize_skipped:"):
        return "info"
    if message.startswith("glossary_verify_critic_skipped:"):
        return "info"
    if message.startswith("include_parity_repaired:"):
        return "info"
    if message.startswith("strip_unreachable_links:"):
        # Intentional Variant A repair (§6.107 / §6.152) — not a merge blocker.
        return "info"
    if message.startswith("strip_unreachable_links_failed:"):
        return "warnings"
    if message.startswith("finalize_en_round_trip:"):
        return "warnings"
    if message.startswith("length_ratio:") and "borderline" in message:
        return "warnings"
    if message.startswith("fence_body_copy:"):
        return "warnings"
    # Residual Cyrillic / unrestored protect markers must never green-merge (§6.164).
    if message.startswith("cyrillic_in_fence:"):
        return "blocking"
    if message.startswith("cyrillic_in_text_fence:"):
        return "blocking"
    if message.startswith("cyrillic_in_code_fence:"):
        return "blocking"
    if message.startswith("Кириллица в EN-тексте"):
        return "blocking"
    if message.startswith("fence_comment_translate_skipped:"):
        return "blocking"
    if message.startswith("text_fence_translate_skipped:"):
        return "blocking"
    if message.startswith("prose_cyrillic_translate_skipped:"):
        return "blocking"
    if message.startswith("md_link_parity:"):
        return "blocking"
    if message.startswith("href_parity:"):
        return "blocking"
    if message.startswith("anchor_parity:"):
        return "blocking"
    if message.startswith("inbound_fragment:"):
        return "blocking"
    if message.startswith("unrestored_placeholder:"):
        return "blocking"
    if message.startswith("unrestored_yfmvar:"):
        return "blocking"
    if message.startswith("broken_inline_code:"):
        return "blocking"
    if message.startswith("include_parity:"):
        return "blocking"
    if message.startswith("include_target:"):
        return "blocking"
    return "blocking"


def _collect_raw_heuristics(
    source_text: str,
    target_text: str,
    *,
    normalized_source_text: str,
    source_lang: str,
    target_lang: str,
    source_file: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
    ignore_link_basenames: set[str] | frozenset[str] | None = None,
    docs_text_reader=None,
    docs_repo_path: str | None = None,
    en_baseline_text: str | None = None,
) -> list[str]:
    from ydbdoc_review.validation.fence_comments import (
        check_cyrillic_in_en_fence_comments,
        check_cyrillic_in_en_text_fences,
    )
    from ydbdoc_review.validation.fence_integrity import (
        check_absolute_paths_in_fences,
        check_fence_body_copy,
    )
    from ydbdoc_review.validation.href_parity import (
        check_heading_anchor_parity,
        check_href_parity,
        check_inbound_fragments,
    )
    from ydbdoc_review.validation.link_locale import check_link_locale_in_en
    from ydbdoc_review.validation.ru_source_bugs import (
        check_required_anchor_lines,
        detect_ru_source_bugs,
    )

    raw: list[str] = []
    if source_lang.lower() in {"ru", "russian"}:
        raw.extend(detect_ru_source_bugs(source_text))
    raw.extend(
        check_length_ratio(
            source_text, target_text, source_lang=source_lang, target_lang=target_lang
        )
    )
    raw.extend(check_cyrillic_in_en(target_text, target_lang=target_lang))
    raw.extend(check_unrestored_placeholders(target_text, target_lang=target_lang))
    raw.extend(check_unrestored_yfmvar_placeholders(target_text, target_lang=target_lang))
    raw.extend(check_broken_inline_code_markup(target_text, target_lang=target_lang))
    # Any fence language (yaml/yql/go/text/…) — hard gate for residual RU (§6.164).
    # Comment / text-fence helpers still auto-translate; this catches leftovers
    # those paths miss (e.g. ``<SID по умолчанию>`` in yaml examples).
    raw.extend(check_cyrillic_in_en_all_fences(target_text, target_lang=target_lang))
    # Keep specialized detectors for messaging parity with older reports; they
    # may overlap with all-fences — duplicate messages are acceptable vs silent miss.
    raw.extend(check_cyrillic_in_en_fence_comments(target_text, target_lang=target_lang))
    raw.extend(check_cyrillic_in_en_text_fences(target_text, target_lang=target_lang))
    raw.extend(
        check_md_link_parity(
            source_text,
            target_text,
            source_lang=source_lang,
            target_lang=target_lang,
            source_file=source_file,
            en_toc_reachable=en_toc_reachable,
            ignore_basenames=ignore_link_basenames,
        )
    )
    href_ignore: set[str] = set(ignore_link_basenames or ())
    if en_toc_reachable is not None and source_file and source_lang.lower() in {"ru", "russian"}:
        from ydbdoc_review.validation.glossary_toc_links import (
            md_link_basenames_outside_reachable,
        )

        href_ignore |= md_link_basenames_outside_reachable(
            source_text,
            file_path=source_file,
            reachable=en_toc_reachable,
        )
    en_page = None
    if source_file:
        en_page = (
            source_file.replace("/docs/ru/", "/docs/en/", 1)
            if "/docs/ru/" in source_file
            else source_file
        )
    raw.extend(
        check_href_parity(
            source_text,
            target_text,
            source_lang=source_lang,
            target_lang=target_lang,
            ignore_basenames=href_ignore or None,
            en_page_path=en_page,
            en_toc_reachable=en_toc_reachable,
            docs_text_reader=docs_text_reader,
            en_baseline_text=en_baseline_text,
        )
    )
    raw.extend(
        check_heading_anchor_parity(
            source_text,
            target_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )
    )
    if source_file and target_lang.lower() in {"en", "english"}:
        # Prefer the in-flight EN text for the page under review; other EN
        # pages come from the worktree / upstream tip (§6.174 / #48792).
        raw.extend(
            check_inbound_fragments(
                source_file.replace("/docs/ru/", "/docs/en/", 1)
                if "/docs/ru/" in source_file
                else source_file,
                target_text,
                repo_path=docs_repo_path,
                read_text=docs_text_reader,
                ru_text=source_text if source_lang.lower() in {"ru", "russian"} else None,
                en_baseline_text=en_baseline_text,
            )
        )
    raw.extend(check_fence_parity(normalized_source_text, target_text, source_lang=source_lang))
    raw.extend(
        check_fence_body_copy(
            source_text,
            target_text,
            source_lang=source_lang,
        )
    )
    raw.extend(check_absolute_paths_in_fences(normalized_source_text, target_text))
    raw.extend(check_required_anchor_lines(source_text, target_text))
    raw.extend(check_heading_parity(normalized_source_text, target_text))
    raw.extend(check_list_tab_parity(normalized_source_text, target_text))
    raw.extend(check_link_locale_in_en(target_text, target_lang=target_lang))
    if source_file and source_lang.lower() in {"ru", "russian"}:
        from ydbdoc_review.validation.include_targets import check_include_parity

        raw.extend(
            check_include_parity(
                source_text,
                target_text,
                source_file=source_file,
            )
        )
    return raw


def run_file_heuristics_classified(
    source_text: str,
    target_text: str,
    *,
    normalized_source_text: str,
    source_lang: str = "ru",
    target_lang: str = "en",
    source_file: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
    ignore_link_basenames: set[str] | frozenset[str] | None = None,
    docs_text_reader=None,
    docs_repo_path: str | None = None,
    en_baseline_text: str | None = None,
) -> ClassifiedHeuristics:
    """Run heuristics and split by blocking / warnings / info (RU-source hints)."""
    out = ClassifiedHeuristics()
    for message in _collect_raw_heuristics(
        source_text,
        target_text,
        normalized_source_text=normalized_source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        source_file=source_file,
        en_toc_reachable=en_toc_reachable,
        ignore_link_basenames=ignore_link_basenames,
        docs_text_reader=docs_text_reader,
        docs_repo_path=docs_repo_path,
        en_baseline_text=en_baseline_text,
    ):
        bucket = _classify_heuristic(message)
        getattr(out, bucket).append(message)
    return out


def stripped_link_basenames_from_warnings(warnings: list[str]) -> set[str]:
    """Parse basenames from ``strip_unreachable_links: … [a.md, b.md]`` info lines."""
    out: set[str] = set()
    for message in warnings:
        if not message.startswith("strip_unreachable_links:"):
            continue
        # Prefer explicit basename list: ``…: `a.md`, `b.md```
        for name in re.findall(r"`([^`]+\.md)`", message):
            out.add(PurePosixPath(name).name)
    return out


def run_file_heuristics(
    source_text: str,
    target_text: str,
    *,
    source_lang: str = "ru",
    target_lang: str = "en",
    source_file: str | None = None,
    en_toc_reachable: frozenset[str] | None = None,
) -> list[str]:
    """Run all markdown file heuristics; return non-info warning strings."""
    from ydbdoc_review.validation.ru_source_bugs import normalize_ru_source_for_translation

    norm = (
        normalize_ru_source_for_translation(source_text)
        if source_lang.lower() in {"ru", "russian"}
        else source_text
    )
    classified = run_file_heuristics_classified(
        source_text,
        target_text,
        normalized_source_text=norm,
        source_lang=source_lang,
        target_lang=target_lang,
        source_file=source_file,
        en_toc_reachable=en_toc_reachable,
    )
    return classified.all_non_info


def _issue_strings(issues: list[TocValidationIssue] | list[RedirectValidationIssue]) -> list[str]:
    return [f"{issue.kind}: {issue.detail}" for issue in issues]


def validate_toc_merge_warnings(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_hrefs: set[str],
    en_main_yaml: str,
    translate_include_paths: set[str] | None = None,
) -> list[str]:
    """Wrap ``validate_toc_merge`` for reporting."""
    return _issue_strings(
        validate_toc_merge(
            ru_pr_yaml,
            en_merged_yaml,
            translate_hrefs=translate_hrefs,
            en_main_yaml=en_main_yaml,
            translate_include_paths=translate_include_paths,
        )
    )


def validate_redirect_merge_warnings(
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    translate_from_paths: set[str],
    en_main_yaml: str,
) -> list[str]:
    """Wrap ``validate_redirect_merge`` for reporting."""
    return _issue_strings(
        validate_redirect_merge(
            ru_pr_yaml,
            en_merged_yaml,
            translate_from_paths=translate_from_paths,
            en_main_yaml=en_main_yaml,
        )
    )


def validate_navigation_merge_warnings(
    path: str,
    ru_pr_yaml: str,
    en_merged_yaml: str,
    *,
    en_main_yaml: str,
    translate_scope: set[str],
    translate_include_scope: set[str] | None = None,
) -> list[str]:
    """TOC or redirect merge validation based on ``path`` kind."""
    kind = navigation_yaml_kind(path)
    if kind == "toc":
        return validate_toc_merge_warnings(
            ru_pr_yaml,
            en_merged_yaml,
            translate_hrefs=translate_scope,
            en_main_yaml=en_main_yaml,
            translate_include_paths=translate_include_scope,
        )
    if kind == "redirect":
        return validate_redirect_merge_warnings(
            ru_pr_yaml,
            en_merged_yaml,
            translate_from_paths=translate_scope,
            en_main_yaml=en_main_yaml,
        )
    return []


def bump_verdict_for_heuristics(
    verdict: Literal["ok", "warnings", "blocked"], warnings: list[str]
) -> Literal["ok", "warnings", "blocked"]:
    if warnings and verdict == "ok":
        return "warnings"
    return verdict


def bump_verdict_for_blocking_heuristics(
    verdict: Literal["ok", "warnings", "blocked"],
    blocking: list[str],
) -> Literal["ok", "warnings", "blocked"]:
    if blocking:
        return "blocked"
    return verdict
