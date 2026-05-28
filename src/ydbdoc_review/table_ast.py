"""Strict AST helpers for markdown table translation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.document_mask import (
    MaskRegistry,
    PLACEHOLDER_RE,
    mask_translatable_text,
    unmask_text,
)
from ydbdoc_review.placeholder_translate import LineUnit

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TABLE_SEP_ROW_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


@dataclass(frozen=True)
class TableCellPart:
    kind: str  # "ph" | "txt"
    text: str
    unit_id: str = ""
    translatable: bool = False


@dataclass(frozen=True)
class TableCellPlan:
    parts: tuple[TableCellPart, ...]


@dataclass(frozen=True)
class TableRowPlan:
    line_no: int
    leading: str
    trailing: str
    cells: tuple[TableCellPlan, ...]
    is_separator: bool
    raw_line: str


def split_table_row(line: str) -> tuple[str, list[str], str] | None:
    leading = line[: len(line) - len(line.lstrip())]
    core = line[len(leading) :]
    if not core.startswith("|"):
        return None
    last = core.rfind("|")
    if last <= 0:
        return None
    body = core[1:last]
    trailing = core[last + 1 :]
    return leading, body.split("|"), trailing


def _needs_translation(text: str, *, source_is_russian: bool) -> bool:
    if not text.strip():
        return False
    if source_is_russian:
        return bool(_CYRILLIC_RE.search(text))
    return bool(_CYRILLIC_RE.search(text))


def _mask_links_keep_label(text: str, registry: MaskRegistry) -> str:
    out: list[str] = []
    pos = 0
    for m in _LINK_RE.finditer(text):
        out.append(text[pos : m.start()])
        label = m.group(1)
        href = m.group(2)
        open_ph = registry.reserve("LINK_OPEN", "[")
        close_ph = registry.reserve("LINK_CLOSE", f"]({href})")
        out.append(open_ph + label + close_ph)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def build_table_row_plan(
    line: str,
    *,
    line_no: int,
    registry: MaskRegistry,
    source_is_russian: bool,
) -> tuple[TableRowPlan, list[LineUnit]] | None:
    parsed = split_table_row(line)
    if parsed is None:
        return None
    leading, cells, trailing = parsed
    is_sep = bool(_TABLE_SEP_ROW_RE.match(line))
    if is_sep:
        return (
            TableRowPlan(
                line_no=line_no,
                leading=leading,
                trailing=trailing,
                cells=tuple(TableCellPlan(parts=(TableCellPart("txt", c),)) for c in cells),
                is_separator=True,
                raw_line=line,
            ),
            [],
        )

    units: list[LineUnit] = []
    cell_plans: list[TableCellPlan] = []
    for cell_idx, cell in enumerate(cells):
        masked_links = _mask_links_keep_label(cell, registry)
        masked = mask_translatable_text(
            masked_links, registry, include_fences=False, mask_links=False
        )
        parts: list[TableCellPart] = []
        last = 0
        prose_idx = 0
        for m in PLACEHOLDER_RE.finditer(masked):
            if m.start() > last:
                prose = masked[last : m.start()]
                trans = _needs_translation(prose, source_is_russian=source_is_russian)
                uid = ""
                if trans:
                    uid = f"C{line_no:05d}_{cell_idx:02d}_{prose_idx:02d}"
                    prose_idx += 1
                    units.append(LineUnit(unit_id=uid, line_no=line_no, source_line=prose))
                parts.append(
                    TableCellPart("txt", prose, unit_id=uid, translatable=trans)
                )
            parts.append(TableCellPart("ph", m.group(0)))
            last = m.end()
        if last < len(masked):
            prose = masked[last:]
            trans = _needs_translation(prose, source_is_russian=source_is_russian)
            uid = ""
            if trans:
                uid = f"C{line_no:05d}_{cell_idx:02d}_{prose_idx:02d}"
                units.append(LineUnit(unit_id=uid, line_no=line_no, source_line=prose))
            parts.append(TableCellPart("txt", prose, unit_id=uid, translatable=trans))
        if not parts:
            parts = [TableCellPart("txt", "")]
        cell_plans.append(TableCellPlan(parts=tuple(parts)))

    return (
        TableRowPlan(
            line_no=line_no,
            leading=leading,
            trailing=trailing,
            cells=tuple(cell_plans),
            is_separator=False,
            raw_line=line,
        ),
        units,
    )


def render_table_row_plan(
    row: TableRowPlan,
    *,
    translations: dict[str, str],
    registry: MaskRegistry,
) -> str:
    if row.is_separator:
        return row.raw_line
    out_cells: list[str] = []
    for cell in row.cells:
        masked_parts: list[str] = []
        for p in cell.parts:
            if p.kind == "ph":
                masked_parts.append(p.text)
                continue
            value = p.text
            if p.translatable and p.unit_id:
                cand = translations.get(p.unit_id, p.text)
                if "|" in cand or "\n" in cand or PLACEHOLDER_RE.search(cand):
                    cand = p.text
                value = cand
            masked_parts.append(value)
        out_cells.append(unmask_text("".join(masked_parts), registry))
    return f"{row.leading}|{'|'.join(out_cells)}|{row.trailing}"
