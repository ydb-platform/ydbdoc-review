"""YFM block plugin: {% if EXPR %} ... [{% elsif EXPR %}] ... [{% else %}] ... {% endif %}."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock


# Opening: {% if EXPR %}
_IF_OPEN_RE = re.compile(r"^\{%\s*if\s+(.+?)\s*%\}\s*$")
# Branch: {% elsif EXPR %}
_ELSIF_RE = re.compile(r"^\{%\s*elsif\s+(.+?)\s*%\}\s*$")
# Branch: {% else %}
_ELSE_RE = re.compile(r"^\{%\s*else\s*%\}\s*$")
# Closing: {% endif %}
_IF_CLOSE_RE = re.compile(r"^\{%\s*endif\s*%\}\s*$")


def _yfm_if_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    """Match an {% if %} ... {% endif %} block."""
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]

    if state.src[pos] != "{":
        return False

    first_line = state.src[pos:max_pos]
    m_open = _IF_OPEN_RE.match(first_line)
    if not m_open:
        return False

    if silent:
        return True

    initial_condition = m_open.group(1)

    # Walk forward to find branches and the closing tag, supporting nested {% if %}.
    # We collect (line_number, kind, condition_or_none) markers.
    nesting = 1
    markers: list[tuple[int, str, str | None]] = [
        (start_line, "if", initial_condition)
    ]
    close_line = -1
    next_line = start_line + 1

    while next_line < end_line:
        pos2 = state.bMarks[next_line] + state.tShift[next_line]
        max_pos2 = state.eMarks[next_line]
        line_content = state.src[pos2:max_pos2]

        if _IF_OPEN_RE.match(line_content):
            nesting += 1
        elif _IF_CLOSE_RE.match(line_content):
            nesting -= 1
            if nesting == 0:
                close_line = next_line
                break
        elif nesting == 1:
            m_elsif = _ELSIF_RE.match(line_content)
            if m_elsif:
                markers.append((next_line, "elsif", m_elsif.group(1)))
            elif _ELSE_RE.match(line_content):
                markers.append((next_line, "else", None))

        next_line += 1

    if close_line == -1:
        return False

    old_parent = state.parentType
    old_line_max = state.lineMax
    state.parentType = "yfm_if"

    # Emit the outer open token.
    outer_open = state.push("yfm_if_open", "div", 1)
    outer_open.markup = first_line
    outer_open.block = True
    outer_open.map = [start_line, close_line + 1]

    # For each branch, emit branch_open, tokenize inner lines, emit branch_close.
    for idx, (marker_line, kind, condition) in enumerate(markers):
        branch_body_start = marker_line + 1
        if idx + 1 < len(markers):
            branch_body_end = markers[idx + 1][0]
        else:
            branch_body_end = close_line

        branch_open = state.push("yfm_if_branch_open", "div", 1)
        branch_open.markup = (
            f"{{% if {condition} %}}" if kind == "if"
            else f"{{% elsif {condition} %}}" if kind == "elsif"
            else "{% else %}"
        )
        branch_open.block = True
        branch_open.meta = {"condition": condition, "branch_kind": kind}

        state.lineMax = branch_body_end
        state.md.block.tokenize(state, branch_body_start, branch_body_end)

        state.push("yfm_if_branch_close", "div", -1)

    outer_close = state.push("yfm_if_close", "div", -1)
    outer_close.markup = "{% endif %}"
    outer_close.block = True

    state.parentType = old_parent
    state.lineMax = old_line_max
    state.line = close_line + 1
    return True


def yfm_if_plugin(md: MarkdownIt) -> None:
    """Register the {% if %} block rule."""
    md.block.ruler.before(
        "fence",
        "yfm_if",
        _yfm_if_rule,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )

