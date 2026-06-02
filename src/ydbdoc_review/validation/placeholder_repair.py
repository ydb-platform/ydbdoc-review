"""Restore placeholders when the model emits the original atom instead of ⟦X⟧."""

from __future__ import annotations

import re

from ydbdoc_review.parsing.ast_types import (
    InlineCode,
    InlineLink,
    InlineNode,
    InlineVariable,
)
from ydbdoc_review.segmentation.types import ProtectedInline, Segment
from ydbdoc_review.validation.markers import (
    extract_placeholders,
    placeholders_match,
    realign_placeholders,
)

# Markdown link destinations that are not already placeholders.
_LINK_DEST_RE = re.compile(r"\]\((?!⟦)([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_LEGACY_LINK_RE = re.compile(r"⟦L\d+⟧")


def _is_url_placeholder_template(node: InlineNode) -> bool:
    return isinstance(node, InlineLink) and not node.children and bool(node.href)


def _variable_placeholder(segment: Segment) -> ProtectedInline | None:
    return next(
        (p for p in segment.placeholders if p.placeholder[1] == "V"),
        None,
    )


def _url_placeholders(segment: Segment) -> list[ProtectedInline]:
    return [p for p in segment.placeholders if _is_url_placeholder_template(p.node)]


def _code_placeholders(segment: Segment) -> list[ProtectedInline]:
    return [p for p in segment.placeholders if isinstance(p.node, InlineCode)]


def _repair_legacy_whole_link_marker(segment: Segment, text: str) -> str:
    """Replace obsolete whole-link ``⟦L1⟧`` with the source link skeleton."""
    if not _LEGACY_LINK_RE.search(text):
        return text
    src_link = _LINK_RE.search(segment.text)
    if not src_link:
        return text
    replacement = f"[{src_link.group(1)}]({src_link.group(2)})"
    return _LEGACY_LINK_RE.sub(replacement, text, count=1)


def _prepend_missing_leading_variable(segment: Segment, text: str) -> str:
    """Restore leading ``⟦V⟧`` only when the source segment starts with it."""
    var = _variable_placeholder(segment)
    if var is None or var.placeholder in text or re.search(r"⟦V\d+⟧", text):
        return text
    if not segment.text.lstrip().startswith(var.placeholder):
        return text
    stripped = text.lstrip()
    if isinstance(var.node, InlineVariable) and stripped.startswith(var.node.raw):
        return var.placeholder + stripped[len(var.node.raw) :].lstrip()
    return f"{var.placeholder} {stripped}"


def _strip_stray_leading_variable(segment: Segment, text: str) -> str:
    """Drop a spurious leading ``⟦V⟧`` when the source keeps ``⟦V⟧`` only inside a link."""
    var = _variable_placeholder(segment)
    if var is None or segment.text.lstrip().startswith(var.placeholder):
        return text
    marker = var.placeholder
    if text.lstrip().startswith(marker):
        text = re.sub(
            rf"^\s*{re.escape(marker)}\s+",
            "",
            text.lstrip(),
            count=1,
        )
    return text


def _dedupe_markers_before_first_link(text: str) -> str:
    """Drop stray ``⟦C⟧``/``⟦U⟧`` before ``[anchor](...)`` (duplicate of in-link markers)."""
    match = _LINK_RE.search(text)
    if not match:
        return text
    prefix = text[: match.start()]
    rest = text[match.start() :]
    anchor_ph = set(extract_placeholders(match.group(1)))
    for marker in list(extract_placeholders(prefix)):
        if marker in anchor_ph:
            prefix = prefix.replace(marker, "", 1)
        elif marker[1] == "C" and any(m[1] == "C" for m in anchor_ph):
            prefix = prefix.replace(marker, "", 1)
        elif marker[1] in "CU":
            prefix = prefix.replace(marker, "", 1)
    prefix = re.sub(r"  +", " ", prefix).rstrip()
    if prefix and not prefix.endswith(" "):
        prefix += " "
    return prefix + rest


def _normalize_all_link_anchors(segment: Segment, text: str) -> str:
    """Fix every ``[anchor](dest)`` — restore in-link ``⟦C⟧`` and ``⟦U⟧`` slots."""
    pos = 0
    while True:
        match = _LINK_RE.search(text, pos)
        if not match:
            break
        anchor, dest = match.group(1), match.group(2)
        for protected in _code_placeholders(segment):
            marker = protected.placeholder
            if marker in anchor:
                continue
            node = protected.node
            assert isinstance(node, InlineCode)
            for candidate in (f"`{node.content}`", node.content):
                if candidate in anchor:
                    anchor = anchor.replace(candidate, marker, 1)
                    break
        if isinstance(_variable_placeholder(segment), ProtectedInline):
            var = _variable_placeholder(segment)
            assert var is not None
            marker = var.placeholder
            if marker not in anchor and isinstance(var.node, InlineVariable):
                for candidate in (var.node.raw, var.node.name):
                    if candidate in anchor:
                        anchor = anchor.replace(candidate, marker, 1)
                        break
        url_ph = next((p.placeholder for p in _url_placeholders(segment)), None)
        if url_ph and not dest.startswith("⟦"):
            dest = url_ph
        rebuilt = f"[{anchor}]({dest})"
        text = text[: match.start()] + rebuilt + text[match.end() :]
        pos = match.start() + len(rebuilt)
    return text


def _repair_missing_url_markers(
    text: str, url_placeholders: list[ProtectedInline]
) -> str:
    """Replace bare ``](url)`` destinations with ⟦U⟧ markers in source order."""
    for protected in url_placeholders:
        marker = protected.placeholder
        if marker in text:
            continue
        text, count = _LINK_DEST_RE.subn(f"]({marker})", text, count=1)
        if not count:
            break
    return text


def _repair_atoms_in_order(segment: Segment, text: str) -> str:
    """Replace rendered atoms left-to-right (handles duplicate ``stdin``, etc.)."""
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    cursor = 0
    for protected in segment.placeholders:
        marker = protected.placeholder
        pos = text.find(marker, cursor)
        if pos != -1:
            cursor = pos + len(marker)
            continue

        node = protected.node
        if _is_url_placeholder_template(node):
            href = re.escape(node.href)
            match = re.search(rf"\]\({href}\)", text[cursor:])
            if match:
                start = cursor + match.start()
                end = cursor + match.end()
                text = text[: start + 1] + f"({marker})" + text[end:]
                cursor = start + len(f"]({marker})")
                continue

        if isinstance(node, InlineVariable):
            pattern = re.compile(
                r"\{\{\s*" + re.escape(node.name) + r"\s*\}\}"
            )
            match = pattern.search(text, cursor)
            if match:
                start, end = match.span()
                text = text[:start] + marker + text[end:]
                cursor = start + len(marker)
                continue

        rendered = _render_inline_node(node)
        if rendered:
            pos = text.find(rendered, cursor)
            if pos != -1:
                text = text[:pos] + marker + text[pos + len(rendered) :]
                cursor = pos + len(marker)
                continue

        if isinstance(node, InlineCode):
            for candidate in (f"`{node.content}`", node.content):
                pos = text.find(candidate, cursor)
                if pos != -1:
                    text = text[:pos] + marker + text[pos + len(candidate) :]
                    cursor = pos + len(marker)
                    break
    return text


def _try_realign(segment: Segment, text: str) -> str:
    """Apply index realignment when placeholder count already matches."""
    src_ph = extract_placeholders(segment.text)
    tgt_ph = extract_placeholders(text)
    if len(src_ph) != len(tgt_ph):
        return text
    aligned = realign_placeholders(segment.text, text)
    return aligned if aligned is not None else text


def _repair_core(segment: Segment, translated: str) -> str:
    """Single pass of structural fixes + atom restoration."""
    text = _repair_legacy_whole_link_marker(segment, translated)
    text = _strip_stray_leading_variable(segment, text)
    text = _prepend_missing_leading_variable(segment, text)
    text = _dedupe_markers_before_first_link(text)
    text = _normalize_all_link_anchors(segment, text)
    text = _repair_missing_url_markers(text, _url_placeholders(segment))
    text = _repair_atoms_in_order(segment, text)
    text = _strip_stray_leading_variable(segment, text)
    text = _try_realign(segment, text)
    return text


def _strip_placeholders_preserving_atoms(segment: Segment, text: str) -> str:
    """Remove ⟦markers⟧ but keep code/var/html content for a second repair pass."""
    from ydbdoc_review.rendering.markdown_renderer import _render_inline_node

    for protected in sorted(
        segment.placeholders,
        key=lambda p: text.find(p.placeholder) if p.placeholder in text else -1,
        reverse=True,
    ):
        marker = protected.placeholder
        if marker not in text:
            continue
        node = protected.node
        if _is_url_placeholder_template(node):
            replacement = ""
        else:
            replacement = _render_inline_node(node)
        text = text.replace(marker, replacement, 1)
    return text


def repair_translation_placeholders(segment: Segment, translated: str) -> str:
    """Fix common LLM placeholder mistakes using segment placeholder metadata.

    Runs before strict validation. If markers still diverge, strips placeholders
    to inline atoms and rebuilds markers from segment metadata (last resort).
    """
    text = _repair_core(segment, translated)
    if placeholders_match(segment.text, text):
        return text
    stripped = _strip_placeholders_preserving_atoms(segment, translated)
    text = _repair_core(segment, stripped)
    if placeholders_match(segment.text, text):
        return text
    text = _repair_core(segment, _strip_placeholders_preserving_atoms(segment, text))
    return _try_realign(segment, text)
