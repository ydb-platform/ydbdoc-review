"""YFM block plugin: {% include [text](path) %} (single-line directive)."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock


# Match a full include directive on a single line.
# Examples:
#   {% include [text](path) %}
#   {% include notitle [text](path) %}
# The text portion may contain anything that's not a closing ']'.
# The path may contain anything that's not a closing ')'.
_INCLUDE_RE = re.compile(
    r"^\{%\s*include\s+(?:(notitle)\s+)?\[([^\]]*)\]\(([^)]+)\)\s*%\}\s*$"
)


def _yfm_include_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    """Match a {% include ... %} directive on a single line."""
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]

    if state.src[pos] != "{":
        return False

    first_line = state.src[pos:max_pos]
    m = _INCLUDE_RE.match(first_line)
    if not m:
        return False

    if silent:
        return True

    notitle_word = m.group(1)
    text = m.group(2)
    path = m.group(3)

    token = state.push("yfm_include", "div", 0)
    token.markup = first_line
    token.block = True
    token.map = [start_line, start_line + 1]
    token.meta = {
        "notitle": bool(notitle_word),
        "text": text,
        "path": path,
    }

    state.line = start_line + 1
    return True


def yfm_include_plugin(md: MarkdownIt) -> None:
    """Register the {% include %} block rule."""
    md.block.ruler.before(
        "fence",
        "yfm_include",
        _yfm_include_rule,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )

