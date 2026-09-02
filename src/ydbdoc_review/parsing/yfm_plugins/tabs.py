"""YFM block plugin: {% list tabs %} ... {% endlist %}."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock

from ydbdoc_review.parsing.ast_types import AmbiguousYfmStructureError
from ydbdoc_review.parsing.yfm_plugins.source_spans import utf8_source_span

# Opening: {% list tabs %}, {% list tabs accordion %}, {% list tabs group=lang %}
# (Diplodoc allows bare tokens or key=value after ``tabs`` — §6.194 / #37673).
_TABS_OPEN_RE = re.compile(
    r"^\{%\s*list\s+tabs(?P<rest>(?:\s+(?:\w+=[^\s%]+|\w+))*)\s*%\}\s*$"
)
# Closing: {% endlist %}
_TABS_CLOSE_RE = re.compile(r"^\{%\s*endlist\s*%\}\s*$")
_TAB_ITEM_RE = re.compile(r"^[-+*]\s+(?P<title>.*)$")


def _is_trivia(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped.startswith("<!--")


def _tokenize_tab_body(state: StateBlock, start_line: int, end_line: int, indent: int) -> None:
    """Tokenize one owned source slice after removing only its tab indentation."""
    old_blk_indent = state.blkIndent
    old_line_max = state.lineMax
    old_counts = state.sCount[start_line:end_line]
    try:
        state.blkIndent = indent + 2
        state.lineMax = min(state.lineMax, end_line)
        for line in range(start_line, end_line):
            # A source-owned unindented sibling is legal only after its tab
            # opener.  Give it the tab's virtual block indent so markdown-it
            # keeps it inside this already-delimited source slice.
            state.sCount[line] = max(state.sCount[line], state.blkIndent)
        state.md.block.tokenize(state, start_line, end_line)
    finally:
        state.blkIndent = old_blk_indent
        state.lineMax = old_line_max
        state.sCount[start_line:end_line] = old_counts


def _yfm_tabs_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    """Match a {% list tabs %} block container."""
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]

    if state.src[pos] != "{":
        return False

    first_line = state.src[pos:max_pos]
    m_open = _TABS_OPEN_RE.match(first_line)
    if not m_open:
        return False

    if silent:
        return True

    rest = (m_open.group("rest") or "").strip()
    variant = "tabs" if not rest else f"tabs {rest}"

    # Find the matching {% endlist %}, supporting nested tabs.
    nesting = 1
    close_line = -1
    next_line = start_line + 1

    while next_line < end_line:
        pos2 = state.bMarks[next_line] + state.tShift[next_line]
        max_pos2 = state.eMarks[next_line]
        line_content = state.src[pos2:max_pos2]

        if _TABS_OPEN_RE.match(line_content):
            nesting += 1
        elif _TABS_CLOSE_RE.match(line_content):
            nesting -= 1
            if nesting == 0:
                close_line = next_line
                break

        next_line += 1

    if close_line == -1:
        return False

    opening_indent = state.tShift[start_line]
    direct_headers: list[tuple[int, str, int, int]] = []
    for line in range(start_line + 1, close_line):
        line_start = state.bMarks[line]
        line_end = state.eMarks[line]
        direct = state.src[line_start + state.tShift[line]:line_end]
        if state.tShift[line] != opening_indent:
            continue
        item = _TAB_ITEM_RE.match(direct)
        if item is not None:
            title_start = line_start + state.tShift[line] + item.start("title")
            direct_headers.append((line, item.group("title"), title_start, line_end))
            continue
        if _is_trivia(direct):
            continue
        if not direct_headers or direct.startswith("{%"):
            raise AmbiguousYfmStructureError(
                "unowned direct-depth bytes inside {% list tabs %}"
            )

    parent_container_id = state.env.get("yfm_tabs_parent_container_id")
    container_id = state.env.get("yfm_tabs_next_container_id", 0) + 1
    state.env["yfm_tabs_next_container_id"] = container_id

    token = state.push("yfm_tabs_open", "div", 1)
    token.markup = first_line
    token.block = True
    token.map = [start_line, close_line + 1]
    token.meta = {
        "variant": variant,
        "container_id": container_id,
        "parent_container_id": parent_container_id,
        "opening_indent": opening_indent,
        "opening_span": utf8_source_span(state.src, pos, max_pos),
        "source_span": utf8_source_span(state.src, pos, max_pos),
    }

    previous_parent = state.env.get("yfm_tabs_parent_container_id")
    state.env["yfm_tabs_parent_container_id"] = container_id
    try:
        for index, (header_line, title, title_start, title_end) in enumerate(direct_headers):
            body_end = direct_headers[index + 1][0] if index + 1 < len(direct_headers) else close_line
            tab_open = state.push("yfm_tab_open", "", 1)
            tab_open.block = True
            tab_open.map = [header_line, body_end]
            tab_open.meta = {
                "container_id": container_id,
                "title": title,
                "title_span": utf8_source_span(state.src, title_start, title_end),
                "source_span": utf8_source_span(
                    state.src, state.bMarks[header_line], state.bMarks[body_end]
                ),
            }
            _tokenize_tab_body(state, header_line + 1, body_end, opening_indent)
            tab_close = state.push("yfm_tab_close", "", -1)
            tab_close.block = True
            boundary = state.bMarks[body_end] + state.tShift[body_end]
            tab_close.meta = {
                "container_id": container_id,
                "source_span": utf8_source_span(state.src, boundary, boundary),
            }
    finally:
        if previous_parent is None:
            state.env.pop("yfm_tabs_parent_container_id", None)
        else:
            state.env["yfm_tabs_parent_container_id"] = previous_parent

    token = state.push("yfm_tabs_close", "div", -1)
    token.markup = "{% endlist %}"
    token.block = True
    token.meta = {
        "container_id": container_id,
        "closing_span": utf8_source_span(
            state.src, state.bMarks[close_line], state.eMarks[close_line]
        ),
        "source_span": utf8_source_span(
            state.src,
            state.bMarks[close_line] + state.tShift[close_line],
            state.eMarks[close_line],
        ),
    }
    state.line = close_line + 1
    return True


def yfm_tabs_plugin(md: MarkdownIt) -> None:
    """Register the {% list tabs %} block rule."""
    md.block.ruler.before(
        "fence",
        "yfm_tabs",
        _yfm_tabs_rule,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )
