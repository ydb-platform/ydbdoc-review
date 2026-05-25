"""Parse YDB markdown into ordered translatable units (pipeline v2)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

UnitKind = Literal["prose", "table", "fence", "diplodoc", "tabs"]

_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_DIPLODOC_END = {
    "note": re.compile(r"^\s*\{%\s*endnote\s*%\}\s*$", re.IGNORECASE),
    "cut": re.compile(r"^\s*\{%\s*endcut\s*%\}\s*$", re.IGNORECASE),
    "tabs": re.compile(r"^\s*\{%\s*endlist\s*%\}\s*$", re.IGNORECASE),
}
_LIST_TABS_START_RE = re.compile(r"^\s*\{%\s*list\s+tabs", re.IGNORECASE)


@dataclass(frozen=True)
class DocumentUnit:
    """One ordered piece of a documentation file."""

    kind: UnitKind
    text: str
    label: str


def _is_fence_toggle(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") and len(s) >= 3


def _diplodoc_start(line: str) -> str | None:
    s = line.strip().lower()
    if _LIST_TABS_START_RE.match(line.strip()):
        return "tabs"
    if s.startswith("{% note"):
        return "note"
    if s.startswith("{% cut"):
        return "cut"
    return None


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line))


def _is_table_sep(line: str) -> bool:
    return bool(_TABLE_SEP_RE.match(line))


def _split_prose_and_tables(blob: str, *, label_prefix: str) -> list[DocumentUnit]:
    """Split a prose blob into alternating prose and markdown table units."""
    if not blob.strip():
        return []

    lines = blob.split("\n")
    units: list[DocumentUnit] = []
    buf: list[str] = []
    i = 0
    unit_idx = 0

    def flush_prose() -> None:
        nonlocal unit_idx
        if not buf:
            return
        text = "\n".join(buf)
        buf.clear()
        if text.strip():
            unit_idx += 1
            units.append(
                DocumentUnit(
                    kind="prose",
                    text=text,
                    label=f"{label_prefix}/prose-{unit_idx}",
                )
            )

    while i < len(lines):
        line = lines[i]
        if _is_table_row(line) and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            flush_prose()
            tbl = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and _is_table_row(lines[i]):
                tbl.append(lines[i])
                i += 1
            unit_idx += 1
            units.append(
                DocumentUnit(
                    kind="table",
                    text="\n".join(tbl),
                    label=f"{label_prefix}/table-{unit_idx}",
                )
            )
            continue
        buf.append(line)
        i += 1
    flush_prose()
    return units


def _read_fence(lines: list[str], start: int) -> tuple[str, int]:
    out = [lines[start]]
    i = start + 1
    while i < len(lines):
        out.append(lines[i])
        if _is_fence_toggle(lines[i]) and i > start:
            i += 1
            break
        i += 1
    return "\n".join(out), i


def _read_diplodoc(lines: list[str], start: int, kind: str) -> tuple[str, int]:
    end_re = _DIPLODOC_END[kind]
    out = [lines[start]]
    i = start + 1
    while i < len(lines):
        out.append(lines[i])
        if end_re.match(lines[i]):
            i += 1
            break
        i += 1
    return "\n".join(out), i


def parse_document_units(text: str, *, doc_label: str = "doc") -> list[DocumentUnit]:
    """
    Split *text* into ordered units: prose (incl. ``#``/``###`` headings), tables,
    ``` fences, ``{% list tabs %}`` blocks, and ``{% note %}`` / ``{% cut %}`` blocks.
    """
    if not text:
        return []

    lines = text.split("\n")
    units: list[DocumentUnit] = []
    prose_buf: list[str] = []
    i = 0
    h2 = 0
    h3 = 0

    def label_prefix() -> str:
        if h3:
            return f"{doc_label}/h3-{h3}"
        if h2:
            return f"{doc_label}/h2-{h2}"
        return f"{doc_label}/preamble"

    def flush_prose() -> None:
        if not prose_buf:
            return
        blob = "\n".join(prose_buf)
        prose_buf.clear()
        units.extend(_split_prose_and_tables(blob, label_prefix=label_prefix()))

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("## ") and not stripped.startswith("### "):
            flush_prose()
            h2 += 1
            h3 = 0
            prose_buf.append(line)
            i += 1
            continue

        if stripped.startswith("### "):
            flush_prose()
            h3 += 1
            prose_buf.append(line)
            i += 1
            continue

        if _is_fence_toggle(line):
            flush_prose()
            block, i = _read_fence(lines, i)
            units.append(
                DocumentUnit(
                    kind="fence",
                    text=block,
                    label=f"{label_prefix()}/fence",
                )
            )
            continue

        dkind = _diplodoc_start(line)
        if dkind:
            flush_prose()
            block, i = _read_diplodoc(lines, i, dkind)
            kind: UnitKind = "tabs" if dkind == "tabs" else "diplodoc"
            units.append(
                DocumentUnit(
                    kind=kind,
                    text=block,
                    label=f"{label_prefix()}/{dkind}",
                )
            )
            continue

        prose_buf.append(line)
        i += 1

    flush_prose()
    return units


def assemble_document_units(units: list[DocumentUnit]) -> str:
    """Join translated units in order."""
    return "\n".join(u.text for u in units)
