"""YFM inline plugin: {{ variable-name }}."""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline

# YFM variable: {{ name }} with optional whitespace.
# Name allows letters, digits, dash, underscore, dot.
_VARIABLE_RE = re.compile(r"\{\{\s*([\w\-\.]+)\s*\}\}")


def _yfm_variable_rule(state: StateInline, silent: bool) -> bool:
    """Match {{ name }} starting at state.pos."""
    if state.src[state.pos] != "{":
        return False
    if state.pos + 1 >= len(state.src) or state.src[state.pos + 1] != "{":
        return False

    # Try to match the full pattern.
    m = _VARIABLE_RE.match(state.src, state.pos)
    if not m:
        return False

    if not silent:
        token = state.push("yfm_variable", "", 0)
        token.content = m.group(1)  # the name
        token.markup = m.group(0)   # the raw "{{ ... }}"

    state.pos = m.end()
    return True


def yfm_variable_plugin(md: MarkdownIt) -> None:
    """Register the {{ variable }} inline rule.

    Placed before 'text' so that {{ ... }} is recognized first.
    """
    md.inline.ruler.before("text", "yfm_variable", _yfm_variable_rule)

