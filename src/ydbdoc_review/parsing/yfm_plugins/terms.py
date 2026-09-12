"""YFM term-reference plugin.

- Block: [*term-id]: definition text
- Inline: [*term-id]
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline


_TERM_ID = r"[A-Za-z][A-Za-z0-9_\-]*"
_TERM_DEF_RE = re.compile(rf"^\[\*({_TERM_ID})\]:\s*(.*)$")
_TERM_REF_RE = re.compile(rf"\[\*({_TERM_ID})\]")


def _yfm_term_def_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    """Match a term definition line: [*term-id]: definition text."""
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]

    if pos >= max_pos or state.src[pos] != "[":
        return False
    # Quick check: second char must be '*' to disambiguate from references.
    if pos + 1 >= max_pos or state.src[pos + 1] != "*":
        return False

    line_content = state.src[pos:max_pos]
    m = _TERM_DEF_RE.match(line_content)
    if not m:
        return False

    if silent:
        return True

    term_id = m.group(1)
    definition_text = m.group(2)

    token = state.push("term_definition_open", "div", 1)
    token.markup = f"[*{term_id}]:"
    token.block = True
    token.map = [start_line, start_line + 1]
    token.meta = {"term_id": term_id}

    # The definition text is inline content. Push as an inline token so
    # markdown-it parses it normally.
    inline_token = state.push("inline", "", 0)
    inline_token.content = definition_text
    inline_token.map = [start_line, start_line + 1]
    inline_token.children = []

    state.push("term_definition_close", "div", -1)

    state.line = start_line + 1
    return True


def _yfm_term_ref_rule(state: StateInline, silent: bool) -> bool:
    """Match an inline term reference: [*term-id]."""
    if state.src[state.pos] != "[":
        return False
    if state.pos + 1 >= len(state.src) or state.src[state.pos + 1] != "*":
        return False

    m = _TERM_REF_RE.match(state.src, state.pos)
    if not m:
        return False

    if not silent:
        token = state.push("term_ref", "", 0)
        token.content = m.group(1)
        token.markup = m.group(0)

    state.pos = m.end()
    return True


def yfm_terms_plugin(md: MarkdownIt) -> None:
    """Register term definition (block) and term reference (inline) rules."""
    md.block.ruler.before(
        "reference",
        "term_definition",
        _yfm_term_def_rule,
        {"alt": ["paragraph"]},
    )
    md.inline.ruler.before("link", "term_ref", _yfm_term_ref_rule)

