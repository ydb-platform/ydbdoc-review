"""Strict AST helpers for markdown table translation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.document_mask import (
    MaskRegistry,
    PLACEHOLDER_RE,
    mask_links_split_label,
    mask_translatable_text,
    unmask_text,
)
from ydbdoc_review.placeholder_translate import LineUnit

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_TABLE_SEP_ROW_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_HTML_TAG_COUNTS = ("<ul>", "</ul>", "<li>", "</li>", "<br>", "<br/>", "<br />")


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


def _html_tag_counts(text: str) -> tuple[int, ...]:
    lower = text.lower()
    return tuple(lower.count(tag) for tag in _HTML_TAG_COUNTS)


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
        masked_links = mask_links_split_label(cell, registry)
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
                if (
                    "|" in cand
                    or "\n" in cand
                    or PLACEHOLDER_RE.search(cand)
                    or _html_tag_counts(cand) != _html_tag_counts(p.text)
                ):
                    cand = p.text
                value = cand
            masked_parts.append(value)
        cell_text = unmask_text("".join(masked_parts), registry)
        source_text = unmask_text("".join(p.text for p in cell.parts), registry)
        if _html_tag_counts(cell_text) != _html_tag_counts(source_text):
            cell_text = source_text
        out_cells.append(cell_text)
    return f"{row.leading}|{'|'.join(out_cells)}|{row.trailing}"


_POSSIBLE_VALUES_MARKER = "Possible values:"


def _dedup_possible_values_cell(cell: str) -> str:
    if cell.count(_POSSIBLE_VALUES_MARKER) <= 1:
        return cell
    first = cell.find(_POSSIBLE_VALUES_MARKER)
    second = cell.find(_POSSIBLE_VALUES_MARKER, first + 1)
    if second < 0:
        return cell
    return cell[:second].rstrip()


def _is_table_header_row(ru_cells: list[str], en_cells: list[str]) -> bool:
    if len(ru_cells) < 2 or len(en_cells) < 2:
        return False
    ru_joined = " ".join(ru_cells).lower()
    if any(k in ru_joined for k in ("имя", "описание", "name", "description")):
        return True
    return len(en_cells) > len(ru_cells) and any(
        _CYRILLIC_RE.search(c) for c in en_cells
    )


def repair_table_rows_from_ru(ru_source: str, en_text: str) -> str:
    """
    Fix table column count drift, duplicated HTML in cells, and header row corruption.

    Operates line-by-line when RU and EN share the same line count.
    """
    ru_lines = ru_source.splitlines()
    en_lines = en_text.splitlines()
    if not ru_lines or len(ru_lines) != len(en_lines):
        return en_text

    out: list[str] = []
    changed = False
    for ru_ln, en_ln in zip(ru_lines, en_lines, strict=True):
        ru_parsed = split_table_row(ru_ln)
        en_parsed = split_table_row(en_ln)
        if ru_parsed is None or en_parsed is None:
            out.append(en_ln)
            continue
        ru_lead, ru_cells, ru_trail = ru_parsed
        en_lead, en_cells, en_trail = en_parsed
        if _TABLE_SEP_ROW_RE.match(ru_ln):
            out.append(ru_ln if not _TABLE_SEP_ROW_RE.match(en_ln) else en_ln)
            if en_ln != out[-1]:
                changed = True
            continue

        new_cells = list(en_cells)
        if len(new_cells) > len(ru_cells):
            new_cells = new_cells[: len(ru_cells)]
            changed = True
        if _is_table_header_row(ru_cells, en_cells):
            for i, ru_cell in enumerate(ru_cells):
                if i >= len(new_cells):
                    break
                if _CYRILLIC_RE.search(new_cells[i]) and not _CYRILLIC_RE.search(ru_cell):
                    if i == 0 and re.search(r"(?i)name|имя", ru_cell):
                        new_cells[i] = "Name"
                    elif re.search(r"(?i)description|описание", ru_cell):
                        new_cells[i] = "Description"
                    else:
                        new_cells[i] = ru_cell
                    changed = True

        for i in range(len(new_cells)):
            deduped = _dedup_possible_values_cell(new_cells[i])
            if deduped != new_cells[i]:
                new_cells[i] = deduped
                changed = True
            if _CYRILLIC_RE.search(new_cells[i]):
                for ru_label, en_label in (("выключено", "disabled"),):
                    if f"[{ru_label}]" in new_cells[i]:
                        new_cells[i] = new_cells[i].replace(
                            f"[{ru_label}]", f"[{en_label}]"
                        )
                        changed = True

        new_line = f"{en_lead}|{'|'.join(new_cells)}|{en_trail}"
        if new_line != en_ln:
            changed = True
        out.append(new_line)

    if not changed:
        return en_text
    return "\n".join(out)
