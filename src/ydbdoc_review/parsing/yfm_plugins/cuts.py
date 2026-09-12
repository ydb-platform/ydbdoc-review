"""YFM block plugin: {% cut "title" %} ... {% endcut %}."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock


# Opening: {% cut "Title text" %}
_CUT_OPEN_RE = re.compile(r"^\{%\s*cut\s+\"([^\"]*)\"\s*%\}\s*$")
# Closing: {% endcut %}
_CUT_CLOSE_RE = re.compile(r"^\{%\s*endcut\s*%\}\s*$")


def _yfm_cut_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    """Match a {% cut %} block container."""
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]

    if state.src[pos] != "{":
        return False

    first_line = state.src[pos:max_pos]
    m_open = _CUT_OPEN_RE.match(first_line)
    if not m_open:
        return False

    if silent:
        return True

    title = m_open.group(1)

    nesting = 1
    close_line = -1
    next_line = start_line + 1

    while next_line < end_line:
        pos2 = state.bMarks[next_line] + state.tShift[next_line]
        max_pos2 = state.eMarks[next_line]
        line_content = state.src[pos2:max_pos2]

        if _CUT_OPEN_RE.match(line_content):
            nesting += 1
        elif _CUT_CLOSE_RE.match(line_content):
            nesting -= 1
            if nesting == 0:
                close_line = next_line
                break

        next_line += 1

    if close_line == -1:
        return False

    old_parent = state.parentType
    old_line_max = state.lineMax
    state.parentType = "yfm_cut"
    state.lineMax = close_line

    token = state.push("yfm_cut_open", "div", 1)
    token.markup = first_line
    token.block = True
    token.map = [start_line, close_line + 1]
    token.meta = {"title": title}

    state.md.block.tokenize(state, start_line + 1, close_line)

    token = state.push("yfm_cut_close", "div", -1)
    token.markup = "{% endcut %}"
    token.block = True

    state.parentType = old_parent
    state.lineMax = old_line_max
    state.line = close_line + 1
    return True


def yfm_cut_plugin(md: MarkdownIt) -> None:
    """Register the {% cut %} block rule."""
    md.block.ruler.before(
        "fence",
        "yfm_cut",
        _yfm_cut_rule,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )

