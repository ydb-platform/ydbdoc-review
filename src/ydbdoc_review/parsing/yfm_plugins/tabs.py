"""YFM block plugin: {% list tabs %} ... {% endlist %}."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock


# Opening: {% list tabs %} or {% list tabs accordion %} or {% list tabs radio %}
_TABS_OPEN_RE = re.compile(r"^\{%\s*list\s+tabs(?:\s+(\w+))?\s*%\}\s*$")
# Closing: {% endlist %}
_TABS_CLOSE_RE = re.compile(r"^\{%\s*endlist\s*%\}\s*$")


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

    variant_suffix = m_open.group(1)
    variant = "tabs" if not variant_suffix else f"tabs {variant_suffix}"

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

    old_parent = state.parentType
    old_line_max = state.lineMax
    state.parentType = "yfm_tabs"
    state.lineMax = close_line

    token = state.push("yfm_tabs_open", "div", 1)
    token.markup = first_line
    token.block = True
    token.map = [start_line, close_line + 1]
    token.meta = {"variant": variant}

    # Inner content is a bullet_list of tab items.
    # Recursively tokenize lines (start_line + 1 .. close_line - 1).
    state.md.block.tokenize(state, start_line + 1, close_line)

    token = state.push("yfm_tabs_close", "div", -1)
    token.markup = "{% endlist %}"
    token.block = True

    state.parentType = old_parent
    state.lineMax = old_line_max
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

