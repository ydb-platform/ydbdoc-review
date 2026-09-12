"""YFM block plugin: {% note TYPE %} ... {% endnote %}."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock


# Opening: {% note TYPE %} or {% note TYPE "Title" %}
_NOTE_OPEN_RE = re.compile(
    r"^\{%\s*note\s+(\w+)(?:\s+\"([^\"]*)\")?\s*%\}\s*$"
)
# Closing: {% endnote %}
_NOTE_CLOSE_RE = re.compile(r"^\{%\s*endnote\s*%\}\s*$")


def _yfm_note_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    """Match a {% note %} block container."""
    # The first line must contain only the opening tag (after stripping).
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]

    # Quick check: must start with '{'.
    if state.src[pos] != "{":
        return False

    first_line = state.src[pos:max_pos]
    m_open = _NOTE_OPEN_RE.match(first_line)
    if not m_open:
        return False

    if silent:
        return True

    note_type = m_open.group(1)
    title = m_open.group(2)  # may be None

    # Find the matching {% endnote %}, supporting nested notes.
    nesting = 1
    close_line = -1
    next_line = start_line + 1

    while next_line < end_line:
        pos2 = state.bMarks[next_line] + state.tShift[next_line]
        max_pos2 = state.eMarks[next_line]
        line_content = state.src[pos2:max_pos2]

        if _NOTE_OPEN_RE.match(line_content):
            nesting += 1
        elif _NOTE_CLOSE_RE.match(line_content):
            nesting -= 1
            if nesting == 0:
                close_line = next_line
                break

        next_line += 1

    if close_line == -1:
        # Unclosed note — treat as not a note.
        return False

    # Emit tokens. Content is parsed recursively as markdown blocks.
    old_parent = state.parentType
    old_line_max = state.lineMax
    state.parentType = "yfm_note"
    state.lineMax = close_line

    token = state.push("yfm_note_open", "div", 1)
    token.markup = first_line
    token.block = True
    token.map = [start_line, close_line + 1]
    token.meta = {"note_type": note_type, "title": title}

    # Recursively tokenize inner lines (start_line + 1 .. close_line - 1).
    state.md.block.tokenize(state, start_line + 1, close_line)

    token = state.push("yfm_note_close", "div", -1)
    token.markup = "{% endnote %}"
    token.block = True

    state.parentType = old_parent
    state.lineMax = old_line_max
    state.line = close_line + 1
    return True


def yfm_note_plugin(md: MarkdownIt) -> None:
    """Register the {% note %} block rule."""
    md.block.ruler.before(
        "fence",
        "yfm_note",
        _yfm_note_rule,
        {"alt": ["paragraph", "reference", "blockquote", "list"]},
    )

