"""Semantic checks for placeholder placement (not just order)."""

from __future__ import annotations

from ydbdoc_review.parsing.ast_types import InlineLink
from ydbdoc_review.segmentation.types import ProtectedInline, Segment


def _variable_placeholder(segment: Segment) -> ProtectedInline | None:
    return next(
        (p for p in segment.placeholders if p.placeholder[1] == "V"),
        None,
    )


def _url_placeholders(segment: Segment) -> list[ProtectedInline]:
    return [
        p
        for p in segment.placeholders
        if isinstance(p.node, InlineLink) and not p.node.children and bool(p.node.href)
    ]


def variable_in_link_destination(text: str, var_ph: str) -> bool:
    return f"]({var_ph})" in text


def url_marker_in_link_destination(text: str, url_ph: str) -> bool:
    return f"]({url_ph})" in text


def placeholder_roles_valid(segment: Segment, translated: str) -> bool:
    """True when V/U markers sit in the same structural roles as in the source."""
    var = _variable_placeholder(segment)
    if var is not None:
        var_ph = var.placeholder
        src_in_link = variable_in_link_destination(segment.text, var_ph)
        tgt_in_link = variable_in_link_destination(translated, var_ph)
        if tgt_in_link != src_in_link:
            return False

    for protected in _url_placeholders(segment):
        url_ph = protected.placeholder
        src_in_link = url_marker_in_link_destination(segment.text, url_ph)
        tgt_in_link = url_marker_in_link_destination(translated, url_ph)
        if tgt_in_link != src_in_link:
            return False
    return True
