"""Line-based structural analysis of YDB markdown for file-level translation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ydbdoc_review.document_segments import (
    _LIST_TABS_START_RE,
    _TABLE_ROW_RE,
    _TABLE_SEP_RE,
    _diplodoc_start,
    _is_fence_toggle,
)
from ydbdoc_review.fence_comments import (
    comment_body_on_line,
    inline_hash_comment_tail,
    inline_sql_comment_tail,
)
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0450-\u045F]")
_DIPLODOC_END = {
    "note": re.compile(r"^\s*\{%\s*endnote\s*%\}\s*$", re.IGNORECASE),
    "cut": re.compile(r"^\s*\{%\s*endcut\s*%\}\s*$", re.IGNORECASE),
    "tabs": re.compile(r"^\s*\{%\s*endlist\s*%\}\s*$", re.IGNORECASE),
}


@dataclass(frozen=True)
class StructureRegion:
    """One contiguous region of a documentation file (1-based line numbers)."""

    start_line: int
    end_line: int
    kind: str
    action: str
    detail: str = ""


def _needs_source_script(text: str, *, source_is_russian: bool) -> bool:
    has_cyr = bool(_CYRILLIC_RE.search(text))
    if source_is_russian:
        return has_cyr
    return bool(text.strip()) and not has_cyr


def _collect_fence_comment_lines(
    lines: list[str], start: int, end: int, *, source_is_russian: bool
) -> list[int]:
    """1-based line numbers inside [start, end] with translatable comments."""
    out: list[int] = []
    for i in range(start, end + 1):
        line = lines[i - 1]
        if _is_fence_toggle(line):
            continue
        body = comment_body_on_line(line)
        if body and _needs_source_script(body[1], source_is_russian=source_is_russian):
            out.append(i)
            continue
        for tail_fn in (inline_sql_comment_tail, inline_hash_comment_tail):
            tail = tail_fn(line)
            if tail and _needs_source_script(tail[1], source_is_russian=source_is_russian):
                out.append(i)
                break
    return out


def _read_fence_block(lines: list[str], start_idx: int) -> tuple[int, int]:
    """0-based indices: start_idx is opening ``` line; returns (start, end inclusive)."""
    end_idx = start_idx
    i = start_idx + 1
    while i < len(lines):
        if _is_fence_toggle(lines[i]):
            end_idx = i
            break
        i += 1
    return start_idx, end_idx


def _read_diplodoc_block(
    lines: list[str], start_idx: int, kind: str
) -> tuple[int, int]:
    end_re = _DIPLODOC_END[kind]
    i = start_idx + 1
    while i < len(lines):
        if end_re.match(lines[i]):
            return start_idx, i
        i += 1
    return start_idx, len(lines) - 1


def _read_table_block(lines: list[str], start_idx: int) -> tuple[int, int]:
    i = start_idx + 1
    while i < len(lines) and _TABLE_ROW_RE.match(lines[i]):
        i += 1
    return start_idx, i - 1


def analyze_document_structure(
    text: str,
    *,
    source_is_russian: bool = True,
) -> list[StructureRegion]:
    """
    Walk the file and return ordered non-overlapping regions with line ranges.

    Line numbers are **1-based** and inclusive (editor style).
    """
    if not text:
        return []

    lines = text.splitlines()
    n = len(lines)
    regions: list[StructureRegion] = []
    prose_start: int | None = None  # 0-based index where current prose run started
    i = 0

    def flush_prose(end_idx: int) -> None:
        nonlocal prose_start
        if prose_start is None:
            return
        if end_idx >= prose_start:
            regions.append(
                StructureRegion(
                    start_line=prose_start + 1,
                    end_line=end_idx + 1,
                    kind="prose",
                    action="translate",
                    detail="Translate headings, lists, and body text.",
                )
            )
        prose_start = None

    while i < n:
        line = lines[i]

        if _is_fence_toggle(line):
            flush_prose(i - 1)
            f0, f1 = _read_fence_block(lines, i)
            sl, el = f0 + 1, f1 + 1
            comment_lines = _collect_fence_comment_lines(
                lines, sl, el, source_is_russian=source_is_russian
            )
            detail = "Copy the fenced block verbatim (including delimiters)."
            if comment_lines:
                nums = ", ".join(str(x) for x in comment_lines)
                detail += (
                    f" Translate only comment text on line(s) {nums}; "
                    "leave all other lines unchanged."
                )
            regions.append(
                StructureRegion(
                    start_line=sl,
                    end_line=el,
                    kind="fence",
                    action="copy_verbatim" if not comment_lines else "fence_comments",
                    detail=detail,
                )
            )
            i = f1 + 1
            continue

        dkind = _diplodoc_start(line)
        if dkind:
            flush_prose(i - 1)
            d0, d1 = _read_diplodoc_block(lines, i, dkind)
            action = "copy_verbatim" if dkind == "tabs" else "translate_diplodoc"
            detail = (
                "Copy the entire {% list tabs %}…{% endlist %} block verbatim "
                "(tab labels, YAML fences, SDK names)."
                if dkind == "tabs"
                else f"Keep {{% {dkind} %}} directives; translate inner text only."
            )
            regions.append(
                StructureRegion(
                    start_line=d0 + 1,
                    end_line=d1 + 1,
                    kind=dkind,
                    action=action,
                    detail=detail,
                )
            )
            i = d1 + 1
            continue

        if _TABLE_ROW_RE.match(line) and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            flush_prose(i - 1)
            t0, t1 = _read_table_block(lines, i)
            regions.append(
                StructureRegion(
                    start_line=t0 + 1,
                    end_line=t1 + 1,
                    kind="table",
                    action="translate_table",
                    detail=(
                        "Translate cell text row by row; keep column count, "
                        "checkmarks (✓), and type identifiers unchanged."
                    ),
                )
            )
            i = t1 + 1
            continue

        if prose_start is None:
            prose_start = i
        i += 1

    flush_prose(n - 1)
    return regions


def format_region_plan(regions: list[StructureRegion]) -> str:
    """Human-readable plan for the translation prompt."""
    if not regions:
        return "_No regions detected._"
    rows: list[str] = []
    for r in regions:
        rows.append(
            f"- **Lines {r.start_line}–{r.end_line}** (`{r.kind}`): {r.detail}"
        )
    return "\n".join(rows)


def split_by_h3_sections(text: str) -> dict[int, str]:
    """
    Split markdown into sections keyed by ### index (0 = preamble before first ###).

    Each value includes the ``###`` heading line when index > 0.
    """
    if not text:
        return {0: ""}

    lines = text.splitlines()
    sections: dict[int, list[str]] = {0: []}
    cur = 0
    for line in lines:
        if line.startswith("### ") and not line.startswith("#### "):
            cur += 1
            sections[cur] = [line]
        else:
            sections.setdefault(cur, []).append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def h3_index_for_line(line_no: int, text: str) -> int:
    """1-based line number → current ### section index (0 = preamble)."""
    cur = 0
    for i, line in enumerate(text.splitlines(), start=1):
        if line.startswith("### ") and not line.startswith("#### "):
            cur += 1
        if i == line_no:
            return cur
    return cur


def regions_for_line_range(
    regions: list[StructureRegion], start_line: int, end_line: int
) -> list[StructureRegion]:
    """Clip *regions* to an inclusive 1-based line window."""
    out: list[StructureRegion] = []
    for r in regions:
        if r.end_line < start_line or r.start_line > end_line:
            continue
        sl = max(r.start_line, start_line)
        el = min(r.end_line, end_line)
        detail = r.detail
        if sl != r.start_line or el != r.end_line:
            detail = f"{r.detail} (partial chunk lines {sl}–{el})"
        out.append(
            StructureRegion(
                start_line=sl,
                end_line=el,
                kind=r.kind,
                action=r.action,
                detail=detail,
            )
        )
    return out
