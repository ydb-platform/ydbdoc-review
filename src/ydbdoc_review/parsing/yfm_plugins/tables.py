"""YFM block table plugin: ``#|`` … ``|#`` → markdown-it GFM table tokens.

Diplodoc YFM tables are not GFM pipe tables. Without this plugin they become a
single paragraph, so RU/EN segment counts diverge (e.g. 5 vs 48) and
``doc_verify`` blocks on alignment even when the table content matches (§6.147).
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock

_OPEN_RE = re.compile(r"^#\|\s*$")
_CLOSE_RE = re.compile(r"^\|#\s*$")
# Row: || cell | cell ||  (leading/trailing || required)
_ROW_RE = re.compile(r"^\|\|\s*(.*?)\s*\|\|\s*$")


def _split_cells(row_inner: str) -> list[str]:
    """Split YFM row body on `` | `` cell separators (not inside backticks)."""
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    in_code = False
    while i < len(row_inner):
        ch = row_inner[i]
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
            i += 1
            continue
        if (
            not in_code
            and ch == "|"
            and i > 0
            and row_inner[i - 1] == " "
            and i + 1 < len(row_inner)
            and row_inner[i + 1] == " "
        ):
            cells.append("".join(buf).strip())
            buf = []
            i += 2  # skip "| "
            continue
        buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _push_inline(state: StateBlock, content: str) -> None:
    # Do not call inline.parse here — markdown-it core runs it later on
    # ``token.content`` (same as the built-in GFM table rule).
    token = state.push("inline", "", 0)
    token.content = content
    token.children = []


def _yfm_table_rule(
    state: StateBlock, start_line: int, end_line: int, silent: bool
) -> bool:
    pos = state.bMarks[start_line] + state.tShift[start_line]
    max_pos = state.eMarks[start_line]
    first = state.src[pos:max_pos]
    if not _OPEN_RE.match(first):
        return False

    close_line = -1
    next_line = start_line + 1
    rows_raw: list[str] = []
    while next_line < end_line:
        pos2 = state.bMarks[next_line] + state.tShift[next_line]
        max_pos2 = state.eMarks[next_line]
        line = state.src[pos2:max_pos2]
        if _CLOSE_RE.match(line):
            close_line = next_line
            break
        if line.strip():
            rows_raw.append(line)
        next_line += 1

    if close_line == -1 or not rows_raw:
        return False

    parsed_rows: list[list[str]] = []
    for raw in rows_raw:
        m = _ROW_RE.match(raw.strip())
        if not m:
            return False
        parsed_rows.append(_split_cells(m.group(1)))

    if silent:
        return True

    header = parsed_rows[0]
    body = parsed_rows[1:]

    token = state.push("table_open", "table", 1)
    token.map = [start_line, close_line + 1]
    token.markup = "yfm"

    state.push("thead_open", "thead", 1)
    state.push("tr_open", "tr", 1)
    for cell in header:
        state.push("th_open", "th", 1)
        _push_inline(state, cell)
        state.push("th_close", "th", -1)
    state.push("tr_close", "tr", -1)
    state.push("thead_close", "thead", -1)

    state.push("tbody_open", "tbody", 1)
    for row in body:
        state.push("tr_open", "tr", 1)
        for idx in range(len(header)):
            cell = row[idx] if idx < len(row) else ""
            state.push("td_open", "td", 1)
            _push_inline(state, cell)
            state.push("td_close", "td", -1)
        state.push("tr_close", "tr", -1)
    state.push("tbody_close", "tbody", -1)

    state.push("table_close", "table", -1)
    state.line = close_line + 1
    return True


def yfm_table_plugin(md: MarkdownIt) -> None:
    """Register YFM ``#|`` / ``|#`` tables before the paragraph rule."""
    md.block.ruler.before("paragraph", "yfm_table", _yfm_table_rule)
